"""Valuation of wealth beyond the financial accounts: housing, gold, and the price indices behind them.

  OECD  DSD_NASEC10@DF_TABLE9B   annual balance sheets for non-financial assets (dwellings N111N, land N211N) by sector
  Fed   Z.1 B.101                households real estate at market value: level (LM), revaluation (FR), transactions (FU)
  BIS   WS_SPP                   quarterly residential property price index (nominal)
  IMF   PCPS                     gold price index (quarterly / monthly)
  OECD / ECB financial accounts  central banks monetary gold (F11): stocks and transactions
"""
from __future__ import annotations

import io
import json
import logging
import zipfile

import pandas as pd

from ..common import Obs, http_get, fetched_at, norm_quarter, sdmx_json_series
from .oecd import OECD_ECONOMIES, fetch_csv as oecd_csv, _area, _status

log = logging.getLogger(__name__)


def fetch_nonfin_assets(store):
    """Households dwellings and land (net, current prices), annual, local currency."""
    n = 0
    urls = []
    for a in OECD_ECONOMIES:
        url = (f"https://sdmx.oecd.org/public/rest/data/OECD.SDD.NAD,DSD_NASEC10@DF_TABLE9B,1.1/A.{a}.S1M+S1..........?"
               f"format=csvfilewithlabels&dimensionAtObservation=AllDimensions&startPeriod=2010")
        try:
            df = pd.read_csv(io.BytesIO(http_get(url, ttl_hours=24 * 7)))
        except Exception as e:
            log.warning("TABLE9B %s: %s", a, str(e)[:100])
            continue
        urls.append(url)
        df = df[(df.UNIT_MEASURE == "XDC") & df.INSTR_ASSET.isin(["N111N", "N211N", "N11N", "NN"])]
        for _, r in df.iterrows():
            sector = {"S1M": "HH", "S1": "TOTAL"}[r["SECTOR"]]
            store.add(Obs("OECD_NFA", _area(a), sector, "NFA", entry="A", instrument=str(r["INSTR_ASSET"]), measure="stock",
                          unit="XDC", unit_mult=int(r.get("UNIT_MULT", 6) or 6), freq="A", period=str(r["TIME_PERIOD"]),
                          value=float(r["OBS_VALUE"]), status=_status(r), note="non-financial assets, net, current prices"))
            n += 1
    store.mark_source("OECD_NFA", name="OECD annual balance sheets for non-financial assets (dwellings, land)", url=" ; ".join(urls[:2]),
                      rows=n, fetched_at=fetched_at(urls[0]) if urls else "", license="OECD terms (CC BY 4.0)")
    log.info("OECD NFA: %d obs", n)


Z1_URL = "https://www.federalreserve.gov/releases/z1/current/z1_csv_files.zip"
Z1_SERIES = {  # code -> (sector, instrument, measure, note)
    "LM155035005": ("HH", "RE", "stock", "households real estate at market value"),
    "FR155035005": ("HH", "RE", "reval", "households real estate: revaluation"),
    "FU155035005": ("HH", "RE", "flow", "households real estate: transactions (unadjusted)"),
    "LM152010005": ("HH", "NFA_T", "stock", "households nonfinancial assets"),
    "LM105035005": ("NFC", "RE", "stock", "nonfinancial corporate real estate at market value"),
    "FR105035005": ("NFC", "RE", "reval", "nonfinancial corporate real estate: revaluation"),
}


def fetch_us_realestate(store):
    z = zipfile.ZipFile(io.BytesIO(http_get(Z1_URL, ttl_hours=24 * 7)))
    index = {}
    for fn in z.namelist():
        if fn.startswith("csv/") and fn.endswith(".csv"):
            head = z.open(fn).readline().decode("utf-8", "replace").strip()
            for col in head.split(",")[1:]:
                index.setdefault(col, fn)
    n = 0
    for code, (sector, instr, measure, note) in Z1_SERIES.items():
        fn = index.get(code + ".Q")
        if not fn:
            log.warning("Z.1 %s not found", code)
            continue
        df = pd.read_csv(z.open(fn), na_values=["ND"], index_col=0)
        s = pd.to_numeric(df[code + ".Q"], errors="coerce").dropna()
        for p, v in s.items():
            q = norm_quarter(str(p).replace(":", "-"))
            if q < "2010-Q1":
                continue
            store.add(Obs("FED_Z1_RE", "USA", sector, "NFA", entry="A", instrument=instr, measure=measure, unit="XDC", unit_mult=6,
                          freq="Q", period=q, value=float(v), note=note))
            n += 1
    store.mark_source("FED_Z1_RE", name="Fed Z.1 B.101/B.103 real estate at market value (level, revaluation, transactions)", url=Z1_URL,
                      rows=n, fetched_at=fetched_at(Z1_URL), license="Public domain")
    log.info("Z.1 real estate: %d obs", n)


BIS_ISO3 = {"JP": "JPN", "US": "USA", "XM": "EA", "GB": "GBR", "DE": "DEU", "FR": "FRA", "IT": "ITA", "ES": "ESP", "NL": "NLD",
            "CA": "CAN", "KR": "KOR", "AU": "AUS", "MX": "MEX", "CN": "CHN", "IN": "IND", "BR": "BRA", "ID": "IDN", "TR": "TUR",
            "ZA": "ZAF", "RU": "RUS", "SA": "SAU", "AR": "ARG", "CH": "CHE", "SG": "SGP", "HK": "HKG", "IE": "IRL"}


def fetch_property_prices(store):
    url = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_SPP/1.0/Q..N.628?format=csv&startPeriod=2010-01-01"
    df = pd.read_csv(io.BytesIO(http_get(url, ttl_hours=24)))
    n = 0
    for _, r in df.iterrows():
        iso3 = BIS_ISO3.get(str(r["REF_AREA"]))
        if not iso3 or pd.isna(r["OBS_VALUE"]):
            continue
        store.add(Obs("BIS_SPP", iso3, "TOTAL", "HPI", measure="price", unit="INDEX", freq="Q", period=norm_quarter(r["TIME_PERIOD"]),
                      value=float(r["OBS_VALUE"]), note="residential property prices, nominal, 2010=100"))
        n += 1
    store.mark_source("BIS_SPP", name="BIS residential property prices (nominal index)", url=url, rows=n, fetched_at=fetched_at(url),
                      license="BIS terms")
    log.info("BIS SPP: %d obs", n)


def fetch_gold_price(store):
    n = 0
    urls = []
    for freq in ("Q", "M"):
        url = f"https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.RES/PCPS/+/G001.PGOLD.INDEX+USD.{freq}?c[TIME_PERIOD]=ge:2010&format=sdmx-json"
        urls.append(url)
        try:
            doc = json.loads(http_get(url, ttl_hours=24))
        except Exception as e:
            log.warning("PCPS %s: %s", freq, str(e)[:100])
            continue
        for dims, attrs, obs in sdmx_json_series(doc):
            unit = "INDEX" if dims["DATA_TRANSFORMATION"] == "INDEX" else "USD"
            for period, val, oa in obs:
                p = period.replace("-M", "-") if freq == "M" else period
                store.add(Obs("IMF_PCPS", "WLD", "TOTAL", "PGOLD", measure="price", unit=unit, freq=freq, period=p, value=val,
                              note="gold price, " + ("index 2016=100" if unit == "INDEX" else "USD per troy ounce")))
                n += 1
    store.mark_source("IMF_PCPS", name="IMF primary commodity prices: gold", url=" ; ".join(urls), rows=n, fetched_at=fetched_at(urls[0]),
                      license="IMF terms of use")
    log.info("PCPS gold: %d obs", n)


def fetch_gold_holdings(store):
    """Monetary gold (F11) of central banks: stocks (LE) and transactions (F). OECD for member economies, ECB for the euro area."""
    n = 0
    urls = []
    for flow_id, transaction, measure in (("DSD_NASEC20@DF_T720R_Q", "LE", "stock"), ("DSD_NASEC20@DF_T620R_Q", "F", "flow")):
        sel = {"FREQ": "Q", "ADJUSTMENT": "N", "REF_AREA": list(OECD_ECONOMIES), "SECTOR": ["S121", "S12"], "COUNTERPART_SECTOR": "S1",
               "CONSOLIDATION": "N", "ACCOUNTING_ENTRY": "A", "TRANSACTION": transaction, "INSTR_ASSET": ["F11", "F1"],
               "UNIT_MEASURE": "XDC", "PRICE_BASE": "V", "TRANSFORMATION": "N"}
        try:
            df = oecd_csv(flow_id, "1.1", sel, start="2010-Q1", ttl=24 * 7)
        except Exception as e:
            log.warning("gold %s: %s", flow_id, str(e)[:100])
            continue
        urls.append(df.attrs["url"])
        for _, r in df.iterrows():
            store.add(Obs("GOLD_HOLD", _area(r["REF_AREA"]), "CB" if r["SECTOR"] == "S121" else "FIN", "FIN", entry="A",
                          instrument=str(r["INSTR_ASSET"]), measure=measure, unit="XDC", unit_mult=int(r.get("UNIT_MULT", 6) or 6),
                          freq="Q", period=norm_quarter(r["TIME_PERIOD"]), value=float(r["OBS_VALUE"]), status=_status(r),
                          note="monetary gold (and SDRs for F1)"))
            n += 1
    for sto, measure in (("LE", "stock"), ("F", "flow")):
        url = f"https://data-api.ecb.europa.eu/service/data/QSA/Q.N.I10.W0.S121+S12.S1.N.A.{sto}.F11+F1._Z._Z.XDC._T.S.V.N._T?format=csvdata&startPeriod=2010-01"
        try:
            df = pd.read_csv(io.BytesIO(http_get(url, ttl_hours=24 * 7)))
        except Exception as e:
            log.warning("ECB gold %s: %s", sto, str(e)[:100])
            continue
        urls.append(url)
        for _, r in df.iterrows():
            store.add(Obs("GOLD_HOLD", "EA", "CB" if r["REF_SECTOR"] == "S121" else "FIN", "FIN", entry="A", instrument=str(r["INSTR_ASSET"]),
                          measure=measure, unit="XDC", unit_mult=int(r.get("UNIT_MULT", 6) or 6), freq="Q",
                          period=norm_quarter(r["TIME_PERIOD"]), value=float(r["OBS_VALUE"]), status=_status(r), note="monetary gold"))
            n += 1
    store.mark_source("GOLD_HOLD", name="Central banks monetary gold (F11): stocks and transactions", url=" ; ".join(urls[:2]), rows=n,
                      fetched_at=fetched_at(urls[0]) if urls else "", license="OECD / ECB terms")
    log.info("gold holdings: %d obs", n)


def fetch_all(store):
    for fn in (fetch_nonfin_assets, fetch_us_realestate, fetch_property_prices, fetch_gold_price, fetch_gold_holdings):
        try:
            fn(store)
        except Exception as e:
            log.exception("wealth %s failed", fn.__name__)
            store.mark_source("WEALTH_" + fn.__name__.replace("fetch_", "").upper(), error=str(e)[:300])
