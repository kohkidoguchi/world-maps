"""Build the World Flow-of-Funds dashboard.

    python build.py            fetch every source, write data/observations.csv and web/data.json
    python build.py --json     rebuild web/data.json from the existing observations.csv (no network)
    python build.py --fetch    fetch only

The two-layer design: the "macro clock" layer holds official quarterly/annual sector accounts
(OECD / ECB / Fed / IMF / BIS); the "market clock" layer holds daily-weekly public indicators used to
nowcast the current quarter (US Treasury, MOF Japan, ECB weekly balance sheet, FX, yields).
Every observation carries source, period, status (actual/preliminary/forecast/nowcast) and, where the
source publishes it, the release date.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline.common import DATA_DIR, ROOT, Store, http_get, now_iso, period_end  # noqa: E402
from pipeline.sources import bis, boj, ecb, fed_z1, imf, mof, oecd, prices, us_treasury, wealth  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout),
                              logging.FileHandler(os.path.join(DATA_DIR, "build.log"), encoding="utf-8")])
log = logging.getLogger("build")

OBS_CSV = os.path.join(DATA_DIR, "observations.csv")
SOURCES_JSON = os.path.join(DATA_DIR, "sources.json")
WEB_DIR = os.path.join(ROOT, "web")
DATA_JSON = os.path.join(WEB_DIR, "data.json")

# ISO3: (name_ja, name_en, iso2, ISO-numeric (topojson id), lat, lon)
COUNTRIES = {
    "ARG": ("アルゼンチン", "Argentina", "AR", 32, -34.6, -58.4), "AUS": ("オーストラリア", "Australia", "AU", 36, -33.9, 151.2),
    "BRA": ("ブラジル", "Brazil", "BR", 76, -15.8, -47.9), "CAN": ("カナダ", "Canada", "CA", 124, 45.4, -75.7),
    "CHN": ("中国", "China", "CN", 156, 39.9, 116.4), "FRA": ("フランス", "France", "FR", 250, 48.9, 2.3),
    "DEU": ("ドイツ", "Germany", "DE", 276, 52.5, 13.4), "IND": ("インド", "India", "IN", 356, 28.6, 77.2),
    "IDN": ("インドネシア", "Indonesia", "ID", 360, -6.2, 106.8), "ITA": ("イタリア", "Italy", "IT", 380, 41.9, 12.5),
    "JPN": ("日本", "Japan", "JP", 392, 35.7, 139.7), "KOR": ("韓国", "Korea", "KR", 410, 37.6, 127.0),
    "MEX": ("メキシコ", "Mexico", "MX", 484, 19.4, -99.1), "RUS": ("ロシア", "Russia", "RU", 643, 55.8, 37.6),
    "SAU": ("サウジアラビア", "Saudi Arabia", "SA", 682, 24.7, 46.7), "ZAF": ("南アフリカ", "South Africa", "ZA", 710, -25.7, 28.2),
    "TUR": ("トルコ", "Türkiye", "TR", 792, 39.9, 32.9), "GBR": ("英国", "United Kingdom", "GB", 826, 51.5, -0.1),
    "USA": ("米国", "United States", "US", 840, 38.9, -77.0), "ESP": ("スペイン", "Spain", "ES", 724, 40.4, -3.7),
    "NLD": ("オランダ", "Netherlands", "NL", 528, 52.4, 4.9), "CHE": ("スイス", "Switzerland", "CH", 756, 46.9, 7.4),
    "SGP": ("シンガポール", "Singapore", "SG", 702, 1.3, 103.8), "HKG": ("香港", "Hong Kong", "HK", 344, 22.3, 114.2),
    "IRL": ("アイルランド", "Ireland", "IE", 372, 53.3, -6.3),
    "EA": ("ユーロ圏", "Euro area", "XM", 0, 50.1, 8.7),
}
G20 = ["ARG", "AUS", "BRA", "CAN", "CHN", "FRA", "DEU", "IND", "IDN", "ITA", "JPN", "KOR", "MEX", "RUS", "SAU", "ZAF",
       "TUR", "GBR", "USA", "EA"]
EA_MEMBERS_NUM = [40, 56, 100, 191, 196, 233, 246, 250, 276, 300, 372, 380, 428, 440, 442, 470, 528, 620, 703, 705, 724]
ECON = {
    "JPN": {"name": "日本", "cur": "JPY", "cur_sym": "円"},
    "USA": {"name": "米国", "cur": "USD", "cur_sym": "$"},
    "EA": {"name": "ユーロ圏", "cur": "EUR", "cur_sym": "€"},
    "GBR": {"name": "英国", "cur": "GBP", "cur_sym": "£"},
    "DEU": {"name": "ドイツ", "cur": "EUR", "cur_sym": "€"},
    "FRA": {"name": "フランス", "cur": "EUR", "cur_sym": "€"},
    "ITA": {"name": "イタリア", "cur": "EUR", "cur_sym": "€"},
    "ESP": {"name": "スペイン", "cur": "EUR", "cur_sym": "€"},
    "NLD": {"name": "オランダ", "cur": "EUR", "cur_sym": "€"},
    "CAN": {"name": "カナダ", "cur": "CAD", "cur_sym": "C$"},
    "AUS": {"name": "オーストラリア", "cur": "AUD", "cur_sym": "A$"},
    "MEX": {"name": "メキシコ", "cur": "MXN", "cur_sym": "MX$"},
    "KOR": {"name": "韓国", "cur": "KRW", "cur_sym": "₩"},
}
REGIONS = {  # regions for the cross-border matrix (members must be in COUNTRIES)
    "JPN": {"name": "日本", "members": ["JPN"]},
    "USA": {"name": "米国", "members": ["USA"]},
    "EA": {"name": "ユーロ圏（主要6か国）", "members": ["DEU", "FRA", "ITA", "ESP", "NLD", "IRL"]},
    "GBR": {"name": "英国", "members": ["GBR"]},
    "CHN": {"name": "中国", "members": ["CHN"]},
    "FC": {"name": "金融センター（香港・シンガポール・スイス）", "members": ["HKG", "SGP", "CHE"]},
    "ADV": {"name": "その他先進国（加・豪・韓）", "members": ["CAN", "AUS", "KOR"]},
    "EM": {"name": "新興国（印・尼・伯・墨・亜・土・南ア・沙・露）", "members": ["IND", "IDN", "BRA", "MEX", "ARG", "TUR", "ZAF", "SAU", "RUS"]},
}
SRC_LABEL = {
    "OECD_QSA": "OECD 四半期部門別勘定（各国統計局のSNA）", "FED_Z1": "FRB Z.1 統合マクロ勘定", "ECB_QSA": "ECB 四半期部門別勘定",
    "OECD_FA_FLOW": "OECD 金融勘定（各国中銀・統計局の資金循環統計）", "ECB_QSA_FIN": "ECB 四半期部門別勘定（金融）",
}
VINTAGE_DIR = os.path.join(DATA_DIR, "vintages")
KEY_COLS = ["src", "area", "sector", "concept", "entry", "instrument", "cp_sector", "cp_area", "measure", "unit", "freq", "period"]
STATUS_FLAG = {"actual": "a", "preliminary": "p", "forecast": "f", "nowcast": "n"}

VENDOR = {
    "lib/d3.min.js": "https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js",
    "lib/d3-sankey.min.js": "https://cdnjs.cloudflare.com/ajax/libs/d3-sankey/0.12.3/d3-sankey.min.js",
    "lib/topojson.min.js": "https://cdnjs.cloudflare.com/ajax/libs/topojson/3.0.2/topojson.min.js",
    "world-110m.json": "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json",
}


# ------------------------------------------------------------------------------------------- fetch
MODULES = {"imf": imf, "oecd": oecd, "ecb": ecb, "fed": fed_z1, "bis": bis, "ust": us_treasury, "mof": mof, "boj": boj, "wealth": wealth, "prices": prices}


def fetch_all(only: list[str] | None = None) -> Store:
    """Fetch every module, or only the named ones (their rows replace the same sources in the existing CSV)."""
    store = Store()
    for name, mod in MODULES.items():
        if only and name not in only:
            continue
        log.info("=== %s", mod.__name__)
        try:
            mod.fetch_all(store)
        except Exception as e:  # each module already guards; belt and braces
            log.exception("module failed: %s", mod.__name__)
            store.mark_source(mod.__name__.split(".")[-1].upper(), error=str(e)[:300])
    summ = store.summary()
    for sid, info in store.sources.items():
        info.update(summ.get(sid, {}))
    prev_sources = {}
    if only and os.path.exists(OBS_CSV):
        new_src = {o.src for o in store.rows}
        old = pd.read_csv(OBS_CSV, dtype=str, keep_default_na=False)
        old = old[~old.src.isin(new_src)]
        tmp = OBS_CSV + ".new"
        store.to_csv(tmp)
        new = pd.read_csv(tmp, dtype=str, keep_default_na=False)
        pd.concat([old, new], ignore_index=True).to_csv(OBS_CSV, index=False)
        os.remove(tmp)
        if os.path.exists(SOURCES_JSON):
            prev_sources = json.load(open(SOURCES_JSON, encoding="utf-8")).get("sources", {})
    else:
        store.to_csv(OBS_CSV)
    sources = {**prev_sources, **store.sources}
    revisions = snapshot_and_diff()
    json.dump({"generated_at": now_iso(), "sources": sources, "revisions": revisions},
              open(SOURCES_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    log.info("wrote %s (%d rows)", OBS_CSV, len(store.rows))
    return store


def snapshot_and_diff() -> dict:
    """Keep a dated snapshot of every observation (vintage) and record how values changed since the previous one.
    Official statistics are revised; without this, a revised 2025-Q4 figure would silently overwrite the old one."""
    os.makedirs(VINTAGE_DIR, exist_ok=True)
    today = dt.date.today().isoformat()
    cur = pd.read_csv(OBS_CSV, dtype=str, keep_default_na=False)
    cur["value"] = pd.to_numeric(cur["value"], errors="coerce")
    snap = os.path.join(VINTAGE_DIR, f"obs_{today}.csv.gz")
    cur[KEY_COLS + ["value", "status", "release"]].to_csv(snap, index=False, compression="gzip")
    prev_files = sorted(f for f in os.listdir(VINTAGE_DIR) if f.startswith("obs_") and f < f"obs_{today}.csv.gz")
    result = {"this_vintage": today, "prev_vintage": None, "count": 0, "new_periods": 0, "top": []}
    if not prev_files:
        return result
    prev_path = os.path.join(VINTAGE_DIR, prev_files[-1])
    result["prev_vintage"] = prev_files[-1][4:14]
    prev = pd.read_csv(prev_path, dtype=str, keep_default_na=False)
    prev["value"] = pd.to_numeric(prev["value"], errors="coerce")
    # compare only slow-moving official statistics (daily/weekly market data is not "revised" in the same sense)
    slow = cur[cur.freq.isin(["Q", "A", "M"])]
    m = slow.merge(prev[KEY_COLS + ["value"]], on=KEY_COLS, how="left", suffixes=("", "_prev"))
    result["new_periods"] = int(m.value_prev.isna().sum())
    rev = m[m.value_prev.notna() & ((m.value - m.value_prev).abs() > 1e-9 * (m.value_prev.abs() + 1))].copy()
    rev["pct"] = (rev.value - rev.value_prev) / rev.value_prev.abs().replace(0, float("nan")) * 100
    rev["prev_vintage"] = result["prev_vintage"]
    rev["this_vintage"] = today
    rev_path = os.path.join(DATA_DIR, "revisions.csv")
    rev.to_csv(rev_path, mode="a", header=not os.path.exists(rev_path), index=False)
    result["count"] = int(len(rev))
    top = rev[rev.concept.isin(["B9", "GDP", "DEBT", "CA", "FIN", "B8G"])].assign(a=lambda d: d.pct.abs()).sort_values("a", ascending=False).head(15)
    result["top"] = [{"src": r.src, "area": r.area, "sector": r.sector, "concept": r.concept, "instrument": r.instrument, "entry": r.entry,
                      "period": r.period, "prev": float(r.value_prev), "new": float(r.value), "pct": round(float(r.pct), 2) if pd.notna(r.pct) else None}
                     for r in top.itertuples()]
    log.info("revisions vs %s: %d changed, %d new observations", result["prev_vintage"], result["count"], result["new_periods"])
    return result


def vendor():
    for rel, url in VENDOR.items():
        path = os.path.join(WEB_DIR, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            open(path, "wb").write(http_get(url, ttl_hours=24 * 365))
            log.info("vendored %s", rel)


# ------------------------------------------------------------------------------------------- JSON
def _ser(df: pd.DataFrame, scale: float = 1.0, nd: int = 3, keep_status=True):
    """DataFrame rows -> [[period, value, status]] sorted by period, one row per period (latest source row wins)."""
    if df.empty:
        return []
    d = df.sort_values(["period"]).drop_duplicates("period", keep="last")
    out = []
    for r in d.itertuples(index=False):
        v = r.value * (10.0 ** r.unit_mult) * scale
        item = [r.period, round(v, nd)]
        if keep_status:
            item.append(STATUS_FLAG.get(r.status, "a"))
        out.append(item)
    return out


def _latest(df: pd.DataFrame):
    if df.empty:
        return None
    r = df.sort_values("period").iloc[-1]
    return {"period": r.period, "value": round(r.value * 10.0 ** r.unit_mult, 3), "status": r.status}


def build_json():
    df = pd.read_csv(OBS_CSV, dtype={"period": str, "release": str, "note": str, "cp_sector": str, "instrument": str,
                                     "entry": str}, keep_default_na=False)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])
    srcdoc = json.load(open(SOURCES_JSON, encoding="utf-8")) if os.path.exists(SOURCES_JSON) else {}
    sources = srcdoc.get("sources", {})
    BN = 1e-9  # to billions of currency units

    def q(**kw):
        m = pd.Series(True, index=df.index)
        for k, v in kw.items():
            m &= df[k].isin(v) if isinstance(v, (list, tuple, set)) else (df[k] == v)
        return df[m]

    out = {"generated_at": now_iso(), "meta": {"sources": sources, "revisions": srcdoc.get("revisions", {})}, "world": {}, "economies": {}, "nowcast": {}}
    # bilateral positions (holder/investor -> issuer/host), USD bn, by year; total economy on both sides for the map
    bil = q(src=["IMF_PIP", "IMF_DIP"])
    bil = bil[(bil.src == "IMF_DIP") | ((bil.sector == "TOTAL") & (bil.cp_sector == "TOTAL"))]
    bilateral = {}
    for (instr, entry), g in bil.groupby(["instrument", "entry"]):
        key = instr if instr.startswith("PI") else f"{instr}_{entry}"
        d = bilateral.setdefault(key, {})
        for r in g.itertuples():
            if r.period < "2015" or r.period.endswith("-06"):
                continue
            d.setdefault(r.period[:4], {}).setdefault(r.area, {})[r.cp_area] = round(r.value * 10.0 ** r.unit_mult * BN, 1)
    out["bilateral"] = bilateral
    # region x sector matrix of cross-border portfolio holdings (holder region x holder sector -> issuer region x issuer sector)
    pip = q(src="IMF_PIP")
    pip = pip[pip.period >= "2019"]   # periods are end-months: 2024-12 (year-end), 2025-06 (end-June)
    iso2region = {iso: rid for rid, r in REGIONS.items() for iso in r["members"]}
    matrix = {"regions": REGIONS, "PI": {}, "PI_F3": {}, "PI_F51": {}}
    # holder sector: prefer S12R over S12P for "other financial"; drop the total-economy row (it is the sum of the parts)
    has_ofc = set(pip[pip.sector == "OFC"].area)
    pip = pip[(pip.sector != "TOTAL") & ~((pip.sector == "OFC2") & pip.area.isin(has_ofc))]
    pip = pip.assign(sector=pip.sector.replace({"OFC2": "OFC", "CB": "GOVCB", "GOV": "GOVCB"}))
    # issuer sector: keep the breakdown where the reporter gives it, otherwise the total
    has_cp = set(zip(pip[pip.cp_sector != "TOTAL"].area, pip[pip.cp_sector != "TOTAL"].cp_area, pip[pip.cp_sector != "TOTAL"].period, pip[pip.cp_sector != "TOTAL"].instrument))
    pip = pip[[(r.cp_sector != "TOTAL") or ((r.area, r.cp_area, r.period, r.instrument) not in has_cp) for r in pip.itertuples()]]
    pip = pip.assign(hr=pip.area.map(iso2region), ir=pip.cp_area.map(iso2region)).dropna(subset=["hr", "ir"])
    for (instr, period, hr, hs, ir, cs), g in pip.groupby(["instrument", "period", "hr", "sector", "ir", "cp_sector"]):
        matrix[instr].setdefault(period, []).append([hr, hs, ir, cs, round(float((g.value * 10.0 ** g.unit_mult).sum()) * BN, 1)])
    out["matrix"] = matrix

    # ---------------- world layer (G20 + extras)
    weo = q(src="IMF_WEO")
    bop = q(src="IMF_BOP")
    iip = q(src="IMF_IIP")
    tc = q(src="BIS_TC")
    qna = q(src="OECD_QNA", concept="GDP")
    for iso3, (ja, en, iso2, num, lat, lon) in COUNTRIES.items():
        c = {"name_ja": ja, "name_en": en, "iso2": iso2, "num": num, "lat": lat, "lon": lon, "g20": iso3 in G20}
        gq = qna[qna.area == iso3]
        c["gdp_q_latest"] = gq.period.max() if not gq.empty else ""
        w = weo[weo.area == iso3]
        c["weo"] = {ind: {r.period: [round(r.value, 3), STATUS_FLAG.get(r.status, "a")] for r in g.itertuples()}
                    for ind, g in w.groupby("note")}
        c["weo_release"] = w.release.max() if not w.empty else ""
        t = tc[tc.area == iso3]
        c["credit"] = {}
        for sector, g in t.groupby("sector"):
            ratio = _ser(g[g.unit == "PT_GDP"], keep_status=False)
            usd = _ser(g[g.unit == "USD"], keep_status=False)
            c["credit"][sector] = {"ratio": ratio[-40:], "usd_bn": [[p, round(v * BN, 1)] for p, v in usd[-40:]]}
        b = bop[(bop.area == iso3) & (bop.period >= "2010-Q1")]
        c["bop"] = {}
        for (concept, instr, entry), g in b.groupby(["concept", "instrument", "entry"]):
            key = "_".join(x for x in (concept, instr, entry) if x)
            c["bop"][key] = [[p, round(v * BN, 2)] for p, v, _ in _ser(g)]
        i = iip[(iip.area == iso3) & (iip.concept == "NIIP")]
        c["niip_usd_bn"] = [[p, round(v * BN, 1)] for p, v, _ in _ser(i)][-40:]
        out["world"][iso3] = c
    out["world_meta"] = {"ea_members_num": EA_MEMBERS_NUM, "g20": G20}

    # ---------------- economies (sector detail)
    NF_SRC = {"USA": "FED_Z1", "EA": "ECB_QSA"}
    FIN_SRC = {"EA": ("ECB_QSA_FIN", "ECB_QSA_FIN")}
    GDP_SRC = {"USA": "FED_Z1", "EA": "ECB_MNA"}
    for iso3, info in ECON.items():
        e = dict(info)
        e["clock"] = {}
        nf_src = NF_SRC.get(iso3, "OECD_QSA")
        fsrc, ssrc = FIN_SRC.get(iso3, ("OECD_FA_FLOW", "OECD_FA_STOCK"))
        e["labels"] = {"nf": SRC_LABEL.get(nf_src, nf_src), "fin": SRC_LABEL.get(fsrc, fsrc)}
        # non-financial by sector
        nf = q(src=nf_src, area=iso3, measure="flow")
        nf = nf[nf.concept != "FIN"]
        e["nf"] = {}
        for (sector, concept, entry), g in nf.groupby(["sector", "concept", "entry"]):
            e["nf"].setdefault(sector, {}).setdefault(concept, {})[entry] = _ser(g[g.period >= "1999-Q1"], BN, 1)
        e["clock"]["nonfin"] = nf.period.max() if not nf.empty else ""
        if nf.empty and q(src=fsrc, area=iso3).empty:
            log.warning("no sector data for %s; skipped", iso3)
            continue
        # financial flows & stocks by sector x entry x instrument
        e["fin"] = {"flow": {}, "stock": {}}
        FIN_KEEP = {"flow": (["F", "F2", "F3", "F4", "F5", "F51", "F52", "F6", "F7", "F8"], "2003-Q1"),
                    "stock": (["F", "F2", "F3", "F4", "F5", "F51", "F52", "F6", "F7", "F8"], "2008-Q1")}
        for measure, src in (("flow", fsrc), ("stock", ssrc)):
            f = q(src=src, area=iso3, concept="FIN", measure=measure, unit="XDC")
            keep, start = FIN_KEEP[measure]
            f = f[(f.cp_sector == "") & f.instrument.isin(keep) & f.entry.isin(["A", "L"]) & (f.sector != "TOTAL")]
            for (sector, entry, instr), g in f.groupby(["sector", "entry", "instrument"]):
                e["fin"][measure].setdefault(sector, {}).setdefault(entry, {})[instr] = _ser(g[g.period >= start], BN, 1)
            e["clock"]["fin_" + measure] = f.period.max() if not f.empty else ""
        # US extras from Z.1 not in the OECD instrument set (FDI split, federal-only)
        if iso3 == "USA":
            z = q(src="FED_Z1", area="USA", concept="FIN", unit="XDC")
            for (measure, sector, entry, instr), g in z.groupby(["measure", "sector", "entry", "instrument"]):
                if instr in ("FD",) or sector in ("GOV_C",):
                    e["fin"][measure].setdefault(sector, {}).setdefault(entry, {})[instr] = _ser(g[g.period >= "1999-Q1"], BN, 2)
        # whom-to-whom (euro area: ECB flows & stocks; Japan: BoJ debt-securities holdings, stocks)
        w2 = q(src=["ECB_W2W", "BOJ_W2W"], area=iso3)
        if not w2.empty:
            e["w2w"] = {"flow": {}, "stock": {}}
            for (measure, instr, holder, issuer), g in w2[w2.entry == "A"].groupby(["measure", "instrument", "sector", "cp_sector"]):
                e["w2w"][measure].setdefault(instr, {}).setdefault(holder, {})[issuer] = _ser(g[g.period >= "2012-Q1"], BN, 2, False)
            e["clock"]["w2w"] = w2.period.max()
        # GDP (quarterly nominal, NSA) with annual WEO fallback
        gdp = q(src=GDP_SRC.get(iso3, "OECD_QNA"), area=iso3, concept="GDP")
        e["gdp"] = _ser(gdp[gdp.period >= "1998-Q1"], BN, 2, False)
        wgdp = weo[(weo.area == iso3) & (weo.note == "NGDP")]
        e["gdp_annual"] = _ser(wgdp, BN, 1)
        e["clock"]["gdp"] = gdp.period.max() if not gdp.empty else ""
        # household GFCF is not published in some OECD QSA series (Japan); derive it as a residual (estimate)
        if "P51G" not in e["nf"].get("HH", {}):
            tot = {p: v for p, v, _ in e["nf"].get("TOTAL", {}).get("P51G", {}).get("D", [])}
            parts = [e["nf"].get(s, {}).get("P51G", {}).get("D", []) for s in ("NFC", "GOV", "FIN")]
            if tot and all(parts):
                maps = [{p: v for p, v, _ in s} for s in parts]
                res = [[p, round(tot[p] - sum(m[p] for m in maps), 2), "n"] for p in sorted(tot) if all(p in m for m in maps)]
                if res:
                    e["nf"].setdefault("HH", {})["P51G"] = {"D": res}
        # government debt: quarterly PSD (JPN, USA) / Maastricht debt (EA) + annual WEO ratio
        psd = q(src=["OECD_PSD", "ECB_GFS"], area=iso3)
        e["debt"] = {}
        for (sector, instr, unit), g in psd.groupby(["sector", "instrument", "unit"]):
            if instr not in ("FD4", "GD", "F3") or unit == "USD":
                continue
            e["debt"].setdefault(sector, {}).setdefault(instr, {})[unit] = _ser(g[g.period >= "2005-Q1"], BN if unit != "PT_GDP" else 1.0, 1)
        e["clock"]["debt"] = psd.period.max() if not psd.empty else ""
        # balance of payments for the RoW sector: IMF (USD bn) for all; ECB BP6 (EUR bn) for the euro area
        b = bop[(bop.area == iso3) & (bop.period >= "2005-Q1")]
        e["bop"] = {}
        for (concept, instr, entry), g in b.groupby(["concept", "instrument", "entry"]):
            key = "_".join(x for x in (concept, instr, entry) if x)
            e["bop"][key] = _ser(g, BN, 2)
        e["clock"]["bop"] = b.period.max() if not b.empty else ""
        bps = q(src="ECB_BPS", area=iso3)
        if not bps.empty:
            e["bop_xdc"] = {}
            for (concept, instr, entry), g in bps.groupby(["concept", "instrument", "entry"]):
                # prefer the total-instrument row when several instrument breakdowns exist
                tot = g[g.note.str.endswith("instrument F") | g.note.str.endswith("instrument _Z")]
                key = "_".join(x for x in (concept, instr, entry) if x)
                e["bop_xdc"][key] = _ser(tot if not tot.empty else g, BN, 2)
            e["clock"]["bop"] = max(e["clock"]["bop"], bps.period.max())
        out["economies"][iso3] = e

    # ---------------- nowcast / market layer
    nc = {}
    u = q(src="UST_DEBT", concept="DEBT_TOTAL")
    nc["USA"] = {
        "debt_total_bn": [[p, round(v * BN, 2)] for p, v, _ in _ser(u[u.period >= "2018-01-01"])],
        "debt_public_bn": [[p, round(v * BN, 2)] for p, v, _ in _ser(q(src="UST_DEBT", concept="DEBT_PUBLIC").query("period >= '2018-01-01'"))],
        "tga_bn": [[p, round(v * BN, 2)] for p, v, _ in _ser(q(src="UST_DTS").query("period >= '2018-01-01'"))],
        "mts": {c: [[p, round(v * BN, 2)] for p, v, _ in _ser(q(src="UST_MTS", concept=c))] for c in ("OTR", "OTE", "B9")},
    }
    mw = q(src="MOF_WEEKLY")
    mm = q(src="MOF_MONTHLY")
    nc["JPN"] = {
        "mof_weekly": {f"{e}_{i}": [[p, round(v * BN, 1)] for p, v, _ in _ser(g[g.period >= "2018-01-01"])]
                       for (e, i), g in mw.groupby(["entry", "instrument"])},
        "mof_monthly": {f"{e}_{i}": [[p, round(v * BN, 1)] for p, v, _ in _ser(g[g.period >= "2010-01"])]
                        for (e, i), g in mm.groupby(["entry", "instrument"])},
        "usdjpy": [[p, v] for p, v, _ in _ser(q(src="BIS_FX").query("period >= '2018-01-01'"))],
    }
    mk = q(src="ECB_MKT")
    nc["EA"] = {
        "cb_assets_bn": [[p, round(v * BN, 1)] for p, v, _ in _ser(mk[(mk.concept == "CB_ASSETS") & (mk.period >= "2015-01-01")])],
        "usdeur": [[p, v] for p, v, _ in _ser(mk[(mk.concept == "FX_USDEUR") & (mk.period >= "2018-01-01")])],
        "jpyeur": [[p, v] for p, v, _ in _ser(mk[(mk.concept == "FX_JPYEUR") & (mk.period >= "2018-01-01")])],
        "yield10y": [[p, v] for p, v, _ in _ser(mk[(mk.concept == "YIELD_10Y") & (mk.period >= "2018-01-01")])],
    }
    out["nowcast"] = nc
    out["market_clock"] = {
        "US連邦債務（日次）": u.period.max() if not u.empty else "",
        "米TGA残高（日次）": q(src="UST_DTS").period.max() if not q(src="UST_DTS").empty else "",
        "米財政収支（月次）": q(src="UST_MTS").period.max() if not q(src="UST_MTS").empty else "",
        "日本 対外・対内証券投資（週次）": mw.period.max() if not mw.empty else "",
        "ユーロシステムB/S（週次）": mk[mk.concept == "CB_ASSETS"].period.max() if not mk.empty else "",
        "為替（日次）": mk[mk.concept == "FX_USDEUR"].period.max() if not mk.empty else "",
    }
    os.makedirs(WEB_DIR, exist_ok=True)
    json.dump(out, open(DATA_JSON, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"), default=str)
    log.info("wrote %s (%.1f MB)", DATA_JSON, os.path.getsize(DATA_JSON) / 1e6)
    write_matrix_data(out)
    return out


MATRIX_JSON = os.path.join(WEB_DIR, "matrix_data.json")


def write_matrix_data(out: dict):
    """Slim dataset for the matrix-only page: who holds whose assets (BS) and where money flowed (CF)."""
    slim = {"generated_at": out["generated_at"], "regions": out["matrix"]["regions"], "matrix": {k: v for k, v in out["matrix"].items() if k != "regions"},
            "economies": {}, "bop": {}, "names": {iso: c["name_ja"] for iso, c in out["world"].items()}}
    for iso, e in out["economies"].items():
        fin = {m: {s: {ent: {i: ser[-36:] for i, ser in ents.items()} for ent, ents in secs.items()} for s, secs in d.items()} for m, d in e["fin"].items()}
        w2w = None
        if e.get("w2w"):
            w2w = {m: {i: {h: {j: ser[-36:] for j, ser in js.items()} for h, js in hs.items()} for i, hs in d.items()} for m, d in e["w2w"].items()}
        slim["economies"][iso] = {"name": e["name"], "cur": e["cur"], "cur_sym": e["cur_sym"], "clock": e["clock"], "labels": e.get("labels", {}),
                                  "fin": fin, "w2w": w2w, "gdp": e["gdp"][-12:]}
    for iso, c in out["world"].items():
        if c.get("bop"):
            slim["bop"][iso] = {k: v[-36:] for k, v in c["bop"].items() if k in ("CA_B", "FA_PI_A", "FA_PI_L", "FA_FD_A", "FA_FD_L", "FA_OI_A", "FA_OI_L", "FA_RA_A", "FA_PI_F3_A", "FA_PI_F5_A", "FA_PI_F3_L", "FA_PI_F5_L")}
    # macro background for the flow explanations (IMF WEO): current account, fiscal balance, growth, inflation, nominal GDP
    slim["weo"] = {}
    for iso, c in out["world"].items():
        w = c.get("weo") or {}
        keep = {ind: {y: v[0] for y, v in w.get(ind, {}).items() if "2015" <= y <= "2026"} for ind in ("BCA_NGDPD", "GGXCNL_NGDP", "NGDP_RPCH", "PCPIPCH", "NGDPD", "GGXWDG_NGDP") if ind in w}
        if keep:
            slim["weo"][iso] = keep
    # market background: euro 10y yield and FX (USD/EUR, JPY per USD) year-end values
    nc = out.get("nowcast", {})
    def yearend(series):
        outd = {}
        for p, v in series or []:
            outd[p[:4]] = v  # sorted ascending -> last observation of each year wins
        return outd
    slim["market"] = {"ea_10y": yearend(nc.get("EA", {}).get("yield10y")), "usdeur": yearend(nc.get("EA", {}).get("usdeur")),
                      "usdjpy": yearend(nc.get("JPN", {}).get("usdjpy")), "ecb_assets_bn": yearend(nc.get("EA", {}).get("cb_assets_bn"))}
    slim["latest_quarter"] = {iso: e["clock"].get("fin_stock", "") for iso, e in out["economies"].items()}
    # wealth beyond the financial accounts: housing (annual stock + quarterly price index), US real estate revaluation, gold
    try:
        wdf = pd.read_csv(OBS_CSV, dtype=str, keep_default_na=False,
                          usecols=["src", "area", "sector", "concept", "instrument", "measure", "unit", "unit_mult", "period", "value"])
        wdf = wdf[wdf.src.isin(["OECD_NFA", "FED_Z1_RE", "BIS_SPP", "IMF_PCPS", "GOLD_HOLD"])].copy()
        wdf["v"] = pd.to_numeric(wdf.value, errors="coerce") * (10.0 ** pd.to_numeric(wdf.unit_mult, errors="coerce").fillna(0))
        wdf = wdf.dropna(subset=["v"])
        BN = 1e-9
        W = {"housing": {}, "hpi": {}, "us_re": {}, "gold": {}, "gold_price": {}}
        for (a, sec, ins), g in wdf[wdf.src == "OECD_NFA"].groupby(["area", "sector", "instrument"]):
            W["housing"].setdefault(a, {}).setdefault(sec, {})[ins] = {r.period: round(r.v * BN, 2) for r in g.itertuples()}
        for a, g in wdf[wdf.src == "BIS_SPP"].groupby("area"):
            W["hpi"][a] = [[r.period, round(r.v, 2)] for r in g.sort_values("period").itertuples()]
        for (sec, ins, m), g in wdf[wdf.src == "FED_Z1_RE"].groupby(["sector", "instrument", "measure"]):
            W["us_re"].setdefault(sec, {}).setdefault(ins, {})[m] = [[r.period, round(r.v * BN, 2)] for r in g.sort_values("period").itertuples()]
        for (a, sec, ins, m), g in wdf[wdf.src == "GOLD_HOLD"].groupby(["area", "sector", "instrument", "measure"]):
            W["gold"].setdefault(a, {}).setdefault(sec, {}).setdefault(ins, {})[m] = [[r.period, round(r.v * BN, 3)] for r in g.sort_values("period").itertuples()]
        for (u, m), g in wdf[wdf.src == "IMF_PCPS"].groupby(["unit", "measure"]):
            W["gold_price"][u] = [[r.period, round(r.v, 2)] for r in g.sort_values("period").itertuples()]
        slim["wealth"] = W
    except Exception as e:
        log.warning("wealth block failed: %s", e)
    # recent price indices for the valuation nowcast (monthly official + daily market)
    try:
        pdf = pd.read_csv(OBS_CSV, dtype=str, keep_default_na=False, usecols=["src", "area", "concept", "unit", "period", "value", "note"])
        pdf = pdf[pdf.src.isin(["OECD_MEI", "YIELD_DAILY", "FX_DAILY", "YAHOO", "IMF_PCPS"])].copy()
        pdf["v"] = pd.to_numeric(pdf.value, errors="coerce")
        pdf = pdf.dropna(subset=["v"])
        PR = {"mei": {}, "yield10": {}, "fx": {}, "eq": {}, "gold_daily": [], "bond_etf": [], "reit_etf": [], "gold_m": [], "labels": {}}
        for (a, c), g in pdf[pdf.src == "OECD_MEI"].groupby(["area", "concept"]):
            PR["mei"].setdefault(a, {})[c] = [[r.period, round(r.v, 3)] for r in g.sort_values("period").itertuples()]
        cut = "2024-01-01"
        for a, g in pdf[(pdf.src == "YIELD_DAILY") & (pdf.period >= cut)].groupby("area"):
            PR["yield10"][a] = [[r.period, round(r.v, 3)] for r in g.sort_values("period").itertuples()]
        for a, g in pdf[(pdf.src == "FX_DAILY") & (pdf.period >= cut)].groupby("area"):
            PR["fx"][a] = [[r.period, round(r.v, 6)] for r in g.sort_values("period").itertuples()]
        y = pdf[(pdf.src == "YAHOO") & (pdf.period >= cut)]
        for (a, c), g in y.groupby(["area", "concept"]):
            ser = [[r.period, round(r.v, 3)] for r in g.sort_values("period").itertuples()]
            lab = g.note.iloc[0].split(" (")[0]
            if c == "EQ_DAILY":
                PR["eq"][a] = ser; PR["labels"][a] = lab
            elif c == "GOLD_DAILY":
                PR["gold_daily"] = ser
            elif c == "BOND_ETF":
                PR["bond_etf"] = ser
            elif c == "REIT_ETF":
                PR["reit_etf"] = ser
        gm = pdf[(pdf.src == "IMF_PCPS") & (pdf.unit == "USD") & (pdf.period.str.len() == 7) & ~pdf.period.str.contains("Q")]
        PR["gold_m"] = [[r.period, round(r.v, 2)] for r in gm.sort_values("period").itertuples()]
        slim["prices"] = PR
    except Exception as e:
        log.warning("prices block failed: %s", e)
    # USD per unit of local currency at quarter end, so domestic blocks can sit next to the cross-border (USD) blocks
    try:
        df = pd.read_csv(OBS_CSV, dtype=str, keep_default_na=False, usecols=["src", "area", "sector", "concept", "entry", "instrument", "unit", "unit_mult", "period", "value"])
        st = df[(df.src == "OECD_FA_STOCK") & (df.sector == "HH") & (df.entry == "A") & (df.instrument == "F")].copy()
        st["v"] = pd.to_numeric(st.value, errors="coerce") * (10.0 ** pd.to_numeric(st.unit_mult, errors="coerce"))
        piv = st.pivot_table(index=["area", "period"], columns="unit", values="v", aggfunc="first").dropna()
        for iso in slim["economies"]:
            fx = {}
            if iso == "USA":
                fx = {p: 1.0 for p in [x[0] for x in slim["economies"][iso]["fin"]["stock"].get("HH", {}).get("A", {}).get("F", [])]}
            elif iso == "EA":
                mk = df[(df.src == "ECB_MKT") & (df.concept == "FX_USDEUR")].sort_values("period")
                mk["v"] = pd.to_numeric(mk.value, errors="coerce")
                for p in [x[0] for x in slim["economies"][iso]["fin"]["stock"].get("HH", {}).get("A", {}).get("F", [])]:
                    end = period_end(p)
                    sub = mk[mk.period <= end]
                    if not sub.empty:
                        fx[p] = round(float(sub.v.iloc[-1]), 4)
            elif iso in piv.index.get_level_values(0):
                sub = piv.loc[iso]
                fx = {p: round(float(r["USD"] / r["XDC"]), 6) for p, r in sub.iterrows() if r["XDC"]}
            slim["economies"][iso]["fx_usd"] = fx
    except Exception as e:  # FX is a convenience for the integrated view; never fail the build on it
        log.warning("fx for matrix data failed: %s", e)
    json.dump(slim, open(MATRIX_JSON, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"), default=str)
    log.info("wrote %s (%.1f MB)", MATRIX_JSON, os.path.getsize(MATRIX_JSON) / 1e6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="fetch only")
    ap.add_argument("--json", action="store_true", help="rebuild web/data.json only")
    ap.add_argument("--only", nargs="*", help=f"fetch only these modules: {', '.join(MODULES)}")
    ap.add_argument("--matrix-json", action="store_true", help="regenerate web/matrix_data.json from the existing web/data.json")
    a = ap.parse_args()
    if a.matrix_json:
        write_matrix_data(json.load(open(DATA_JSON, encoding="utf-8")))
        return
    if not a.json:
        fetch_all(a.only)
    if not a.fetch:
        vendor()
        build_json()


if __name__ == "__main__":
    main()
