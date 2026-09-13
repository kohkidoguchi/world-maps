"""BIS Data Portal SDMX v2 API: total credit to the non-financial sector (by borrowing sector, % of GDP and USD),
and daily USD/JPY."""
from __future__ import annotations

import io
import logging

import pandas as pd

from ..common import Obs, http_get, fetched_at, norm_quarter

log = logging.getLogger(__name__)
BASE = "https://stats.bis.org/api/v2/data/dataflow/BIS"

ISO2 = {"ARG": "AR", "AUS": "AU", "BRA": "BR", "CAN": "CA", "CHN": "CN", "FRA": "FR", "DEU": "DE", "IND": "IN",
        "IDN": "ID", "ITA": "IT", "JPN": "JP", "KOR": "KR", "MEX": "MX", "RUS": "RU", "SAU": "SA", "ZAF": "ZA",
        "TUR": "TR", "GBR": "GB", "USA": "US", "ESP": "ES", "NLD": "NL", "CHE": "CH", "SGP": "SG", "HKG": "HK",
        "IRL": "IE", "EA": "XM"}
ISO3 = {v: k for k, v in ISO2.items()}
BORROWER = {"C": "TOTAL", "G": "GOV", "H": "HH", "N": "NFC", "P": "PRIVATE"}


def fetch_total_credit(store):
    ctys = "+".join(ISO2.values())
    url = (f"{BASE}/WS_TC/2.0/Q.{ctys}.C+G+H+N+P.A.M+N.770+USD+XDC.A?format=csv&startPeriod=2000-01-01")
    df = pd.read_csv(io.BytesIO(http_get(url, ttl_hours=24)))
    n = 0
    for _, r in df.iterrows():
        if pd.isna(r["OBS_VALUE"]):
            continue
        # market value for private sectors; nominal value for government (BIS convention)
        if r["TC_BORROWERS"] == "G" and r["VALUATION"] != "N":
            continue
        if r["TC_BORROWERS"] != "G" and r["VALUATION"] != "M":
            continue
        unit = {"770": "PT_GDP"}.get(str(r["UNIT_TYPE"]), str(r["UNIT_TYPE"]))
        store.add(Obs("BIS_TC", ISO3.get(r["BORROWERS_CTY"], r["BORROWERS_CTY"]), BORROWER[r["TC_BORROWERS"]], "CREDIT",
                      entry="L", measure="ratio" if unit == "PT_GDP" else "stock", unit=unit,
                      unit_mult=0 if unit == "PT_GDP" else int(r.get("UNIT_MULT", 0) or 0), freq="Q",
                      period=norm_quarter(r["TIME_PERIOD"]), value=float(r["OBS_VALUE"]), note=str(r.get("TITLE_TS", ""))[:80]))
        n += 1
    store.mark_source("BIS_TC", name="BIS total credit to the non-financial sector", url=url, rows=n,
                      fetched_at=fetched_at(url), license="BIS terms (free re-use with attribution)")
    log.info("BIS TC: %d obs", n)


def fetch_usdjpy(store):
    url = f"{BASE}/WS_XRU/1.0/D.JP.JPY.A?format=csv&startPeriod=2015-01-01"
    df = pd.read_csv(io.BytesIO(http_get(url, ttl_hours=3)))
    n = 0
    for _, r in df.iterrows():
        if pd.isna(r["OBS_VALUE"]):
            continue
        store.add(Obs("BIS_FX", "JPN", "TOTAL", "FX_USDJPY", measure="price", unit="INDEX", freq="D",
                      period=str(r["TIME_PERIOD"]), value=float(r["OBS_VALUE"]), note="JPY per USD (BIS)"))
        n += 1
    store.mark_source("BIS_FX", name="BIS daily USD/JPY", url=url, rows=n, fetched_at=fetched_at(url), license="BIS terms")


def fetch_all(store):
    for fn in (fetch_total_credit, fetch_usdjpy):
        try:
            fn(store)
        except Exception as e:
            log.exception("BIS %s failed", fn.__name__)
            store.mark_source("BIS_" + fn.__name__.replace("fetch_", "").upper(), error=str(e)[:300])
