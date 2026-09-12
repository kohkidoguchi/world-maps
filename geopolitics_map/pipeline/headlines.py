"""Real headlines for the top events.

GDELT's event rows carry only the source URL, so the map used to show a title reconstructed from the URL slug.
This module fetches each article page and reads its og:title / <title>, with a persistent cache
(data/headlines.json) so a daily rebuild only fetches URLs it has never seen.

Only the first 120 KB of each page is read, requests run in parallel with a short timeout, and failures fall back
to the URL-derived title. Nothing but the title is stored.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from .common import DATA_DIR

log = logging.getLogger("geopolitics.headlines")

CACHE = os.path.join(DATA_DIR, "headlines.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en,ja;q=0.8"}
BAD = re.compile(r"^(just a moment|attention required|access denied|are you a robot|error|page not found|403 forbidden|"
                 r"one moment, please|bot verification|security check)", re.I)
# trailing site names: "Headline | CNN", "Headline - Reuters"
TAIL = re.compile(r"\s*[|–—-]\s*[^|–—-]{2,40}$")


def _clean(title: str, domain: str) -> str:
    t = re.sub(r"\s+", " ", html.unescape(title or "")).strip()
    if not t or BAD.match(t):
        return ""
    site = domain.split(".")[0].lower()
    m = TAIL.search(t)
    if m and len(t) - len(m.group(0)) > 25:
        tail = m.group(0).lower()
        if site[:6] in tail.replace(" ", "") or any(w in tail for w in ("news", "times", "post", "herald", "tribune", "daily", "com")):
            t = t[: -len(m.group(0))].strip()
    return t[:160]


def _fetch(url: str) -> str:
    try:
        r = requests.get(url, headers=UA, timeout=8, stream=True, allow_redirects=True)
        if r.status_code >= 400:
            return ""
        raw = r.raw.read(120_000, decode_content=True)
        txt = raw.decode(r.encoding or "utf-8", "replace")
    except Exception:
        return ""
    og = (re.search(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']{5,300})["\']', txt, re.I)
          or re.search(r'<meta[^>]+content=["\']([^"\']{5,300})["\'][^>]*property=["\']og:title["\']', txt, re.I))
    ti = re.search(r"<title[^>]*>(.*?)</title>", txt, re.S | re.I)
    domain = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    for cand in (og.group(1) if og else None, ti.group(1) if ti else None):
        t = _clean(cand or "", domain)
        if t:
            return t
    return ""


def _load() -> dict:
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def enrich(events: list[dict], limit: int = 400, workers: int = 8) -> int:
    """Fill event['title'] with the real headline where we can get it. Returns how many were newly fetched."""
    cache = _load()
    todo = []
    for e in events[:limit]:
        u = e.get("url")
        if not u:
            continue
        if u in cache:
            if cache[u]:
                e["title"] = cache[u]
                e["real_title"] = True
            continue
        todo.append(u)
    t0 = time.time()
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for u, title in zip(todo, ex.map(_fetch, todo)):
                cache[u] = title
        for e in events[:limit]:
            u = e.get("url")
            if u and cache.get(u):
                e["title"] = cache[u]
                e["real_title"] = True
        log.info("headlines: fetched %d new titles in %.0fs (%d hit)", len(todo), time.time() - t0,
                 sum(1 for e in events[:limit] if e.get("real_title")))
    if len(cache) > 20000:  # keep the cache from growing without bound
        cache = dict(list(cache.items())[-12000:])
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    return len(todo)
