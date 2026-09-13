"""Recent price indices used to nowcast valuation changes after the last statistical quarter.

  OECD  DSD_STES@DF_FINMARK   monthly share price index (SHARE) and long-term interest rate (IRLT) - official, ~1 month lag
  MOF / US Treasury / BoE / ECB   daily 10-year government bond yields
  BIS   WS_XRU               daily exchange rates against USD
  Yahoo Finance chart API    daily equity indices, gold futures, US bond and REIT ETFs - market data, unofficial (labelled 参考)
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import re

import pandas as pd

from ..common import Obs, http_get, fetched_at

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36"}

MEI_AREAS = ["JPN", "USA", "GBR", "DEU", "FRA", "ITA", "ESP", "NLD", "CAN", "KOR", "AUS", "MEX", "EA20", "CHN", "IND", "BRA", "IDN", "TUR", "ZAF", "CHE"]


def fetch_oecd_mei(store):
    url = ("https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_FINMARK,/" + "+".join(MEI_AREAS) +
           ".M.SHARE+IRLT......?format=csvfilewithlabels&dimensionAtObservation=AllDimensions&startPeriod=2015-01")
    df = pd.read_csv(io.BytesIO(http_get(url, ttl_hours=24)))
    n = 0
    for _, r in df.iterrows():
        if pd.isna(r["OBS_VALUE"]):
            continue
        area = {"EA20": "EA"}.get(r["REF_AREA"], r["REF_AREA"])
        concept = "EQ_INDEX" if r["MEASURE"] == "SHARE" else "YIELD_LT"
        store.add(Obs("OECD_MEI", area, "TOTAL", concept, measure="price", unit="INDEX" if concept == "EQ_INDEX" else "PCT", freq="M",
                      period=str(r["TIME_PERIOD"]), value=float(r["OBS_VALUE"]),
                      note="OECD MEI " + ("share prices, 2015=100" if concept == "EQ_INDEX" else "long-term (10y) government bond yield")))
        n += 1
    store.mark_source("OECD_MEI", name="OECD Main Economic Indicators: share prices, long-term interest rates (monthly)", url=url, rows=n,
                      fetched_at=fetched_at(url), license="OECD terms (CC BY 4.0)")
    log.info("OECD MEI: %d obs", n)


def _add_daily(store, src, area, concept, series, note, unit="PCT"):
    n = 0
    for d, v in series:
        store.add(Obs(src, area, "TOTAL" if concept != "YIELD_10Y" else "GOV", concept, measure="price", unit=unit, freq="D", period=d, value=v, note=note))
        n += 1
    return n


def fetch_yields_daily(store):
    n = 0
    urls = []
    # Japan: MOF JGB yield curve, full history (English file: Date,1Y,...,10Y,...) plus the current-month Japanese file (R8.9.1 = 2026-09-01)
    ser = []
    try:
        url = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv"
        rows = list(csv.reader(io.StringIO(http_get(url, ttl_hours=24).decode("cp932", "replace"))))
        hdr = next(i for i, r in enumerate(rows) if r and r[0].strip() == "Date")
        col10 = rows[hdr].index("10Y")
        for r in rows[hdr + 1:]:
            if len(r) > col10 and re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", r[0].strip()):
                try:
                    y, m, d = map(int, r[0].split("/")); ser.append((dt.date(y, m, d).isoformat(), float(r[col10])))
                except ValueError:
                    pass
        urls.append(url)
    except Exception as e:
        log.warning("MOF JGB history: %s", repr(e)[:120])
    try:
        url = "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"
        rows = list(csv.reader(io.StringIO(http_get(url, ttl_hours=6).decode("cp932", "replace"))))
        hdr = next(i for i, r in enumerate(rows) if r and r[0].strip() in ("基準日", "\u57fa\u6e96\u65e5"))
        col10 = next(i for i, c in enumerate(rows[hdr]) if c.strip() in ("10年", "10\u5e74"))
        era = {"R": 2018, "H": 1988, "S": 1925}
        have = {d for d, _ in ser}
        for r in rows[hdr + 1:]:
            m = re.fullmatch(r"([RHS])(\d+)\.(\d+)\.(\d+)", (r[0] or "").strip()) if r else None
            if not m or len(r) <= col10:
                continue
            try:
                d = dt.date(era[m.group(1)] + int(m.group(2)), int(m.group(3)), int(m.group(4))).isoformat()
                if d not in have:
                    ser.append((d, float(r[col10])))
            except ValueError:
                pass
        urls.append(url)
    except Exception as e:
        log.warning("MOF JGB current: %s", repr(e)[:120])
    ser = sorted(x for x in ser if x[0] >= "2015-01-01")
    n += _add_daily(store, "YIELD_DAILY", "JPN", "YIELD_10Y", ser, "JGB 10y, MOF")
    # United States: daily Treasury par yield curve
    ser = []
    for year in range(2015, dt.date.today().year + 1):
        url = (f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all"
               f"?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")
        try:
            df = pd.read_csv(io.BytesIO(http_get(url, ttl_hours=6 if year == dt.date.today().year else 24 * 30)))
            for _, r in df.iterrows():
                v = r.get("10 Yr")
                if pd.notna(v):
                    ser.append((dt.datetime.strptime(r["Date"], "%m/%d/%Y").date().isoformat(), float(v)))
        except Exception as e:
            log.warning("UST %s: %s", year, str(e)[:100])
    ser.sort()
    n += _add_daily(store, "YIELD_DAILY", "USA", "YIELD_10Y", ser, "US Treasury 10y par yield")
    urls.append("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/...")
    # United Kingdom: BoE 10y nominal zero-coupon gilt yield
    url = ("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom=01/Jan/2015&Dateto=now"
           "&SeriesCodes=IUDMNZC&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N")
    try:
        df = pd.read_csv(io.BytesIO(http_get(url, ttl_hours=6)))
        ser = [(dt.datetime.strptime(r["DATE"], "%d %b %Y").date().isoformat(), float(r["IUDMNZC"])) for _, r in df.iterrows() if pd.notna(r["IUDMNZC"])]
        n += _add_daily(store, "YIELD_DAILY", "GBR", "YIELD_10Y", ser, "UK 10y gilt, BoE IUDMNZC"); urls.append(url)
    except Exception as e:
        log.warning("BoE: %s", str(e)[:120])
    # Euro area: ECB AAA 10y spot (also in ECB_MKT; duplicated here under the same concept for uniform access)
    url = "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y?format=csvdata&startPeriod=2015-01-01"
    try:
        df = pd.read_csv(io.BytesIO(http_get(url, ttl_hours=6)))
        ser = [(str(r["TIME_PERIOD"]), float(r["OBS_VALUE"])) for _, r in df.iterrows() if pd.notna(r["OBS_VALUE"])]
        n += _add_daily(store, "YIELD_DAILY", "EA", "YIELD_10Y", ser, "euro area AAA 10y spot, ECB"); urls.append(url)
    except Exception as e:
        log.warning("ECB YC: %s", str(e)[:120])
    store.mark_source("YIELD_DAILY", name="Daily 10-year government bond yields (MOF, US Treasury, BoE, ECB)", url=" ; ".join(urls[:4]), rows=n,
                      fetched_at=fetched_at(urls[0]) if urls else "", license="Public / official terms")
    log.info("yields daily: %d obs", n)


BIS_FX = {"JP": ("JPN", "JPY"), "XM": ("EA", "EUR"), "GB": ("GBR", "GBP"), "CA": ("CAN", "CAD"), "KR": ("KOR", "KRW"), "AU": ("AUS", "AUD"),
          "CH": ("CHE", "CHF"), "CN": ("CHN", "CNY"), "IN": ("IND", "INR"), "BR": ("BRA", "BRL"), "MX": ("MEX", "MXN"), "HK": ("HKG", "HKD"),
          "SG": ("SGP", "SGD"), "ID": ("IDN", "IDR"), "TR": ("TUR", "TRY"), "ZA": ("ZAF", "ZAR")}


def fetch_fx_daily(store):
    url = ("https://stats.bis.org/api/v2/data/dataflow/BIS/WS_XRU/1.0/D." + "+".join(BIS_FX) + "." + "+".join(v[1] for v in BIS_FX.values()) +
           ".A?format=csv&startPeriod=2015-01-01")
    df = pd.read_csv(io.BytesIO(http_get(url, ttl_hours=6)))
    n = 0
    for _, r in df.iterrows():
        if pd.isna(r["OBS_VALUE"]) or str(r["REF_AREA"]) not in BIS_FX:
            continue
        iso3, cur = BIS_FX[str(r["REF_AREA"])]
        # BIS WS_XRU quotes every currency as units of local currency per USD -> store as USD per unit of local currency
        v = float(r["OBS_VALUE"])
        usd_per_local = 1.0 / v if v else None
        if not usd_per_local:
            continue
        store.add(Obs("FX_DAILY", iso3, "TOTAL", "FX_USD", measure="price", unit="INDEX", freq="D", period=str(r["TIME_PERIOD"]),
                      value=usd_per_local, note=f"USD per {cur} (BIS)"))
        n += 1
    store.mark_source("FX_DAILY", name="BIS daily exchange rates (USD per unit of local currency)", url=url, rows=n, fetched_at=fetched_at(url),
                      license="BIS terms")
    log.info("fx daily: %d obs", n)


YAHOO = {  # concept, area, symbol, label
    ("EQ_DAILY", "JPN"): ("^N225", "日経平均"), ("EQ_DAILY", "USA"): ("^GSPC", "S&P 500"), ("EQ_DAILY", "EA"): ("^STOXX50E", "ユーロ・ストックス50"),
    ("EQ_DAILY", "GBR"): ("^FTSE", "FTSE 100"), ("EQ_DAILY", "CHN"): ("000300.SS", "CSI 300"), ("EQ_DAILY", "HKG"): ("^HSI", "ハンセン"),
    ("EQ_DAILY", "SGP"): ("^STI", "ストレーツ・タイムズ"), ("EQ_DAILY", "CHE"): ("^SSMI", "SMI"), ("EQ_DAILY", "CAN"): ("^GSPTSE", "S&P/TSX"),
    ("EQ_DAILY", "KOR"): ("^KS11", "KOSPI"), ("EQ_DAILY", "AUS"): ("^AXJO", "S&P/ASX 200"), ("EQ_DAILY", "IND"): ("^BSESN", "SENSEX"),
    ("EQ_DAILY", "BRA"): ("^BVSP", "ボベスパ"), ("EQ_DAILY", "MEX"): ("^MXX", "IPC"), ("EQ_DAILY", "DEU"): ("^GDAXI", "DAX"),
    ("GOLD_DAILY", "WLD"): ("GC=F", "金先物（COMEX）"), ("BOND_ETF", "USA"): ("BND", "米国総合債券ETF（BND）"), ("REIT_ETF", "USA"): ("VNQ", "米国REIT ETF（VNQ）"),
}


def fetch_yahoo(store):
    n = 0
    ok = []
    for (concept, area), (sym, label) in YAHOO.items():
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=2y&interval=1d"
        try:
            doc = json.loads(http_get(url, ttl_hours=6, headers=UA))
            res = doc["chart"]["result"][0]
            ts, cl = res["timestamp"], res["indicators"]["quote"][0]["close"]
            for t, c in zip(ts, cl):
                if c is None:
                    continue
                store.add(Obs("YAHOO", area, "TOTAL", concept, measure="price", unit="INDEX", freq="D",
                              period=dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), value=float(c), note=f"{label} ({sym}), {res['meta'].get('currency', '')}"))
                n += 1
            ok.append(sym)
        except Exception as e:
            log.warning("yahoo %s: %s", sym, str(e)[:100])
    store.mark_source("YAHOO", name="Yahoo Finance daily closes (equity indices, gold futures, US bond/REIT ETFs) - market reference", url="https://query1.finance.yahoo.com/v8/finance/chart/",
                      rows=n, fetched_at=fetched_at("https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=2y&interval=1d"), symbols=ok,
                      license="Reference only; not for redistribution")
    log.info("yahoo: %d obs (%d symbols)", n, len(ok))


def fetch_all(store):
    for fn in (fetch_oecd_mei, fetch_yields_daily, fetch_fx_daily, fetch_yahoo):
        try:
            fn(store)
        except Exception as e:
            log.exception("prices %s failed", fn.__name__)
            store.mark_source("PRICES_" + fn.__name__.replace("fetch_", "").upper(), error=str(e)[:300])
