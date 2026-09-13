"""Research Map builder.

Pulls every article / preprint / review that OpenAlex says was published in the
last N days, estimates an *expected-impact* score for each one (new papers have
no citations yet, so we lean on author / venue / institution priors plus any
early citation signal), aggregates onto the OpenAlex topic hierarchy
(domain > field > subfield > topic) and writes web/data.json for the D3 map.

Usage:
    python build.py                      # last 7 days ending yesterday
    python build.py --days 7 --end 2026-09-11 --baseline-weeks 8
    python build.py --skip-authors       # faster: venue/institution prior only
    python build.py --refetch            # ignore the raw day cache

Raw pages are cached under data/raw/ so scoring can be iterated offline.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import os
import random
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

try:  # corporate proxy: trust the OS certificate store
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover
    pass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RAW = DATA / "raw"
ENT = DATA / "entities"
SNAP = DATA / "snapshots"
WEB = ROOT / "web"
for p in (RAW, ENT, SNAP, WEB):
    p.mkdir(parents=True, exist_ok=True)

API = "https://api.openalex.org"
MAILTO = os.environ.get("OPENALEX_MAILTO", "koh.kidoguchi@gmail.com")
HEADERS = {"User-Agent": f"research-map/0.1 (mailto:{MAILTO})"}
TYPES = "article|preprint|review"
WORK_SELECT = ",".join(
    [
        "id", "doi", "title", "publication_date", "type", "cited_by_count", "fwci",
        "referenced_works_count", "primary_topic", "topics", "authorships",
        "primary_location", "open_access", "language",
    ]
)
THREADS = 5
ENTITY_TTL_DAYS = 30
MAX_AUTHORS_PER_WORK = 5      # first, last, corresponding + fill
SHORTLIST_PER_FIELD = 140     # works per field that get author-level scoring
SHORTLIST_CAP = 4000
# entity lookups are the budget hog (free tier: 1,000 requests/day). Long-tail venues / institutions that
# appear in only a couple of the week's papers are skipped (scored as unknown = 0); over a 7-day window
# sources>=2 keeps 97% of mentions in ~155 requests, institutions>=4 ~95% in ~300.
MIN_SOURCE_MENTIONS = 2
MIN_INST_MENTIONS = 4
TOP_PAPERS_PER_TOPIC = 3
TOP_PAPERS_OVERALL = 300
MAX_EDGES = 400

_session = requests.Session()
_session.headers.update(HEADERS)
_lock = threading.Lock()
_stats = Counter()
_budget = {"remaining": None, "exhausted": False}
OFFLINE = False


class BudgetExhausted(RuntimeError):
    """OpenAlex free tier: 1,000 requests ($0.10) per day, resets at midnight UTC."""


# ----------------------------------------------------------------------------- HTTP
def get_json(url: str, params: dict | None = None, retries: int = 6) -> dict:
    if OFFLINE or _budget["exhausted"]:
        raise BudgetExhausted("offline / budget exhausted")
    params = dict(params or {})
    params.setdefault("mailto", MAILTO)
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, timeout=90)
            with _lock:
                _stats["requests"] += 1
                rem = r.headers.get("X-RateLimit-Remaining")
                if rem is not None:
                    _budget["remaining"] = int(rem)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429 and ("budget" in r.text.lower() or int(r.headers.get("Retry-After", "0") or 0) > 300):
                _budget["exhausted"] = True
                raise BudgetExhausted(r.text[:200])
            if r.status_code in (429, 500, 502, 503, 504):
                wait = min(60, 2 ** attempt + random.random())
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {r.status_code} for {r.url[:200]}: {r.text[:200]}")
        except (requests.ConnectionError, requests.Timeout):
            time.sleep(min(60, 2 ** attempt + random.random()))
    raise RuntimeError(f"gave up on {url} {params}")


def paginate(path: str, params: dict, key: str = "results", per_page: int = 200):
    """Cursor pagination generator (works for /works, /topics and group_by)."""
    cursor = "*"
    while cursor:
        page = get_json(f"{API}{path}", {**params, "per-page": per_page, "cursor": cursor})
        yield from page.get(key, [])
        cursor = page.get("meta", {}).get("next_cursor")


def oa_id(x: str | None) -> str | None:
    return x.rsplit("/", 1)[-1] if x else None


# ----------------------------------------------------------------------------- topics
def load_topics() -> dict[str, dict]:
    f = ENT / "topics.json"
    if f.exists() and (time.time() - f.stat().st_mtime) < ENTITY_TTL_DAYS * 86400:
        return json.loads(f.read_text(encoding="utf-8"))
    print("fetching topic hierarchy ...")
    topics = {}
    for t in paginate("/topics", {"select": "id,display_name,subfield,field,domain,keywords,works_count"}):
        topics[oa_id(t["id"])] = {
            "id": oa_id(t["id"]),
            "name": t["display_name"],
            "subfield": oa_id(t["subfield"]["id"]),
            "subfield_name": t["subfield"]["display_name"],
            "field": oa_id(t["field"]["id"]),
            "field_name": t["field"]["display_name"],
            "domain": oa_id(t["domain"]["id"]),
            "domain_name": t["domain"]["display_name"],
            "keywords": (t.get("keywords") or [])[:6],
            "works_total": t.get("works_count", 0),
        }
    f.write_text(json.dumps(topics, ensure_ascii=False), encoding="utf-8")
    print(f"  {len(topics)} topics")
    return topics


# ----------------------------------------------------------------------------- works
def slim_work(w: dict) -> dict | None:
    pt = w.get("primary_topic") or {}
    if not pt.get("id"):
        return None
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    authors = []
    for a in w.get("authorships") or []:
        au = a.get("author") or {}
        authors.append(
            {
                "id": oa_id(au.get("id")),
                "name": au.get("display_name"),
                "pos": a.get("author_position"),
                "corr": bool(a.get("is_corresponding")),
                "inst": [oa_id(i.get("id")) for i in (a.get("institutions") or []) if i.get("id")],
                "inst_names": [i.get("display_name") for i in (a.get("institutions") or [])][:2],
                "countries": a.get("countries") or [],
            }
        )
    return {
        "id": oa_id(w["id"]),
        "doi": w.get("doi"),
        "title": w.get("title") or "(untitled)",
        "date": w.get("publication_date"),
        "type": w.get("type"),
        "cited": w.get("cited_by_count") or 0,
        "fwci": w.get("fwci"),
        "nref": w.get("referenced_works_count") or 0,
        "topic": oa_id(pt["id"]),
        "topic_score": pt.get("score"),
        "topics": [oa_id(t["id"]) for t in (w.get("topics") or [])[:3] if t.get("id")],
        "source": oa_id(src.get("id")),
        "source_name": src.get("display_name"),
        "source_type": src.get("type"),
        "url": loc.get("landing_page_url") or w.get("doi"),
        "oa": bool((w.get("open_access") or {}).get("is_oa")),
        "lang": w.get("language"),
        "n_authors": len(w.get("authorships") or []),
        "authors": authors,
    }


def fetch_day_field(day: str, field_id: str) -> list[dict]:
    flt = f"from_publication_date:{day},to_publication_date:{day},type:{TYPES},primary_topic.field.id:{field_id}"
    out = []
    for w in paginate("/works", {"filter": flt, "select": WORK_SELECT}):
        s = slim_work(w)
        if s:
            out.append(s)
    return out


STALE_HOURS = 60   # a day fetched sooner than this after its date was still being ingested -> refetch


def load_day(day: str, field_ids: list[str], refetch: bool = False) -> list[dict]:
    f = RAW / f"works_{day}.jsonl.gz"
    if f.exists() and not refetch and not OFFLINE and not _budget["exhausted"]:
        fetched_after_h = (f.stat().st_mtime - dt.datetime.fromisoformat(day).timestamp()) / 3600
        if fetched_after_h < STALE_HOURS:
            print(f"  {day}: cached copy was fetched only {fetched_after_h:.0f}h after the date -> refetching")
            refetch = True
    if f.exists() and (not refetch or OFFLINE or _budget["exhausted"]):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]
    t0 = time.time()
    works: list[dict] = []
    with ThreadPoolExecutor(THREADS) as ex:
        futs = {ex.submit(fetch_day_field, day, fid): fid for fid in field_ids}
        for fut in as_completed(futs):
            works.extend(fut.result())
    with gzip.open(f, "wt", encoding="utf-8") as fh:
        for w in works:
            fh.write(json.dumps(w, ensure_ascii=False) + "\n")
    print(f"  {day}: {len(works):6d} works  ({time.time() - t0:.0f}s, {_stats['requests']} req so far)")
    return works


# ----------------------------------------------------------------------------- entities
def load_entity_cache(name: str) -> dict:
    f = ENT / f"{name}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_entity_cache(name: str, cache: dict) -> None:
    (ENT / f"{name}.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def fetch_entities(kind: str, ids, select: str, slimmer, priority: Counter | None = None) -> dict:
    """Batch-fetch sources / institutions / authors / works by id with a TTL cache.

    `priority` (id -> weight) orders the fetch so that, if the daily budget runs out
    half-way, the ids that matter most are the ones already cached. Progress is
    saved every few batches and on budget exhaustion; the build then continues
    with whatever is cached and reports what is still missing.
    """
    cache = load_entity_cache(kind)
    now = time.time()
    missing = [i for i in ids if i and (i not in cache or now - cache[i].get("_t", 0) > ENTITY_TTL_DAYS * 86400)]
    if priority:
        missing.sort(key=lambda i: -priority.get(i, 0))
    if not missing:
        return cache
    if OFFLINE or _budget["exhausted"]:
        print(f"  {kind}: {len(missing)} not cached (offline / budget exhausted) -> scored without them")
        _stats[f"missing_{kind}"] = len(missing)
        return cache
    print(f"fetching {len(missing)} {kind} ({len(ids) - len(missing)} cached, budget left {_budget['remaining']}) ...")
    batches = [missing[i:i + 50] for i in range(0, len(missing), 50)]

    def one(batch):
        j = get_json(f"{API}/{kind}", {"filter": "ids.openalex:" + "|".join(batch), "select": select, "per-page": 50})
        found = [slimmer(e) for e in j.get("results", [])]
        got = {e["id"] for e in found}
        found += [{"id": i, "missing": True} for i in batch if i not in got]  # remember empties too
        return found

    done = 0
    try:
        with ThreadPoolExecutor(THREADS) as ex:
            futs = [ex.submit(one, b) for b in batches]
            for fut in as_completed(futs):
                try:
                    res = fut.result()
                except BudgetExhausted:
                    for f in futs:
                        f.cancel()
                    raise
                for e in res:
                    e["_t"] = now
                    cache[e["id"]] = e
                done += 1
                if done % 40 == 0:
                    save_entity_cache(kind, cache)
    except BudgetExhausted:
        left = len(batches) - done
        _stats[f"missing_{kind}"] = left * 50
        print(f"  budget exhausted after {done}/{len(batches)} {kind} batches; ~{left * 50} ids left for tomorrow")
    save_entity_cache(kind, cache)
    return cache


def slim_source(s):
    ss = s.get("summary_stats") or {}
    return {"id": oa_id(s["id"]), "name": s.get("display_name"), "type": s.get("type"),
            "c2": ss.get("2yr_mean_citedness") or 0.0, "h": ss.get("h_index") or 0,
            "doaj": bool(s.get("is_in_doaj")), "host": s.get("host_organization_name")}


def slim_inst(i):
    ss = i.get("summary_stats") or {}
    return {"id": oa_id(i["id"]), "name": i.get("display_name"), "cc": i.get("country_code"),
            "type": i.get("type"), "c2": ss.get("2yr_mean_citedness") or 0.0, "h": ss.get("h_index") or 0}


def slim_author(a):
    ss = a.get("summary_stats") or {}
    return {"id": oa_id(a["id"]), "c2": ss.get("2yr_mean_citedness") or 0.0, "h": ss.get("h_index") or 0,
            "i10": ss.get("i10_index") or 0, "n": a.get("works_count") or 0}


def slim_refs(w):
    return {"id": oa_id(w["id"]), "refs": [oa_id(r) for r in (w.get("referenced_works") or [])]}


# ----------------------------------------------------------------------------- links
MAX_LINKS_PER_PAPER = 4
MAX_LINKS = 6000
GENERIC_REF_CUTOFF = 150   # a reference cited by this many displayed papers is a textbook / method, not a tie


def build_links(paper_ids: set[str], works_by_id: dict, refs: dict) -> list[dict]:
    """Edges between displayed papers: bibliographic coupling (shared references),
    direct citation between new papers, and shared authors."""
    ids = [i for i in paper_ids if i in works_by_id]
    ref_lists = {i: set(refs.get(i, {}).get("refs") or []) for i in ids}
    inv: dict[str, list[str]] = defaultdict(list)
    for i, rs in ref_lists.items():
        for r in rs:
            inv[r].append(i)
    shared: Counter = Counter()
    for r, ps in inv.items():
        if len(ps) < 2 or len(ps) > GENERIC_REF_CUTOFF:
            continue
        ps.sort()
        for x in range(len(ps)):
            for y in range(x + 1, len(ps)):
                shared[(ps[x], ps[y])] += 1
    cand: dict[tuple, dict] = {}
    for (a, b), n in shared.items():
        if n < 2:
            continue
        cos = n / math.sqrt(max(1, len(ref_lists[a])) * max(1, len(ref_lists[b])))
        cand[(a, b)] = {"a": a, "b": b, "k": "ref", "n": n, "w": round(cos, 3)}
    # direct citation among new papers
    pid = set(ids)
    for i in ids:
        for r in ref_lists[i]:
            if r in pid and r != i:
                a, b = sorted((i, r))
                e = cand.get((a, b))
                if e:
                    e["k"] = "cite"; e["w"] = round(max(e["w"], 0.6), 3)
                else:
                    cand[(a, b)] = {"a": a, "b": b, "k": "cite", "n": 1, "w": 0.6}
    # shared authors
    by_author: dict[str, list[str]] = defaultdict(list)
    for i in ids:
        for au in works_by_id[i]["authors"]:
            if au["id"]:
                by_author[au["id"]].append(i)
    for au, ps in by_author.items():
        if len(ps) < 2 or len(ps) > 12:
            continue
        ps = sorted(set(ps))
        for x in range(len(ps)):
            for y in range(x + 1, len(ps)):
                key = (ps[x], ps[y])
                e = cand.get(key)
                if e:
                    e["n"] += 0; e["w"] = round(min(1.0, e["w"] + 0.25), 3); e["au"] = True
                else:
                    cand[key] = {"a": ps[x], "b": ps[y], "k": "au", "n": 0, "w": 0.3, "au": True}
    # keep the strongest few per paper, then a global cap
    per: dict[str, list[dict]] = defaultdict(list)
    for e in cand.values():
        per[e["a"]].append(e); per[e["b"]].append(e)
    keep: dict[tuple, dict] = {}
    for p, es in per.items():
        es.sort(key=lambda e: e["w"], reverse=True)
        for e in es[:MAX_LINKS_PER_PAPER]:
            keep[(e["a"], e["b"])] = e
    links = sorted(keep.values(), key=lambda e: e["w"], reverse=True)[:MAX_LINKS]
    print(f"links: {len(cand)} candidates -> {len(links)} kept "
          f"(ref {sum(e['k']=='ref' for e in links)}, cite {sum(e['k']=='cite' for e in links)}, author {sum(e['k']=='au' for e in links)})")
    return links


# ----------------------------------------------------------------------------- baseline
def fetch_topic_baseline(start: str, end: str) -> dict[str, int]:
    """Topic counts over a longer prior window (group_by, cheap) for share-based momentum."""
    f = ENT / f"baseline_{start}_{end}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    print(f"fetching topic baseline {start}..{end} ...")
    counts = {}
    flt = f"from_publication_date:{start},to_publication_date:{end},type:{TYPES}"
    for g in paginate("/works", {"filter": flt, "group_by": "primary_topic.id"}, key="group_by"):
        if g.get("key"):
            counts[oa_id(g["key"])] = g["count"]
    f.write_text(json.dumps(counts), encoding="utf-8")
    return counts


# ----------------------------------------------------------------------------- scoring
def pct_rank_within(groups: dict[str, list[float]]) -> dict[str, dict[float, float]]:
    """Return per-group mapping value -> percentile (0..1), ties averaged."""
    out = {}
    for g, vals in groups.items():
        s = sorted(vals)
        n = len(s)
        ranks: dict[float, float] = {}
        i = 0
        while i < n:
            j = i
            while j + 1 < n and s[j + 1] == s[i]:
                j += 1
            ranks[s[i]] = ((i + j) / 2 + 0.5) / n if n else 0.5
            i = j + 1
        out[g] = ranks
    return out


def score_works(works: list[dict], topics: dict, sources: dict, insts: dict, authors: dict | None) -> None:
    """Attach w['s_*'] components and w['score'] (0..100) in place."""
    by_field_src: dict[str, list[float]] = defaultdict(list)
    by_field_inst: dict[str, list[float]] = defaultdict(list)
    by_field_auth: dict[str, list[float]] = defaultdict(list)
    for w in works:
        t = topics.get(w["topic"])
        w["field"] = t["field"] if t else "?"
        src = sources.get(w["source"] or "", {})
        w["_src_raw"] = math.log1p(src.get("c2", 0.0)) if not src.get("missing") else 0.0
        inst_vals = [insts.get(i, {}).get("c2", 0.0) for a in w["authors"] for i in a["inst"]]
        w["_inst_raw"] = math.log1p(max(inst_vals)) if inst_vals else 0.0
        by_field_src[w["field"]].append(w["_src_raw"])
        by_field_inst[w["field"]].append(w["_inst_raw"])
        if authors is not None and w.get("_shortlisted"):
            hs = [authors.get(a["id"], {}).get("h", 0) for a in w["authors"][:MAX_AUTHORS_PER_WORK] if a["id"]]
            c2s = [authors.get(a["id"], {}).get("c2", 0.0) for a in w["authors"][:MAX_AUTHORS_PER_WORK] if a["id"]]
            w["_auth_raw"] = (math.log1p(max(hs)) if hs else 0.0) * 0.6 + (math.log1p(max(c2s)) if c2s else 0.0) * 0.4
            by_field_auth[w["field"]].append(w["_auth_raw"])
    src_pct = pct_rank_within(by_field_src)
    inst_pct = pct_rank_within(by_field_inst)
    auth_pct = pct_rank_within(by_field_auth)

    for w in works:
        f = w["field"]
        s_src = src_pct[f][w["_src_raw"]] if w["_src_raw"] > 0 else 0.0
        s_inst = inst_pct[f][w["_inst_raw"]] if w["_inst_raw"] > 0 else 0.0
        quality = (0.4 * (w["nref"] > 0) + 0.3 * any(a["inst"] for a in w["authors"])
                   + 0.2 * (w["source"] is not None) + 0.1 * (w["n_authors"] >= 2))
        early = min(1.0, math.log1p(w["cited"]) / math.log1p(20)) * 0.7 + min(1.0, (w.get("fwci") or 0) / 5) * 0.3
        if w.get("_auth_raw") is not None and f in auth_pct:
            s_auth = auth_pct[f][w["_auth_raw"]] if w["_auth_raw"] > 0 else 0.0
            score = 0.32 * s_src + 0.15 * s_inst + 0.33 * s_auth + 0.10 * quality + 0.10 * early
            w["s_auth"] = round(s_auth, 3)
        else:
            # no author data: cap so that un-shortlisted works cannot outrank shortlisted ones by construction
            score = (0.55 * s_src + 0.25 * s_inst + 0.10 * quality + 0.10 * early) * 0.85
            w["s_auth"] = None
        w["s_src"], w["s_inst"], w["s_q"], w["s_early"] = round(s_src, 3), round(s_inst, 3), round(quality, 2), round(early, 3)
        w["score"] = round(100 * score, 1)


def pick_shortlist(works: list[dict]) -> list[dict]:
    """Works that deserve author-level lookups: top-N per field by prior + anything already cited."""
    by_field: dict[str, list[dict]] = defaultdict(list)
    for w in works:
        by_field[w["field"]].append(w)
    chosen: list[dict] = []
    for f, ws in by_field.items():
        ws.sort(key=lambda w: (w["score"], w["cited"]), reverse=True)
        chosen.extend(ws[:SHORTLIST_PER_FIELD])
        chosen.extend(w for w in ws[SHORTLIST_PER_FIELD:] if w["cited"] >= 2)
    seen = set()
    out = []
    for w in sorted(chosen, key=lambda w: w["score"], reverse=True):
        if w["id"] not in seen:
            seen.add(w["id"])
            out.append(w)
    return out[:SHORTLIST_CAP]


def author_ids_for(works: list[dict]) -> set[str]:
    ids = set()
    for w in works:
        auths = w["authors"]
        prio = [a for a in auths if a["pos"] in ("first", "last") or a["corr"]]
        rest = [a for a in auths if a not in prio]
        for a in (prio + rest)[:MAX_AUTHORS_PER_WORK]:
            if a["id"]:
                ids.add(a["id"])
    return ids


# ----------------------------------------------------------------------------- aggregation
def paper_view(w: dict, insts: dict, sources: dict, authors: dict | None) -> dict:
    first = next((a for a in w["authors"] if a["pos"] == "first"), w["authors"][0] if w["authors"] else None)
    inst_name = None
    cc = None
    if first:
        if first["inst"]:
            i = insts.get(first["inst"][0], {})
            inst_name, cc = i.get("name"), i.get("cc")
        inst_name = inst_name or (first["inst_names"][0] if first["inst_names"] else None)
        cc = cc or (first["countries"][0] if first["countries"] else None)
    top_h = None
    if authors:
        hs = [authors.get(a["id"], {}).get("h") for a in w["authors"] if a["id"] in authors]
        hs = [h for h in hs if h is not None]
        top_h = max(hs) if hs else None
    return {
        "id": w["id"], "t": w["title"][:220], "d": w["date"], "ty": w["type"],
        "u": w["url"], "src": w["source_name"], "au": first["name"] if first else None,
        "na": w["n_authors"], "inst": inst_name, "cc": cc, "sc": w["score"],
        "c": w["cited"], "h": top_h, "oa": w["oa"], "tp": w["topic"],
        "comp": [w["s_src"], w["s_inst"], w["s_auth"], w["s_q"], w["s_early"]],
    }


def build_output(works, topics, sources, insts, authors, baseline, window, generated) -> dict:
    total = len(works)
    base_total = sum(baseline.values()) or 1
    by_topic: dict[str, list[dict]] = defaultdict(list)
    for w in works:
        by_topic[w["topic"]].append(w)

    topic_rows = []
    papers: dict[str, dict] = {}
    for tid, ws in by_topic.items():
        t = topics.get(tid)
        if not t:
            continue
        n = len(ws)
        share = n / total
        base_share = (baseline.get(tid, 0) + 0.5) / (base_total + 0.5 * len(topics))
        momentum = math.log2((share + 1e-7) / (base_share + 1e-7))
        ws.sort(key=lambda w: w["score"], reverse=True)
        cc = Counter()
        for w in ws:
            seen = set()
            for a in w["authors"]:
                for c in a["countries"]:
                    if c not in seen:
                        cc[c] += 1
                        seen.add(c)
        top = ws[:TOP_PAPERS_PER_TOPIC]
        for w in top:
            papers[w["id"]] = paper_view(w, insts, sources, authors)
        scores = [w["score"] for w in ws]
        topic_rows.append({
            "id": tid, "name": t["name"], "sub": t["subfield"], "sub_name": t["subfield_name"],
            "field": t["field"], "field_name": t["field_name"], "domain": t["domain"], "domain_name": t["domain_name"],
            "kw": t["keywords"], "n": n, "base": baseline.get(tid, 0),
            "w": round(sum(scores) / 100, 2),              # weighted volume (sum of score/100)
            "avg": round(sum(scores) / n, 1), "max": max(scores),
            "mom": round(momentum, 3), "cited": sum(w["cited"] for w in ws),
            "cc": cc.most_common(5), "top": [w["id"] for w in top],
        })

    # cross-topic edges (secondary topic co-assignment)
    edge = Counter()
    for w in works:
        for t2 in w["topics"]:
            if t2 != w["topic"] and t2 in topics:
                a, b = sorted((w["topic"], t2))
                edge[(a, b)] += 1
    edges = [{"a": a, "b": b, "n": n} for (a, b), n in edge.most_common(MAX_EDGES)]

    # field / domain aggregates incl. country mix
    fields: dict[str, dict] = {}
    domains: dict[str, dict] = {}
    for w in works:
        t = topics.get(w["topic"])
        if not t:
            continue
        for key, store, name in ((t["field"], fields, t["field_name"]), (t["domain"], domains, t["domain_name"])):
            row = store.setdefault(key, {"id": key, "name": name, "n": 0, "w": 0.0, "cc": Counter(), "cited": 0,
                                         "domain": t["domain"] if store is fields else None})
            row["n"] += 1
            row["w"] += w["score"] / 100
            row["cited"] += w["cited"]
            seen = set()
            for a in w["authors"]:
                for c in a["countries"]:
                    if c not in seen:
                        row["cc"][c] += 1
                        seen.add(c)
    for store in (fields, domains):
        for row in store.values():
            row["w"] = round(row["w"], 1)
            row["cc"] = row["cc"].most_common(8)

    works_sorted = sorted(works, key=lambda w: w["score"], reverse=True)
    top_overall = []
    for w in works_sorted[:TOP_PAPERS_OVERALL]:
        papers[w["id"]] = paper_view(w, insts, sources, authors)
        top_overall.append(w["id"])

    # source and country leaderboards for context
    src_counter = Counter(w["source_name"] for w in works if w["source_name"])
    return {
        "generated": generated,
        "window": window,
        "totals": {"works": total, "topics": len(topic_rows), "baseline_works": base_total,
                   "shortlisted": sum(1 for w in works if w.get("_shortlisted")),
                   "with_author_scores": sum(1 for w in works if w.get("s_auth") is not None)},
        "domains": sorted(domains.values(), key=lambda r: -r["n"]),
        "fields": sorted(fields.values(), key=lambda r: -r["n"]),
        "topics": topic_rows,
        "edges": edges,
        "top": top_overall,
        "papers": papers,
        "top_sources": src_counter.most_common(30),
        "method": {
            "types": TYPES,
            "score": "0.32*venue + 0.15*institution + 0.33*author + 0.10*metadata quality + 0.10*early citations; "
                     "venue/institution/author = within-field percentile of log(2-yr mean citedness / h-index). "
                     "Works outside the author-scored shortlist use venue+institution only, capped at 85%.",
            "momentum": "log2( topic share of this window / topic share of the baseline window )",
        },
    }


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    # OpenAlex needs ~2 days to ingest a publication date (yesterday holds <5% of its final count)
    ap.add_argument("--end", default=(dt.date.today() - dt.timedelta(days=2)).isoformat())
    ap.add_argument("--baseline-weeks", type=int, default=8)
    ap.add_argument("--skip-authors", action="store_true")
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--offline", action="store_true", help="no network: build from cached days / entities only")
    args = ap.parse_args()
    global OFFLINE
    OFFLINE = args.offline

    end = dt.date.fromisoformat(args.end)
    days = [(end - dt.timedelta(days=i)).isoformat() for i in range(args.days)][::-1]
    base_end = end - dt.timedelta(days=args.days)
    base_start = base_end - dt.timedelta(days=7 * args.baseline_weeks - 1)
    t0 = time.time()

    topics = load_topics()
    field_ids = sorted({t["field"] for t in topics.values()}, key=lambda x: int(x))

    print(f"loading works {days[0]}..{days[-1]} ({len(field_ids)} fields x {len(days)} days)")
    works: list[dict] = []
    for d in days:
        works.extend(load_day(d, field_ids, refetch=args.refetch))
    # de-duplicate (a work can sit on a day boundary twice if OpenAlex updates between fetches)
    uniq = {}
    for w in works:
        uniq[w["id"]] = w
    works = list(uniq.values())
    print(f"{len(works)} works in window")

    baseline = fetch_topic_baseline(base_start.isoformat(), base_end.isoformat())

    # entities, most-used first so a partial day still covers the bulk of the papers
    src_prio = Counter(w["source"] for w in works if w["source"])
    inst_prio = Counter(i for w in works for a in w["authors"] for i in a["inst"])
    src_ids = [i for i, c in src_prio.items() if c >= MIN_SOURCE_MENTIONS]
    inst_ids = [i for i, c in inst_prio.items() if c >= MIN_INST_MENTIONS]
    sources = fetch_entities("sources", src_ids, "id,display_name,type,summary_stats,is_in_doaj,host_organization_name", slim_source, src_prio)
    insts = fetch_entities("institutions", inst_ids, "id,display_name,country_code,type,summary_stats", slim_inst, inst_prio)

    # pass 1: venue / institution prior -> shortlist
    score_works(works, topics, sources, insts, None)
    authors = None
    if not args.skip_authors:
        short = pick_shortlist(works)
        for w in short:
            w["_shortlisted"] = True
        au_prio = Counter()
        for rank, w in enumerate(short):
            for a in author_ids_for([w]):
                au_prio[a] = max(au_prio[a], len(short) - rank)
        authors = fetch_entities("authors", au_prio.keys(), "id,summary_stats,works_count", slim_author, au_prio)
        # pass 2: add author prior for the shortlist
        score_works(works, topics, sources, insts, authors)

    window = {"start": days[0], "end": days[-1], "baseline_start": base_start.isoformat(), "baseline_end": base_end.isoformat()}
    generated = dt.datetime.now().isoformat(timespec="seconds")
    out = build_output(works, topics, sources, insts, authors, baseline, window, generated)

    # relations between the displayed papers (reference lists fetched only for those)
    works_by_id = {w["id"]: w for w in works}
    ref_prio = Counter({pid: out["papers"][pid]["sc"] for pid in out["papers"]})
    refs = fetch_entities("works", ref_prio.keys(), "id,referenced_works", slim_refs, ref_prio)
    out["links"] = build_links(set(out["papers"].keys()), works_by_id, refs)
    out["method"]["links"] = ("ref = shared references (bibliographic coupling, >=2 shared, weight = cosine); "
                              "cite = one new paper cites another; au = shared author. Top 4 per paper.")

    (WEB / "data.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    snap = {"window": window, "generated": generated,
            "topics": {t["id"]: {"n": t["n"], "w": t["w"], "avg": t["avg"], "mom": t["mom"]} for t in out["topics"]}}
    (SNAP / f"{days[-1]}.json").write_text(json.dumps(snap), encoding="utf-8")
    size = (WEB / "data.json").stat().st_size / 1e6
    print(f"wrote web/data.json ({size:.1f} MB): {out['totals']}  in {time.time() - t0:.0f}s, {_stats['requests']} requests")
    gaps = {k[8:]: v for k, v in _stats.items() if k.startswith("missing_")}
    if gaps:
        print(f"INCOMPLETE - entities still to fetch (re-run after the UTC-midnight budget reset): {gaps}")


if __name__ == "__main__":
    main()
