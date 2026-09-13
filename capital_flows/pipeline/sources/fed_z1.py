"""Federal Reserve Z.1 Financial Accounts of the United States (full CSV package).

We use the Integrated Macroeconomic Accounts sector tables (S.3 households, S.5 non-financial
corporations, S.7 federal government, S.8 state & local, S.6 financial business, S.9 rest of the world)
and map their lines onto the ESA/SNA-style concept codes used for Japan and the euro area.

Units: flows (FA) are seasonally adjusted annual rates in millions of USD -> divided by 4 to a quarterly
rate; levels (FL) are amounts outstanding in millions of USD; FU = unadjusted quarterly flows.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile

import pandas as pd

from ..common import Obs, http_get, fetched_at

log = logging.getLogger(__name__)
URL = "https://www.federalreserve.gov/releases/z1/current/z1_csv_files.zip"

# (sector, concept, entry, instrument, [series], sign, note)
MAP = [
    # ---- households & NPISH (S.3)
    ("HH", "B9", "B", "", ["FA155000005"], 1, "net lending, financial account basis"),
    ("HH", "B8G", "C", "", ["FA156000105"], 1, "gross saving less net capital transfers (incl. durables)"),
    ("HH", "B6G", "C", "", ["FA156012005"], 1, "disposable personal income"),
    ("HH", "INC", "C", "", ["FA156010001"], 1, "personal income"),
    ("HH", "P3", "D", "", ["FA156900005"], 1, "personal outlays"),
    ("HH", "P5", "D", "", ["FA155050005"], 1, "capital expenditures incl. consumer durables"),
    ("HH", "P51G", "D", "", ["FA155012005"], 1, "residential investment"),
    ("HH", "D5", "D", "", ["FA156210005"], 1, "personal current taxes"),
    ("HH", "FIN", "A", "F", ["FA154090005"], 1, ""),
    ("HH", "FIN", "L", "F", ["FA154190005"], 1, ""),
    ("HH", "FIN", "A", "F2", ["FA153020005", "FA153030005"], 1, "deposits & currency"),
    ("HH", "FIN", "A", "F3", ["FA154022005"], 1, "debt securities"),
    ("HH", "FIN", "A", "F4", ["FA154035005"], 1, "loans"),
    ("HH", "FIN", "A", "F51", ["FA153064105", "FA153081115"], 1, "corporate & other equity"),
    ("HH", "FIN", "A", "F52", ["FA153034005", "FA153064205", "FA153064705", "FA153064505"], 1, "MMF, mutual, hedge, private debt funds"),
    ("HH", "FIN", "A", "F6", ["FA153040005", "FA153050005"], 1, "life insurance & pension entitlements"),
    ("HH", "FIN", "L", "F4", ["FA154135005"], 1, "loans (mortgages, consumer credit)"),
    # ---- non-financial corporate business (S.5)
    ("NFC", "B9", "B", "", ["FA105000005"], 1, ""),
    ("NFC", "B8G", "C", "", ["FA106000105"], 1, "gross saving incl. foreign retained earnings"),
    ("NFC", "B2A3G", "C", "", ["FA106060005"], 1, "profits before tax (proxy for operating surplus)"),
    ("NFC", "P5", "D", "", ["FA105050005"], 1, "capital expenditures"),
    ("NFC", "P51G", "D", "", ["FA105019005"], 1, "gross fixed investment"),
    ("NFC", "D5", "D", "", ["FA106231005"], 1, "taxes on corporate income"),
    ("NFC", "D42", "D", "", ["FA106121075"], 1, "net dividends paid"),
    ("NFC", "FIN", "A", "F", ["FA104090005"], 1, ""),
    ("NFC", "FIN", "L", "F", ["FA104190005"], 1, ""),
    ("NFC", "FIN", "A", "F2", ["FA103020005", "FA103030003"], 1, ""),
    ("NFC", "FIN", "A", "F3", ["FA104022005"], 1, ""),
    ("NFC", "FIN", "A", "F51", ["FA103064103", "FA103092105"], 1, "equities incl. direct investment abroad"),
    ("NFC", "FIN", "L", "F3", ["FA104122005"], 1, "debt securities"),
    ("NFC", "FIN", "L", "F4", ["FA104135005"], 1, "loans"),
    ("NFC", "FIN", "L", "F51", ["FA103164105"], 1, "corporate equities"),
    ("NFC", "FIN", "L", "FD", ["FA103192105"], 1, "foreign direct investment in US"),
    # ---- general government = federal (S.7) + state & local (S.8)
    ("GOV", "B9", "B", "", ["FA315000005", "FA215000005"], 1, "federal + state/local"),
    ("GOV", "B8G", "C", "", ["FA316000105", "FA216000105"], 1, ""),
    ("GOV", "OTR", "C", "", ["FA316010105", "FA216010105"], 1, "current receipts"),
    ("GOV", "OTE", "D", "", ["FA316900005", "FA216900005"], 1, "current expenditures"),
    ("GOV", "D41", "D", "", ["FA316130001", "FA216130001"], 1, "interest paid"),
    ("GOV", "D5", "C", "", ["FA316210001", "FA316231001", "FA216210001", "FA216231001"], 1, "personal + corporate income taxes"),
    ("GOV", "D61", "C", "", ["FA316601001", "FA216601001"], 1, "social insurance contributions"),
    ("GOV", "D62", "D", "", ["FA316404001", "FA216404001"], 1, "social benefits (IMA 'social contributions paid')"),
    ("GOV", "P3", "D", "", ["FA316901001", "FA216901001"], 1, "consumption expenditures"),
    ("GOV", "P51G", "D", "", ["FA315019001", "FA215019001"], 1, "gross fixed investment"),
    ("GOV", "FIN", "A", "F", ["FA314090005", "FA214090005"], 1, ""),
    ("GOV", "FIN", "L", "F", ["FA314190005", "FA214190005"], 1, ""),
    ("GOV", "FIN", "L", "F3", ["FA314122005", "FA213162005"], 1, "Treasury + municipal securities"),
    ("GOV", "FIN", "L", "F4", ["FA314135005", "FA214141005"], 1, ""),
    ("GOV_C", "B9", "B", "", ["FA315000005"], 1, "federal only"),
    ("GOV_C", "FIN", "L", "F3", ["FA314122005"], 1, "federal debt securities"),
    # ---- domestic financial sectors (S.6)
    ("FIN", "B9", "B", "", ["FA795000005"], 1, ""),
    ("FIN", "FIN", "A", "F", ["FA794090005"], 1, ""),
    ("FIN", "FIN", "L", "F", ["FA794190005"], 1, ""),
    ("FIN", "FIN", "L", "F2", ["FA793120005", "FA703130005"], 1, "deposits"),
    ("FIN", "FIN", "A", "F3", ["FA794022005"], 1, ""),
    ("FIN", "FIN", "A", "F4", ["FA794035005"], 1, ""),
    ("FIN", "FIN", "A", "F51", ["FA793064105"], 1, ""),
    ("FIN", "FIN", "L", "F3", ["FA794122005"], 1, ""),
    ("FIN", "FIN", "L", "F6", ["FA543140005", "FA583150005"], 1, "life insurance reserves + pension entitlements"),
    # ---- rest of the world (S.9): assets = foreign claims on US (inflows), liabilities = US claims abroad (outflows)
    ("ROW", "B9", "B", "", ["FA265000005"], 1, "RoW net lending = -US current account (approx.)"),
    ("ROW", "CA", "B", "", ["FA266000005"], 1, "US current account balance (NIPA); RoW net lending = -CA"),
    ("ROW", "FIN", "A", "F", ["FA264090005"], 1, "foreign acquisition of US assets"),
    ("ROW", "FIN", "L", "F", ["FA264190005"], 1, "US acquisition of foreign assets"),
    ("ROW", "FIN", "A", "F3", ["FA263061105", "FA263063005", "FA263061705"], 1, "Treasuries, corporate bonds, agencies"),
    ("ROW", "FIN", "A", "F51", ["FA263064105"], 1, "US corporate equities"),
    ("ROW", "FIN", "A", "FD", ["FA263092101", "FA263092305"], 1, "FDI into US (equity + intercompany debt)"),
    ("ROW", "FIN", "L", "FD", ["FA263192101", "FA263192305"], 1, "US direct investment abroad"),
    ("ROW", "FIN", "L", "F51", ["FA263164105"], 1, "foreign equities held by US"),
    ("ROW", "FIN", "L", "F3", ["FA264122005"], 1, "foreign bonds held by US"),
]
GDP_SERIES = "FU086902005"   # nominal GDP, quarterly unadjusted, millions USD (IMA S.1)


class Z1:
    def __init__(self, raw: bytes):
        self.z = zipfile.ZipFile(io.BytesIO(raw))
        self.index: dict[str, str] = {}
        for n in self.z.namelist():
            if n.startswith("csv/") and n.endswith(".csv"):
                head = self.z.open(n).readline().decode("utf-8", "replace").strip()
                for col in head.split(",")[1:]:
                    self.index.setdefault(col, n)
        self._cache: dict[str, pd.DataFrame] = {}

    def series(self, code: str) -> pd.Series | None:
        col = code if code.endswith(".Q") else code + ".Q"
        fn = self.index.get(col)
        if not fn:
            return None
        if fn not in self._cache:
            df = pd.read_csv(self.z.open(fn), na_values=["ND"], index_col=0)
            self._cache[fn] = df
        s = pd.to_numeric(self._cache[fn][col], errors="coerce").dropna()
        s.index = [i.replace(":", "-") for i in s.index]  # 2026:Q1 -> 2026-Q1
        return s


def fetch_all(store):
    try:
        raw = http_get(URL, ttl_hours=24 * 7)
        z1 = Z1(raw)
        n = 0
        missing = []
        for sector, concept, entry, instr, codes, sign, note in MAP:
            for prefix, measure, div in (("FA", "flow", 4.0), ("FL", "stock", 1.0)):
                if measure == "stock" and concept != "FIN":
                    continue
                parts = []
                for c in codes:
                    s = z1.series(prefix + c[2:])
                    if s is None:
                        missing.append(prefix + c[2:])
                    else:
                        parts.append(s)
                if not parts:
                    continue
                tot = pd.concat(parts, axis=1).sum(axis=1, min_count=1).dropna()
                tot = tot[tot.index >= "1995-Q1"]
                for period, val in tot.items():
                    store.add(Obs("FED_Z1", "USA", sector, concept, entry=entry, instrument=instr, measure=measure,
                                  unit="XDC", unit_mult=6, freq="Q", period=period, value=float(val) * sign / div,
                                  status="actual", note=(note + ("; SAAR/4" if measure == "flow" else "")).strip("; ")))
                    n += 1
        gdp = z1.series(GDP_SERIES)
        if gdp is not None:
            for period, val in gdp[gdp.index >= "1995-Q1"].items():
                store.add(Obs("FED_Z1", "USA", "TOTAL", "GDP", entry="B", measure="flow", unit="XDC", unit_mult=6, freq="Q",
                              period=period, value=float(val), note="nominal GDP, NSA quarterly (IMA)"))
                n += 1
        if missing:
            log.warning("Z.1 missing series: %s", sorted(set(missing))[:20])
        store.mark_source("FED_Z1", name="Federal Reserve Z.1 Financial Accounts (Integrated Macro Accounts)", url=URL,
                          rows=n, fetched_at=fetched_at(URL), missing=sorted(set(missing)), license="Public domain (US government)")
        log.info("Fed Z.1: %d obs", n)
    except Exception as e:
        log.exception("Fed Z.1 failed")
        store.mark_source("FED_Z1", error=str(e)[:300])
