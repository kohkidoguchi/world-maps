"""GDELT 2.0 Events: the fast clock of the map.

Every 15 minutes GDELT publishes a CSV of machine-coded events (who did what to whom, where, with how much media
attention). We fetch the last 24 hours at full resolution (96 files) plus an hourly sample of the previous weeks to
build a per-country / per-dyad baseline, and persist per-day aggregates in data/gdelt_*_daily.csv so that later
runs only fetch what is new.

Only *political* events are kept: an international dyad (two different actor countries), or an actor typed as a
state / military / rebel / legislative / judicial / police / intergovernmental body, or a protest / force-posture /
coercion / mass-violence root code. This removes the flood of US local news (crime, weather, sports) that dominates
raw GDELT article counts.

Weighting of one event (transparent, shown in the UI):
    score = NumSources (distinct outlets, robust to syndication) x intensity (1 + |Goldstein| / 5)
            x actor weight (1 + 0.5 * (power1 + power2)) x 1.5 if international
where power is a 0-1 index of the actor country's economic + military size (World Bank data, see structure.py).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import os
import re
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from .common import COUNTRIES, DATA_DIR, FIPS_TO_ISO3, cameo_label, http_get, purge_raw

log = logging.getLogger("geopolitics.gdelt")

BASE = "http://data.gdeltproject.org/gdeltv2/"
COUNTRY_DAILY = os.path.join(DATA_DIR, "gdelt_country_daily.csv")
DYAD_DAILY = os.path.join(DATA_DIR, "gdelt_dyad_daily.csv")
GLOBAL_DAILY = os.path.join(DATA_DIR, "gdelt_global_daily.csv")

# column indices in the 61-column export format
C_ID, C_DAY, C_A1CODE, C_A1NAME, C_A1CC, C_A2CODE, C_A2NAME, C_A2CC = 0, 1, 5, 6, 7, 15, 16, 17
C_ROOT_EVENT, C_EVENT, C_BASE, C_ROOT, C_QUAD, C_GOLD, C_MENTIONS, C_SOURCES, C_ARTICLES, C_TONE = 25, 26, 27, 28, 29, 30, 31, 32, 33, 34
C_A1GEO_CC, C_A2GEO_CC = 37, 45
C_A1TYPES, C_A2TYPES = (12, 13, 14), (22, 23, 24)
C_GEO_TYPE, C_GEO_NAME, C_GEO_CC, C_GEO_LAT, C_GEO_LON, C_ADDED, C_URL = 51, 52, 53, 56, 57, 59, 60

POLITICAL_TYPES = {"GOV", "MIL", "REB", "OPP", "LEG", "JUD", "COP", "SPY", "UAF", "INS", "SEP", "IGO", "ELI"}
POLITICAL_ROOTS = {"14", "15", "16", "20"}  # protest, force posture, reduce relations, mass violence: always political
INTL_FACTOR = 1.5
# Domestic events (same actor country on both sides, or one side missing) are kept only when the article URL
# reads as political — GDELT tags local police / city-hall / court stories with GOV/COP/JUD actor types, and in
# the US that syndicated local news would otherwise swamp the map.
_POLITICAL_WORDS = r"""government govt president presidential minister ministers ministry parliament parliamentary election
elections electoral senate senator senators congress congressional court supreme military army navy airforce troops
soldiers pentagon defense defence protest protests protesters rally party vote votes voting voters sanction sanctions
coup war lawmakers lawmaker bill governor mayor campaign immigration migrants tariff tariffs trade border prime cabinet
opposition nuclear treaty summit diplomat diplomats diplomatic embassy ambassador ceasefire rebel rebels militant militants
terror terrorist terrorism missile missiles drone drones regime coalition referendum constitution constitutional impeach
impeachment democracy democrats republican republicans labour conservative conservatives tory tories politics political
policy politician politicians legislature legislation legislative assembly federal gop whitehouse kremlin junta insurgents
insurgency separatist separatists militia autocracy dictator dictatorship sovereignty annexation blockade genocide
peacekeeping peacekeepers intelligence espionage spy spies cyberattack hack hackers asylum refugees deportation deportations
ice shutdown budget deficit debt fed inflation strike unions union pension riots riot unrest mobilization mobilisation
conscription draft nato eu un brics opec asean shanghai
trump vance biden harris putin zelensky xi modi netanyahu erdogan macron starmer merz meloni lula milei sheinbaum carney
khamenei kim orban fico duda nawrocki tusk sanchez scholz albanese luxon ramaphosa tinubu sisi mbs salman
gouvernement ministre election elections president parlement regierung minister wahl bundestag kanzler gobierno ministro
eleccion elecciones presidente governo eleicao eleicoes politica politique politik""".split()
POLITICAL_URL = re.compile(r"(?<![a-z])(" + "|".join(sorted(set(_POLITICAL_WORDS), key=len, reverse=True)) + r")(?![a-z])")


def url_is_political(url: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    slug = re.sub(r"[-_/.+%]+", " ", p.path.lower())
    return bool(POLITICAL_URL.search(slug))

COUNTRY_COLS = ["date", "iso3", "files", "articles", "sources", "events", "q1", "q2", "q3", "q4", "gold_w", "score"]
DYAD_COLS = ["date", "a1", "a2", "files", "articles", "events", "coop", "conf", "gold_w", "score"]
GLOBAL_COLS = ["date", "files", "articles", "sources", "events", "q1", "q2", "q3", "q4", "gold_w", "score"]


def last_update() -> dt.datetime:
    txt = http_get(BASE + "lastupdate.txt", ttl_hours=0.2, timeout=30).decode()
    m = re.search(r"(\d{14})\.export\.CSV\.zip", txt)
    if not m:
        raise RuntimeError("cannot parse lastupdate.txt")
    return dt.datetime.strptime(m.group(1), "%Y%m%d%H%M%S")


def slot_url(ts: dt.datetime) -> str:
    return f"{BASE}{ts:%Y%m%d%H%M%S}.export.CSV.zip"


def fetch_slot(ts: dt.datetime) -> list[list[str]]:
    """Rows of one 15-minute file (empty list if the slot does not exist)."""
    try:
        blob = http_get(slot_url(ts), ttl_hours=24 * 400, timeout=90, ok404=True)
    except Exception as e:  # network hiccup: treat as missing slot, keep going
        log.warning("slot %s failed: %s", ts, str(e)[:100])
        return []
    if not blob:
        return []
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        raw = z.read(z.namelist()[0]).decode("utf-8", "replace")
    except Exception as e:
        log.warning("slot %s unreadable: %s", ts, e)
        return []
    rows = []
    for line in raw.split("\n"):
        f = line.split("\t")
        if len(f) >= 61:
            rows.append(f)
    return rows


def _actor_iso(code: str, geo_fips: str) -> str:
    if code and code in COUNTRIES:
        return code
    return FIPS_TO_ISO3.get(geo_fips, "") if geo_fips else ""


def _f(x: str, default: float = 0.0) -> float:
    try:
        return float(x)
    except ValueError:
        return default


def _headline_from_url(url: str) -> tuple[str, str]:
    """Best-effort headline from the URL slug + domain (GDELT events carry no title)."""
    try:
        p = urlparse(url)
    except Exception:
        return "", ""
    domain = p.netloc.replace("www.", "")
    segs = [s for s in p.path.split("/") if s]
    best = ""
    for s in reversed(segs):
        s = re.sub(r"\.(html?|php|aspx?|cfm|shtml)$", "", s)
        s = re.sub(r"[-_.,+%]+", " ", s)
        toks = s.split()
        words = [w for w in toks if re.fullmatch(r"[a-zA-Z][a-zA-Z']{0,24}", w)]
        if len(words) >= 4 and len(words) >= 0.7 * len(toks):
            best = " ".join(words)
            break
    if best:
        best = best[:1].upper() + best[1:]
    return best[:140], domain


class Agg:
    """Aggregates one set of event rows into country / dyad / global totals plus grouped top events."""

    def __init__(self, power: dict[str, float]):
        self.power = power
        self.country = defaultdict(lambda: [0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0])  # articles, events, q1..q4, gold_w, score, sources
        self.dyad = defaultdict(lambda: [0, 0, 0.0, 0.0, 0.0, 0.0])  # articles, events, coop, conf, gold_w, score
        self.glob = [0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0]
        self.groups: dict[tuple, dict] = {}
        self.files = 0
        self.seen = 0

    def add_rows(self, rows: list[list[str]], keep_events: bool):
        self.files += 1
        for f in rows:
            try:
                articles = int(f[C_ARTICLES] or 0)
                sources = int(f[C_SOURCES] or 0)
                quad = int(f[C_QUAD] or 0)
                gold = _f(f[C_GOLD])
            except ValueError:
                continue
            if articles <= 0 or quad not in (1, 2, 3, 4):
                continue
            self.seen += 1
            a1 = _actor_iso(f[C_A1CC], f[C_A1GEO_CC])
            a2 = _actor_iso(f[C_A2CC], f[C_A2GEO_CC])
            intl = bool(a1 and a2 and a1 != a2)
            if not intl and f[C_ROOT] not in POLITICAL_ROOTS:
                types = {f[i] for i in C_A1TYPES + C_A2TYPES if f[i]}
                if not (types & POLITICAL_TYPES) or not url_is_political(f[C_URL]):
                    continue  # domestic non-political news (crime, weather, sports, business)
            loc = FIPS_TO_ISO3.get(f[C_GEO_CC], "") or a1 or a2
            intensity = 1 + abs(gold) / 5
            actor_w = 1 + 0.5 * (self.power.get(a1, 0.0) + self.power.get(a2, 0.0))
            score = max(sources, 1) * intensity * actor_w * (INTL_FACTOR if intl else 1.0)
            g = self.glob
            g[0] += articles; g[1] += 1; g[1 + quad] += articles; g[6] += gold * articles; g[7] += score; g[8] += sources
            if loc:
                c = self.country[loc]
                c[0] += articles; c[1] += 1; c[1 + quad] += articles; c[6] += gold * articles; c[7] += score; c[8] += sources
            if a1 and a2 and a1 != a2:
                d = self.dyad[(a1, a2)]
                d[0] += articles; d[1] += 1
                if quad <= 2:
                    d[2] += score
                else:
                    d[3] += score
                d[4] += gold * articles; d[5] += score
            if keep_events:
                key = (loc, f[C_BASE], a1, a2)
                grp = self.groups.get(key)
                if grp is None:
                    grp = self.groups[key] = {
                        "loc": loc, "base": f[C_BASE], "root": f[C_ROOT], "a1": a1, "a2": a2, "quad": quad,
                        "articles": 0, "sources": 0, "events": 0, "score": 0.0, "gold_w": 0.0, "intensity": intensity,
                        "actor_w": round(actor_w, 2), "intl": intl, "best": 0, "url": "", "place": "", "lat": None, "lon": None,
                        "a1name": "", "a2name": "", "first": f[C_ADDED], "last": f[C_ADDED],
                    }
                grp["articles"] += articles; grp["sources"] += int(f[C_SOURCES] or 0); grp["events"] += 1
                grp["score"] += score; grp["gold_w"] += gold * articles
                grp["last"] = max(grp["last"], f[C_ADDED]); grp["first"] = min(grp["first"], f[C_ADDED])
                if articles > grp["best"]:
                    grp["best"] = articles; grp["url"] = f[C_URL]; grp["place"] = f[C_GEO_NAME]
                    grp["lat"] = _f(f[C_GEO_LAT], None) if f[C_GEO_LAT] else None
                    grp["lon"] = _f(f[C_GEO_LON], None) if f[C_GEO_LON] else None
                    grp["a1name"] = f[C_A1NAME].title(); grp["a2name"] = f[C_A2NAME].title()

    def top_events(self, n: int = 400) -> list[dict]:
        out = []
        seen_urls: set[str] = set()
        for g in sorted(self.groups.values(), key=lambda x: -x["score"]):
            if len(out) >= n:
                break
            if g["url"] in seen_urls:  # the same story coded under several CAMEO verbs: keep the strongest
                continue
            seen_urls.add(g["url"])
            title, domain = _headline_from_url(g["url"])
            out.append({
                "loc": g["loc"], "base": g["base"], "root": g["root"], "label": cameo_label(g["base"], g["root"]),
                "a1": g["a1"], "a2": g["a2"], "a1name": g["a1name"], "a2name": g["a2name"], "quad": g["quad"],
                "articles": g["articles"], "sources": g["sources"], "events": g["events"],
                "score": round(g["score"], 1), "gold": round(g["gold_w"] / g["articles"], 2) if g["articles"] else 0,
                "intensity": round(g["intensity"], 2), "actor_w": g["actor_w"], "intl": g["intl"], "url": g["url"], "title": title,
                "domain": domain, "place": g["place"], "lat": g["lat"], "lon": g["lon"],
                "first": g["first"][:12], "last": g["last"][:12],
            })
        return out


# --------------------------------------------------------------------------- persistence
def _read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv(path: str, rows: list[dict], cols: list[str]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def planned_slots(day: dt.date, full: bool) -> list[dt.datetime]:
    base = dt.datetime(day.year, day.month, day.day)
    step = 15 if full else 60
    return [base + dt.timedelta(minutes=i) for i in range(0, 24 * 60, step)]


def fetch_all(power: dict[str, float], history_days: int = 28, workers: int = 6) -> dict:
    """Fetch new slots, update the daily stores, and return the 24h window aggregates + history."""
    latest = last_update()
    today = latest.date()
    log.info("GDELT latest slot %s", latest)

    country_hist = _read_csv(COUNTRY_DAILY)
    dyad_hist = _read_csv(DYAD_DAILY)
    global_hist = _read_csv(GLOBAL_DAILY)
    have_files = {r["date"]: int(r["files"]) for r in global_hist}

    # which days need (re)fetching: today + yesterday at full resolution, older days hourly-sampled
    days: list[tuple[dt.date, bool]] = []
    for k in range(history_days, -1, -1):
        d = today - dt.timedelta(days=k)
        full = k <= 1
        want = 96 if full else 24
        if d != today and have_files.get(d.isoformat(), 0) >= want:
            continue  # already complete in the store
        days.append((d, full))

    # slot list; everything in the last 24h is needed in memory for the window aggregation
    win_start = latest - dt.timedelta(hours=24)
    slot_jobs: list[tuple[dt.date, dt.datetime]] = []
    for d, full in days:
        for ts in planned_slots(d, full):
            if ts > latest:
                break
            slot_jobs.append((d, ts))
    win_slots = [latest - dt.timedelta(minutes=15 * i) for i in range(96)]
    needed = {ts for _, ts in slot_jobs} | {ts for ts in win_slots if ts > win_start}
    log.info("fetching %d GDELT slots (%d days, workers=%d)", len(needed), len(days), workers)
    fetched: dict[dt.datetime, list[list[str]]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for ts, rows in zip(sorted(needed), ex.map(fetch_slot, sorted(needed))):
            fetched[ts] = rows
    ok_slots = sum(1 for r in fetched.values() if r)
    log.info("got %d non-empty slots", ok_slots)

    # per-day aggregates -> stores
    new_country, new_dyad, new_global = [], [], []
    redone_days = set()
    for d, full in days:
        agg = Agg(power)
        for ts in planned_slots(d, full):
            if ts > latest:
                break
            rows = fetched.get(ts)
            if rows:
                agg.add_rows(rows, keep_events=False)
        if agg.files == 0:
            continue
        redone_days.add(d.isoformat())
        ds = d.isoformat()
        for iso, c in agg.country.items():
            new_country.append({"date": ds, "iso3": iso, "files": agg.files, "articles": c[0], "sources": c[8], "events": c[1],
                                "q1": round(c[2]), "q2": round(c[3]), "q3": round(c[4]), "q4": round(c[5]),
                                "gold_w": round(c[6], 1), "score": round(c[7], 1)})
        dy = sorted(agg.dyad.items(), key=lambda kv: -kv[1][5])[:600]
        for (a1, a2), v in dy:
            new_dyad.append({"date": ds, "a1": a1, "a2": a2, "files": agg.files, "articles": v[0], "events": v[1],
                             "coop": round(v[2], 1), "conf": round(v[3], 1), "gold_w": round(v[4], 1), "score": round(v[5], 1)})
        g = agg.glob
        new_global.append({"date": ds, "files": agg.files, "articles": g[0], "sources": g[8], "events": g[1], "q1": round(g[2]),
                           "q2": round(g[3]), "q3": round(g[4]), "q4": round(g[5]), "gold_w": round(g[6], 1), "score": round(g[7], 1)})
    keep_from = (today - dt.timedelta(days=400)).isoformat()
    country_hist = [r for r in country_hist if r["date"] not in redone_days and r["date"] >= keep_from] + new_country
    dyad_hist = [r for r in dyad_hist if r["date"] not in redone_days and r["date"] >= keep_from] + new_dyad
    global_hist = [r for r in global_hist if r["date"] not in redone_days and r["date"] >= keep_from] + new_global
    country_hist.sort(key=lambda r: (r["date"], r["iso3"]))
    dyad_hist.sort(key=lambda r: (r["date"], r["a1"], r["a2"]))
    global_hist.sort(key=lambda r: r["date"])
    _write_csv(COUNTRY_DAILY, country_hist, COUNTRY_COLS)
    _write_csv(DYAD_DAILY, dyad_hist, DYAD_COLS)
    _write_csv(GLOBAL_DAILY, global_hist, GLOBAL_COLS)

    # 24-hour window at full resolution, with grouped events
    win = Agg(power)
    for ts in sorted(needed):
        if ts > win_start and fetched.get(ts):
            win.add_rows(fetched[ts], keep_events=True)
    purged = purge_raw(35, BASE)
    if purged:
        log.info("purged %d cached GDELT slots older than 35 days", purged)
    log.info("24h window: %d rows seen, %d political events kept", win.seen, win.glob[1])
    return {
        "latest": latest.strftime("%Y-%m-%dT%H:%M") + "Z",
        "window_start": win_start.strftime("%Y-%m-%dT%H:%M") + "Z",
        "window_files": win.files, "window_seen": win.seen,
        "window_country": {k: {"articles": v[0], "sources": v[8], "events": v[1], "q": [round(v[2]), round(v[3]), round(v[4]), round(v[5])],
                               "gold": round(v[6] / v[0], 2) if v[0] else 0, "score": round(v[7], 1)} for k, v in win.country.items()},
        "window_dyads": [{"a1": a1, "a2": a2, "articles": v[0], "events": v[1], "coop": round(v[2], 1), "conf": round(v[3], 1),
                          "gold": round(v[4] / v[0], 2) if v[0] else 0, "score": round(v[5], 1)}
                         for (a1, a2), v in sorted(win.dyad.items(), key=lambda kv: -kv[1][5])[:800]],
        "window_global": {"articles": win.glob[0], "sources": win.glob[8], "events": win.glob[1], "q": [round(x) for x in win.glob[2:6]],
                          "gold": round(win.glob[6] / win.glob[0], 2) if win.glob[0] else 0, "score": round(win.glob[7], 1)},
        "events": win.top_events(600),
        "country_daily": country_hist, "dyad_daily": dyad_hist, "global_daily": global_hist,
    }
