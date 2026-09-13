"""US Treasury Fiscal Data API - the daily / monthly nowcast layer for the US government sector.

  Debt to the Penny            daily total public debt outstanding and debt held by the public
  Daily Treasury Statement     Treasury General Account closing balance
  Monthly Treasury Statement   receipts, outlays, deficit by month
"""
from __future__ import annotations

import json
import logging

from ..common import Obs, http_get, fetched_at

log = logging.getLogger(__name__)
BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
MONTHS = {m: i + 1 for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                           "September", "October", "November", "December"])}


def _pages(path, params, ttl):
    out = []
    page = 1
    while True:
        url = f"{BASE}/{path}?{params}&page[size]=10000&page[number]={page}"
        j = json.loads(http_get(url, ttl_hours=ttl))
        out.extend(j["data"])
        if page >= j["meta"].get("total-pages", 1):
            break
        page += 1
    return out, url


def fetch_debt(store):
    rows, url = _pages("v2/accounting/od/debt_to_penny", "sort=-record_date&filter=record_date:gte:2010-01-01", 3)
    n = 0
    for r in rows:
        for fld, concept in (("tot_pub_debt_out_amt", "DEBT_TOTAL"), ("debt_held_public_amt", "DEBT_PUBLIC")):
            v = r.get(fld)
            if v in (None, "null", ""):
                continue
            store.add(Obs("UST_DEBT", "USA", "GOV_C", concept, entry="L", measure="stock", unit="XDC", unit_mult=0, freq="D",
                          period=r["record_date"], value=float(v), note="Debt to the Penny (federal, par value)"))
            n += 1
    store.mark_source("UST_DEBT", name="US Treasury Debt to the Penny (daily)", url=url, rows=n, fetched_at=fetched_at(url),
                      license="Public domain")
    log.info("UST debt: %d obs", n)


def fetch_tga(store):
    rows, url = _pages("v1/accounting/dts/operating_cash_balance",
                       "sort=-record_date&filter=record_date:gte:2015-01-01,account_type:in:(Treasury General Account (TGA) Closing Balance,Federal Reserve Account)", 3)
    n = 0
    seen = set()
    for r in rows:
        d = r["record_date"]
        if d in seen:
            continue
        v = r.get("close_today_bal")
        if v in (None, "null", ""):
            v = r.get("open_today_bal")  # new-format DTS puts the closing balance here
        if v in (None, "null", ""):
            continue
        seen.add(d)
        store.add(Obs("UST_DTS", "USA", "GOV_C", "TGA", entry="A", measure="stock", unit="XDC", unit_mult=6, freq="D",
                      period=d, value=float(v), note="Treasury General Account closing balance"))
        n += 1
    store.mark_source("UST_DTS", name="US Daily Treasury Statement (TGA balance)", url=url, rows=n, fetched_at=fetched_at(url),
                      license="Public domain")
    log.info("UST TGA: %d obs", n)


def fetch_mts(store):
    rows, url = _pages("v1/accounting/mts/mts_table_1", "sort=-record_date&filter=record_date:gte:2015-01-01", 12)
    # keep, for every calendar month, the value from the latest record_date that contains it
    best: dict[str, tuple[str, dict]] = {}
    for r in rows:
        desc = r.get("classification_desc")
        if desc not in MONTHS:
            continue
        fy = int(r["record_fiscal_year"])
        m = MONTHS[desc]
        cal_year = fy - 1 if m >= 10 else fy
        # rows may belong to the prior fiscal year block (same record_date); parent decides
        period = f"{cal_year:04d}-{m:02d}"
        if r.get("current_month_gross_rcpt_amt") in (None, "null"):
            continue
        if period not in best or r["record_date"] > best[period][0]:
            best[period] = (r["record_date"], r)
    n = 0
    for period, (rd, r) in sorted(best.items()):
        rc, ou, df_ = (float(r["current_month_gross_rcpt_amt"]), float(r["current_month_gross_outly_amt"]),
                       float(r["current_month_dfct_sur_amt"]))
        for concept, entry, v in (("OTR", "C", rc), ("OTE", "D", ou), ("B9", "B", -df_)):
            store.add(Obs("UST_MTS", "USA", "GOV_C", concept, entry=entry, measure="flow", unit="XDC", unit_mult=0, freq="M",
                          period=period, value=v, release=rd, note="Monthly Treasury Statement, cash basis"))
            n += 1
    store.mark_source("UST_MTS", name="US Monthly Treasury Statement (receipts, outlays, deficit)", url=url, rows=n,
                      fetched_at=fetched_at(url), license="Public domain")
    log.info("UST MTS: %d obs", n)


def fetch_all(store):
    for fn in (fetch_debt, fetch_tga, fetch_mts):
        try:
            fn(store)
        except Exception as e:
            log.exception("UST %s failed", fn.__name__)
            store.mark_source("UST_" + fn.__name__.replace("fetch_", "").upper(), error=str(e)[:300])
