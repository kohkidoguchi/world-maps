"""Japan Ministry of Finance - International Transactions in Securities (weekly & monthly, designated major investors).

Weekly cross-border portfolio flows: the fastest public indicator of Japan's portfolio investment
(assets = residents' net purchases of foreign securities; liabilities = non-residents' net purchases
of Japanese securities). Unit: 100 million yen. Positive = net acquisition.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import re

from ..common import Obs, http_get, fetched_at

log = logging.getLogger(__name__)
BASE = "https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/"

# column layout of week.csv (0-based): see header rows in the file
W_COLS = {  # (entry, instrument) -> column index of the NET figure
    ("A", "F5"): 3, ("A", "F3L"): 6, ("A", "F3S"): 10, ("A", "PI"): 11,
    ("L", "F5"): 14, ("L", "F3L"): 17, ("L", "F3S"): 21, ("L", "PI"): 22,
}
M_COLS = {("A", "F5"): 5, ("A", "F3L"): 8, ("A", "F3S"): 12, ("A", "PI"): 13,
          ("L", "F5"): 16, ("L", "F3L"): 19, ("L", "F3S"): 23, ("L", "PI"): 24}

_num = re.compile(r"[-−▲]?\s*[\d,]+")


def _val(s: str) -> float | None:
    s = (s or "").replace("　", "").replace(" ", "").replace("▲", "-").replace("−", "-").replace(",", "")
    if s in ("", "-", "―", "…"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _z2h(s: str) -> str:
    return s.translate(str.maketrans("０１２３４５６７８９．～", "0123456789.~"))


def _week_end(label: str) -> str | None:
    """'2026．8．30～9．5' -> '2026-09-05' (handles year roll-over)."""
    t = _z2h(label).replace(" ", "")
    m = re.fullmatch(r"(\d{4})\.(\d{1,2})\.(\d{1,2})~(\d{1,2})\.(\d{1,2})", t)
    if not m:
        return None
    y, m1, d1, m2, d2 = map(int, m.groups())
    y2 = y + 1 if m2 < m1 else y
    try:
        return dt.date(y2, m2, d2).isoformat()
    except ValueError:
        return None


def fetch_weekly(store):
    url = BASE + "week.csv"
    raw = http_get(url, ttl_hours=6)
    text = raw.decode("cp932", "replace")
    upd = re.search(r"Final Update\s+([A-Za-z]+)\s+(\d+)\s*,\s*(\d{4})", text)
    release = ""
    if upd:
        try:
            release = dt.datetime.strptime(f"{upd.group(1)} {upd.group(2)} {upd.group(3)}", "%B %d %Y").date().isoformat()
        except ValueError:
            pass
    n = 0
    for row in csv.reader(io.StringIO(text)):
        if not row or len(row) < 23:
            continue
        end = _week_end(row[0])
        if not end:
            continue
        for (entry, instr), ci in W_COLS.items():
            v = _val(row[ci])
            if v is None:
                continue
            store.add(Obs("MOF_WEEKLY", "JPN", "ROW", "PI_WEEKLY", entry=entry, instrument=instr, measure="flow", unit="XDC",
                          unit_mult=8, freq="W", period=end, value=v, status="preliminary", release=release,
                          note="designated major investors; net acquisition"))
            n += 1
    store.mark_source("MOF_WEEKLY", name="MOF international transactions in securities (weekly)", url=url, rows=n,
                      fetched_at=fetched_at(url), release=release, license="MOF terms (free re-use with attribution)")
    log.info("MOF weekly: %d obs (release %s)", n, release)


def fetch_monthly(store):
    url = BASE + "montha1.csv"
    raw = http_get(url, ttl_hours=24)
    text = raw.decode("cp932", "replace")
    n = 0
    year = None
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 25:
            continue
        y = _z2h(row[0]).strip()
        if re.fullmatch(r"\d{4}", y):
            year = int(y)
        mon = _z2h(row[1]).replace("月", "").strip()
        if not (year and re.fullmatch(r"\d{1,2}", mon)):
            continue
        period = f"{year:04d}-{int(mon):02d}"
        for (entry, instr), ci in M_COLS.items():
            v = _val(row[ci])
            if v is None:
                continue
            store.add(Obs("MOF_MONTHLY", "JPN", "ROW", "PI_MONTHLY", entry=entry, instrument=instr, measure="flow", unit="XDC",
                          unit_mult=8, freq="M", period=period, value=v, status="preliminary",
                          note="designated major investors; net acquisition"))
            n += 1
    store.mark_source("MOF_MONTHLY", name="MOF international transactions in securities (monthly)", url=url, rows=n,
                      fetched_at=fetched_at(url), license="MOF terms")
    log.info("MOF monthly: %d obs", n)


def fetch_all(store):
    for fn in (fetch_weekly, fetch_monthly):
        try:
            fn(store)
        except Exception as e:
            log.exception("MOF %s failed", fn.__name__)
            store.mark_source("MOF_" + fn.__name__.replace("fetch_", "").upper(), error=str(e)[:300])
