"""各地図の web/data.json から、メール要約・トップページ用の summary.json を書き出す。

    python tools/export_summary.py        # out/geo/summary.json, out/corp/summary.json

data.json の細部が変わっても落ちないよう、値は全て .get で防御的に取る。
"""
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))

def dump(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {p.relative_to(ROOT)} ({p.stat().st_size//1024} KB)")

# 地政学
def geo_summary(d: dict) -> dict:
    names = d.get("countries", {})
    ja = lambda iso: (names.get(iso) or {}).get("ja") or iso
    gd = d.get("gdelt", {})
    events = gd.get("events", []) if isinstance(gd.get("events"), list) else []
    # 国際的な出来事（国をまたぐ dyad）を優先し、国内ニュース（GDELTは米国ローカル報道が多い）は
    # 上位の少数だけ添える — メールの目的は「地政学」なので。
    def ranked(evs):
        return sorted(evs, key=lambda x: -(x.get("score") or 0))
    intl = ranked([e for e in events if e.get("intl")])
    dom  = ranked([e for e in events if not e.get("intl")])
    seen, top = set(), []
    for e in intl[:9] + dom[:3] + intl[9:]:
        title = str(e.get("title") or "").strip()
        key = title.lower()[:80]
        if not title or key in seen:
            continue
        seen.add(key)
        top.append({
            "title": title,
            "label": e.get("label"),
            "a1": ja(e.get("a1")), "a2": ja(e.get("a2")),
            "a1name": e.get("a1name"), "a2name": e.get("a2name"),
            "place": e.get("place"),
            "score": round(e.get("score") or 0, 1),
            "sources": e.get("sources"), "articles": e.get("articles"),
            "intl": e.get("intl"), "gold": e.get("gold"),
            "url": e.get("url"),
        })
        if len(top) >= 12:
            break
    country = gd.get("country", {}) if isinstance(gd.get("country"), dict) else {}
    top_c = []
    for iso, c in sorted(country.items(), key=lambda kv: -((kv[1] or {}).get("score_adj") or 0))[:10]:
        top_c.append({"iso": iso, "name": ja(iso),
                      "score": round(c.get("score_adj") or 0, 1),
                      "attention_ratio": c.get("ratio"), "tone": c.get("gold"),
                      "sources": c.get("sources"), "articles": c.get("articles")})
    mk = d.get("markets", {})
    markets = []
    for m in (mk.get("events") or [])[:8]:
        if isinstance(m, dict):
            keep = {k: m.get(k) for k in ("title", "question", "slug", "prob", "probability",
                                        "yes", "volume", "end", "endDate", "tag") if k in m}
            markets.append(keep or {"raw": str(m)[:200]})
    clocks = d.get("clocks", {})
    return {
        "map": "geo", "title": "世界の政治・地政学マップ",
        "generated": d.get("generated"),
        "exported": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "freshness": clocks.get("events", [])[:3],
        "top_events": top, "top_countries": top_c, "markets": markets,
    }

# 企業活動
def corp_summary(d: dict) -> dict:
    days = d.get("days") or []
    day = days[-1] if days else {}
    labels = d.get("action_labels", {})
    evs = day.get("events") or []
    top = []
    def wtotal(x):
        w = x.get("weight")
        return (w.get("total") if isinstance(w, dict) else w) or 0
    for e in sorted(evs, key=lambda x: -wtotal(x))[:12]:
        actor = e.get("actor") or {}; loc = e.get("location") or {}; imp = e.get("impact") or {}
        top.append({
            "title": e.get("title_ja"), "summary": e.get("summary_ja"),
            "actor": actor.get("name_ja") or actor.get("name"), "actor_country": actor.get("country"),
            "action": labels.get(e.get("action_type"), e.get("action_type")),
            "action_type": e.get("action_type"), "theme": e.get("theme"),
            "country": loc.get("country"), "city": loc.get("city"),
            "amount": e.get("amount_text"), "amount_usd": e.get("amount_usd"),
            "impact": imp.get("score"), "horizon": imp.get("horizon"), "scope": imp.get("scope"),
            "rationale": imp.get("rationale_ja"), "cross_border": e.get("cross_border"),
            "weight": round(wtotal(e), 2),
        })
    by_action, by_theme = {}, {}
    for e in evs:
        by_action[e.get("action_type")] = by_action.get(e.get("action_type"), 0) + 1
        by_theme[e.get("theme")] = by_theme.get(e.get("theme"), 0) + 1
    return {
        "map": "corp", "title": "世界の企業活動マップ",
        "date": day.get("date"), "generated": day.get("generated_at"),
        "exported": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": day.get("model"), "headline_count": day.get("headline_count"),
        "event_count": len(evs), "daily_note": day.get("daily_note_ja"),
        "top_events": top,
        "by_action": {labels.get(k, k): v for k, v in sorted(by_action.items(), key=lambda kv: -kv[1])},
        "by_theme": dict(sorted(by_theme.items(), key=lambda kv: -kv[1])[:8]),
    }

MAPS = {
    "geo":  (ROOT / "geopolitics_map" / "web" / "data.json", geo_summary),
    "corp": (ROOT / "corp_activity_map" / "web" / "data.json", corp_summary),
}

if __name__ == "__main__":
    ok = True
    for key, (src, fn) in MAPS.items():
        try:
            dump(OUT / key / "summary.json", fn(load(src)))
        except Exception as e:
            ok = False
            print(f"[WARN] {key}: summary failed: {e}")
    sys.exit(0 if ok else 1)
