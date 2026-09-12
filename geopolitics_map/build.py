"""Build the World Politics & Geopolitics map.

    python build.py             fetch every source, update data/ stores, write web/data.json
    python build.py --only gdelt,markets     refresh only some modules (others reuse the previous data.json)
    python build.py --history 28             days of GDELT baseline to keep (default 28)

Two clocks, like the flow-of-funds dashboard:
  * the "event clock" (fast, partial): GDELT machine-coded events every 15 minutes, Polymarket odds, sanctions
    designations, UN Security Council coverage;
  * the "structure clock" (slow, fundamental): military spending, governance quality, population/GDP (World Bank),
    alliances and blocs, the election calendar.
Every number in data.json carries its source and observation time; the UI shows a provenance table.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline.common import COUNTRIES, DATA_DIR, ROOT, now_iso  # noqa: E402
from pipeline import gdelt, headlines, markets, sanctions, structure  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout),
                              logging.FileHandler(os.path.join(DATA_DIR, "build.log"), encoding="utf-8")])
log = logging.getLogger("build")
WEB_DIR = os.path.join(ROOT, "web")
DATA_JSON = os.path.join(WEB_DIR, "data.json")
BLOCS_UPDATED = "2025-10"  # when the hand-maintained alliance table was last reviewed


def _scaled_series(rows: list[dict], key_fields: tuple[str, ...], dates: list[str], fields: tuple[str, ...]) -> dict:
    """rows -> {key: {field: [value scaled to a full 96-slot day, aligned to dates]}}"""
    idx = {d: i for i, d in enumerate(dates)}
    out: dict[str, dict] = {}
    for r in rows:
        i = idx.get(r["date"])
        if i is None:
            continue
        files = int(r["files"] or 0)
        if files <= 0:
            continue
        k = "|".join(r[f] for f in key_fields)
        ser = out.setdefault(k, {f: [None] * len(dates) for f in fields})
        for f in fields:
            ser[f][i] = round(float(r[f]) * 96 / files, 1)
    return out


def assemble(prev: dict, g: dict | None, m: dict | None, s: dict | None, st: dict | None) -> dict:
    g = g or prev.get("gdelt")
    m = m or prev.get("markets")
    s = s or prev.get("sanctions")
    st = st or prev.get("structure")
    if not (g and st):
        raise SystemExit("gdelt and structure data are required for a first build")

    # --- GDELT history -> compact aligned series + baselines
    dates = sorted({r["date"] for r in g["global_daily"]})
    glob = _scaled_series(g["global_daily"], (), dates, ("articles", "events", "q1", "q2", "q3", "q4", "score")).get("", {})
    cs = _scaled_series(g["country_daily"], ("iso3",), dates, ("articles", "q3", "q4", "score"))
    dyad_rows = g["dyad_daily"]
    dyad_tot: dict[str, float] = {}
    for r in dyad_rows:
        k = r["a1"] + "|" + r["a2"]
        dyad_tot[k] = dyad_tot.get(k, 0) + float(r["score"])
    top_dyads = set(sorted(dyad_tot, key=lambda k: -dyad_tot[k])[:400])
    ds = _scaled_series([r for r in dyad_rows if r["a1"] + "|" + r["a2"] in top_dyads], ("a1", "a2"), dates, ("coop", "conf", "articles"))
    latest_day = g["latest"][:10]
    base_days = [d for d in dates if d < (dt.date.fromisoformat(latest_day) - dt.timedelta(days=1)).isoformat()]
    base_idx = [dates.index(d) for d in base_days]
    baseline = {}
    for iso, ser in cs.items():
        vals = [ser["articles"][i] for i in base_idx if ser["articles"][i] is not None]
        sc = [ser["score"][i] for i in base_idx if ser["score"][i] is not None]
        if len(vals) >= 5:
            med = statistics.median(vals)
            win = g["window_country"].get(iso, {}).get("articles", 0)
            baseline[iso] = {"med_articles": round(med, 1), "med_score": round(statistics.median(sc), 1), "days": len(vals),
                             "ratio": round(win / med, 2) if med > 0 else None}
    # country score for the map = window score x novelty boost (1 + 0.5*log2(ratio) capped)
    import math
    country_now = {}
    # news importance per country = sum of the importance of its top stories (grouped events, deduplicated by URL),
    # so the bubble reflects "how much important news" rather than the raw count of coded rows
    news_imp: dict[str, float] = {}
    news_n: dict[str, int] = {}
    for e in g["events"]:
        for iso in {e["loc"], e["a1"], e["a2"]}:
            if iso:
                news_imp[iso] = news_imp.get(iso, 0.0) + e["score"]
                news_n[iso] = news_n.get(iso, 0) + 1
    for iso, w in g["window_country"].items():
        b = baseline.get(iso, {})
        ratio = b.get("ratio")
        boost = 1.0
        if ratio and ratio > 1:
            boost = 1 + min(1.5, 0.5 * math.log2(ratio))
        conf_share = (w["q"][2] + w["q"][3]) / w["articles"] if w["articles"] else 0
        mat_share = w["q"][3] / w["articles"] if w["articles"] else 0
        country_now[iso] = {**w, "ratio": ratio, "boost": round(boost, 2), "score_adj": round(w["score"] * boost, 1),
                            "news": round(news_imp.get(iso, 0.0) * boost, 1), "news_n": news_n.get(iso, 0),
                            "conf_share": round(conf_share, 3), "mat_share": round(mat_share, 3)}

    # --- sanctions per country (stock)
    sanc_iso: dict[str, dict] = {}
    for src in ("ofac", "eu", "un"):
        for iso, n in (s or {}).get(src, {}).get("by_iso", {}).items():
            sanc_iso.setdefault(iso, {})[src] = n

    # --- clocks
    wbm = st["wb"]["meta"]
    ev_clock = [
        ["GDELT イベント（最新15分スロット, UTC）", g["latest"].replace("T", " ")],
        ["GDELT 直近24時間の取込ファイル数", f'{g["window_files"]} / 96'],
        ["Polymarket 予測市場", (m or {}).get("fetched", "") or prev.get("markets", {}).get("fetched", "")],
        ["OFAC SDN リスト（米財務省）", (s or {}).get("ofac", {}).get("fetched", "")[:16]],
        ["OFAC 最新アクション", ((s or {}).get("ofac", {}).get("actions") or [{}])[0].get("date", "")],
        ["EU 制裁リスト（生成日）", (s or {}).get("eu", {}).get("generated", "")],
        ["国連安保理 制裁リスト（生成日）", (s or {}).get("un", {}).get("generated", "")],
        ["国連プレスリリース（最新）", (st.get("un_press") or [{}])[0].get("date", "")],
    ]
    st_clock = [
        ["世銀 軍事費・兵員（SIPRI由来）", wbm.get("milex_gdp", {}).get("year", "")],
        ["世銀 ガバナンス指標（WGI）", wbm.get("wgi_pv", {}).get("year", "")],
        ["世銀 人口・GDP", wbm.get("gdp_usd", {}).get("year", "")],
        ["同盟・ブロック表（手動保守）", BLOCS_UPDATED],
        ["選挙カレンダー（Wikipedia）", (st.get("fetched", {}).get("wb") or now_iso())[:10]],
    ]
    sources = [
        ["GDELT 2.0 Events", "15分ごとの機械コード化イベント（CAMEO）。主体・対象・場所・記事数・媒体数・Goldstein強度。国際的な主体間の事象、または政府・軍・反政府勢力・議会・司法・警察・国際機関が関与する事象のみ保持", g["latest"], now_iso()[:16], f'{g["window_global"]["events"]:,} 件（24h、全 {g.get("window_seen", 0):,} 行のうち政治的事象）', "記事数は英語圏メディアに偏る。同一事象が複数行に分かれるため集計は「注目度」として読む。スコアの注目度は媒体数（転載に頑健）"],
        ["Polymarket Gamma API", "地政学・選挙タグの予測市場（YES価格＝確率）", (m or {}).get("fetched", "")[:16], (m or {}).get("fetched", "")[:16], f'{len((m or {}).get("events", []))} 件', "取引量の薄い市場は価格が粗い。米国居住者は取引不可のため参加者構成に偏り"],
        ["OFAC SDN（米財務省）", "特別指定国民リスト全件。プログラム別・対象国別に集計、前回vintageとの差分", (s or {}).get("ofac", {}).get("fetched", "")[:16], (s or {}).get("ofac", {}).get("fetched", "")[:16], f'{(s or {}).get("ofac", {}).get("entries", 0):,} 件', "国別対応はプログラム名からの推定（SDGT等の主題別は国に割り当てない）"],
        ["EU 金融制裁統合リスト", "EU制裁対象の全件。programme別、直近45日の官報公布日で新規を抽出", (s or {}).get("eu", {}).get("generated", ""), (s or {}).get("eu", {}).get("fetched", "")[:16], f'{(s or {}).get("eu", {}).get("entities", 0):,} 件', ""],
        ["国連安保理 統合制裁リスト", "安保理決議に基づく制裁対象。リスト別、LISTED_ON 直近60日", (s or {}).get("un", {}).get("generated", ""), (s or {}).get("un", {}).get("fetched", "")[:16], f'{(s or {}).get("un", {}).get("entries", 0):,} 件', ""],
        ["世界銀行 WDI / WGI", "軍事費（SIPRI由来）・兵員・人口・GDP・ガバナンス6指標", wbm.get("milex_gdp", {}).get("year", ""), st.get("fetched", {}).get("wb", "")[:16], f'{len(st["wb"]["data"].get("gdp_usd", {}))} か国', "台湾は世銀に含まれない（勢力指数は手動値）"],
        ["Wikipedia 選挙カレンダー", "今年・来年の国政選挙の予定日", "", now_iso()[:16], f'{len(st.get("elections", []))} 件', "斜体（暫定）は未確定の日程"],
        ["国連プレスリリース RSS", "安保理・総会の会合報道", (st.get("un_press") or [{}])[0].get("date", ""), st.get("fetched", {}).get("un_press", "")[:16], f'{len(st.get("un_press", []))} 件', ""],
        ["同盟・ブロック表", "NATO・EU・QUAD・BRICS+・SCO・CSTO・ASEAN・GCC・核保有国 等（手動保守）", BLOCS_UPDATED, "", f'{len(st["blocs"])} 区分', "加盟状況は変化する。注記参照"],
    ]

    countries = {}
    for iso, v in COUNTRIES.items():
        c = {"num": v[0], "ja": v[2], "en": v[3]}
        if len(v) > 4:
            c["lat"], c["lon"] = v[4]
        countries[iso] = c

    return {
        "generated": now_iso(),
        "countries": countries,
        "clocks": {"events": ev_clock, "structure": st_clock},
        "gdelt": {
            "latest": g["latest"], "window_start": g["window_start"], "window_files": g["window_files"],
            "global": g["window_global"], "country": country_now, "dyads": g["window_dyads"], "events": g["events"],
            "dates": dates, "global_daily": glob, "country_daily": cs, "dyad_daily": ds,
        },
        "baseline": baseline,
        "markets": m or prev.get("markets") or {"events": []},
        "sanctions": {**(s or {}), "by_iso": sanc_iso},
        "structure": st,
        "sources": sources,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated subset of: structure,gdelt,markets,sanctions")
    ap.add_argument("--history", type=int, default=28)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    run = lambda name: not only or name in only

    prev = {}
    if os.path.exists(DATA_JSON):
        try:
            prev = json.load(open(DATA_JSON, encoding="utf-8"))
        except Exception:
            prev = {}

    st = m = s = g = None
    if run("structure"):
        log.info("=== structure (World Bank, blocs, elections, UN press)")
        st = structure.fetch_all()
    power = (st or prev.get("structure", {})).get("power") or {}
    if run("gdelt"):
        log.info("=== GDELT")
        g = gdelt.fetch_all(power, history_days=args.history, workers=args.workers)
        try:
            headlines.enrich(g["events"], limit=600)
        except Exception:
            log.exception("headline fetch failed (URL-derived titles kept)")
    if run("markets"):
        log.info("=== Polymarket")
        try:
            m = markets.fetch_all()
            m["fetched"] = now_iso()
        except Exception:
            log.exception("markets failed")
    if run("sanctions"):
        log.info("=== sanctions")
        s = sanctions.fetch_all()
    if g is None and prev.get("gdelt"):
        # rebuild the raw daily lists from the CSV stores so assemble() can recompute series
        g = {**prev["gdelt"], "window_country": prev["gdelt"]["country"], "window_dyads": prev["gdelt"]["dyads"],
             "window_global": prev["gdelt"]["global"], "country_daily": gdelt._read_csv(gdelt.COUNTRY_DAILY),
             "dyad_daily": gdelt._read_csv(gdelt.DYAD_DAILY), "global_daily": gdelt._read_csv(gdelt.GLOBAL_DAILY)}
    data = assemble(prev, g, m, s, st)
    json.dump(data, open(DATA_JSON, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log.info("wrote %s (%.1f MB)", DATA_JSON, os.path.getsize(DATA_JSON) / 1e6)


if __name__ == "__main__":
    main()
