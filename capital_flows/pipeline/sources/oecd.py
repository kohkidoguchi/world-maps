"""OECD SDMX API (sdmx.oecd.org) - national accounts by institutional sector.

Dataflows used (agency OECD.SDD.NAD):
  DSD_NASEC1@DF_QSA        quarterly non-financial sector accounts (Japan; euro area cross-check)
  DSD_NASEC20@DF_T620R_Q   quarterly financial accounts, transactions (flows), non-consolidated  (Japan, US)
  DSD_NASEC20@DF_T720R_Q   quarterly financial balance sheets (stocks), non-consolidated          (Japan, US)
  DSD_NASEC20@DF_T7PSD_Q   quarterly public sector debt                                         (Japan, US)
  DSD_NAMAIN1@DF_QNA       quarterly GDP (nominal, national currency)
Japan's OECD series are the BoJ flow of funds / Cabinet Office SNA re-coded to ESA2010-style codes,
which is what makes them directly comparable with the ECB and (via mapping) the Fed.
"""
from __future__ import annotations

import io
import json
import logging
import re

import pandas as pd

from ..common import Obs, SECTOR_MAP, http_get, fetched_at, norm_quarter

log = logging.getLogger(__name__)
AGENCY = "OECD.SDD.NAD"
BASE = "https://sdmx.oecd.org/public/rest"


def dims_of(flow: str, version: str) -> list[str]:
    url = f"{BASE}/dataflow/{AGENCY}/{flow}/{version}?references=all&format=json-structure-2.0.0"
    j = json.loads(http_get(url, ttl_hours=24 * 14))
    dsd = j["data"]["dataStructures"][0]
    return [d["id"] for d in dsd["dataStructureComponents"]["dimensionList"]["dimensions"]]


def fetch_csv(flow: str, version: str, sel: dict, start="1995-Q1", ttl=12) -> pd.DataFrame:
    dims = dims_of(flow, version)
    key = ".".join("+".join(sel[d]) if isinstance(sel.get(d), (list, tuple)) else sel.get(d, "") for d in dims)
    url = (f"{BASE}/data/{AGENCY},{flow},{version}/{key}?format=csvfilewithlabels"
           f"&dimensionAtObservation=AllDimensions&startPeriod={start}")
    raw = http_get(url, ttl_hours=ttl)
    df = pd.read_csv(io.BytesIO(raw))
    df.attrs["url"] = url
    return df


def _area(a: str) -> str:
    return {"EA20": "EA", "EA19": "EA", "EA": "EA"}.get(a, a)


def _status(row) -> str:
    st = str(row.get("OBS_STATUS", "") or "")
    return "preliminary" if st.upper().startswith(("P", "E")) else "actual"


# ------------------------------------------------------------------ non-financial (QSA)
QSA_TRANSACTIONS = ["B9", "B8G", "B6G", "B5G", "P3", "P31", "P51G", "P5", "D1", "D4", "D41", "D42", "D5",
                    "D61", "D62", "D2", "D9", "B2A3G", "OTR", "OTE", "B1GQ", "B101"]


OECD_ECONOMIES = ("JPN", "USA", "GBR", "DEU", "FRA", "ITA", "CAN", "AUS", "ESP", "NLD", "MEX", "KOR")


def fetch_qsa(store, areas=("JPN", "EA", "GBR", "DEU", "FRA", "ITA", "CAN", "AUS", "ESP", "NLD", "MEX", "KOR")):
    sel = {"FREQ": "Q", "ADJUSTMENT": "N", "REF_AREA": list(areas), "SECTOR": ["S1M", "S11", "S12", "S13", "S1", "S2"],
           "COUNTERPART_SECTOR": "S1", "TRANSACTION": QSA_TRANSACTIONS, "INSTR_ASSET": "_Z",
           "UNIT_MEASURE": "XDC", "PRICE_BASE": "V", "TRANSFORMATION": "N"}
    df = fetch_csv("DSD_NASEC1@DF_QSA", "1.1", sel)
    n = 0
    for _, r in df.iterrows():
        sector = SECTOR_MAP.get(r["SECTOR"])
        if not sector:
            continue
        store.add(Obs("OECD_QSA", _area(r["REF_AREA"]), sector, r["TRANSACTION"], entry=str(r["ACCOUNTING_ENTRY"]),
                      measure="flow", unit="XDC", unit_mult=int(r.get("UNIT_MULT", 6) or 6), freq="Q",
                      period=norm_quarter(r["TIME_PERIOD"]), value=float(r["OBS_VALUE"]), status=_status(r),
                      note="NSA; " + str(r.get("Institutional sector", ""))[:40]))
        n += 1
    store.mark_source("OECD_QSA", name="OECD Quarterly Sector Accounts (non-financial)", url=df.attrs["url"], rows=n,
                      fetched_at=fetched_at(df.attrs["url"]), license="OECD terms (CC BY 4.0)")
    log.info("OECD QSA: %d obs", n)


# ------------------------------------------------------------------ financial accounts (flows & stocks)
FA_INSTR = ["F", "F2", "F3", "F4", "F5", "F51", "F52", "F6", "F7", "F8", "B9F", "BF90"]
SAAR_AREAS = {"USA"}   # quarterly flows published at annual rates -> divided by 4


def _fetch_fa(store, flow_id, transaction, measure, src, areas):
    sel = {"FREQ": "Q", "ADJUSTMENT": "N", "REF_AREA": list(areas), "SECTOR": ["S1M", "S11", "S12", "S13", "S2", "S1"],
           "COUNTERPART_SECTOR": "S1", "CONSOLIDATION": "N", "ACCOUNTING_ENTRY": ["A", "L", "N"],
           "TRANSACTION": transaction, "INSTR_ASSET": FA_INSTR, "UNIT_MEASURE": ["XDC", "USD"],
           "PRICE_BASE": "V", "TRANSFORMATION": "N"}
    df = fetch_csv(flow_id, "1.1", sel)
    df = df[df["MATURITY"].isin(["T", "_Z"])]
    n = 0
    for _, r in df.iterrows():
        sector = SECTOR_MAP.get(r["SECTOR"])
        if not sector:
            continue
        area = _area(r["REF_AREA"])
        # OECD publishes the US quarterly financial transactions at seasonally adjusted annual rates (as in Fed Z.1 F tables)
        saar = measure == "flow" and area in SAAR_AREAS
        store.add(Obs(src, area, sector, "FIN", entry=str(r["ACCOUNTING_ENTRY"]),
                      instrument=str(r["INSTR_ASSET"]), measure=measure, unit=str(r["UNIT_MEASURE"]),
                      unit_mult=int(r.get("UNIT_MULT", 6) or 6), freq="Q", period=norm_quarter(r["TIME_PERIOD"]),
                      value=float(r["OBS_VALUE"]) / (4.0 if saar else 1.0), status=_status(r),
                      note="non-consolidated" + ("; SAAR/4 -> quarterly" if saar else "")))
        n += 1
    store.mark_source(src, name=f"OECD Financial accounts ({'flows' if measure == 'flow' else 'stocks'})",
                      url=df.attrs["url"], rows=n, fetched_at=fetched_at(df.attrs["url"]), license="OECD terms (CC BY 4.0)")
    log.info("%s: %d obs", src, n)


def fetch_fa_flows(store, areas=OECD_ECONOMIES):
    _fetch_fa(store, "DSD_NASEC20@DF_T620R_Q", "F", "flow", "OECD_FA_FLOW", areas)


def fetch_fa_stocks(store, areas=OECD_ECONOMIES):
    _fetch_fa(store, "DSD_NASEC20@DF_T720R_Q", "LE", "stock", "OECD_FA_STOCK", areas)


# ------------------------------------------------------------------ public sector debt
def fetch_psd(store, areas=OECD_ECONOMIES):
    sel = {"FREQ": "Q", "ADJUSTMENT": "N", "REF_AREA": list(areas), "SECTOR": ["S13", "S1311"], "COUNTERPART_SECTOR": "S1",
           "CONSOLIDATION": "C", "ACCOUNTING_ENTRY": "L", "TRANSACTION": "LE", "INSTR_ASSET": ["FD4", "F3", "F4", "F2"],
           "MATURITY": "T", "UNIT_MEASURE": ["XDC", "USD", "PT_B1GQ"], "TRANSFORMATION": "N"}
    df = fetch_csv("DSD_NASEC20@DF_T7PSD_Q", "1.1", sel)
    n = 0
    for _, r in df.iterrows():
        unit = {"PT_B1GQ": "PT_GDP"}.get(r["UNIT_MEASURE"], r["UNIT_MEASURE"])
        store.add(Obs("OECD_PSD", _area(r["REF_AREA"]), SECTOR_MAP.get(r["SECTOR"], r["SECTOR"]), "DEBT", entry="L",
                      instrument=str(r["INSTR_ASSET"]), measure="ratio" if unit == "PT_GDP" else "stock", unit=unit,
                      unit_mult=0 if unit == "PT_GDP" else int(r.get("UNIT_MULT", 6) or 6), freq="Q",
                      period=norm_quarter(r["TIME_PERIOD"]), value=float(r["OBS_VALUE"]), status=_status(r),
                      note="consolidated, nominal value"))
        n += 1
    store.mark_source("OECD_PSD", name="OECD Quarterly Public Sector Debt", url=df.attrs["url"], rows=n,
                      fetched_at=fetched_at(df.attrs["url"]), license="OECD terms (CC BY 4.0)")
    log.info("OECD PSD: %d obs", n)


# ------------------------------------------------------------------ quarterly GDP
def fetch_qna_gdp(store, areas=OECD_ECONOMIES + ("EA20", "CHN", "IND", "BRA", "IDN", "TUR", "ZAF", "ARG", "SAU", "RUS")):
    sel = {"FREQ": "Q", "ADJUSTMENT": "N", "REF_AREA": list(areas), "SECTOR": "S1", "COUNTERPART_SECTOR": "S1",
           "TRANSACTION": "B1GQ", "INSTR_ASSET": "_Z", "UNIT_MEASURE": "XDC", "PRICE_BASE": "V", "TRANSFORMATION": "N"}
    df = fetch_csv("DSD_NAMAIN1@DF_QNA", "1.1", sel)
    # keep the most complete series per area
    n = 0
    for area, g in df.groupby("REF_AREA"):
        keycols = [c for c in ["ACCOUNTING_ENTRY", "TABLE_IDENTIFIER", "VALUATION", "EXPENDITURE", "ACTIVITY"] if c in g.columns]
        best = None
        for _, sub in g.groupby(keycols) if keycols else [((), g)]:
            if best is None or len(sub) > len(best):
                best = sub
        for _, r in best.iterrows():
            store.add(Obs("OECD_QNA", _area(area), "TOTAL", "GDP", entry="B", measure="flow", unit="XDC",
                          unit_mult=int(r.get("UNIT_MULT", 6) or 6), freq="Q", period=norm_quarter(r["TIME_PERIOD"]),
                          value=float(r["OBS_VALUE"]), status=_status(r), note="NSA nominal GDP"))
            n += 1
    store.mark_source("OECD_QNA", name="OECD Quarterly National Accounts (nominal GDP)", url=df.attrs["url"], rows=n,
                      fetched_at=fetched_at(df.attrs["url"]), license="OECD terms (CC BY 4.0)")
    log.info("OECD QNA: %d obs", n)


def fetch_all(store):
    for fn in (fetch_qsa, fetch_fa_flows, fetch_fa_stocks, fetch_psd, fetch_qna_gdp):
        try:
            fn(store)
        except Exception as e:
            log.exception("OECD %s failed", fn.__name__)
            store.mark_source("OECD_" + fn.__name__.replace("fetch_", "").upper(), error=str(e)[:300])
