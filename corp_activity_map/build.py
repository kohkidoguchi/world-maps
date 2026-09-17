"""
世界企業活動マップ — 日次ビルド

  1. 世界のニュース（Google News RSS 多言語検索 + FT）から直近24時間の企業活動の見出しを集める
  2. Claude が見出し群から「企業活動イベント」を構造化抽出する
       主体（企業）・相手・活動種別・場所（緯度経度）・資本量・主体規模・将来影響
  3. 重み = 主体の大きさ × 動く資本量 × 将来影響 を計算して data/events/YYYY-MM-DD.json に保存
  4. 直近 N 日分を web/data.json にまとめる（D3 の地図が読む）

使い方:
  python build.py              # 今日の分を取得・抽出・保存して web/data.json を再生成
  python build.py --date 2026-09-11   # 保存日付を指定（見出しの取得はいつも直近24時間）
  python build.py --fetch-only # 見出しの取得だけ（Claude を呼ばない・費用ゼロ）
  python build.py --json       # 保存済みイベントから web/data.json だけ再生成
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import feedparser
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent
DATA = ROOT / "data"
RAW_DIR = DATA / "raw"
EVENTS_DIR = DATA / "events"
WEB_DATA = ROOT / "web" / "data.json"

# .env はこのフォルダのものを優先、無ければ news_digest のものを流用
for env_path in (ROOT / ".env", ROOT.parent / "news_digest" / ".env"):
    if env_path.exists():
        load_dotenv(env_path, override=False)
        break

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
try:  # BCG の SSL 検査プロキシ配下では Windows 証明書ストアが必要
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

MODEL = "claude-sonnet-5"
DAYS_IN_WEB = 30          # web/data.json に入れる日数
MAX_PER_FEED = 40         # 1フィードあたりの見出し上限
MAX_TO_CLAUDE = 1300      # Claude に渡す見出し上限（約35フィード×40件）
UA = {"User-Agent": "CorpActivityMap/1.0 (personal research)"}

# ── 収集する検索フィード ────────────────────────────────────────────────────────
# (地域ラベル, hl, gl, ceid, クエリ)。Google News の検索 RSS は直近24時間 (when:1d) に絞る。
GN = [
    # 英語（米・英・印・シンガポール・豪の各エディションで地域を散らす）
    ("US", "en-US", "US", "US:en", '"to acquire" OR "acquisition of" OR merger OR takeover billion'),
    ("US", "en-US", "US", "US:en", '"to invest" OR "will invest" OR "new plant" OR factory OR "data center" billion'),
    ("US", "en-US", "US", "US:en", 'IPO OR "raises" OR "funding round" OR valuation OR "share buyback" billion'),
    ("US", "en-US", "US", "US:en", 'layoffs OR "job cuts" OR bankruptcy OR "chapter 11" OR "shut down" OR "pulls out" company'),
    ("US", "en-US", "US", "US:en", 'antitrust OR regulator OR fine OR sanctions OR lawsuit company billion'),
    ("US", "en-US", "US", "US:en", '"joint venture" OR partnership OR "signs contract" OR "wins contract" OR "supply deal" billion'),
    ("US", "en-US", "US", "US:en", '"power plant" OR LNG OR mine OR pipeline OR gigawatt OR "chip plant" investment'),
    ("US", "en-US", "US", "US:en", '"sovereign wealth fund" OR "state-owned" OR "private equity" deal billion'),
    ("GB", "en-GB", "GB", "GB:en", 'acquisition OR takeover OR merger OR "to invest" OR "job cuts" billion OR bn'),
    ("IN", "en-IN", "IN", "IN:en", 'acquisition OR "to invest" OR plant OR IPO OR stake crore'),
    ("SG", "en-SG", "SG", "SG:en", 'acquisition OR "to invest" OR plant OR stake OR "data centre" billion'),
    ("AU", "en-AU", "AU", "AU:en", 'acquisition OR takeover OR mine OR "to invest" OR "job cuts" billion'),
    ("ZA", "en-ZA", "ZA", "ZA:en", 'acquisition OR "to invest" OR mine OR plant billion'),
    ("AE", "en-AE", "AE", "AE:en", 'acquisition OR "to invest" OR stake OR "sovereign" billion'),
    # 日本語
    ("JP", "ja", "JP", "JP:ja", '買収 OR 出資 OR 経営統合 OR 子会社化 億円'),
    ("JP", "ja", "JP", "JP:ja", '工場 OR 設備投資 OR データセンター OR 新拠点 億円'),
    ("JP", "ja", "JP", "JP:ja", '上場 OR IPO OR 増資 OR 自社株買い OR 社債 億円'),
    ("JP", "ja", "JP", "JP:ja", 'リストラ OR 撤退 OR 経営破綻 OR 希望退職 OR 民事再生'),
    ("JP", "ja", "JP", "JP:ja", '提携 OR 合弁 OR 受注 OR 大型契約 億円'),
    # ドイツ語
    ("DE", "de", "DE", "DE:de", 'Übernahme OR Fusion OR Beteiligung Milliarden'),
    ("DE", "de", "DE", "DE:de", 'Investition OR Werk OR Fabrik OR Rechenzentrum Milliarden'),
    ("DE", "de", "DE", "DE:de", 'Stellenabbau OR Insolvenz OR Werksschließung'),
    # 中国語（簡体）
    ("CN", "zh-CN", "CN", "CN:zh-Hans", '收购 OR 并购 OR 入股 亿元'),
    ("CN", "zh-CN", "CN", "CN:zh-Hans", '投资 OR 工厂 OR 项目 OR 产能 亿元'),
    ("CN", "zh-CN", "CN", "CN:zh-Hans", '裁员 OR 破产 OR 退出'),
    ("TW", "zh-TW", "TW", "TW:zh-Hant", '收購 OR 投資 OR 設廠 億元'),
    # フランス語・スペイン語・ポルトガル語・韓国語・アラビア語
    ("FR", "fr", "FR", "FR:fr", 'acquisition OR rachat OR fusion milliards'),
    ("FR", "fr", "FR", "FR:fr", 'investissement OR usine OR "plan social" milliards'),
    ("ES", "es", "ES", "ES:es", 'adquisición OR compra OR fusión OR OPA millones'),
    ("MX", "es-419", "MX", "MX:es-419", 'inversión OR planta OR adquisición millones de dólares'),
    ("BR", "pt-BR", "BR", "BR:pt-419", 'aquisição OR compra OR investimento OR fábrica bilhões'),
    ("KR", "ko", "KR", "KR:ko", '인수 OR 합병 OR 투자 OR 공장 조원 OR 억원'),
    ("SA", "ar", "SA", "SA:ar", 'استحواذ OR استثمار OR صفقة مليار'),
]
OTHER_FEEDS = [
    ("FT Companies", "https://www.ft.com/companies?format=rss"),
]

# ── 活動種別・テーマ ────────────────────────────────────────────────────────────
ACTION_TYPES = ["ma", "capex", "finance", "alliance", "contraction", "regulation"]
ACTION_LABELS = {
    "ma": "M&A・出資", "capex": "設備投資・新拠点", "finance": "資金調達・資本政策",
    "alliance": "提携・大型契約", "contraction": "縮小・撤退・破綻", "regulation": "規制・制裁・訴訟",
}
THEMES = ["AI・半導体", "エネルギー転換", "サプライチェーン再編", "金融再編", "地政学・経済安保",
          "ヘルスケア", "消費・小売", "モビリティ", "素材・資源", "不動産・インフラ", "その他"]

# ── 一面レンズ用の軸（主要媒体トップ面の選択基準から抽出、2026-09-12）───────────────
MATERIALITY = {"paper": "所有権・資金の移転", "capital": "資本投下の約束", "physical": "物理的変化（建設・閉鎖・停止・生産）", "rule": "制度・規制・契約の変化"}
STAGES = ["構想", "計画", "合意", "実行", "完了"]
TAGS = ["国家接点", "雇用・生活", "先例・意外性", "能力の変化", "権力者の意思", "供給網・インフラ"]
LENS_FIELDS = {
    "revenue_usd": {"type": ["number", "null"], "description": "主体の年間売上（金融は運用資産、政府は年間予算）の米ドル概算。不明なら null"},
    "signal": {
        "type": "object", "additionalProperties": False, "required": ["score", "frontpage_reason_ja"],
        "properties": {
            "score": {"type": "integer", "enum": [1, 2, 3, 4, 5],
                      "description": "シグナル性：この出来事が、より大きな問い（AIバブルか、信用収縮か、中国の過剰生産か、脱ドル化か…）の証拠・先行指標になる度合い。1=単発 3=業界の転換を示す 5=世界の資本・技術・権力の潮目を示す"},
            "frontpage_reason_ja": {"type": "string", "description": "FT と日経の一面担当がこれをトップに置くとしたら、その理由を1文で。置かないなら「一面級ではない」と理由"},
        },
    },
    "materiality": {"type": "string", "enum": list(MATERIALITY), "description": "実物性。paper=株式・債権・資金の持ち替え（M&A、出資、売却、資金調達） capital=資本投下の約束（投資計画、契約） physical=原子が動く（工場稼働・建設着工・閉鎖・停止・解雇の実施・生産） rule=制度や規制、契約条件の変化（承認、制裁、判決、提携の枠組み）"},
    "stage": {"type": "string", "enum": STAGES, "description": "実現段階。構想=検討・観測報道 計画=正式発表だが未着手 合意=契約・承認済み 実行=着工・執行中 完了=完了・稼働"},
    "tags": {"type": "array", "items": {"type": "string", "enum": TAGS}, "description": "該当するものすべて。国家接点=規制・司法・国営・経済安保が絡む／雇用・生活=雇用・賃金・生活コストに直接届く／先例・意外性=初めて・最大・逆張り・コンセンサスとの乖離／能力の変化=技術や生産能力そのものの変化／権力者の意思=創業者・CEO・オーナーの個人的決断や交代／供給網・インフラ=エネルギー・物流・通信・素材の供給に影響"},
}


# ═══════════════════════════ 1. 見出しの収集 ═══════════════════════════════════
def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def fetch_headlines() -> list[dict]:
    items, seen = [], set()

    def add(title, source, link, region, lang, published):
        key = re.sub(r"[^\w]", "", title.lower())[:80]
        if not title or key in seen:
            return
        seen.add(key)
        items.append({"i": len(items), "title": title, "source": source, "link": link,
                      "region": region, "lang": lang, "published": published})

    for region, hl, gl, ceid, q in GN:
        url = f"https://news.google.com/rss/search?q={quote(q + ' when:1d')}&hl={hl}&gl={gl}&ceid={ceid}"
        try:
            r = requests.get(url, headers=UA, timeout=30)
            feed = feedparser.parse(r.content)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {region} {q[:30]}: {e}")
            continue
        n = 0
        for e in feed.entries[:MAX_PER_FEED]:
            title = _clean(e.get("title", ""))
            src = e.get("source", {}).get("title", "") if isinstance(e.get("source"), dict) else ""
            if src and title.endswith(" - " + src):
                title = title[: -len(src) - 3].strip()
            add(title, src, e.get("link", ""), region, hl, e.get("published", ""))
            n += 1
        print(f"  {region:3} {hl:6} {n:3} | {q[:60]}")
        time.sleep(0.3)

    for name, url in OTHER_FEEDS:
        try:
            r = requests.get(url, headers=UA, timeout=30)
            feed = feedparser.parse(r.content)
            for e in feed.entries[:MAX_PER_FEED]:
                add(_clean(e.get("title", "")) + (" — " + _clean(e.get("summary", ""))[:120] if e.get("summary") else ""),
                    name, e.get("link", ""), "GB", "en", e.get("published", ""))
            print(f"  {name}: {len(feed.entries)}")
        except Exception as e:  # noqa: BLE001
            print(f"  ! {name}: {e}")
    return items


# ═══════════════════════════ 2. Claude による抽出 ═══════════════════════════════
EVENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["daily_note_ja", "events"],
    "properties": {
        "daily_note_ja": {"type": "string", "description": "この日の企業活動の地図が示す構造を3〜5文で。個別の羅列ではなく、資本がどこからどこへ・何に向かっているか、逆流や収縮はどこか、を描く"},
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title_ja", "summary_ja", "actor", "counterparty", "action_type", "theme",
                             "location", "origin", "amount_usd", "amount_text", "impact", "sources", "is_followup",
                             *LENS_FIELDS],
                "properties": {
                    **LENS_FIELDS,
                    "title_ja": {"type": "string", "description": "日本語の短い見出し（30字以内）"},
                    "summary_ja": {"type": "string", "description": "何が起きたか＋なぜ構造的に重要か、2〜3文"},
                    "actor": {
                        "type": "object", "additionalProperties": False,
                        "required": ["name", "name_ja", "country", "sector", "scale_tier", "scale_note"],
                        "properties": {
                            "name": {"type": "string", "description": "主体（企業・ファンド・国営企業）の英語名"},
                            "name_ja": {"type": "string"},
                            "country": {"type": "string", "description": "本社所在国 ISO 3166-1 alpha-2"},
                            "sector": {"type": "string", "description": "業種（日本語・短く）"},
                            "scale_tier": {"type": "integer", "enum": [1, 2, 3, 4, 5],
                                           "description": "主体の大きさ。年間売上（または運用資産）で 1:<10億ドル 2:10〜100億 3:100〜500億 4:500〜2000億 5:>2000億ドル"},
                            "scale_note": {"type": "string", "description": "根拠（例: 売上約30兆円、時価総額3兆ドル）"},
                        },
                    },
                    "counterparty": {
                        "type": "object", "additionalProperties": False,
                        "required": ["name", "country"],
                        "properties": {
                            "name": {"type": "string", "description": "相手（買収対象・提携先・当局など）。無ければ空文字"},
                            "country": {"type": "string", "description": "ISO alpha-2。無ければ空文字"},
                        },
                    },
                    "action_type": {"type": "string", "enum": ACTION_TYPES},
                    "theme": {"type": "string", "enum": THEMES},
                    "location": {
                        "type": "object", "additionalProperties": False,
                        "required": ["city", "country", "lat", "lon", "kind"],
                        "properties": {
                            "city": {"type": "string", "description": "活動が起きる場所（工場の立地、買収対象の本社、契約の対象地など）"},
                            "country": {"type": "string", "description": "ISO alpha-2"},
                            "lat": {"type": "number"}, "lon": {"type": "number"},
                            "kind": {"type": "string", "enum": ["site", "target", "hq", "market"],
                                     "description": "site=工場・拠点の立地 target=買収・出資対象の所在 hq=主体本社（他に場所が無い時） market=対象市場・当局の所在"},
                        },
                    },
                    "origin": {
                        "type": "object", "additionalProperties": False,
                        "required": ["city", "country", "lat", "lon"],
                        "properties": {
                            "city": {"type": "string", "description": "資本・意思決定の出所（主体の本社）"},
                            "country": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"},
                        },
                    },
                    "amount_usd": {"type": ["number", "null"], "description": "動く資本量（米ドル換算）。不明なら null。概算可"},
                    "amount_text": {"type": "string", "description": "元の表記（例: 400億円、$2.6bn）。不明なら空文字"},
                    "impact": {
                        "type": "object", "additionalProperties": False,
                        "required": ["score", "horizon", "scope", "rationale_ja"],
                        "properties": {
                            "score": {"type": "integer", "enum": [1, 2, 3, 4, 5],
                                      "description": "将来に与える影響。1=一過性・局所 3=国レベルで産業構造に影響 5=世界の産業・技術・地政学の構造を変えうる。不可逆性・波及範囲・時間軸で判断"},
                            "horizon": {"type": "string", "enum": ["短期", "中期", "長期"]},
                            "scope": {"type": "string", "enum": ["地域", "国", "世界"]},
                            "rationale_ja": {"type": "string", "description": "影響度の根拠を1文で"},
                        },
                    },
                    "sources": {"type": "array", "items": {"type": "integer"}, "description": "根拠にした見出し番号（複数可）"},
                    "is_followup": {"type": "boolean", "description": "直近数日に既に地図に載った出来事の続報なら true"},
                },
            },
        },
    },
}

SYSTEM_PROMPT = """あなたは世界の企業活動を観測するアナリストです。多言語のニュース見出し群から、
「世界地図に載せる価値のある企業活動イベント」を構造化して抽出します。

## 抽出の原則
- 対象は企業・ファンド・国営企業・政府系投資機関などの**具体的な活動**（買収、出資、設備投資、新拠点、資金調達、
  大型契約、提携、撤退、リストラ、破綻、規制当局による制裁・承認・訴訟）。相場観・論評・一般論・製品発表単体は除く。
- 同じ出来事を複数の見出しが報じている場合は 1 件にまとめ、sources に全ての見出し番号を入れる。
- 見出ししか無いので、金額や規模は自分の知識で補完してよい（概算）。不明なら null / 空文字。
- 場所（location）は「活動が起きる場所」を選ぶ。工場なら立地、買収なら対象の所在地、提携なら主たる対象地。
  origin は主体の本社所在地。両者の国が違えば「国境を越える資本の動き」として地図に矢印が描かれる。
  緯度経度は都市の中心でよい（小数1桁で十分）。
- scale_tier は主体の年間売上（金融なら運用資産）の桁で判定。impact.score は構造的な重要性で判定し、
  金額の大小に引きずられない（小さくても不可逆・波及の大きいものは高く、巨額でも金融的な入れ替えに過ぎないものは低く）。
- 「投資家として」等の立場に立った助言は書かない。事象の構造と含意だけを書く。
- 件数の目安は 40〜90 件。重要度の低い小型案件は無理に拾わない。ただし世界の分布を偏らせないよう、
  米国以外（日本・欧州・中国・インド・中東・アフリカ・中南米）の案件は小型でも拾う。
- 直近数日に地図に載った出来事（別途渡す）の続報は、新しい展開が無ければ拾わない。新展開があれば is_followup=true で載せる。
- 金額以外の軸（signal / materiality / stage / tags / revenue_usd）は「主要媒体の一面はなぜそれを選ぶか」を基準に付ける。
  一面は金額ではなく、より大きな問いの証拠（シグナル）、国家と企業の接点、先例性、人への影響、能力の変化で選ぶ。
  signal.score は impact.score と独立に判定する（将来影響が大きくても既知の延長なら signal は低い。金額が小さくても潮目を示すなら高い）。
- 文章はすべて日本語（actor.name のみ英語）。"""


def _usable_events(raw: dict) -> list[dict]:
    """題名と主体名が入っているイベントだけ（退化出力の空イベントを除く）。"""
    return [e for e in (raw.get("events") or [])
            if isinstance(e, dict) and (e.get("title_ja") or "").strip()
            and ((e.get("actor") or {}).get("name") or "").strip()]


def extract_events(headlines: list[dict], recent_titles: list[str], run_date: str, attempts: int = 2) -> dict:
    """見出し群から企業活動イベントを抽出する。
    出力がほぼ空（総評だけ書いて空のイベント1件で終わる退化出力、2026-09-16 に発生）なら1回だけ
    やり直し、それでも駄目なら例外にして前回の地図データを維持する（空の地図を公開しない）。"""
    n_head = min(len(headlines), MAX_TO_CLAUDE)
    need = 1 if n_head < 50 else min(10, max(3, n_head // 50))
    for attempt in range(1, attempts + 1):
        raw = _extract_once(headlines, recent_titles, run_date)
        usable = _usable_events(raw)
        print(f"  events: {len(raw.get('events') or [])} 件（使える {len(usable)} 件、必要 {need} 件以上）")
        if len(usable) >= need:
            raw["events"] = usable
            return raw
        print(f"  ! 退化出力 — 再試行 {attempt}/{attempts}")
    raise RuntimeError("Claude の出力が退化（イベントがほぼ空）したため中断。前回の地図データを維持します")


def _extract_once(headlines: list[dict], recent_titles: list[str], run_date: str) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    lines = "\n".join(f"[{h['i']}] ({h['region']}/{h['source']}) {h['title']}" for h in headlines[:MAX_TO_CLAUDE])
    recent = "\n".join(f"- {t}" for t in recent_titles[:150]) or "（なし）"
    user = f"""基準日: {run_date}（直近24時間の見出し）

## 直近3日に既に地図へ載せた出来事（続報は新展開がある場合のみ）
{recent}

## 見出し（{min(len(headlines), MAX_TO_CLAUDE)}件）
{lines}
"""
    kwargs = dict(
        model=MODEL,
        max_tokens=120000,   # 純粋な JSON で 100 件 ≈ 52k トークン（09-17 実測）。claude-sonnet-5 の上限は 128k
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
        # 抽出・分類タスクなので思考は切る。思考が出力予算（max_tokens／effort）を食うと、
        # 64k で途中終了したり（09-13 以前）、総評だけ書いて空イベント1件で終わったり（09-16）する。
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": EVENT_SCHEMA}},
    )
    t0 = time.time()
    try:  # 安全分類器による拒否時にサーバー側で別モデルへ切り替える（対応SDK・APIの場合のみ）
        with client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs) as stream:
            msg = stream.get_final_message()
    except (TypeError, anthropic.BadRequestError) as e:
        print(f"  (fallbacks 非対応のため通常呼び出しに切替: {str(e)[:80]})")
        try:
            with client.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except TypeError:  # 古い SDK は output_config を知らないので extra_body で渡す
            oc = kwargs.pop("output_config")
            with client.messages.stream(extra_body={"output_config": oc}, **kwargs) as stream:
                msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"モデルが応答を拒否しました: {getattr(msg, 'stop_details', None)}")
    text = next(b.text for b in msg.content if b.type == "text")
    u = msg.usage
    print(f"  Claude: {time.time()-t0:.0f}s  in={u.input_tokens} out={u.output_tokens} stop={msg.stop_reason}")
    if msg.stop_reason == "max_tokens":
        raw = _salvage_truncated(text)
        if not raw or not raw.get("events"):
            raise RuntimeError(f"出力が max_tokens で途中終了（out={u.output_tokens}）し、完成したイベントを取り出せず中断。")
        print(f"  ! 出力が max_tokens で途中終了（out={u.output_tokens}）— 完成している {len(raw['events'])} 件だけ採用")
        return raw
    return json.loads(text)


def _salvage_truncated(text: str) -> dict | None:
    """途中で切れた JSON から、完成している events の要素までを取り出す。
    daily_note_ja が events より後ろにあって失われた場合は空文字で補う。"""
    i = text.find('"events"')
    j = text.find("[", i) if i >= 0 else -1
    if j < 0:
        return None
    depth, in_str, esc, last_end = 0, False, False, None
    for k in range(j, len(text)):
        ch = text[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 1 and ch == "}":   # events 配列直下の要素が閉じた位置
                last_end = k
    if last_end is None:
        return None
    try:
        raw = json.loads(text[:last_end + 1] + "]}")
    except Exception:
        return None
    raw.setdefault("daily_note_ja", "")
    return raw


# ═══════════════════════════ 3. 重みづけ ════════════════════════════════════════
def capital_score(amount_usd) -> float:
    """資本量 → 1〜5。1e8ドル=2, 1e9=3, 1e10=4, 1e11=5（対数補間）。不明は 2。"""
    if not amount_usd or amount_usd <= 0:
        return 2.0
    return max(1.0, min(5.0, math.log10(amount_usd) - 6))


MAT_FRONT = {"paper": 0.8, "capital": 1.0, "physical": 1.2, "rule": 1.0}      # 一面レンズの実物性係数
MAT_PHYS = {"paper": 0.5, "capital": 0.9, "physical": 1.35, "rule": 0.8}      # 実物レンズの実物性係数
STAGE_COEF = {"構想": 0.6, "計画": 0.7, "合意": 0.85, "実行": 0.95, "完了": 1.0}
TIER_REVENUE = {1: 3e8, 2: 3e9, 3: 2e10, 4: 1e11, 5: 5e11}                      # revenue_usd が無い時の代表値
DEFAULT_MAT = {"ma": "paper", "finance": "paper", "capex": "capital", "alliance": "rule", "regulation": "rule", "contraction": "physical"}


def relative_bet(amount_usd, revenue_usd, tier: int) -> float:
    """賭けの相対的大きさ → 1〜5。金額÷売上が 0.1%=1, 1%=2, 10%=3, 100%=4, 1000%=5。不明は 2。"""
    if not amount_usd or amount_usd <= 0:
        return 2.0
    rev = revenue_usd if revenue_usd and revenue_usd > 0 else TIER_REVENUE.get(int(tier), 2e10)
    return max(1.0, min(5.0, math.log10(amount_usd / rev) + 4))


def weigh(ev: dict) -> dict:
    """3つのレンズで重みを計算する。
    money    = 0.3×主体規模 + 0.3×資本量(絶対額) + 0.4×将来影響                              …発表額の大きさに素直
    frontpage= (0.2×主体規模 + 0.2×賭けの相対的大きさ + 0.3×将来影響 + 0.3×シグナル性) × 実物性 × 実現段階
    physical = (0.2×主体規模 + 0.3×賭けの相対的大きさ + 0.5×将来影響) × 実物性(強) × 実現段階   …原子が動くものを重く
    """
    s = float(ev["actor"]["scale_tier"])
    c = capital_score(ev.get("amount_usd"))
    f = float(ev["impact"]["score"])
    r = relative_bet(ev.get("amount_usd"), ev.get("revenue_usd"), ev["actor"]["scale_tier"])
    sig = float((ev.get("signal") or {}).get("score") or f)          # 旧データは将来影響で代用
    mat = ev.get("materiality") or DEFAULT_MAT[ev["action_type"]]
    stage = ev.get("stage") or "計画"
    st = STAGE_COEF.get(stage, 0.8)
    clamp = lambda x: round(max(1.0, min(5.0, x)), 3)
    money = 0.3 * s + 0.3 * c + 0.4 * f
    front = (0.2 * s + 0.2 * r + 0.3 * f + 0.3 * sig) * MAT_FRONT[mat] * st
    phys = (0.2 * s + 0.3 * r + 0.5 * f) * MAT_PHYS[mat] * st
    ev["weight"] = {"scale": round(s, 2), "capital": round(c, 2), "relative": round(r, 2), "impact": round(f, 2),
                    "signal": round(sig, 2), "mat_coef": MAT_FRONT[mat], "stage_coef": st,
                    "lens": {"money": clamp(money), "frontpage": clamp(front), "physical": clamp(phys)},
                    "total": clamp(money)}
    ev.setdefault("materiality", mat); ev.setdefault("stage", stage); ev.setdefault("tags", [])
    return ev


def _norm_country(code: str) -> str:
    return (code or "").strip().upper()[:2]


def finalize(raw: dict, headlines: list[dict], run_date: str) -> dict:
    hmap = {h["i"]: h for h in headlines}
    events = []
    for k, ev in enumerate(raw.get("events", [])):
        try:
            if not (ev.get("title_ja") or "").strip():
                print(f"  ! event {k} skipped: empty title")
                continue
            ev["actor"]["country"] = _norm_country(ev["actor"]["country"])
            ev["location"]["country"] = _norm_country(ev["location"]["country"])
            ev["origin"]["country"] = _norm_country(ev["origin"]["country"])
            ev["counterparty"]["country"] = _norm_country(ev["counterparty"]["country"])
            if not (-90 <= ev["location"]["lat"] <= 90 and -180 <= ev["location"]["lon"] <= 180):
                continue
            ev["cross_border"] = bool(ev["origin"]["country"] and ev["origin"]["country"] != ev["location"]["country"])
            ev["sources"] = [{"title": hmap[i]["title"], "source": hmap[i]["source"], "link": hmap[i]["link"]}
                             for i in ev.get("sources", []) if i in hmap][:6]
            ev["id"] = f"{run_date}-{k:03d}"
            ev["date"] = run_date
            events.append(weigh(ev))
        except (KeyError, TypeError) as e:
            print(f"  ! event {k} skipped: {e}")
    events.sort(key=lambda e: -e["weight"]["total"])
    return {"date": run_date, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": MODEL, "headline_count": len(headlines), "daily_note_ja": raw.get("daily_note_ja", ""),
            "events": events}


# ═══════════════════════════ 4. web/data.json ═══════════════════════════════════
def build_web_json() -> None:
    days = []
    for p in sorted(EVENTS_DIR.glob("*.json"))[-DAYS_IN_WEB:]:
        days.append(json.loads(p.read_text(encoding="utf-8")))
    out = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action_labels": ACTION_LABELS, "themes": THEMES, "materiality": MATERIALITY, "stages": STAGES, "tags": TAGS,
        "lenses": {"money": "金額", "frontpage": "一面", "physical": "実物"},
        "lens_formula": {
            "money": "0.3×主体規模 + 0.3×資本量（絶対額） + 0.4×将来影響",
            "frontpage": "(0.2×主体規模 + 0.2×賭けの相対的大きさ + 0.3×将来影響 + 0.3×シグナル性) × 実物性係数 × 実現段階係数",
            "physical": "(0.2×主体規模 + 0.3×賭けの相対的大きさ + 0.5×将来影響) × 実物性係数（強） × 実現段階係数"},
        "weight_formula": "total = 0.3×主体規模(1-5) + 0.3×資本量(1-5, log10 USD−6) + 0.4×将来影響(1-5)",
        "days": days,
    }
    WEB_DATA.parent.mkdir(parents=True, exist_ok=True)
    WEB_DATA.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n = sum(len(d["events"]) for d in days)
    print(f"web/data.json: {len(days)} 日 / {n} イベント / {WEB_DATA.stat().st_size/1024:.0f} KB")


def recent_event_titles(before: str, ndays: int = 3) -> list[str]:
    titles = []
    d0 = date.fromisoformat(before)
    for p in sorted(EVENTS_DIR.glob("*.json")):
        d = date.fromisoformat(p.stem)
        if 0 < (d0 - d).days <= ndays:
            titles += [f"{e['actor']['name']}: {e['title_ja']}" for e in json.loads(p.read_text(encoding="utf-8"))["events"]]
    return titles


ENRICH_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["events"],
    "properties": {"events": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", *LENS_FIELDS],
        "properties": {"id": {"type": "string"}, **LENS_FIELDS}}}},
}


def enrich_day(run_date: str) -> None:
    """保存済みの data/events/<date>.json に一面レンズ用フィールドを追記し、重みを再計算する（再抽出より安価）。"""
    import anthropic

    path = EVENTS_DIR / f"{run_date}.json"
    day = json.loads(path.read_text(encoding="utf-8"))
    todo = [e for e in day["events"] if "signal" not in e]
    if not todo:
        print(f"  {run_date}: 追記済み"); return
    client = anthropic.Anthropic()
    lines = "\n".join(
        f"[{e['id']}] {e['actor']['name']}（{e['actor']['scale_note']}）｜{ACTION_LABELS[e['action_type']]}｜{e['title_ja']}｜{e['summary_ja']}"
        f"｜金額 {e['amount_text'] or e['amount_usd'] or '不明'}｜影響{e['impact']['score']}: {e['impact']['rationale_ja']}" for e in todo)
    user = f"""次の企業活動イベント（{run_date}）それぞれに、一面レンズ用のフィールドを付けてください。id は必ずそのまま返すこと。

{lines}"""
    kwargs = dict(model=MODEL, max_tokens=64000, system=SYSTEM_PROMPT, messages=[{"role": "user", "content": user}],
                  output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ENRICH_SCHEMA}})
    t0 = time.time()
    with client.messages.stream(**kwargs) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"モデルが応答を拒否しました: {getattr(msg, 'stop_details', None)}")
    got = {x["id"]: x for x in json.loads(next(b.text for b in msg.content if b.type == "text"))["events"]}
    u = msg.usage
    print(f"  Claude: {time.time()-t0:.0f}s  in={u.input_tokens} out={u.output_tokens}  {len(got)}/{len(todo)} 件に追記")
    for e in day["events"]:
        if e["id"] in got:
            e.update({k: v for k, v in got[e["id"]].items() if k != "id"})
        weigh(e)
    day["events"].sort(key=lambda e: -e["weight"]["lens"]["frontpage"])
    path.write_text(json.dumps(day, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--reuse", action="store_true", help="保存済みの見出し (data/raw/<date>.json) を再利用して取得を省略")
    ap.add_argument("--enrich", action="store_true", help="保存済みイベントに一面レンズ用フィールドを追記して重みを再計算（再抽出なし）")
    a = ap.parse_args()
    for d in (RAW_DIR, EVENTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    if a.json:
        build_web_json()
        return
    if a.enrich:
        print(f"== {a.date}: 一面レンズ用フィールドを追記")
        enrich_day(a.date)
        build_web_json()
        return

    raw_path = RAW_DIR / f"{a.date}.json"
    if a.reuse and raw_path.exists():
        headlines = json.loads(raw_path.read_text(encoding="utf-8"))
        print(f"== {a.date}: 保存済みの見出し {len(headlines)} 件を再利用")
    else:
        print(f"== {a.date}: 見出しを収集")
        headlines = fetch_headlines()
        raw_path.write_text(json.dumps(headlines, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"  合計 {len(headlines)} 件（重複除去後）")
    if a.fetch_only:
        return

    print("== Claude で企業活動イベントを抽出")
    raw = extract_events(headlines, recent_event_titles(a.date), a.date)
    day = finalize(raw, headlines, a.date)
    (EVENTS_DIR / f"{a.date}.json").write_text(json.dumps(day, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {len(day['events'])} イベント保存 → data/events/{a.date}.json")
    for e in day["events"][:10]:
        print(f"   {e['weight']['total']:.2f} [{ACTION_LABELS[e['action_type']]}] {e['actor']['name']} — {e['title_ja']}")
    build_web_json()


if __name__ == "__main__":
    main()
