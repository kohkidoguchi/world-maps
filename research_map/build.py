"""Research Map builder (important-papers edition).

Two modes, both cheap enough for the OpenAlex free tier (1,000 requests/day):

  python build.py --init --months 6     # first build: per field x month, the most-cited papers;
                                        # keep those in the top 1% of their field-year (or FWCI>=10)
  python build.py --update              # daily: look at the last 3 weeks per field, add anything that
                                        # already clears the same bar, refresh citation counts of papers
                                        # younger than 90 days, rebuild web/data.json
  python build.py --rebuild             # no network: regenerate data.json from data/map/papers.json
  python build.py --dev-from-raw        # no network: fake a map from data/raw/*.jsonl.gz (UI testing)

State lives in data/map/papers.json (one record per admitted paper, incl. its reference list, so the
links between papers need no extra requests). Shared HTTP / topic / link code is imported from
build_weekly.py (the earlier "every paper of the week" pipeline).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import gzip
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import build_weekly as bw
from build_weekly import (API, BudgetExhausted, ENT, SNAP, WEB, get_json, load_topics, oa_id, build_links, _budget, _stats)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAP = bw.DATA / "map"
MAP.mkdir(parents=True, exist_ok=True)
STATE = MAP / "papers.json"
LOG = MAP / "runs.jsonl"

TYPES = "article|preprint|review"
PER_PAGE = 200
MAX_PAGES_PER_CELL = 3          # field x month cells where the 200th paper still qualifies get more pages
MIN_CITED = 5
MIN_FWCI = 10.0                 # admitted if top-1% of field-year OR fwci >= this
MAX_CITED_SANITY = 20000        # above this within a year it is a data glitch, not a paper
UPDATE_LOOKBACK_DAYS = 21
REFRESH_AGE_DAYS = 90
SELECT = ",".join([
    "id", "doi", "title", "publication_date", "type", "cited_by_count", "fwci", "citation_normalized_percentile",
    "referenced_works_count", "referenced_works", "primary_topic", "topics", "authorships", "primary_location",
    "open_access", "language", "is_retracted",
])


# ----------------------------------------------------------------------------- papers
def importance(fwci: float | None, cited: int) -> float:
    """0..100. FWCI is field- and age-normalised by OpenAlex (1.0 = world average), so it is the
    cross-field comparable core; raw citations only break ties. 20*log2(1+fwci): 1->20, 3->40, 7->60, 15->80."""
    f = max(0.0, fwci or 0.0)
    return round(min(100.0, 20 * math.log2(1 + f) + min(5.0, math.log10(1 + cited))), 1)


def admits(p: dict) -> bool:
    if p["retracted"] or not p["title"] or p["topic"] is None:
        return False
    if p["cited"] < MIN_CITED or p["cited"] > MAX_CITED_SANITY or p["nref"] == 0:
        return False
    return bool(p["top1"]) or (p["fwci"] or 0) >= MIN_FWCI


def slim(w: dict) -> dict | None:
    pt = w.get("primary_topic") or {}
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    pct = w.get("citation_normalized_percentile") or {}
    authors = []
    for a in (w.get("authorships") or [])[:30]:
        au = a.get("author") or {}
        authors.append({
            "id": oa_id(au.get("id")), "name": au.get("display_name"), "pos": a.get("author_position"),
            "corr": bool(a.get("is_corresponding")),
            "inst": [oa_id(i.get("id")) for i in (a.get("institutions") or []) if i.get("id")],
            "inst_names": [i.get("display_name") for i in (a.get("institutions") or [])][:2],
            "countries": a.get("countries") or [],
        })
    date = w.get("publication_date") or ""
    return {
        "id": oa_id(w["id"]), "doi": w.get("doi"), "title": (w.get("title") or "").strip(), "date": date, "month": date[:7],
        "type": w.get("type"), "cited": w.get("cited_by_count") or 0, "fwci": w.get("fwci"),
        "pct": pct.get("value"), "top1": bool(pct.get("is_in_top_1_percent")), "top10": bool(pct.get("is_in_top_10_percent")),
        "nref": w.get("referenced_works_count") or 0, "refs": [oa_id(r) for r in (w.get("referenced_works") or [])],
        "topic": oa_id(pt.get("id")) if pt.get("id") else None, "topic_score": pt.get("score"),
        "topics": [oa_id(t["id"]) for t in (w.get("topics") or [])[:3] if t.get("id")],
        "source": oa_id(src.get("id")), "source_name": src.get("display_name"), "source_type": src.get("type"),
        "url": loc.get("landing_page_url") or w.get("doi"), "oa": bool((w.get("open_access") or {}).get("is_oa")),
        "lang": w.get("language"), "retracted": bool(w.get("is_retracted")),
        "n_authors": len(w.get("authorships") or []), "authors": authors,
    }


def load_state() -> dict[str, dict]:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(state: dict[str, dict]) -> None:
    STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def log_run(entry: dict) -> None:
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ----------------------------------------------------------------------------- fetching
def fetch_cell(field_id: str, start: str, end: str, max_pages: int, admitted_today: str) -> tuple[list[dict], int]:
    """Most-cited papers of one field in one date range; stops when a page's tail no longer qualifies."""
    flt = f"from_publication_date:{start},to_publication_date:{end},type:{TYPES},primary_topic.field.id:{field_id},is_retracted:false"
    out, pages = [], 0
    cursor = "*"
    while cursor and pages < max_pages:
        j = get_json(f"{API}/works", {"filter": flt, "sort": "cited_by_count:desc", "per-page": PER_PAGE, "cursor": cursor, "select": SELECT})
        pages += 1
        res = j.get("results", [])
        keep_going = False
        for w in res:
            p = slim(w)
            if p and admits(p):
                p["added"] = admitted_today
                out.append(p)
                keep_going = True   # at least one on this page qualified
        # if the last item of a full page still clears the citation floor, the next page may hold more
        last_ok = res and (res[-1].get("cited_by_count") or 0) >= MIN_CITED and keep_going
        cursor = j.get("meta", {}).get("next_cursor") if (len(res) == PER_PAGE and last_ok) else None
    return out, pages


def month_ranges(months: int, end: dt.date) -> list[tuple[str, str]]:
    """Calendar months covering the last `months` months up to `end` (inclusive)."""
    out = []
    first = end.replace(day=1)
    for i in range(months):
        y, m = first.year, first.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        s = dt.date(y, m, 1)
        e = (dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1))
        out.append((s.isoformat(), min(e, end).isoformat()))
    return out[::-1]


def init_map(state: dict, field_ids: list[str], months: int, end: dt.date, today: str) -> None:
    ranges = month_ranges(months, end)
    print(f"initial build: {len(field_ids)} fields x {len(ranges)} months ({ranges[0][0]}..{ranges[-1][1]}), budget left {_budget['remaining']}")
    added = 0
    try:
        for (s, e) in ranges:
            for fid in field_ids:
                papers, pages = fetch_cell(fid, s, e, MAX_PAGES_PER_CELL, today)
                new = [p for p in papers if p["id"] not in state]
                for p in new:
                    state[p["id"]] = p
                added += len(new)
                print(f"  {s[:7]} field {fid:>2}: {len(papers):4d} qualify, {len(new):4d} new ({pages} page{'s' if pages > 1 else ''})")
            save_state(state)
    except BudgetExhausted:
        save_state(state)
        print("  budget exhausted - state saved; re-run --init tomorrow to fill the remaining cells (cells already done are skipped only by de-dup, so it costs the same requests again: prefer --update once most cells are in)")
    print(f"  {added} papers added, map now {len(state)}")
    log_run({"run": "init", "at": today, "added": added, "total": len(state), "requests": _stats["requests"]})


def update_map(state: dict, field_ids: list[str], end: dt.date, today: str) -> None:
    start = (end - dt.timedelta(days=UPDATE_LOOKBACK_DAYS)).isoformat()
    print(f"update: candidates {start}..{end} per field, budget left {_budget['remaining']}")
    added = 0
    try:
        for fid in field_ids:
            papers, _ = fetch_cell(fid, start, end.isoformat(), 1, today)
            new = [p for p in papers if p["id"] not in state]
            for p in new:
                state[p["id"]] = p
            added += len(new)
            if new:
                print(f"  field {fid:>2}: +{len(new)}  " + " | ".join(p["title"][:50] for p in new[:3]))
        save_state(state)
        # refresh citation counts of the young papers (they move fast)
        young = [pid for pid, p in state.items() if p["date"] and (end - dt.date.fromisoformat(p["date"])).days <= REFRESH_AGE_DAYS]
        print(f"  refreshing {len(young)} papers younger than {REFRESH_AGE_DAYS} days ({(len(young) + 49) // 50} requests)")
        for i in range(0, len(young), 50):
            batch = young[i:i + 50]
            j = get_json(f"{API}/works", {"filter": "ids.openalex:" + "|".join(batch), "per-page": 50,
                                          "select": "id,cited_by_count,fwci,citation_normalized_percentile,is_retracted"})
            for w in j.get("results", []):
                p = state.get(oa_id(w["id"]))
                if not p:
                    continue
                pct = w.get("citation_normalized_percentile") or {}
                p.update({"cited": w.get("cited_by_count") or 0, "fwci": w.get("fwci"), "pct": pct.get("value"),
                          "top1": bool(pct.get("is_in_top_1_percent")), "top10": bool(pct.get("is_in_top_10_percent")),
                          "retracted": bool(w.get("is_retracted")), "refreshed": today})
        save_state(state)
    except BudgetExhausted:
        save_state(state)
        print("  budget exhausted mid-update - state saved, continuing with what we have")
    dropped = [pid for pid, p in state.items() if p.get("retracted")]
    for pid in dropped:
        del state[pid]
    print(f"  +{added} papers, {len(dropped)} retracted removed, map now {len(state)}")
    log_run({"run": "update", "at": today, "added": added, "total": len(state), "requests": _stats["requests"]})


def dev_from_raw(state: dict, today: str) -> None:
    """UI testing without network: take whatever in data/raw already has citations."""
    n = 0
    for f in sorted(glob.glob(str(bw.RAW / "works_*.jsonl.gz"))):
        for line in gzip.open(f, "rt", encoding="utf-8"):
            w = json.loads(line)
            if (w.get("cited") or 0) < 1 or not w.get("topic"):
                continue
            p = {**w, "month": (w.get("date") or "")[:7], "pct": None, "top1": (w.get("fwci") or 0) >= 3,
                 "top10": (w.get("fwci") or 0) >= 1, "refs": [], "retracted": False, "added": today}
            state[p["id"]] = p
            n += 1
    print(f"dev: {n} cited papers taken from raw cache")


# ----------------------------------------------------------------------------- output
def paper_view(p: dict) -> dict:
    first = next((a for a in p["authors"] if a["pos"] == "first"), p["authors"][0] if p["authors"] else None)
    inst = first["inst_names"][0] if first and first["inst_names"] else None
    cc = first["countries"][0] if first and first["countries"] else None
    return {
        "id": p["id"], "t": p["title"][:220], "d": p["date"], "ty": p["type"], "u": p["url"], "src": p["source_name"],
        "au": first["name"] if first else None, "na": p["n_authors"], "inst": inst, "cc": cc,
        "sc": p["sc"], "c": p["cited"], "fw": round(p["fwci"], 2) if p["fwci"] is not None else None,
        "p1": p["top1"], "oa": p["oa"], "tp": p["topic"], "add": p.get("added"),
    }


def build_output(state: dict, topics: dict, months: int, end: dt.date, generated: str) -> dict:
    papers = [p for p in state.values() if p["topic"] in topics]
    for p in papers:
        p["sc"] = importance(p["fwci"], p["cited"])
    ranges = month_ranges(months, end)
    all_months = [r[0][:7] for r in ranges]
    recent = set(all_months[-2:])
    tot_recent = sum(1 for p in papers if p["month"] in recent) or 1
    tot_earlier = sum(1 for p in papers if p["month"] not in recent) or 1

    by_topic: dict[str, list[dict]] = defaultdict(list)
    for p in papers:
        by_topic[p["topic"]].append(p)
    topic_rows, views = [], {}
    for tid, ps in by_topic.items():
        t = topics[tid]
        ps.sort(key=lambda p: (p["sc"], p["cited"]), reverse=True)
        n = len(ps)
        n_recent = sum(1 for p in ps if p["month"] in recent)
        share_recent = (n_recent + 0.5) / tot_recent
        share_earlier = (n - n_recent + 0.5) / tot_earlier
        cc = Counter()
        for p in ps:
            seen = set()
            for a in p["authors"]:
                for c in a["countries"]:
                    if c not in seen:
                        cc[c] += 1; seen.add(c)
        for p in ps:
            views[p["id"]] = paper_view(p)
        monthly = Counter(p["month"] for p in ps)
        topic_rows.append({
            "id": tid, "name": t["name"], "sub": t["subfield"], "sub_name": t["subfield_name"],
            "field": t["field"], "field_name": t["field_name"], "domain": t["domain"], "domain_name": t["domain_name"],
            "kw": t["keywords"], "n": n, "n_recent": n_recent, "w": round(sum(p["sc"] for p in ps) / 100, 2),
            "avg": round(sum(p["sc"] for p in ps) / n, 1), "max": max(p["sc"] for p in ps),
            "mom": round(math.log2(share_recent / share_earlier), 3), "cited": sum(p["cited"] for p in ps),
            "months": [monthly.get(m, 0) for m in all_months],
            "cc": cc.most_common(5), "top": [p["id"] for p in ps[:5]],
        })

    fields, domains = {}, {}
    for p in papers:
        t = topics[p["topic"]]
        for key, store, name in ((t["field"], fields, t["field_name"]), (t["domain"], domains, t["domain_name"])):
            row = store.setdefault(key, {"id": key, "name": name, "n": 0, "w": 0.0, "cc": Counter(), "cited": 0,
                                         "domain": t["domain"] if store is fields else None, "months": Counter()})
            row["n"] += 1; row["w"] += p["sc"] / 100; row["cited"] += p["cited"]; row["months"][p["month"]] += 1
            seen = set()
            for a in p["authors"]:
                for c in a["countries"]:
                    if c not in seen:
                        row["cc"][c] += 1; seen.add(c)
    for store in (fields, domains):
        for row in store.values():
            row["w"] = round(row["w"], 1); row["cc"] = row["cc"].most_common(8)
            row["months"] = [row["months"].get(m, 0) for m in all_months]

    papers.sort(key=lambda p: (p["sc"], p["cited"]), reverse=True)
    works_by_id = {p["id"]: p for p in papers}
    refs = {p["id"]: {"refs": p.get("refs") or []} for p in papers}
    links = build_links(set(works_by_id), works_by_id, refs)
    newest = sorted(papers, key=lambda p: (p.get("added") or "", p["sc"]), reverse=True)
    last_added = newest[0].get("added") if newest else None
    return {
        "generated": generated, "mode": "important",
        "window": {"start": ranges[0][0], "end": ranges[-1][1], "months": all_months, "recent_months": sorted(recent)},
        "totals": {"papers": len(papers), "topics": len(topic_rows), "top1": sum(1 for p in papers if p["top1"]),
                   "added_last_run": sum(1 for p in papers if p.get("added") == last_added), "last_added": last_added,
                   "cited_total": sum(p["cited"] for p in papers)},
        "domains": sorted(domains.values(), key=lambda r: -r["n"]),
        "fields": sorted(fields.values(), key=lambda r: -r["n"]),
        "topics": topic_rows, "links": links,
        "top": [p["id"] for p in papers[:400]],
        "new": [p["id"] for p in newest if p.get("added") == last_added][:100],
        "papers": views,
        "top_sources": Counter(p["source_name"] for p in papers if p["source_name"]).most_common(30),
        "method": {
            "types": TYPES,
            "admission": f"top 1% of field-year (OpenAlex citation_normalized_percentile) or FWCI >= {MIN_FWCI:g}; "
                         f"cited >= {MIN_CITED}; has references; not retracted",
            "score": "20*log2(1+FWCI) + log10(1+citations), capped at 100 (FWCI = field- and age-normalised citation impact, 1.0 = world average)",
            "momentum": "log2( topic share of admitted papers in the last 2 months / its share in the earlier months )",
            "links": "ref = shared references (>=2, cosine); cite = one map paper cites another; au = shared author; top 4 per paper",
        },
    }


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="first build over --months")
    ap.add_argument("--update", action="store_true", help="daily: add new qualifying papers, refresh young ones")
    ap.add_argument("--rebuild", action="store_true", help="regenerate data.json from state, no network")
    ap.add_argument("--dev-from-raw", action="store_true", help="fake state from data/raw for UI work, no network")
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--end", default=(dt.date.today() - dt.timedelta(days=2)).isoformat())
    args = ap.parse_args()
    end = dt.date.fromisoformat(args.end)
    today = dt.date.today().isoformat()
    t0 = time.time()

    bw.OFFLINE = args.rebuild or args.dev_from_raw
    topics = load_topics()
    field_ids = sorted({t["field"] for t in topics.values()}, key=int)
    state = load_state()
    print(f"state: {len(state)} papers")

    if args.dev_from_raw:
        dev_from_raw(state, today)
    elif args.init:
        init_map(state, field_ids, args.months, end, today)
    elif args.update:
        update_map(state, field_ids, end, today)
    elif not args.rebuild:
        # default: init if empty, else update
        (init_map if not state else update_map)(state, field_ids, *((args.months, end, today) if not state else (end, today)))

    generated = dt.datetime.now().isoformat(timespec="seconds")
    out = build_output(state, topics, args.months, end, generated)
    (WEB / "data.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (SNAP / f"map_{today}.json").write_text(json.dumps({"generated": generated, "totals": out["totals"],
        "topics": {t["id"]: {"n": t["n"], "w": t["w"], "mom": t["mom"]} for t in out["topics"]}}), encoding="utf-8")
    size = (WEB / "data.json").stat().st_size / 1e6
    print(f"wrote web/data.json ({size:.1f} MB): {out['totals']} in {time.time() - t0:.0f}s, {_stats['requests']} requests, budget left {_budget['remaining']}")


if __name__ == "__main__":
    main()
