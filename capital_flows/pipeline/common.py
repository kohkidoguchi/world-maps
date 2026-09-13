"""Shared plumbing for the capital-flows pipeline.

* HTTP with on-disk caching (works behind the corporate SSL proxy via truststore)
* The common observation model:  area x sector x counterpart x instrument
  x measure (flow / stock / ratio) x period, with status and release metadata
* Small period helpers
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, fields

import requests

try:  # corporate proxy: trust the Windows certificate store
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
os.makedirs(RAW_DIR, exist_ok=True)

log = logging.getLogger("capital_flows")

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) capital-flows-dashboard/0.1",
    "Accept": "*/*",
}


# --------------------------------------------------------------------------- HTTP
def http_get(url: str, *, ttl_hours: float = 6, timeout: int = 240, headers: dict | None = None) -> bytes:
    """GET with a simple file cache keyed by URL. Raises on HTTP errors."""
    key = hashlib.sha1(url.encode()).hexdigest()
    path = os.path.join(RAW_DIR, key + ".bin")
    meta = os.path.join(RAW_DIR, key + ".json")
    if os.path.exists(path) and os.path.exists(meta):
        age_h = (time.time() - os.path.getmtime(path)) / 3600
        if age_h < ttl_hours:
            return open(path, "rb").read()
    t0 = time.time()
    last_exc = None
    for attempt in range(3):  # transient resets happen on large SDMX responses behind the proxy
        try:
            r = requests.get(url, timeout=timeout, headers={**UA, **(headers or {})})
            break
        except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
            last_exc = e
            log.warning("retry %d for %s: %s", attempt + 1, url[:100], str(e)[:80])
            time.sleep(3 * (attempt + 1))
    else:
        raise RuntimeError(f"network failure for {url[:160]} :: {last_exc}")
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} for {url[:160]} :: {r.text[:200]}")
    open(path, "wb").write(r.content)
    json.dump({"url": url, "fetched_at": now_iso(), "status": r.status_code, "bytes": len(r.content),
               "seconds": round(time.time() - t0, 1)}, open(meta, "w"))
    log.info("GET %s -> %d bytes in %.1fs", url[:110], len(r.content), time.time() - t0)
    return r.content


def fetched_at(url: str) -> str:
    key = hashlib.sha1(url.encode()).hexdigest()
    meta = os.path.join(RAW_DIR, key + ".json")
    if os.path.exists(meta):
        return json.load(open(meta)).get("fetched_at", "")
    return ""


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- periods
def period_end(period: str) -> str:
    """'2026-Q1' -> '2026-03-31', '2026-08' -> '2026-08-31', '2026' -> '2026-12-31', ISO date -> itself."""
    p = period.strip()
    m = re.fullmatch(r"(\d{4})-?Q([1-4])", p)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        mth = q * 3
        return f"{y}-{mth:02d}-{_month_end(y, mth):02d}"
    m = re.fullmatch(r"(\d{4})-(\d{2})", p)
    if m:
        y, mth = int(m.group(1)), int(m.group(2))
        return f"{y}-{mth:02d}-{_month_end(y, mth):02d}"
    m = re.fullmatch(r"(\d{4})", p)
    if m:
        return f"{p}-12-31"
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", p)
    if m:
        return p
    m = re.fullmatch(r"(\d{4})-S([12])", p)
    if m:
        y, s = int(m.group(1)), int(m.group(2))
        return f"{y}-06-30" if s == 1 else f"{y}-12-31"
    m = re.fullmatch(r"(\d{4})-W(\d{2})", p)  # ISO week -> Friday of that week
    if m:
        return dt.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 5).isoformat()
    raise ValueError(f"unrecognised period {period!r}")


def _month_end(y: int, m: int) -> int:
    nxt = dt.date(y + (m // 12), (m % 12) + 1, 1)
    return (nxt - dt.timedelta(days=1)).day


def norm_quarter(p: str) -> str:
    """Normalise quarter labels ('2026-Q1', '2026Q1', '2026:Q1') to '2026-Q1'."""
    m = re.fullmatch(r"(\d{4})[-:]?Q([1-4])", p.strip())
    if not m:
        raise ValueError(p)
    return f"{m.group(1)}-Q{m.group(2)}"


def quarter_of(date_iso: str) -> str:
    d = dt.date.fromisoformat(date_iso)
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


# --------------------------------------------------------------------------- model
SECTOR_MAP = {
    "S1M": "HH", "S14": "HH", "S14_S15": "HH", "S11": "NFC", "S12": "FIN", "S13": "GOV",
    "S1311": "GOV_C", "S13M": "GOV_L", "S2": "ROW", "S1": "TOTAL", "S121": "CB",
}
SECTOR_JA = {
    "HH": "家計", "NFC": "企業（非金融法人）", "FIN": "金融機関", "GOV": "政府", "GOV_C": "中央政府",
    "GOV_L": "地方政府", "ROW": "海外", "TOTAL": "国内経済全体", "CB": "中央銀行", "PRIVATE": "民間非金融部門",
}


@dataclass
class Obs:
    src: str                 # data source id (IMF_WEO, OECD_FA, FED_Z1, ...)
    area: str                # ISO3 or EA
    sector: str              # HH NFC FIN GOV ROW TOTAL CB ...
    concept: str             # harmonised concept code (B9, B8G, P3, FIN, DEBT, CA, ...)
    entry: str = ""          # A assets / L liabilities / N net / B balance / C resources / D uses
    instrument: str = ""     # F2 F3 F4 F5 F51 F52 F6 F7 F8 FD PI OI RA ...
    cp_sector: str = ""      # counterpart sector (whom-to-whom) if known
    cp_area: str = ""        # counterpart economy (bilateral positions) if known
    measure: str = "flow"    # flow | stock | ratio | price | level
    unit: str = "XDC"        # XDC (domestic currency) | USD | PT_GDP | PCT | INDEX
    unit_mult: int = 0       # power of ten (6 = millions)
    freq: str = "Q"          # A Q M W D
    period: str = ""         # label, e.g. 2026-Q1 / 2026-08 / 2026-09-10
    obs_date: str = ""       # ISO date of period end
    value: float = 0.0
    status: str = "actual"   # actual | preliminary | forecast | nowcast
    release: str = ""        # publication / last-update date if the source provides it
    note: str = ""           # free text: adjustment, mapping caveats

    def __post_init__(self):
        if not self.obs_date and self.period:
            self.obs_date = period_end(self.period)


class Store:
    """In-memory collection of observations with CSV export."""

    def __init__(self):
        self.rows: list[Obs] = []
        self.sources: dict[str, dict] = {}

    def add(self, o: Obs):
        self.rows.append(o)

    def extend(self, it):
        for o in it:
            self.rows.append(o)

    def mark_source(self, sid: str, **info):
        self.sources.setdefault(sid, {}).update(info)

    def to_csv(self, path: str):
        cols = [f.name for f in fields(Obs)]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for o in self.rows:
                w.writerow(asdict(o))

    def summary(self) -> dict:
        out: dict[str, dict] = {}
        for o in self.rows:
            s = out.setdefault(o.src, {"rows": 0, "latest": "", "areas": set()})
            s["rows"] += 1
            s["areas"].add(o.area)
            if o.obs_date > s["latest"]:
                s["latest"] = o.obs_date
        for s in out.values():
            s["areas"] = sorted(s["areas"])
        return out


# --------------------------------------------------------------------------- SDMX-JSON helper
def _vid(v):
    if isinstance(v, dict):
        return v.get("id") or v.get("value") or v.get("name")
    return v


def sdmx_json_series(doc: dict):
    """Yield (dims: dict, attrs: dict, observations: list[(period, value, obs_attrs)]) from an SDMX-JSON 2.x message."""
    data = doc["data"]
    struct = data["structures"][0]
    sdims = struct["dimensions"]["series"]
    odim = struct["dimensions"]["observation"][0]
    periods = [_vid(v) for v in odim["values"]]
    sattrs = struct.get("attributes", {}).get("series", [])
    oattrs = struct.get("attributes", {}).get("observation", [])
    for ds in data["dataSets"]:
        for key, ser in ds.get("series", {}).items():
            idx = [int(i) for i in key.split(":")]
            dims = {sdims[i]["id"]: _vid(sdims[i]["values"][idx[i]]) for i in range(len(idx))}
            attrs = {}
            for i, a in enumerate(ser.get("attributes") or []):
                if i < len(sattrs) and a is not None:
                    vals = sattrs[i].get("values") or []
                    attrs[sattrs[i]["id"]] = vals[a]["id"] if isinstance(a, int) and a < len(vals) else a
            obs = []
            for oi, ov in ser.get("observations", {}).items():
                val = ov[0]
                if val is None or val == "":
                    continue
                oa = {}
                for j, a in enumerate(ov[1:]):
                    if j < len(oattrs) and a is not None:
                        vals = oattrs[j].get("values") or []
                        oa[oattrs[j]["id"]] = vals[a]["id"] if isinstance(a, int) and a < len(vals) else a
                try:
                    obs.append((periods[int(oi)], float(val), oa))
                except (ValueError, IndexError):
                    continue
            yield dims, attrs, obs
