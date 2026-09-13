"""IMF SDMX 3.0 API (api.imf.org): WEO (annual, with projections), BOP (quarterly flows), IIP (quarterly stocks).

The legacy DataMapper endpoint is blocked from this network, the SDMX endpoint is open.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from ..common import Obs, http_get, sdmx_json_series, fetched_at

log = logging.getLogger(__name__)

BASE = "https://api.imf.org/external/sdmx/3.0/data/dataflow"

# G20 members + a few large open economies + euro area aggregate
G20 = ["ARG", "AUS", "BRA", "CAN", "CHN", "FRA", "DEU", "IND", "IDN", "ITA", "JPN", "KOR", "MEX",
       "RUS", "SAU", "ZAF", "TUR", "GBR", "USA"]
EXTRA = ["ESP", "NLD", "CHE", "SGP", "HKG", "IRL"]
WEO_AREAS = G20 + EXTRA + ["G163"]          # G163 = Euro Area in WEO
AREA_ALIAS = {"G163": "EA"}

WEO_INDICATORS = {
    # code: (sector, concept, measure, unit)
    "NGDPD": ("TOTAL", "GDP", "level", "USD"),
    "NGDP": ("TOTAL", "GDP", "level", "XDC"),
    "NGDP_RPCH": ("TOTAL", "GDP_GROWTH", "ratio", "PCT"),
    "BCA": ("ROW", "CA", "flow", "USD"),
    "BCA_NGDPD": ("ROW", "CA", "ratio", "PT_GDP"),
    "NGSD_NGDP": ("TOTAL", "SAVING", "ratio", "PT_GDP"),
    "NID_NGDP": ("TOTAL", "INVEST", "ratio", "PT_GDP"),
    "GGXWDG_NGDP": ("GOV", "DEBT", "ratio", "PT_GDP"),
    "GGXWDN_NGDP": ("GOV", "NETDEBT", "ratio", "PT_GDP"),
    "GGXCNL_NGDP": ("GOV", "B9", "ratio", "PT_GDP"),
    "GGXONLB_NGDP": ("GOV", "PRIMARY_BAL", "ratio", "PT_GDP"),
    "GGR_NGDP": ("GOV", "OTR", "ratio", "PT_GDP"),
    "GGX_NGDP": ("GOV", "OTE", "ratio", "PT_GDP"),
    "PCPIPCH": ("TOTAL", "CPI", "ratio", "PCT"),
}


def _weo_url():
    return (f"{BASE}/IMF.RES/WEO/+/{'+'.join(WEO_AREAS)}.{'+'.join(WEO_INDICATORS)}.A"
            f"?c[TIME_PERIOD]=ge:2000&format=sdmx-json")


def fetch_weo(store):
    url = _weo_url()
    doc = json.loads(http_get(url, ttl_hours=24))
    n = 0
    latest_vintage = ""
    for dims, attrs, obs in sdmx_json_series(doc):
        area = AREA_ALIAS.get(dims["COUNTRY"], dims["COUNTRY"])
        sector, concept, measure, unit = WEO_INDICATORS[dims["INDICATOR"]]
        upd = str(attrs.get("COUNTRY_UPDATE_DATE", "") or "")
        vintage_year = None
        try:  # '9/23/2025'
            m, d, y = upd.split("/")
            upd_iso = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
            vintage_year = int(y)
            latest_vintage = max(latest_vintage, upd_iso)
        except Exception:
            upd_iso = ""
        for period, val, oa in obs:
            year = int(period[:4])
            # WEO marks nothing per observation; treat the vintage year and later as projections
            status = "forecast" if (vintage_year and year >= vintage_year) else "actual"
            store.add(Obs("IMF_WEO", area, sector, concept, entry="B" if concept in ("B9", "CA", "PRIMARY_BAL") else "",
                          measure=measure, unit=unit, unit_mult=0, freq="A", period=period, value=val,
                          status=status, release=upd_iso, note=dims["INDICATOR"]))
            n += 1
    store.mark_source("IMF_WEO", name="IMF World Economic Outlook", url=url, rows=n, fetched_at=fetched_at(url),
                      vintage=latest_vintage, license="IMF terms of use (free re-use with attribution)")
    log.info("WEO: %d obs", n)


# ---------------------------------------------------------------------------- BOP
BOP_AREAS = G20 + EXTRA + ["G163"]   # G163 = euro area aggregate in the IMF BOP/IIP country list
BOP_ENTRY = {"A_NFA_T": "A", "L_NIL_T": "L", "NNAFANIL_T": "N", "NETCD_T": "B", "A_T": "A"}
BOP_IND = {  # indicator -> (concept, instrument)
    "CAB": ("CA", ""), "KAB": ("KA", ""), "FAB": ("FA", ""), "EO": ("EO", ""),
    "D_F": ("FA", "FD"), "P_F": ("FA", "PI"), "P_F3": ("FA", "PI_F3"), "P_F5": ("FA", "PI_F5"),
    "O_F": ("FA", "OI"), "R_F": ("FA", "RA"), "F_F7": ("FA", "F7"),
}


def fetch_bop(store):
    areas = "+".join(BOP_AREAS)
    entries = "+".join(BOP_ENTRY)
    inds = "+".join(BOP_IND)
    url = f"{BASE}/IMF.STA/BOP/+/{areas}.{entries}.{inds}.USD.Q?c[TIME_PERIOD]=ge:2005&format=sdmx-json"
    doc = json.loads(http_get(url, ttl_hours=24))
    n = 0
    seen_areas = set()
    for dims, attrs, obs in sdmx_json_series(doc):
        area = AREA_ALIAS.get(dims["COUNTRY"], dims["COUNTRY"])
        seen_areas.add(area)
        concept, instr = BOP_IND[dims["INDICATOR"]]
        entry = BOP_ENTRY[dims["BOP_ACCOUNTING_ENTRY"]]
        for period, val, oa in obs:
            st = oa.get("OBS_STATUS", "")
            status = "preliminary" if st in ("P", "E") else "actual"
            store.add(Obs("IMF_BOP", area, "ROW", concept, entry=entry, instrument=instr, measure="flow",
                          unit="USD", unit_mult=0, freq="Q", period=period, value=val, status=status,
                          note=dims["INDICATOR"]))
            n += 1
    store.mark_source("IMF_BOP", name="IMF Balance of Payments (BPM6)", url=url, rows=n, fetched_at=fetched_at(url),
                      areas=sorted(seen_areas), license="IMF terms of use")
    log.info("BOP: %d obs, areas=%s", n, sorted(seen_areas))


def fetch_iip(store):
    areas = "+".join(BOP_AREAS)
    url = (f"{BASE}/IMF.STA/IIP/+/{areas}.NETAL_P+A_P+L_P.NIIP+IIP+D+P_MV+O+R.USD.Q"
           f"?c[TIME_PERIOD]=ge:2005&format=sdmx-json")
    doc = json.loads(http_get(url, ttl_hours=24))
    n = 0
    imap = {"NIIP": ("NIIP", ""), "IIP": ("IIP", ""), "D": ("IIP", "FD"), "P_MV": ("IIP", "PI"), "O": ("IIP", "OI"),
            "R": ("IIP", "RA")}
    emap = {"NETAL_P": "N", "A_P": "A", "L_P": "L"}
    for dims, attrs, obs in sdmx_json_series(doc):
        area = AREA_ALIAS.get(dims["COUNTRY"], dims["COUNTRY"])
        concept, instr = imap[dims["INDICATOR"]]
        for period, val, oa in obs:
            store.add(Obs("IMF_IIP", area, "ROW", concept, entry=emap[dims["BOP_ACCOUNTING_ENTRY"]], instrument=instr,
                          measure="stock", unit="USD", unit_mult=0, freq="Q", period=period, value=val,
                          note=dims["INDICATOR"]))
            n += 1
    store.mark_source("IMF_IIP", name="IMF International Investment Position", url=url, rows=n,
                      fetched_at=fetched_at(url), license="IMF terms of use")
    log.info("IIP: %d obs", n)


# ---------------------------------------------------------------------------- bilateral positions (PIP = ex-CPIS, DIP = ex-CDIS)
BILATERAL_AREAS = G20 + EXTRA


PIP_HOLDER = {"S1": "TOTAL", "S121": "CB", "S122": "BANK", "S12R": "OFC", "S12P": "OFC2", "S13": "GOV", "S1V": "NFP"}
PIP_ISSUER = {"S1": "TOTAL", "S12": "FIN", "S13": "GOV", "S1V": "NFP"}


def fetch_pip(store):
    """Portfolio investment positions by counterpart economy (holder -> issuer), USD, end-year.
    Holder sector (central bank, banks, other financial, government, non-financial private) and, where the
    reporter provides it, issuer sector (financial / government / non-financial private)."""
    ctys = "+".join(BILATERAL_AREAS)
    inds = {"P_TOTINV_P_USD": "PI", "P_F3_P_USD": "PI_F3", "P_F51_P_USD": "PI_F51"}
    n = 0
    urls = []
    for i in range(0, len(BILATERAL_AREAS), 5):
        reps = "+".join(BILATERAL_AREAS[i:i + 5])
        # semiannual series: S2 = end-December (same as the annual series), S1 = end-June -> the freshest bilateral data
        url = (f"{BASE}/IMF.STA/PIP/+/{reps}.A.{'+'.join(inds)}.{'+'.join(PIP_HOLDER)}.{'+'.join(PIP_ISSUER)}.{ctys}.S"
               f"?c[TIME_PERIOD]=ge:2015&format=sdmx-json")
        urls.append(url)
        try:
            doc = json.loads(http_get(url, ttl_hours=24 * 7))
        except Exception as e:
            log.warning("PIP chunk %s failed: %s", reps, str(e)[:120])
            continue
        for dims, attrs, obs in sdmx_json_series(doc):
            holder, issuer = dims["COUNTRY"], dims["COUNTERPART_COUNTRY"]
            if holder == issuer:
                continue
            hs, cs = PIP_HOLDER[dims["SECTOR"]], PIP_ISSUER[dims["COUNTERPART_SECTOR"]]
            for period, val, oa in obs:
                p = period.replace("-S1", "-06").replace("-S2", "-12")   # 2025-S1 -> 2025-06 (end of June)
                store.add(Obs("IMF_PIP", holder, hs, "BILATERAL", entry="A", instrument=inds[dims["INDICATOR"]], cp_sector=cs,
                              cp_area=issuer, measure="stock", unit="USD", unit_mult=0, freq="S", period=p, value=val,
                              note=dims["INDICATOR"]))
                n += 1
    store.mark_source("IMF_PIP", name="IMF Portfolio Investment Positions by counterpart (CPIS), by holder/issuer sector",
                      url=" ; ".join(urls), rows=n, fetched_at=fetched_at(urls[0]), license="IMF terms of use")
    log.info("PIP: %d obs", n)


def fetch_dip(store):
    """Direct investment positions by counterpart economy (outward net & inward net), USD, annual."""
    ctys = "+".join(BILATERAL_AREAS)
    inds = {"OTWD_D_NETAL_FALL_ALL": ("A", "FD"), "INWD_D_NETLA_FALL_ALL": ("L", "FD")}
    n = 0
    urls = []
    for i in range(0, len(BILATERAL_AREAS), 5):  # the full matrix in one call is too slow for the proxy; chunk by reporter
        reps = "+".join(BILATERAL_AREAS[i:i + 5])
        url = (f"{BASE}/IMF.STA/DIP/+/{reps}.O.{'+'.join(inds)}.{ctys}.A?c[TIME_PERIOD]=ge:2013&format=sdmx-json")
        urls.append(url)
        try:
            doc = json.loads(http_get(url, ttl_hours=24 * 7))
        except Exception as e:
            log.warning("DIP chunk %s failed: %s", reps, str(e)[:120])
            continue
        for dims, attrs, obs in sdmx_json_series(doc):
            rep, cp = dims["COUNTRY"], dims["COUNTERPART_COUNTRY"]
            if rep == cp:
                continue
            entry, instr = inds[dims["INDICATOR"]]
            for period, val, oa in obs:
                store.add(Obs("IMF_DIP", rep, "ROW", "BILATERAL", entry=entry, instrument=instr, cp_area=cp, measure="stock",
                              unit="USD", unit_mult=0, freq="A", period=period, value=val, note=dims["INDICATOR"]))
                n += 1
    store.mark_source("IMF_DIP", name="IMF Direct Investment Positions by counterpart (CDIS)", url=" ; ".join(urls), rows=n,
                      fetched_at=fetched_at(urls[0]), license="IMF terms of use")
    log.info("DIP: %d obs", n)


def fetch_all(store):
    for fn in (fetch_weo, fetch_bop, fetch_iip, fetch_pip, fetch_dip):
        try:
            fn(store)
        except Exception as e:  # keep the build going; report in sources.json
            log.exception("IMF %s failed", fn.__name__)
            store.mark_source(fn.__name__.replace("fetch_", "IMF_").upper(), error=str(e)[:300])
