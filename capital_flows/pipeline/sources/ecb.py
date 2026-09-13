"""ECB Data Portal API (data-api.ecb.europa.eu).

QSA  - euro area quarterly sector accounts: non-financial transactions, financial transactions and
       balance sheets by instrument, and whom-to-whom detail (counterpart sector, W2 = domestic).
MNA  - nominal GDP.
EXR / YC / ILM - daily FX, daily 10y AAA yield, weekly Eurosystem balance sheet (nowcast layer).

Area code I10 = euro area (changing composition, currently 21 members).
"""
from __future__ import annotations

import io
import logging

import pandas as pd

from ..common import Obs, SECTOR_MAP, http_get, fetched_at, norm_quarter

log = logging.getLogger(__name__)
BASE = "https://data-api.ecb.europa.eu/service/data"
EA = "I10"


def fetch_csv(flow: str, key: str, start: str | None = None, ttl=12, extra="") -> pd.DataFrame:
    url = f"{BASE}/{flow}/{key}?format=csvdata" + (f"&startPeriod={start}" if start else "") + extra
    df = pd.read_csv(io.BytesIO(http_get(url, ttl_hours=ttl)))
    df.attrs["url"] = url
    return df


def _status(r) -> str:
    st = str(r.get("OBS_STATUS", "") or "")
    return "preliminary" if st.upper().startswith(("P", "E")) else "actual"


def _mult(r) -> int:
    try:
        return int(r.get("UNIT_MULT", 6))
    except Exception:
        return 6


NONFIN_STO = ["B9", "B8G", "B6G", "B5G", "P3", "P31", "P51G", "P5", "D1", "D4", "D41", "D42", "D5", "D61", "D62",
              "D2", "D9", "B2A3G", "OTR", "OTE", "B101"]


def fetch_qsa_nonfin(store):
    # consolidation: N = transactions, _Z = balancing items (B9, B8G, ...), P = government totals (OTR/OTE), C = gov interest
    # expenditure dimension: _T for government uses (P3, D62, D41 paid, OTE), _Z elsewhere
    key = (f"Q.N.{EA}.W0.S1M+S11+S12+S13+S1.S1.N+_Z+P+C.C+D+B.{'+'.join(NONFIN_STO)}._Z._Z._Z+_T.XDC._T.S.V.N._T")
    df = fetch_csv("QSA", key, start="1999-01")
    n = 0
    for _, r in df.iterrows():
        sector = SECTOR_MAP.get(r["REF_SECTOR"])
        if not sector:
            continue
        store.add(Obs("ECB_QSA", "EA", sector, str(r["STO"]), entry=str(r["ACCOUNTING_ENTRY"]), measure="flow",
                      unit="XDC", unit_mult=_mult(r), freq="Q", period=norm_quarter(r["TIME_PERIOD"]),
                      value=float(r["OBS_VALUE"]), status=_status(r), release=str(r.get("LAST_UPDATE", "") or "")[:10],
                      note="NSA"))
        n += 1
    store.mark_source("ECB_QSA", name="ECB Euro area quarterly sector accounts (non-financial)", url=df.attrs["url"],
                      rows=n, fetched_at=fetched_at(df.attrs["url"]), license="ECB free re-use with attribution")
    log.info("ECB QSA nonfin: %d obs", n)


FIN_INSTR = ["F", "F2", "F3", "F4", "F5", "F51", "F52", "F6", "F7", "F8", "F2M"]


def fetch_qsa_fin(store):
    key = (f"Q.N.{EA}.W0.S1M+S11+S12+S13+S2+S1.S1.N.A+L+N.F+LE.{'+'.join(FIN_INSTR)}.T+_Z._Z.XDC._T.S.V.N._T")
    df = fetch_csv("QSA", key, start="1999-01")
    n = 0
    for _, r in df.iterrows():
        sector = SECTOR_MAP.get(r["REF_SECTOR"])
        if not sector:
            continue
        measure = "flow" if r["STO"] == "F" else "stock"
        store.add(Obs("ECB_QSA_FIN", "EA", sector, "FIN", entry=str(r["ACCOUNTING_ENTRY"]), instrument=str(r["INSTR_ASSET"]),
                      measure=measure, unit="XDC", unit_mult=_mult(r), freq="Q", period=norm_quarter(r["TIME_PERIOD"]),
                      value=float(r["OBS_VALUE"]), status=_status(r), release=str(r.get("LAST_UPDATE", "") or "")[:10],
                      note="non-consolidated"))
        n += 1
    store.mark_source("ECB_QSA_FIN", name="ECB Euro area financial accounts (flows & stocks)", url=df.attrs["url"],
                      rows=n, fetched_at=fetched_at(df.attrs["url"]), license="ECB free re-use with attribution")
    log.info("ECB QSA fin: %d obs", n)


def fetch_qsa_w2w(store):
    """Whom-to-whom: holder sector (REF_SECTOR) x issuer sector (COUNTERPART_SECTOR) by instrument.
    W2 = domestic counterparts; W1 = rest of the world (counterpart sector S1 -> mapped to ROW)."""
    instr = "F2+F3+F4+F5+F51+F52+F6"
    n = 0
    urls = []
    for cp_area, cp_sectors in (("W2", "S1M+S11+S12+S13"), ("W1", "S1")):
        key = f"Q.N.{EA}.{cp_area}.S1M+S11+S12+S13+S2.{cp_sectors}.N.A+L.F+LE.{instr}.T+_Z._Z.XDC._T.S.V.N._T"
        try:
            df = fetch_csv("QSA", key, start="2010-01")
        except Exception as e:
            log.warning("ECB w2w %s failed: %s", cp_area, str(e)[:120])
            continue
        urls.append(df.attrs["url"])
        for _, r in df.iterrows():
            sector = SECTOR_MAP.get(r["REF_SECTOR"])
            cp = "ROW" if cp_area == "W1" else SECTOR_MAP.get(r["COUNTERPART_SECTOR"], "")
            if not sector or not cp:
                continue
            store.add(Obs("ECB_W2W", "EA", sector, "FIN", entry=str(r["ACCOUNTING_ENTRY"]), instrument=str(r["INSTR_ASSET"]),
                          cp_sector=cp, measure="flow" if r["STO"] == "F" else "stock", unit="XDC", unit_mult=_mult(r),
                          freq="Q", period=norm_quarter(r["TIME_PERIOD"]), value=float(r["OBS_VALUE"]), status=_status(r),
                          note="whom-to-whom"))
            n += 1
    store.mark_source("ECB_W2W", name="ECB Euro area whom-to-whom financial accounts", url=" ; ".join(urls), rows=n,
                      fetched_at=fetched_at(urls[0]) if urls else "", license="ECB free re-use with attribution")
    log.info("ECB w2w: %d obs", n)


def fetch_row(store):
    """Rest-of-the-world sector for the euro area.
    (a) financial flows/stocks of the total economy vis-a-vis W1 (RoW), re-expressed from the RoW's side;
    (b) BP6 balance of payments: current account and financial account by functional category (EUR)."""
    n = 0
    urls = []
    key = f"Q.N.{EA}.W1.S1.S1.N.A+L.F+LE.{'+'.join(FIN_INSTR)}.T+_Z._Z.XDC._T.S.V.N._T"
    try:
        df = fetch_csv("QSA", key, start="1999-01")
        urls.append(df.attrs["url"])
        for _, r in df.iterrows():
            entry = "L" if r["ACCOUNTING_ENTRY"] == "A" else "A"   # EA assets on RoW = RoW liabilities
            store.add(Obs("ECB_QSA_FIN", "EA", "ROW", "FIN", entry=entry, instrument=str(r["INSTR_ASSET"]),
                          measure="flow" if r["STO"] == "F" else "stock", unit="XDC", unit_mult=_mult(r), freq="Q",
                          period=norm_quarter(r["TIME_PERIOD"]), value=float(r["OBS_VALUE"]), status=_status(r),
                          note="RoW side of total-economy positions vs W1"))
            n += 1
    except Exception as e:
        log.warning("ECB RoW financial (W1) failed: %s", str(e)[:120])
    # BP6: CA balance; FA by functional category, assets / liabilities / net
    cat = {"D": "FD", "P": "PI", "O": "OI", "R": "RA", "_Z": ""}
    for key2 in (f"Q.N.{EA}.W1.S1.S1.T.B.CA._Z._Z._Z.EUR._T._X.N.ALL",
                 f"Q.N.{EA}.W1.S1.S1.T.A+L+N.FA.D+P+O+R+_Z.._Z.EUR._T._X.N.ALL"):
        try:
            df = fetch_csv("BPS", key2, start="1999-01")
        except Exception as e:
            log.warning("ECB BPS failed: %s", str(e)[:120])
            continue
        urls.append(df.attrs["url"])
        for _, r in df.iterrows():
            concept = "CA" if r["INT_ACC_ITEM"] == "CA" else "FA"
            instr = cat.get(str(r.get("FUNCTIONAL_CAT", "_Z")), str(r.get("FUNCTIONAL_CAT", "")))
            sub = str(r.get("INSTR_ASSET", "F"))
            store.add(Obs("ECB_BPS", "EA", "ROW", concept, entry=str(r["ACCOUNTING_ENTRY"]), instrument=instr,
                          measure="flow", unit="XDC", unit_mult=_mult(r), freq="Q", period=norm_quarter(r["TIME_PERIOD"]),
                          value=float(r["OBS_VALUE"]), status=_status(r), note=f"BP6; instrument {sub}"))
            n += 1
    store.mark_source("ECB_BPS", name="ECB euro area balance of payments (BP6) and RoW financial account", url=" ; ".join(urls),
                      rows=n, fetched_at=fetched_at(urls[0]) if urls else "", license="ECB free re-use with attribution")
    log.info("ECB RoW/BPS: %d obs", n)


def fetch_gfs_debt(store):
    """Maastricht (EDP) general government gross debt, consolidated, nominal value."""
    n = 0
    urls = []
    for unit in ("XDC", "PT_B1GQ"):
        key = f"Q.N.{EA}.W0.S13.S1.C.L.LE.GD.T._Z.{unit}._T.F.V.N._T"
        try:
            df = fetch_csv("GFS", key, start="1999-01")
        except Exception as e:
            log.warning("ECB GFS %s failed: %s", unit, str(e)[:120])
            continue
        urls.append(df.attrs["url"])
        for _, r in df.iterrows():
            u = "PT_GDP" if unit == "PT_B1GQ" else "XDC"
            store.add(Obs("ECB_GFS", "EA", "GOV", "DEBT", entry="L", instrument="GD", measure="ratio" if u == "PT_GDP" else "stock",
                          unit=u, unit_mult=0 if u == "PT_GDP" else _mult(r), freq="Q", period=norm_quarter(r["TIME_PERIOD"]),
                          value=float(r["OBS_VALUE"]), status=_status(r), note="Maastricht debt"))
            n += 1
    store.mark_source("ECB_GFS", name="ECB euro area government debt (Maastricht)", url=" ; ".join(urls), rows=n,
                      fetched_at=fetched_at(urls[0]) if urls else "", license="ECB free re-use with attribution")
    log.info("ECB GFS: %d obs", n)


def fetch_gdp(store):
    df = fetch_csv("MNA", f"Q.N.{EA}.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.V.N", start="1999-01")
    n = 0
    for _, r in df.iterrows():
        store.add(Obs("ECB_MNA", "EA", "TOTAL", "GDP", entry="B", measure="flow", unit="XDC", unit_mult=_mult(r), freq="Q",
                      period=norm_quarter(r["TIME_PERIOD"]), value=float(r["OBS_VALUE"]), status=_status(r),
                      note="NSA nominal GDP"))
        n += 1
    store.mark_source("ECB_MNA", name="ECB/Eurostat euro area nominal GDP", url=df.attrs["url"], rows=n,
                      fetched_at=fetched_at(df.attrs["url"]), license="ECB free re-use with attribution")


# ---------------------------------------------------------------- market / nowcast layer
def fetch_market(store):
    n = 0
    urls = []
    # daily FX: USD per EUR, JPY per EUR
    for cur in ("USD", "JPY"):
        df = fetch_csv("EXR", f"D.{cur}.EUR.SP00.A", start="2015-01-01", ttl=3)
        urls.append(df.attrs["url"])
        for _, r in df.iterrows():
            if pd.isna(r["OBS_VALUE"]):
                continue
            store.add(Obs("ECB_MKT", "EA", "TOTAL", f"FX_{cur}EUR", measure="price", unit="INDEX", freq="D",
                          period=str(r["TIME_PERIOD"]), value=float(r["OBS_VALUE"]), note=f"{cur} per EUR, ECB reference"))
            n += 1
    # 10y euro area AAA government yield
    df = fetch_csv("YC", "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y", start="2015-01-01", ttl=3)
    urls.append(df.attrs["url"])
    for _, r in df.iterrows():
        if pd.isna(r["OBS_VALUE"]):
            continue
        store.add(Obs("ECB_MKT", "EA", "GOV", "YIELD_10Y", measure="price", unit="PCT", freq="D",
                      period=str(r["TIME_PERIOD"]), value=float(r["OBS_VALUE"]), note="AAA euro area spot 10y"))
        n += 1
    # weekly Eurosystem consolidated balance sheet: total assets
    df = fetch_csv("ILM", "W.U2.C.T000000.Z5.Z01", start="2015-01-01", ttl=12)
    urls.append(df.attrs["url"])
    for _, r in df.iterrows():
        if pd.isna(r["OBS_VALUE"]):
            continue
        store.add(Obs("ECB_MKT", "EA", "CB", "CB_ASSETS", entry="A", measure="stock", unit="XDC", unit_mult=_mult(r),
                      freq="W", period=str(r["TIME_PERIOD"]), value=float(r["OBS_VALUE"]), note="Eurosystem total assets"))
        n += 1
    store.mark_source("ECB_MKT", name="ECB daily FX / yields / weekly Eurosystem balance sheet", url=" ; ".join(urls),
                      rows=n, fetched_at=fetched_at(urls[0]), license="ECB free re-use with attribution")
    log.info("ECB market: %d obs", n)


def fetch_all(store):
    for fn in (fetch_qsa_nonfin, fetch_qsa_fin, fetch_qsa_w2w, fetch_row, fetch_gfs_debt, fetch_gdp, fetch_market):
        try:
            fn(store)
        except Exception as e:
            log.exception("ECB %s failed", fn.__name__)
            store.mark_source("ECB_" + fn.__name__.replace("fetch_", "").upper(), error=str(e)[:300])
