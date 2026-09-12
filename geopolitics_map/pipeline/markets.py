"""Polymarket prediction markets on geopolitics and elections: the "market clock" of politics.

Public Gamma API, no key. We pull events tagged geopolitics / elections / major countries, keep the most traded
markets of each event, and map each to countries by keywords so the map can show "what the market thinks".
"""
from __future__ import annotations

import json
import logging
import re

from .common import COUNTRIES, http_get

log = logging.getLogger("geopolitics.markets")

API = "https://gamma-api.polymarket.com/events"
TAGS = ["geopolitics", "elections", "middle-east", "china", "russia", "ukraine", "iran", "israel", "taiwan",
        "north-korea", "world", "politics"]

# keyword -> ISO3 (checked against title + question, case-insensitive, longest keywords first)
KEYWORDS = {
    "hormuz": "IRN", "kharg": "IRN", "tehran": "IRN", "khamenei": "IRN", "iranian": "IRN", "iran": "IRN",
    "israel": "ISR", "netanyahu": "ISR", "idf": "ISR", "gaza": "PSE", "hamas": "PSE", "west bank": "PSE", "palestin": "PSE",
    "hezbollah": "LBN", "lebanon": "LBN", "beirut": "LBN", "houthi": "YEM", "yemen": "YEM", "bab el-mandeb": "YEM",
    "red sea": "YEM", "syria": "SYR", "damascus": "SYR", "iraq": "IRQ", "baghdad": "IRQ",
    "putin": "RUS", "kremlin": "RUS", "moscow": "RUS", "russia": "RUS", "zelensky": "UKR", "kyiv": "UKR", "ukrain": "UKR",
    "belarus": "BLR", "lukashenko": "BLR", "taiwan": "TWN", "taipei": "TWN", "kaohsiung": "TWN", "xi jinping": "CHN",
    "beijing": "CHN", "chinese": "CHN", "china": "CHN", "hong kong": "HKG", "north korea": "PRK", "kim jong": "PRK",
    "pyongyang": "PRK", "south korea": "KOR", "seoul": "KOR", "japan": "JPN", "tokyo": "JPN", "philippines": "PHL",
    "vietnam": "VNM", "thailand": "THA", "thai": "THA", "myanmar": "MMR", "indonesia": "IDN", "malaysia": "MYS",
    "india": "IND", "modi": "IND", "pakistan": "PAK", "kashmir": "IND", "bangladesh": "BGD", "afghanistan": "AFG",
    "taliban": "AFG", "trump": "USA", "white house": "USA", "congress": "USA", "senate": "USA", "u.s.": "USA", "us ": "USA",
    "united states": "USA", "america": "USA", "pentagon": "USA", "fed ": "USA", "canada": "CAN", "carney": "CAN",
    "mexico": "MEX", "sheinbaum": "MEX", "venezuela": "VEN", "maduro": "VEN", "cuba": "CUB", "brazil": "BRA", "lula": "BRA",
    "argentina": "ARG", "milei": "ARG", "colombia": "COL", "chile": "CHL", "peru": "PER", "bolivia": "BOL", "ecuador": "ECU",
    "haiti": "HTI", "panama": "PAN", "greenland": "GRL", "denmark": "DNK",
    "united kingdom": "GBR", "britain": "GBR", "british": "GBR", "starmer": "GBR", "uk ": "GBR", "france": "FRA",
    "french": "FRA", "macron": "FRA", "germany": "DEU", "german": "DEU", "merz": "DEU", "italy": "ITA", "meloni": "ITA",
    "spain": "ESP", "netherlands": "NLD", "dutch": "NLD", "belgium": "BEL", "poland": "POL", "polish": "POL",
    "hungary": "HUN", "orban": "HUN", "czech": "CZE", "slovakia": "SVK", "austria": "AUT", "sweden": "SWE",
    "swedish": "SWE", "norway": "NOR", "finland": "FIN", "denmark": "DNK", "romania": "ROU", "bulgaria": "BGR",
    "greece": "GRC", "turkey": "TUR", "türkiye": "TUR", "erdogan": "TUR", "serbia": "SRB", "kosovo": "XKX",
    "bosnia": "BIH", "moldova": "MDA", "georgia": "GEO", "armenia": "ARM", "azerbaijan": "AZE", "ireland": "IRL",
    "portugal": "PRT", "switzerland": "CHE", "nato": None, "european union": None, "eu ": None,
    "saudi": "SAU", "riyadh": "SAU", "uae": "ARE", "emirates": "ARE", "qatar": "QAT", "kuwait": "KWT", "oman": "OMN",
    "bahrain": "BHR", "jordan": "JOR", "egypt": "EGY", "libya": "LBY", "tunisia": "TUN", "algeria": "DZA",
    "morocco": "MAR", "sudan": "SDN", "ethiopia": "ETH", "eritrea": "ERI", "somalia": "SOM", "kenya": "KEN",
    "nigeria": "NGA", "south africa": "ZAF", "congo": "COD", "rwanda": "RWA", "mali": "MLI", "niger": "NER",
    "burkina": "BFA", "chad": "TCD", "senegal": "SEN", "ghana": "GHA", "cameroon": "CMR", "uganda": "UGA",
    "tanzania": "TZA", "zimbabwe": "ZWE", "mozambique": "MOZ", "angola": "AGO", "australia": "AUS", "new zealand": "NZL",
    "kazakhstan": "KAZ", "uzbekistan": "UZB", "mongolia": "MNG", "nepal": "NPL", "sri lanka": "LKA", "singapore": "SGP",
    "cambodia": "KHM", "laos": "LAO", "papua": "PNG",
}
_KW = sorted(KEYWORDS.items(), key=lambda kv: -len(kv[0]))
GEO_HINT = re.compile(r"invade|invasion|ceasefire|cease-fire|war|military|strike|missile|nuclear|blockade|sanction|"
                      r"election|president|prime minister|parliament|regime|leader|coup|treaty|peace|troops|"
                      r"annex|territor|border|strait|airspace|mobiliz|nato|summit|referendum|impeach|chancellor|"
                      r"out as|resign|recogni|hostage|attack|control", re.I)
ELECTION_HINT = re.compile(r"election|nominee|primary|next (prime minister|president|chancellor|leader|mayor|governor)|"
                           r"presidential|parliament|seats|referendum|runoff|win the|winner|ballot|vote share", re.I)


def countries_of(text: str) -> list[str]:
    t = " " + (text or "").lower() + " "
    out: list[str] = []
    for kw, iso in _KW:
        if kw in t:
            if iso and iso not in out:
                out.append(iso)
            t = t.replace(kw, " ")  # avoid double counting substrings
    return out[:3]


def _num(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def fetch_all() -> dict:
    events: dict[str, dict] = {}
    fetched_tags = []
    for tag in TAGS:
        url = f"{API}?tag_slug={tag}&active=true&closed=false&limit=200&order=volume24hr&ascending=false"
        try:
            blob = http_get(url, ttl_hours=1, timeout=90)
        except Exception as e:
            log.warning("polymarket tag %s failed: %s", tag, str(e)[:100])
            continue
        fetched_tags.append(tag)
        for ev in json.loads(blob):
            eid = str(ev.get("id"))
            if eid in events:
                events[eid]["tags"].add(tag)
                continue
            events[eid] = {"raw": ev, "tags": {tag}}
    out = []
    for eid, e in events.items():
        ev = e["raw"]
        title = ev.get("title") or ""
        text = title + " " + (ev.get("description") or "")[:300]
        isos = countries_of(title) or countries_of(text)
        geo = bool(e["tags"] & {"geopolitics", "middle-east", "ukraine", "iran", "israel", "taiwan", "north-korea", "russia", "china"}) or bool(GEO_HINT.search(title))
        if not geo and "elections" not in e["tags"]:
            continue
        markets = []
        for m in ev.get("markets") or []:
            if m.get("closed") or not m.get("active", True):
                continue
            try:
                prices = json.loads(m.get("outcomePrices") or "[]")
                outcomes = json.loads(m.get("outcomes") or "[]")
            except Exception:
                prices, outcomes = [], []
            if not prices:
                continue
            p_yes = _num(prices[0])
            if p_yes is None:
                continue
            markets.append({
                "q": m.get("groupItemTitle") or m.get("question") or title,
                "p": round(p_yes, 3), "yes": (outcomes[0] if outcomes else "Yes"),
                "d1": _num(m.get("oneDayPriceChange")), "d7": _num(m.get("oneWeekPriceChange")), "d30": _num(m.get("oneMonthPriceChange")),
                "vol24": round(_num(m.get("volume24hr"), 0) or 0), "vol": round(_num(m.get("volumeNum"), 0) or _num(m.get("volume"), 0) or 0),
                "end": (m.get("endDate") or "")[:10], "slug": m.get("slug") or "",
            })
        if not markets:
            continue
        markets.sort(key=lambda m: -(m["vol"] or 0))
        multi = len(markets) > 1
        if multi:  # keep the leading outcomes of multi-outcome events (elections, "who will be...")
            markets.sort(key=lambda m: -m["p"])
            markets = markets[:4]
        out.append({
            "id": eid, "title": title, "slug": ev.get("slug") or "", "countries": isos, "tags": sorted(e["tags"]),
            "vol24": round(_num(ev.get("volume24hr"), 0) or 0), "vol": round(_num(ev.get("volume"), 0) or 0),
            "liq": round(_num(ev.get("liquidity"), 0) or 0), "end": (ev.get("endDate") or "")[:10],
            "multi": multi, "markets": markets,
            "kind": "election" if ("elections" in e["tags"] or ELECTION_HINT.search(title)) and not re.search(r"invade|ceasefire|war|military|strike|blockade|regime", title, re.I) else "geo",
        })
    out.sort(key=lambda e: -e["vol24"])
    log.info("polymarket: %d events kept from tags %s", len(out), fetched_tags)
    return {"events": out[:250], "tags": fetched_tags}
