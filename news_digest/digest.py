#!/usr/bin/env python3
"""
Daily News Digest
Fetches RSS feeds twice daily, selects key articles with Claude, sends HTML email.

Usage:
  python digest.py           # Start scheduler (07:00 and 21:00 JST)
  python digest.py --now     # Send immediately (for testing)
  python digest.py --morning # Force morning edition
  python digest.py --evening # Force evening edition
"""

import feedparser
import anthropic
import smtplib
import imaplib
import email as emaillib
import html as htmllib
import json
import os
import re
import sys
import schedule
import time
import hashlib
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email import encoders
from email.header import decode_header, make_header
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

# Fix console encoding on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    import truststore
    truststore.inject_into_ssl()

# ── Configuration ──────────────────────────────────────────────────────────────

SENDER_EMAIL     = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASS   = os.environ.get("GMAIL_APP_PASSWORD", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY   = os.environ.get("OPENAI_API_KEY", "")  # 音声読み上げ用（無ければ音声添付をスキップ）

# 配信は「自己完結アカウント」方式：各アドレスが自分自身へ配信し、返信も自分で受ける。
# 英語日記アプリと同じ設計。2人目（dunsri）はアプリパスワードが設定されている時だけ有効。
_DUNSRI_ADDRESS = os.environ.get("DUNSRI_ADDRESS", "")
DELIVERY_ACCOUNTS = [(SENDER_EMAIL, GMAIL_APP_PASS)]           # (アドレス, アプリパスワード)
if os.environ.get("DUNSRI_GMAIL_APP_PASSWORD"):
    DELIVERY_ACCOUNTS.append((_DUNSRI_ADDRESS, os.environ["DUNSRI_GMAIL_APP_PASSWORD"]))

# 世界地図（world-maps リポジトリが GitHub Pages に自動公開）— 本紙に要約＋PNG＋リンクを載せる
MAPS_BASE_URL = "https://kohkidoguchi.github.io/world-maps"
DIGEST_MAPS   = ["geo", "corp"]   # 月・木の本紙に載せる地図（地政学・企業活動）

RECIPIENT_EMAILS = [addr for addr, _ in DELIVERY_ACCOUNTS]     # 表示・フィルタ用アドレス一覧
SEND_ACCOUNTS    = {addr: pw for addr, pw in DELIVERY_ACCOUNTS}  # 返信チェック対象アカウント

# ── Text-to-Speech (音声読み上げ) ───────────────────────────────────────────────
TTS_MODEL = "tts-1"   # OpenAI TTS。より高品質なら "tts-1-hd"
TTS_VOICE = "nova"    # alloy / echo / fable / onyx / nova / shimmer
TTS_FORMAT = "aac"    # aac（小容量・iOS/Mac再生可） / mp3（最も汎用だが大きい）
TTS_CHAR_LIMIT = 3800 # 1リクエストあたりの文字数上限（APIは4096字まで）
# 形式 → (拡張子, MIMEサブタイプ)。ADTS/mp3はフレーム自己完結なのでバイト連結で再生可。
_TTS_EXT = {"mp3": ("mp3", "mpeg"), "aac": ("aac", "aac"), "wav": ("wav", "x-wav")}

CACHE_FILE           = Path(__file__).parent / "seen_articles.json"
RECENT_TOPICS_FILE   = Path(__file__).parent / "recent_topics.json"
RECENT_TOPICS_KEEP   = 6   # 直近何号分のテーマを記憶するか（連日の焼き直しを防ぐ）
REPLY_LOG_FILE       = Path(__file__).parent / "reply_log.json"  # 返信で回答済みのMessage-ID
REPLY_LOG_KEEP       = 500 # 処理済みID保持数（古いものから破棄）
MAX_REPLIES_PER_RUN  = 5   # 1回の実行で回答する返信の上限（ジョブ時間の暴走防止）
DRY_RUN = "--dry-run" in sys.argv   # 送信せずHTMLをファイルに書き出す（検証用）
MAX_PER_FEED         = 5    # articles fetched per feed
MAX_TO_CLAUDE        = 120  # cap sent to Claude（全カテゴリを含める）
ARTICLES_IN_DIGEST   = 10   # 取り上げるニュース件数（半分の長さの解説＋星の影響度つき）

# ── RSS Feeds ──────────────────────────────────────────────────────────────────

RSS_FEEDS = {
    "国際政治・地政学": [
        ("Foreign Affairs",     "https://www.foreignaffairs.com/rss.xml"),
        ("The Diplomat",        "https://thediplomat.com/feed/"),
        ("Foreign Policy",      "https://foreignpolicy.com/feed/"),
        ("CFR",                 "https://www.cfr.org/rss/region/all"),
        ("Politico",            "https://www.politico.com/rss/politicopicks.xml"),
        ("Reuters World",       "https://feeds.reuters.com/reuters/worldNews"),
    ],
    "マクロ経済・金融": [
        ("The Economist",       "https://www.economist.com/finance-and-economics/rss.xml"),
        ("Reuters Business",    "https://feeds.reuters.com/reuters/businessNews"),
        ("IMF Blog",            "https://www.imf.org/en/News/rss?language=eng"),
        ("Project Syndicate",   "https://www.project-syndicate.org/rss"),
        ("Nikkei Asia",         "https://asia.nikkei.com/rss/feed/nar"),
    ],
    "ビジネス・経済（世界）": [
        ("Harvard Business Review", "https://feeds.hbr.org/harvardbusiness"),
        ("MIT Tech Review",     "https://www.technologyreview.com/feed/"),
        ("Axios",               "https://api.axios.com/feed/"),
        ("The Atlantic",        "https://www.theatlantic.com/feed/all/"),
    ],
    "ビジネス・経済（国内）": [
        ("NHK経済",             "https://www3.nhk.or.jp/rss/news/cat6.xml"),
        ("東洋経済オンライン",  "https://toyokeizai.net/list/feed/rss"),
        ("日経ビジネス",        "https://business.nikkei.com/rss/sns/nb.rdf"),
        ("ITmedia",             "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml"),
        ("朝日新聞経済",        "https://www.asahi.com/rss/asahi/business.rdf"),
    ],
    "芸術・哲学・科学・宗教": [
        ("Aeon",                "https://aeon.co/feed.rss"),
        ("The Marginalian",     "https://www.themarginalian.org/feed/"),
        ("Arts & Letters Daily","https://aldaily.com/feed/"),
        ("Lion's Roar",         "https://www.lionsroar.com/feed/"),
        ("Tricycle",            "https://tricycle.org/feed/"),
        ("The New Yorker",      "https://www.newyorker.com/feed/everything"),
    ],
}

# 朝刊・夜刊の配分
# ワクワク系週次メール用 RSS
FUTURE_FEEDS = {
    "テクノロジー・発見": [
        ("Wired",               "https://www.wired.com/feed/rss"),
        ("MIT Tech Review",     "https://www.technologyreview.com/feed/"),
        ("Singularity Hub",     "https://singularityhub.com/feed/"),
        ("Nature News",         "https://www.nature.com/nature.rss"),
        ("Science Daily",       "https://www.sciencedaily.com/rss/all.xml"),
    ],
    "スタートアップ・事業": [
        ("a16z",                "https://a16z.com/feed/"),
        ("First Round Review",  "https://review.firstround.com/rss"),
        ("Y Combinator Blog",   "https://www.ycombinator.com/blog/rss"),
        ("Fast Company",        "https://www.fastcompany.com/latest/rss"),
        ("TED Blog",            "https://blog.ted.com/feed/"),
    ],
    "思想・未来予測": [
        ("Aeon",                "https://aeon.co/feed.rss"),
        ("The Long Now",        "https://longnow.org/seminars/rss/"),
        ("Edge",                "https://www.edge.org/rss"),
        ("Benedict Evans",      "https://www.ben-evans.com/benedictevans/rss.xml"),
    ],
}

FUTURE_DIGEST_COUNT = 5  # 週次メールの記事数

MORNING_SLOTS = [
    ("国際政治・地政学",      1),
    ("マクロ経済・金融",      2),
    ("ビジネス・経済（世界）", 4),
    ("ビジネス・経済（国内）", 3),
]

EVENING_SLOTS = [
    ("国際政治・地政学",      1),
    ("マクロ経済・金融",      2),
    ("ビジネス・経済（世界）", 3),
    ("ビジネス・経済（国内）", 3),
    ("芸術・哲学・科学・宗教", 1),
]

# 読者プロフィール。公開リポジトリで動かす際は USER_PROFILE 環境変数（GitHub Secret）から読む。
_DEFAULT_PROFILE = ""   # 公開用：プロフィールは USER_PROFILE 環境変数（Secret）で渡す
USER_PROFILE = os.environ.get("USER_PROFILE") or _DEFAULT_PROFILE

# ── Cache (deduplication across AM/PM editions) ────────────────────────────────

def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_cache(data: dict):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    fresh = {k: v for k, v in data.items() if v > cutoff}
    CACHE_FILE.write_text(json.dumps(fresh, ensure_ascii=False, indent=2), encoding="utf-8")

def _load_recent_topics() -> list:
    """直近の号で取り上げたテーマ（タイトル・見出し語）の履歴を読む。"""
    if RECENT_TOPICS_FILE.exists():
        try:
            return json.loads(RECENT_TOPICS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def _save_recent_topics(entries: list):
    trimmed = entries[-RECENT_TOPICS_KEEP:]
    RECENT_TOPICS_FILE.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")

def _load_reply_log() -> list:
    """既に回答した返信メールのMessage-ID一覧（重複回答を防ぐ）。"""
    if REPLY_LOG_FILE.exists():
        try:
            return json.loads(REPLY_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def _save_reply_log(ids: list):
    trimmed = ids[-REPLY_LOG_KEEP:]
    REPLY_LOG_FILE.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")

def _normalize_url(url: str) -> str:
    """クエリパラメータ・フラグメント・末尾スラッシュを除去して正規化"""
    try:
        p = urlparse(url)
        normalized = urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", "", ""))
        return normalized
    except Exception:
        return url

def _normalize_title(title: str) -> str:
    """タイトルを正規化してハッシュキーを生成"""
    normalized = re.sub(r"[^\w\s]", "", title.lower().strip())[:80]
    return normalized

def _article_id(url: str) -> str:
    return hashlib.sha256(_normalize_url(url).encode()).hexdigest()[:16]

def _title_id(title: str) -> str:
    return "t:" + hashlib.sha256(_normalize_title(title).encode()).hexdigest()[:16]

# ── RSS Fetching ───────────────────────────────────────────────────────────────

def fetch_articles() -> list[dict]:
    articles = []
    seen_urls: set[str] = set()  # deduplicate within the same batch
    for category, feeds in RSS_FEEDS.items():
        for source_name, url in feeds:
            try:
                feed = feedparser.parse(url, request_headers={"User-Agent": "NewsDigest/1.0"})
                for entry in feed.entries[:MAX_PER_FEED]:
                    entry_url = entry.get("link", "")
                    norm_url = _normalize_url(entry_url)
                    if not entry_url or norm_url in seen_urls:
                        continue
                    seen_urls.add(norm_url)
                    raw_summary = entry.get("summary", entry.get("description", ""))
                    clean_summary = re.sub(r"<[^>]+>", "", raw_summary)[:600]
                    articles.append({
                        "category": category,
                        "source":   source_name,
                        "title":    entry.get("title", "").strip(),
                        "url":      entry_url,
                        "summary":  clean_summary.strip(),
                        "published": entry.get("published", ""),
                    })
            except Exception as e:
                print(f"  [WARN] {source_name}: {e}")
    return articles

# ── Market Data (定点観測) ──────────────────────────────────────────────────────

# (ticker, display_name, unit, category, change_mode)
# change_mode "pp": show raw point change (for yields/rates)
# change_mode "pct": show % change
MARKET_SPECS = [
    # 経済・資本
    ("^TNX",     "米10年債利回り",  "%",       "経済・資本",   "pp"),
    ("^GSPC",    "S&P 500",         "pt",      "経済・資本",   "pct"),
    ("^N225",    "日経225",          "pt",      "経済・資本",   "pct"),
    ("JPY=X",    "ドル円",           "¥",       "経済・資本",   "pct"),
    # 地政学
    ("DX-Y.NYB", "ドル指数 (DXY)",  "pt",      "地政学",      "pct"),
    ("BZ=F",     "原油 (Brent)",    "$/bbl",   "地政学",      "pct"),
    ("NG=F",     "天然ガス",         "$/MMBtu", "地政学",      "pct"),
    ("GC=F",     "金",               "$/oz",    "地政学",      "pct"),
    # 技術
    ("^SOX",     "SOX半導体指数",    "pt",      "技術",        "pct"),
    ("^IXIC",    "ナスダック総合",   "pt",      "技術",        "pct"),
    # 国家・リスク（政策/地政学の不確実性を市場が織り込む度合い）
    ("^VIX",     "VIX (株式の不安)", "pt",      "国家・リスク", "pct"),
    ("^MOVE",    "MOVE (債券の不安)","pt",      "国家・リスク", "pct"),
]

# ── 構造統計（人口など年次更新の参照値）─────────────────────────────────────────
# 公式統計の年次リリース時に手動更新する。ライブ取得しない（年1回しか動かないため）。
# 各要素: {"category", "name", "value", "prev", "asof", "source"}
STRUCTURAL_STATS = [
    {"category": "人口・動態", "name": "世界人口", "value": "81.6億人", "prev": "80.9億人",
     "asof": "2024", "source": "国連"},
    {"category": "人口・動態", "name": "日本 合計特殊出生率", "value": "1.20", "prev": "1.26",
     "asof": "2023", "source": "厚労省"},
    {"category": "人口・動態", "name": "米 合計特殊出生率", "value": "1.62", "prev": "1.67",
     "asof": "2023", "source": "CDC"},
    {"category": "人口・動態", "name": "米 純国際移民", "value": "+280万人", "prev": "+230万人",
     "asof": "FY2024", "source": "Census(推計)"},
    {"category": "人口・動態", "name": "EU 純移民", "value": "+170万人", "prev": "+270万人",
     "asof": "2023", "source": "Eurostat(推計)"},
]

def fetch_market_data() -> dict:
    """Fetch live market data from Yahoo Finance API using requests."""
    import requests as _req
    from urllib.parse import quote as _q

    def _get_closes(ticker: str) -> list[float]:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{_q(ticker, safe='')}"
            f"?interval=1d&range=13mo"
        )
        r = _req.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        return [c for c in closes if c is not None]

    data = {}
    for sym, name, unit, category, change_mode in MARKET_SPECS:
        try:
            closes = _get_closes(sym)
            if len(closes) < 5:
                continue

            current = closes[-1]
            prev = closes[-2]                                   # 前営業日
            m1 = closes[-22] if len(closes) > 22 else closes[0]
            y1 = closes[-252] if len(closes) > 252 else closes[0]

            if change_mode == "pp":
                ch_1d = round(current - prev, 2)
                ch_1m = round(current - m1, 2)
                ch_1y = round(current - y1, 2)
                ch_unit = "pp"
            else:
                ch_1d = round((current - prev) / abs(prev) * 100, 1) if prev else 0.0
                ch_1m = round((current - m1) / abs(m1) * 100, 1) if m1 else 0.0
                ch_1y = round((current - y1) / abs(y1) * 100, 1) if y1 else 0.0
                ch_unit = "%"

            # 負のゼロ(-0.0)を正規化して "+-0.0" のような表示を防ぐ
            ch_1d = ch_1d if ch_1d != 0 else 0.0
            ch_1m = ch_1m if ch_1m != 0 else 0.0
            ch_1y = ch_1y if ch_1y != 0 else 0.0

            if sym == "^TNX":
                cur_fmt = f"{current:.2f}%"
            elif sym in ("^GSPC", "^N225", "^SOX", "^IXIC"):
                cur_fmt = f"{current:,.0f}"
            elif sym == "JPY=X":
                cur_fmt = f"¥{current:.2f}"
            elif sym in ("CL=F", "BZ=F"):
                cur_fmt = f"${current:.1f}"
            elif sym == "NG=F":
                cur_fmt = f"${current:.2f}"
            elif sym == "GC=F":
                cur_fmt = f"${current:,.0f}"
            elif sym in ("^VIX", "^MOVE"):
                cur_fmt = f"{current:.1f}"
            else:
                cur_fmt = f"{current:.2f}"

            data[sym] = {
                "name": name,
                "unit": unit,
                "category": category,
                "current": current,
                "current_fmt": cur_fmt,
                "ch_1d": ch_1d,
                "ch_1m": ch_1m,
                "ch_1y": ch_1y,
                "ch_unit": ch_unit,
            }
        except Exception as e:
            print(f"  [WARN] Market {sym}: {e}")

    return data


def _format_market_for_prompt(market_data: dict) -> str:
    lines = []
    current_cat = None
    if market_data:
        for sym, d in market_data.items():
            if d["category"] != current_cat:
                current_cat = d["category"]
                lines.append(f"\n{current_cat}:")
            s1d = "+" if d["ch_1d"] >= 0 else ""
            s1m = "+" if d["ch_1m"] >= 0 else ""
            s1y = "+" if d["ch_1y"] >= 0 else ""
            lines.append(
                f"  {d['name']}: {d['current_fmt']} "
                f"(前日 {s1d}{d['ch_1d']}{d['ch_unit']}, "
                f"1ヶ月 {s1m}{d['ch_1m']}{d['ch_unit']}, "
                f"1年 {s1y}{d['ch_1y']}{d['ch_unit']})"
            )
    else:
        lines.append("(市場データ取得不可)")

    # 構造統計（年次）
    current_cat = None
    for s in STRUCTURAL_STATS:
        if s["category"] != current_cat:
            current_cat = s["category"]
            lines.append(f"\n{current_cat}（年次統計）:")
        lines.append(
            f"  {s['name']}: {s['value']} "
            f"(前年 {s['prev']}, {s['asof']}, {s['source']})"
        )
    return "\n".join(lines)


def _build_indicators_html(market_data: dict, indicators_analysis: str) -> str:
    """Build the 定点観測 panel with live data table + Claude's analysis."""
    has_table = bool(market_data) or bool(STRUCTURAL_STATS)
    if not has_table and not indicators_analysis:
        return ""

    # Fixed-width badge so the 前日 / 1ヶ月 / 1年 columns align across every row
    def badge(val: float, unit: str) -> str:
        positive = val >= 0
        color  = "#16a34a" if positive else "#dc2626"
        bg     = "#dcfce7" if positive else "#fee2e2"
        sign   = "+" if positive else ""
        return (
            f'<span style="display:inline-block;min-width:48px;text-align:center;'
            f'background:{bg};color:{color};font-size:10px;'
            f'padding:2px 0;border-radius:3px;font-weight:700;'
            f'white-space:nowrap;">{sign}{val}{unit}</span>'
        )

    def cat_header(cat: str) -> str:
        return f"""
        <tr>
          <td colspan="5"
              style="padding:8px 14px 4px;font-size:10px;font-weight:700;
                     color:#7c86b5;letter-spacing:2px;text-transform:uppercase;
                     background:#1a1a2e;border-bottom:1px solid #2d2d4e;">
            {cat}
          </td>
        </tr>"""

    name_td = 'padding:8px 6px 8px 14px;font-size:13px;color:#374151;'
    val_td  = 'padding:8px 6px;font-size:14px;font-weight:700;color:#1a1a2e;text-align:right;white-space:nowrap;'
    chg_td  = 'padding:8px 3px;text-align:center;white-space:nowrap;'

    # ── Tier A: market data, grouped by category ──
    categories: dict[str, list] = {}
    for sym, d in market_data.items():
        categories.setdefault(d["category"], []).append(d)

    rows_html = ""
    for cat, items in categories.items():
        rows_html += cat_header(cat)
        for d in items:
            rows_html += f"""
        <tr style="border-bottom:1px solid #f1f5f9;">
          <td style="{name_td}">{d['name']}</td>
          <td style="{val_td}">{d['current_fmt']}</td>
          <td style="{chg_td}">{badge(d['ch_1d'], d['ch_unit'])}</td>
          <td style="{chg_td}">{badge(d['ch_1m'], d['ch_unit'])}</td>
          <td style="{chg_td}padding-right:14px;">{badge(d['ch_1y'], d['ch_unit'])}</td>
        </tr>"""

    # ── Tier B: structural (annual) stats ──
    struct_cats: dict[str, list] = {}
    for s in STRUCTURAL_STATS:
        struct_cats.setdefault(s["category"], []).append(s)

    for cat, items in struct_cats.items():
        rows_html += cat_header(cat)
        for s in items:
            rows_html += f"""
        <tr style="border-bottom:1px solid #f1f5f9;">
          <td style="{name_td}">
            {s['name']}
            <span style="font-size:10px;color:#9ca3af;">（{s['asof']}・{s['source']}）</span>
          </td>
          <td style="{val_td}">{s['value']}</td>
          <td colspan="3" style="padding:8px 14px 8px 4px;text-align:right;
                     white-space:nowrap;font-size:11px;color:#9ca3af;">
            前年 {s['prev']}
          </td>
        </tr>"""

    legend_html = """
    <tr>
      <td colspan="5"
          style="padding:4px 14px 6px;font-size:10px;color:#9ca3af;text-align:right;">
        年次統計（前年比）。出典・年度は各行に表示。
      </td>
    </tr>""" if has_table else ""

    analysis_html = ""
    if indicators_analysis:
        analysis_html = f"""
  <div style="background:#eef2ff;border-left:4px solid #4361ee;
              padding:16px 20px;border-radius:0 0 8px 8px;margin-bottom:4px;">
    <div style="font-size:10px;font-weight:700;color:#4361ee;
                letter-spacing:2px;text-transform:uppercase;margin-bottom:8px;">
      なぜ動いたか
    </div>
    <p style="margin:0;font-size:13px;color:#333;line-height:1.9;white-space:pre-line;">
      {indicators_analysis}
    </p>
  </div>"""

    table_html = ""
    if has_table:
        table_html = f"""
    <table style="width:100%;border-collapse:collapse;table-layout:fixed;
                  box-shadow:0 1px 5px rgba(0,0,0,0.1);border-radius:8px 8px 0 0;
                  overflow:hidden;">
      <colgroup>
        <col style="width:32%;">
        <col style="width:20%;">
        <col style="width:16%;">
        <col style="width:16%;">
        <col style="width:16%;">
      </colgroup>
      <thead>
        <tr style="background:#0f0f23;">
          <th colspan="2"
              style="padding:10px 14px;font-size:10px;font-weight:700;
                     color:#4361ee;letter-spacing:3px;text-transform:uppercase;
                     text-align:left;">
            定点観測 ── 人口・動態
          </th>
          <th colspan="3" style="padding:10px 14px 10px 3px;font-size:9px;font-weight:600;
                     color:#7c86b5;text-align:right;">前年</th>
        </tr>
      </thead>
      <tbody style="background:white;">
        {rows_html}
        {legend_html}
      </tbody>
    </table>"""

    return f"""
  <div style="margin-bottom:28px;">
    {table_html}
    {analysis_html}
  </div>"""

# ── World maps (GitHub Pages) ──────────────────────────────────────────────────

MAP_TITLES = {"geo": "世界の政治・地政学マップ", "corp": "世界の企業活動マップ"}

def fetch_map_summaries(keys: list | None = None) -> dict:
    """Pages に公開された各地図の summary.json と map.png を取得。失敗した地図は黙って省く。"""
    import requests as _req
    out = {}
    for k in (keys or DIGEST_MAPS):
        try:
            r = _req.get(f"{MAPS_BASE_URL}/{k}/summary.json", timeout=20)
            r.raise_for_status()
            item = {"summary": r.json(), "url": f"{MAPS_BASE_URL}/{k}/", "png": None}
            try:
                pr = _req.get(f"{MAPS_BASE_URL}/{k}/map.png", timeout=30)
                pr.raise_for_status()
                if pr.headers.get("content-type", "").startswith("image/"):
                    item["png"] = pr.content
            except Exception as e:
                print(f"  [WARN] map png {k}: {e}")
            out[k] = item
        except Exception as e:
            print(f"  [WARN] map summary {k}: {e}")
    return out

def _format_maps_for_prompt(maps: dict) -> str:
    if not maps:
        return ""
    lines = ["\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
             "【世界地図（自動生成）が示していること — 記事と突き合わせて読むための材料】"]
    g = (maps.get("geo") or {}).get("summary") or {}
    if g:
        lines.append(f"\n■ 政治・地政学マップ（GDELT等, 生成 {g.get('generated', '')}）")
        for e in g.get("top_events", [])[:10]:
            a1, a2 = e.get("a1"), e.get("a2")
            arrow = f"{a1}→{a2}" if a2 and a2 != a1 else f"{a1}"
            lines.append(f"  - [{e.get('label', '')}] {e.get('title', '')}（{arrow}／{e.get('place', '')}／情報源{e.get('sources')}）")
        if g.get("top_countries"):
            cs = ", ".join(f"{c.get('name')}(注目度×{c.get('attention_ratio')})" for c in g["top_countries"][:8])
            lines.append(f"  注目が集まる国（28日平均比）: {cs}")
        if g.get("markets"):
            names = [str(m.get("title") or m.get("question") or m)[:60] for m in g["markets"][:5]]
            lines.append("  予測市場（Polymarket）: " + " / ".join(names))
    c = (maps.get("corp") or {}).get("summary") or {}
    if c:
        lines.append(f"\n■ 企業活動マップ（{c.get('date', '')}, 見出し{c.get('headline_count')}件→イベント{c.get('event_count')}件）")
        if c.get("daily_note"):
            lines.append(f"  日次総評: {c['daily_note']}")
        for e in c.get("top_events", [])[:10]:
            lines.append(f"  - {e.get('title', '')}｜{e.get('actor', '')}／{e.get('action', '')}／{e.get('theme', '')}／{e.get('country', '')}／{e.get('amount') or ''}／影響{e.get('impact')}")
        if c.get("by_action"):
            lines.append("  種類別件数: " + ", ".join(f"{k}{v}" for k, v in list(c["by_action"].items())[:6]))
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    return "\n".join(lines)

def _build_maps_html(maps: dict, reading: str) -> str:
    if not maps:
        return ""
    cards = ""
    for k in DIGEST_MAPS:
        m = maps.get(k)
        if not m:
            continue
        sm = m.get("summary") or {}
        gen = (sm.get("generated") or sm.get("date") or "")[:16].replace("T", " ")
        url = m["url"]
        img = ""
        if m.get("png"):
            img = (f'<a href="{url}"><img src="cid:map_{k}" alt="{MAP_TITLES.get(k, k)}" '
                   f'style="width:100%;display:block;border-radius:6px;border:1px solid #e5e7eb;"></a>')
        tops = "".join(f'<li style="margin:2px 0;">{htmllib.escape(str(e.get("title", "")))}</li>'
                       for e in sm.get("top_events", [])[:3])
        cards += f"""
        <div style="margin:0 0 14px;">
          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;">
            <span style="font-size:13px;font-weight:700;color:#1a1a2e;">{MAP_TITLES.get(k, k)}</span>
            <span style="font-size:10px;color:#9ca3af;">{gen}</span>
          </div>
          {img}
          <ul style="margin:8px 0 4px;padding-left:18px;font-size:12px;color:#555;line-height:1.6;">{tops}</ul>
          <a href="{url}" style="font-size:12px;color:#4361ee;text-decoration:none;">対話地図を開く →</a>
        </div>"""
    reading_html = ""
    if reading:
        reading_html = f'<p style="margin:12px 0 0;font-size:13px;color:#333;line-height:1.9;white-space:pre-line;">{reading}</p>'
    return f"""
  <div style="background:white;margin:0 0 20px;border-radius:10px;padding:18px 24px;
              box-shadow:0 1px 5px rgba(0,0,0,0.07);">
    <div style="font-size:10px;font-weight:700;color:#4361ee;letter-spacing:2px;
                text-transform:uppercase;margin-bottom:12px;">🗺 地図から読む</div>
    {cards}
    {reading_html}
  </div>"""

# ── Nowcast（資産クラス別の評価額変動）と重要論文（world-maps / GitHub Pages）────────

def fetch_side_summaries() -> dict:
    """capital（Nowcast: summary.json + sheet.png）と research（重要論文: summary.json）を取得。"""
    import requests as _req
    out = {}
    for k, png_name in (("capital", "sheet.png"), ("research", None)):
        try:
            r = _req.get(f"{MAPS_BASE_URL}/{k}/summary.json", timeout=20)
            r.raise_for_status()
            item = {"summary": r.json(), "url": f"{MAPS_BASE_URL}/{k}/", "png": None}
            if png_name:
                try:
                    pr = _req.get(f"{MAPS_BASE_URL}/{k}/{png_name}", timeout=30)
                    pr.raise_for_status()
                    if pr.headers.get("content-type", "").startswith("image/"):
                        item["png"] = pr.content
                except Exception as e:
                    print(f"  [WARN] {k} png: {e}")
            out[k] = item
        except Exception as e:
            print(f"  [WARN] {k} summary: {e}")
    return out

def _pct(v, d=1):
    return "–" if v is None else f"{v*100:+.{d}f}%"

def _bn(v):
    """USD 10億 → 兆ドル/億ドル表記"""
    if v is None:
        return "–"
    if abs(v) >= 1000:
        return f"{v/1000:+.2f}兆ドル"
    return f"{v*10:+,.0f}億ドル"

def _format_nowcast_for_prompt(cap) -> str:
    nc = (cap or {}).get("summary") or {}
    if not nc or not nc.get("indices"):
        return ""
    names = nc.get("region_names", {})
    lines = ["\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
             f"【資産クラス別の評価額変動 Nowcast — 統計時点 {nc.get('stat_period_ja','')}末（{nc.get('from','')}）以降の値動き、保有主体＝全部門】",
             "■ 地域別の価格の動き（統計時点末→直近）"]
    for r in nc["indices"]:
        idx = "・".join(f"{i.get('label')} {i.get('level'):,.0f}({str(i.get('date',''))[5:]})"
                        for i in r.get("indices", []) if i.get("level"))
        y = r.get("yield10_level"); dy = r.get("yield10_chg_pp") or 0
        ystr = f"10年利回り {y:.2f}%（{'+' if dy >= 0 else ''}{dy*100:.0f}bp）" if y is not None else "利回り –"
        hp = f"；住宅 {_pct(r.get('hpi_since_stat'))}" if r.get("hpi_since_stat") is not None else ""
        lines.append(f"  - {r.get('name')}: 株価 {_pct(r.get('eq_since_stat'))}（年初来 {_pct(r.get('eq_ytd'))}／12か月 {_pct(r.get('eq_12m'))}）{idx}；{ystr}；対ドル {_pct(r.get('fx_vs_usd_since_stat'))}{hp}")
    g = nc.get("gold")
    if g:
        lines.append(f"  - 金: {g.get('level'):,.0f}ドル/oz（統計後 {_pct(g.get('since_stat'))}、年初来 {_pct(g.get('ytd'))}）")
    be = nc.get("bond_etf") or {}; re_ = nc.get("reit_etf") or {}
    lines.append(f"  - 米債券ETF {_pct(be.get('since_stat'))}／米REIT {_pct(re_.get('since_stat'))}（統計後）")
    lines.append("■ 資産クラス別の評価変動（統計残高 × 値動き、USD、全部門）")
    for c, v in (nc.get("valuation") or {}).items():
        cells = "、".join(f"{names.get(r, r)} {_bn(x.get('usd_bn'))}({_pct(x.get('pct'))})"
                          for r, x in (v.get("by_region") or {}).items())
        lines.append(f"  - {v.get('label')}: {cells}")
    tot = nc.get("totals") or {}
    lines.append("  - 合計: " + "、".join(f"{names.get(r, r)} {_bn(x.get('usd_bn'))}({_pct(x.get('pct'))})" for r, x in tot.items()))
    fx = nc.get("fx_effect") or {}
    lines.append("  - 参考・為替による期首残高のドル換算変化: " + "、".join(f"{names.get(r, r)} {_pct(x.get('pct'))}" for r, x in fx.items() if x))
    lines.append("（前提: " + (nc.get("assumptions") or "")[:160] + "）")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    return "\n".join(lines)

def _format_papers_for_prompt(res) -> str:
    sm = (res or {}).get("summary") or {}
    papers = sm.get("top_papers") or []
    if not papers:
        return ""
    lines = ["\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
             "【直近30日の重要論文（OpenAlex、分野正規化被引用 FWCI 順・学術誌掲載のみ）】"]
    for i, p in enumerate(papers, 1):
        lines.append(f"  [P{i}] {p.get('title')}｜{p.get('field')}／{p.get('topic')}｜{p.get('source')}｜{p.get('first_author')}（{p.get('institution')}, {p.get('country')}）｜{p.get('date')}｜FWCI {p.get('fwci')}・被引用 {p.get('cited')}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    return "\n".join(lines)

def _pcell(v):
    if v is None:
        return '<td style="text-align:right;color:#9ca3af;">–</td>'
    col = "#6b4fa0" if v >= 0 else "#b5651d"
    return f'<td style="text-align:right;color:{col};font-weight:600;white-space:nowrap;">{_pct(v)}</td>'

def _build_nowcast_html(cap, reading: str) -> str:
    nc = (cap or {}).get("summary") or {}
    if not nc or not nc.get("indices"):
        return ""
    url = cap.get("url", "#")
    img = ""
    if cap.get("png"):
        img = (f'<a href="{url}"><img src="cid:sheet_capital" alt="評価額変動のNowcast" '
               f'style="width:100%;display:block;border-radius:6px;border:1px solid #e5e7eb;"></a>')
    tot = nc.get("totals") or {}
    dash = '<td style="text-align:right;color:#9ca3af;">–</td>'
    rows = ""
    for r in nc["indices"]:
        rid = r.get("region"); t = tot.get(rid) or {}
        y = r.get("yield10_level"); dy = r.get("yield10_chg_pp") or 0
        if y is not None:
            ycol = "#b5651d" if dy > 0 else "#6b4fa0"
            ystr = f"{y:.2f}% <span style='color:{ycol};font-size:10px;'>{'+' if dy >= 0 else ''}{dy*100:.0f}bp</span>"
        else:
            ystr = "–"
        if t:
            tcol = "#6b4fa0" if (t.get("usd_bn") or 0) >= 0 else "#b5651d"
            tstr = f"<span style='color:{tcol};'>{_bn(t.get('usd_bn'))}</span> <span style='color:#888;font-size:10px;'>({_pct(t.get('pct'))})</span>"
        else:
            tstr = "–"
        fxcell = dash if rid == "USA" else _pcell(r.get("fx_vs_usd_since_stat"))
        rows += (f'<tr style="border-bottom:1px solid #f1f5f9;">'
                 f'<td style="padding:6px 8px;font-weight:600;color:#1a1a2e;">{r.get("name")}</td>'
                 f'{_pcell(r.get("eq_since_stat"))}{_pcell(r.get("eq_ytd"))}'
                 f'<td style="text-align:right;white-space:nowrap;">{ystr}</td>{fxcell}'
                 f'<td style="text-align:right;white-space:nowrap;">{tstr}</td></tr>')
    g = nc.get("gold") or {}
    gold_row = ""
    if g:
        gold_row = (f'<tr><td style="padding:6px 8px;color:#666;">金（USD/oz）</td>{_pcell(g.get("since_stat"))}{_pcell(g.get("ytd"))}'
                    f'<td colspan="3" style="text-align:right;color:#888;font-size:11px;">{g.get("level"):,.0f}ドル（{g.get("date","")}）</td></tr>')
    table = f"""
      <table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:10px;">
        <thead><tr style="color:#7c86b5;font-size:10px;letter-spacing:1px;">
          <th style="text-align:left;padding:4px 8px;">地域</th><th>株価 統計後</th><th>年初来</th><th>10年利回り</th><th>対ドル</th><th>評価変動 合計</th>
        </tr></thead>
        <tbody>{rows}{gold_row}</tbody>
      </table>
      <div style="font-size:10px;color:#9ca3af;margin-top:6px;">統計時点＝{nc.get('stat_period_ja','')}末。評価変動＝統計残高（全部門）×その後の値動き、取引は含まない。上昇＝紫、下落・金利上昇＝橙。</div>"""
    reading_html = f'<p style="margin:12px 0 0;font-size:13px;color:#333;line-height:1.9;white-space:pre-line;">{reading}</p>' if reading else ""
    return f"""
  <div style="background:white;margin:0 0 20px;border-radius:0 0 10px 10px;padding:18px 24px;
              box-shadow:0 1px 5px rgba(0,0,0,0.07);border-left:4px solid #6b4fa0;">
    <div style="font-size:10px;font-weight:700;color:#6b4fa0;letter-spacing:2px;text-transform:uppercase;margin-bottom:4px;">
      統計の後で何が動いたか ── 資産クラス別の評価額変動 Nowcast
    </div>
    <div style="font-size:11px;color:#888;margin-bottom:10px;">資金循環統計（{nc.get('stat_period_ja','')}末）の残高 × 直近の株価・金利・為替　<a href="{url}" style="color:#4361ee;text-decoration:none;">対話版を開く →</a></div>
    {img}
    {table}
    {reading_html}
  </div>"""

def _build_papers_html(res, readings) -> str:
    sm = (res or {}).get("summary") or {}
    papers = sm.get("top_papers") or []
    if not papers:
        return ""
    rd = {str(x.get("id")): x for x in (readings or []) if isinstance(x, dict)}
    items = ""
    for i, p in enumerate(papers, 1):
        r = rd.get(f"P{i}") or rd.get(str(i)) or {}
        title_ja = r.get("title_ja") or p.get("title") or ""
        gist = r.get("gist") or ""
        meta = f"{p.get('field') or ''} · {p.get('source') or ''} · {p.get('institution') or ''}（{p.get('country') or ''}）· {p.get('date','')}"
        gist_html = f'<p style="margin:4px 0 0;font-size:12px;color:#555;line-height:1.7;">{htmllib.escape(gist)}</p>' if gist else ""
        items += f"""
        <div style="padding:10px 0;border-bottom:1px solid #eee;">
          <div style="font-size:10px;color:#999;margin-bottom:2px;">{htmllib.escape(meta)}</div>
          <a href="{p.get('url','#')}" style="font-size:14px;font-weight:600;color:#1a1a2e;text-decoration:none;line-height:1.4;">{htmllib.escape(str(title_ja))}</a>
          <div style="font-size:11px;color:#777;margin-top:2px;">{htmllib.escape(str(p.get('title') or ''))}</div>
          {gist_html}
          <div style="font-size:10px;color:#9ca3af;margin-top:3px;">FWCI {p.get('fwci')}（分野平均の{p.get('fwci')}倍の被引用）· 被引用 {p.get('cited')}</div>
        </div>"""
    url = (res or {}).get("url", "#")
    return f"""
  <div style="background:white;margin:8px 0 20px;border-radius:10px;padding:8px 24px 16px;
              box-shadow:0 1px 5px rgba(0,0,0,0.07);">
    <div style="font-size:11px;font-weight:700;color:#0f766e;letter-spacing:2px;text-transform:uppercase;padding:14px 0 4px;">
      📄 直近の重要論文
    </div>
    <div style="font-size:11px;color:#888;margin-bottom:4px;">直近30日・分野正規化被引用（FWCI）順・学術誌掲載のみ　<a href="{url}" style="color:#4361ee;text-decoration:none;">研究マップを開く →</a></div>
    {items}
  </div>"""

# ── Claude: select + summarize ─────────────────────────────────────────────────

def _build_prompt(articles: list[dict], session_label: str, edition: str, market_data: dict,
                  recent_topics: list | None = None, maps: dict | None = None,
                  side: dict | None = None) -> str:
    slots = MORNING_SLOTS if edition == "morning" else EVENING_SLOTS
    edition_role = (
        "【朝刊の役割】今日何が動くかを掴み、仕事・判断の材料を提供する。ビジネス・政治寄りの視点で。"
        if edition == "morning" else
        "【夕刊の役割】一日の終わりに世界の深層を読む。思索・知的探求寄りの視点で。"
    )

    market_block = _format_market_for_prompt(market_data)
    maps_block = _format_maps_for_prompt(maps or {})
    nowcast_block = _format_nowcast_for_prompt((side or {}).get('capital'))
    papers_block  = _format_papers_for_prompt((side or {}).get('research'))

    if edition == "morning":
        indicators_instruction = (
            "①定点観測の数値が動いた理由を、3つの時間軸を意識して読む：前日比（昨日〜今日の値動き）は"
            "今回の個別ニュースで起きた出来事に対応するはずなので、記事群と結びつけて『何がこの動きを生んだか』を説明する。"
            "1ヶ月・1年の変化は、その背後にある構造的な力（金融政策の方向感、地政学、需給など）として読む。"
            "②数値表だけでは見えない『国家：主要政策・外交動向』の質的な動きと、人口（出生率・移民）の長期トレンドが"
            "いま何を意味するかを、今回の記事群と統計から読み取る。"
            "③これらが束になって指し示す、社会・世界の深層レイヤーのトレンドや構造変化を締め括りとして。"
            "全体5〜8文・1〜2段落。物語を読むように自然に頭に入ってくる平易な言葉で。"
        )
    else:
        indicators_instruction = (
            "本日の定点観測の数値変化について、なぜそう動いたかを3〜5文で端的に分析する。"
            "特に前日比（昨日〜今日の値動き）は本日の個別ニュースで起きた出来事に対応するはずなので、記事群と結びつけて説明する。"
            "1ヶ月・1年の変化は構造的な力（金融政策の方向感、地政学的緊張、需給バランス、技術への資金流入、"
            "政策・外交の不確実性など）として読む。平易な言葉で。"
        )

    category_sections = ""
    cat_notes = {
        "ビジネス・経済（国内）": "※必ず日本国内の企業・産業・政策・経済に関する記事を選ぶこと（海外の話題は不可）"
    }
    for cat, count in slots:
        cat_articles = [a for a in articles if a["category"] == cat]
        if not cat_articles:
            continue
        note = cat_notes.get(cat, "")
        category_sections += f"\n\n### {cat}（この中から{count}件選ぶこと）{' ' + note if note else ''}\n"
        for a in cat_articles:
            category_sections += (
                f"\n[{articles.index(a)+1}] {a['source']}\n"
                f"Title: {a['title']}\n"
                f"Summary: {a['summary']}\n"
            )

    slot_summary = "、".join(f"{c}{n}件" for c, n in slots)

    recent_block = ""
    if recent_topics:
        lines = []
        for t in recent_topics:
            titles = " / ".join(t.get("titles", []))
            lines.append(f"- {t.get('date','')} {t.get('edition','')}：{titles}")
        recent_block = (
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "【直近の号で既に取り上げたテーマ（連日の焼き直しを避けるため）】\n"
            + "\n".join(lines)
            + "\n※ 上記と同じ主題・同じ切り口の記事は、重要な"
              "新展開・新事実がある場合を除き選ばないこと。読者が『また同じ話か』と感じる焼き直しを避ける。\n"
              "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )

    return f"""
{USER_PROFILE}
{edition_role}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【定点観測 — 最新値（取得日時: {session_label}）】
{market_block}

※ 上記は実データ。変化幅は1ヶ月前・1年前との比較。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{recent_block}{nowcast_block}{maps_block}{papers_block}
以下は{session_label}に取得した記事一覧です。カテゴリ別に整理しています。

{category_sections}

---

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【文体ルール（全ての日本語テキストに適用・最優先）】
このメールは音声でも読み上げられる。だから『耳で一度聞いただけで理解できる』ことを最優先にする。
- 一文を短く。目安40〜60字で区切り、一文に情報を詰め込みすぎない。
- 硬い漢語調・書き言葉ではなく、日常の話し言葉に近い自然な言い回しを選ぶ（例：「〜を余儀なくされた」→「〜せざるを得なくなった」、「示唆している」→「という見方が出ている／〜かもしれない」）。
- 専門用語やカタカナ語を使うときは、その直後に一言でやさしく言い換える（例：「利回り、つまりお金を貸したときの利息の割合が…」）。
- 目で見て初めて分かる書き方（記号の羅列、括弧の入れ子、「A/B」のスラッシュ）は避け、耳で追える語順にする。
- ただし、やさしくしても内容の深さ・分析の鋭さ・事実の正確さは一切落とさない。難しいことを、易しい言葉で、正確に伝える。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

各カテゴリから指定件数を必ず選び、合計10件を以下のJSON形式のみで返してください（前後の説明文は不要）：

{{
  "nowcast_reading": "（Nowcastデータが提示されている場合のみ）統計時点末から直近までに『誰の富が、どの資産で、どれだけ増減したか』を4〜6文で読む。①地域別の価格の動き（株価・金利・為替・金）のうち最も大きい変化とその意味、②評価変動の合計で見て資産が増えた地域・減った地域、増減の主因となった資産クラス、③今日の記事10件との関係——価格の動きを説明する出来事が記事にあるか、逆に記事に出ていない動きが数字に見えるか。文体ルールに従い、耳で聞いて分かる平易な言葉で。金額は兆ドル・億ドルで丸めて言う。データが無ければ空文字列。",
  "maps_reading": "（地図データが提示されている場合のみ）2枚の世界地図が今日示していることを4〜6文で読む。①地政学マップ：世界のどこに注目が集中し、どんな種類の出来事（衝突・協調・制裁など）が動いているか。②企業活動マップ：資本や投資がどこへ向かい、どの産業・国が主役か。③その2つと、上の定点観測・今日の記事10件との関係——地図が記事を裏づけているか、記事に出ていない動きが地図に見えるか。文体ルールに従い、耳で聞いて分かる平易な言葉で。地図データが無ければ空文字列。",
  "articles": [
    {{
      "index": <元の番号（整数）>,
      "category": "国際政治・地政学 / マクロ経済・金融 / ビジネス（世界）/ ビジネス（国内）/ 芸術・哲学・科学・宗教 のいずれか",
      "title_ja": "記事タイトルの日本語訳（意訳可・簡潔に）",
      "impact": <影響度を5段階の整数（1〜5）で。5=世界や社会を大きく動かす重大ニュース、4=重要、3=中程度、2=やや小さい、1=限定的。その出来事が経済・政治・社会に与えるインパクトの大きさで判断する>,
      "unique_point": "この記事のユニークな点・最大のポイントを1文で端的に（要約の前に読者の関心を引く導入）",
      "summary_ja": "この時事ニュースについて【①何が起きたか】【②なぜそうなったか・背景の構造】【③何を意味するか】を簡潔にまとめる。全体で従来の半分・200字程度に収める。特に②では、この出来事の背後で働く構造やトレンドを説明する（関連するトレンド解説記事の知見があれば、その内容も取り入れて背景を厚くしてよい）。ただし『権力は腐敗する』式の何にでも当てはまる抽象論は避け、この出来事に根ざした具体的な洞察にする。文体ルールに従い、耳で聞いて一度で分かる平易な言葉で。自然科学・哲学の記事は①②③にこだわらず最適な形で書く。",
      "one_point_lesson": {{
        "field": "分野名（経済／社会／ビジネス／政治／技術／倫理／自然科学／芸術 など）",
        "theme": "解説テーマ名（例：比較優位、認知バイアス、地政学リスク など）",
        "content": "記事に関連するトピックをひとつ選び、学習のためのワンポイント解説を書く。背景知識がなくても分かるよう具体例を交えて平易に（100〜130字程度）"
      }}
    }}
  ],
  "papers": [
    {{"id": "P1", "title_ja": "論文タイトルの日本語訳（簡潔に）", "gist": "この研究が何を明らかにし、なぜ重要なのかを1〜2文・80字程度で。専門用語は言い換える。"}}
  ],
  "headline_word": "今日の主要ニュース10件が束になって示す『世界の今の状態』を、コンセプチュアルに15文字以内で表現する。具体的な固有名詞や出来事を列挙するのではなく、それらを貫く本質的なテーマやダイナミクスを一言で捉えること。抽象的すぎず、具体的すぎず、読んだ人が『なるほど、そういう時代か』と感じられる表現。例：「秩序の再編と問い直し」「加速する断絶と模索」「実利主義が席巻する世界」など"
}}

配分（厳守）：{slot_summary}
※「国内」は日本国内を指す。
※同じ出来事・発表を複数のソースが報じている場合は、最も詳細な1件のみ選ぶこと。
※【時事ニュース優先・重要】取り上げる10件は、最近実際に起きた出来事・発表・動き（＝時事ニュース）を選ぶこと。時系列のない一般論・トレンド解説エッセイを単体で取り上げてはいけない。そうしたトレンド解説記事は、選んだ時事ニュースの背景・構造を説明する材料として summary_ja の②に活用すること。速報の羅列ではなく、構造的な意味を持つ出来事を優先する。もしあるカテゴリのプールに時事性のある記事が乏しい場合は、その中で最も『出来事性』の高いものを選ぶ。
※【重要論文】提示された論文 [P1]..[Pn] すべてについて、papers 配列に id・title_ja・gist を返すこと（論文が提示されていなければ空配列）。gist は「何を明らかにしたか」と「なぜ重要か」を平易に。
※【テーマ分散・厳守】10件は互いに異なる主題で構成すること。同一号の中で同じ主題の記事を偏って選ばない（例：AI・生成AIばかり、同じ紛争ばかり、にしない）。同種の候補しかない場合のみ重複を許容する。
※「投資家として」「コンサルタントとして」「政治家として」のような、特定の立場に立った示唆・アドバイスは書かないこと。あくまで事象そのものの構造と含意を描くこと。
"""

def select_and_summarize(articles: list[dict], session_label: str, edition: str, market_data: dict,
                         recent_topics: list | None = None, maps: dict | None = None,
                         side: dict | None = None) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = _build_prompt(articles[:MAX_TO_CLAUDE], session_label, edition, market_data, recent_topics, maps, side)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text
    json_match = re.search(r"\{[\s\S]+\}", raw)
    if not json_match:
        raise ValueError(f"No JSON found in Claude response:\n{raw[:300]}")
    return json.loads(json_match.group())

# ── HTML Email Builder ─────────────────────────────────────────────────────────

def build_html(digest: dict, articles: list[dict], session_label: str, market_data: dict,
               maps: dict | None = None, side: dict | None = None) -> str:
    now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    article_map = {i + 1: a for i, a in enumerate(articles)}

    # 定点観測 panel
    side = side or {}
    nowcast_panel = _build_nowcast_html(side.get("capital"), digest.get("nowcast_reading", ""))
    indicators_panel = _build_indicators_html({}, "")          # 人口・動態（年次）のみ
    maps_panel = _build_maps_html(maps or {}, digest.get("maps_reading", ""))
    papers_panel = _build_papers_html(side.get("research"), digest.get("papers", []))

    def stars(n) -> str:
        try:
            n = max(0, min(5, int(n)))
        except (TypeError, ValueError):
            return ""
        return "★" * n + "☆" * (5 - n)

    cards_html = ""
    for i, item in enumerate(digest.get("articles", []), 1):
        idx = item.get("index", 0)
        orig = article_map.get(idx, {})

        star_str = stars(item.get("impact"))
        star_html = ""
        if star_str:
            star_html = f"""
            <span style="font-size:12px;color:#f59e0b;letter-spacing:1px;white-space:nowrap;"
                  title="影響度">{star_str}</span>"""

        lesson = item.get("one_point_lesson", {})
        lesson_html = ""
        if lesson:
            lesson_html = f"""
          <div style="background:#f0fdf4;border-left:3px solid #16a34a;
                      padding:12px 16px;margin-top:14px;border-radius:4px;">
            <div style="font-size:11px;font-weight:700;color:#16a34a;
                        letter-spacing:1px;margin-bottom:4px;">
              📚 ワンポイント解説 — {lesson.get('field','')}「{lesson.get('theme','')}」
            </div>
            <p style="margin:0;font-size:12px;color:#333;line-height:1.75;">
              {lesson.get('content','')}
            </p>
          </div>"""

        cards_html += f"""
        <div style="background:white;margin:0 0 20px;border-radius:10px;
                    padding:20px 24px;box-shadow:0 1px 5px rgba(0,0,0,0.07);">
          <div style="margin-bottom:8px;display:flex;justify-content:space-between;
                      align-items:center;">
            <span style="font-size:11px;color:#888;">
              {i}. {orig.get('category','')} &nbsp;·&nbsp; {orig.get('source','')}
            </span>
            {star_html}
          </div>
          <h2 style="margin:6px 0 10px;font-size:16px;font-weight:600;line-height:1.45;">
            <a href="{orig.get('url','#')}"
               style="color:#1a1a2e;text-decoration:none;">{item.get('title_ja', orig.get('title',''))}</a>
          </h2>
          <div style="background:#fffbeb;border-left:3px solid #f59e0b;
                      padding:10px 14px;margin-bottom:12px;border-radius:4px;">
            <p style="margin:0;font-size:13px;color:#78350f;font-weight:500;line-height:1.6;">
              🔑 {item.get('unique_point','')}
            </p>
          </div>
          <p style="margin:0 0 10px;font-size:13px;color:#444;line-height:1.85;white-space:pre-line;">
            {item.get('summary_ja','')}
          </p>
          {lesson_html}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f5f5f3;font-family:'Helvetica Neue',Arial,sans-serif;">
<div style="max-width:660px;margin:0 auto;padding:24px 16px;">

  <!-- Header -->
  <div style="background:#1a1a2e;color:white;padding:24px 28px;border-radius:10px 10px 0 0;margin-bottom:0;">
    <div style="font-size:12px;letter-spacing:3px;color:#7c86b5;text-transform:uppercase;">
      Daily Digest — {session_label}
    </div>
    <div style="font-size:22px;font-weight:300;margin-top:6px;letter-spacing:1px;">
      {now_str}
    </div>
  </div>

  <!-- Nowcast: 資産クラス別の評価額変動 -->
  {nowcast_panel}

  <!-- 定点観測（人口・動態） -->
  {indicators_panel}

  <!-- 地図から読む -->
  {maps_panel}

  <!-- Article Cards -->
  {cards_html}

  <!-- 直近の重要論文 -->
  {papers_panel}

  <!-- Reply invitation -->
  <div style="background:#1a1a2e;color:#e2e6f3;padding:18px 24px;border-radius:10px;margin:4px 0 20px;">
    <div style="font-size:12px;font-weight:700;letter-spacing:1px;margin-bottom:6px;color:#7c86b5;">
      💬 このメールに返信して、対話を始めましょう
    </div>
    <p style="margin:0;font-size:13px;line-height:1.8;">
      気になった点・疑問・違う見方など、なんでも返信してください。追加で調べたうえでお返事し、
      一緒に深掘りしていきます。配信後の数日は、何度でもやり取りできます。
    </p>
  </div>

  <!-- Footer -->
  <div style="text-align:center;padding:20px;font-size:11px;color:#aaa;">
    Generated by Claude
  </div>

</div>
</body>
</html>"""

# ── Text-to-Speech (音声読み上げ) ───────────────────────────────────────────────

def _clean_for_tts(text: str) -> str:
    """読み上げ向けに整形。【①…】等のマーカーは読むと不自然なので句点に置換。"""
    text = re.sub(r"【[^】]*】", "。", text)
    text = re.sub(r"[🔑📚🌍🚀✨🔭]", "", text)
    text = re.sub(r"[ 　]*。[ 　]*", "。", text)   # 句点まわりの空白を除去
    text = re.sub(r"。{2,}", "。", text)            # 連続する句点を1つに
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()

def _chunk_text(text: str, limit: int = TTS_CHAR_LIMIT) -> list[str]:
    """文の境界で limit 文字以下のチャンクに分割（TTS APIの1回あたり上限対策）。"""
    sentences = re.split(r"(?<=[。！？\n])", text)
    chunks, cur = [], ""
    for s in sentences:
        while len(s) > limit:                 # 1文が上限を超える稀なケース
            if cur:
                chunks.append(cur); cur = ""
            chunks.append(s[:limit]); s = s[limit:]
        if len(cur) + len(s) > limit:
            chunks.append(cur); cur = ""
        cur += s
    if cur:
        chunks.append(cur)
    return chunks

def build_tts_script(digest: dict, label: str, headline: str) -> str:
    """配信内容から読み上げ用の台本テキストを組み立てる。"""
    date = datetime.now().strftime("%m月%d日")
    parts = [f"{date}の{label}です。今日のキーワードは、{headline}。"]

    nowcast = digest.get("nowcast_reading", "")
    if nowcast:
        parts.append("まず、統計の後で何が動いたか。資産の評価額から。" + nowcast)

    maps_reading = digest.get("maps_reading", "")
    if maps_reading:
        parts.append("次に、世界地図から。" + maps_reading)

    articles = digest.get("articles", [])
    if articles:
        parts.append(f"ここからは、今日の主要ニュース{len(articles)}件です。")
        for i, item in enumerate(articles, 1):
            impact = item.get("impact")
            impact_str = f"影響度は5段階中の{int(impact)}。" if impact else ""
            parts.append(
                f"{i}件目。{item.get('title_ja','')}。{impact_str}"
                f"{item.get('unique_point','')} {item.get('summary_ja','')}"
            )

    papers = digest.get("papers", [])
    if papers:
        parts.append("最後に、直近の重要な論文です。")
        for pp in papers[:6]:
            if pp.get("title_ja"):
                parts.append(f"{pp.get('title_ja')}。{pp.get('gist','')}")

    parts.append("以上、本日のダイジェストでした。")
    return _clean_for_tts("\n\n".join(parts))

def synthesize_audio(text: str) -> bytes | None:
    """OpenAI TTSでmp3を生成。APIキーが無い/失敗時は None を返す。"""
    if not OPENAI_API_KEY:
        return None
    import requests as _req
    audio = b""
    for chunk in _chunk_text(text):
        if not chunk.strip():
            continue
        r = _req.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": TTS_MODEL, "voice": TTS_VOICE,
                  "input": chunk, "response_format": TTS_FORMAT},
            timeout=120,
        )
        r.raise_for_status()
        audio += r.content
    return audio or None

# ── Email Sender ───────────────────────────────────────────────────────────────

def send_email(subject: str, html_body: str, attachments: list | None = None,
               to_addrs: list | None = None, extra_headers: dict | None = None,
               sender: str | None = None, sender_pw: str | None = None,
               inline_images: list | None = None):
    """attachments: list of (filename, bytes, audio_subtype) tuples.
    to_addrs: 宛先（省略時は全配信先）。extra_headers: In-Reply-To/References等。
    sender/sender_pw: 送信元アカウントを差し替える（省略時は既定のGMAIL_ADDRESS）。"""
    from_addr = sender or SENDER_EMAIL
    from_pw   = sender_pw if sender is not None else GMAIL_APP_PASS
    to_addrs  = to_addrs or RECIPIENT_EMAILS
    root = MIMEMultipart("mixed")
    root["Subject"] = subject
    root["From"]    = from_addr
    root["To"]      = ", ".join(to_addrs)
    root["X-News-Bot"] = "1"   # 自分の送信メールを受信処理で除外するための目印
    for k, v in (extra_headers or {}).items():
        if v:
            root[k] = v

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    if inline_images:                      # 本文中の <img src="cid:.."> 用
        rel = MIMEMultipart("related")
        rel.attach(alt)
        for cid, data in inline_images:
            img = MIMEImage(data, _subtype="png")
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
            rel.attach(img)
        root.attach(rel)
    else:
        root.attach(alt)

    for fname, data, subtype in (attachments or []):
        part = MIMEBase("audio", subtype)
        part.set_payload(data)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=fname)
        root.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(from_addr, from_pw)
        server.sendmail(from_addr, to_addrs, root.as_string())

# ── Main Digest Flow ───────────────────────────────────────────────────────────

def run_digest(edition: str):
    """edition: 'morning' | 'evening'（週2回・月木配信。ラベルは曜日ベース）"""
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][datetime.now().weekday()]
    label = f"{weekday_ja}曜号"
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting {label}...")

    # 1. Fetch live market data
    market_data = {}   # 冒頭のマーケット指標は Nowcast シートに置き換え（人口・動態のみ残す）

    # 1b. Fetch world-map summaries + screenshots from GitHub Pages (optional)
    maps = fetch_map_summaries()
    print(f"  Maps available: {list(maps.keys()) or 'none'}")
    side = fetch_side_summaries()
    print(f"  Side data: {list(side.keys()) or 'none'}")

    # 2. Load seen-article cache
    cache = _load_cache()
    seen  = set(cache.keys())

    # 3. Fetch all articles
    all_articles = fetch_articles()
    print(f"  Fetched {len(all_articles)} articles from {sum(len(v) for v in RSS_FEEDS.values())} feeds")

    # 4. Filter to new articles (by URL and title); fall back to all if too few
    new_articles = [
        a for a in all_articles
        if _article_id(a["url"]) not in seen and _title_id(a["title"]) not in seen
    ]
    if len(new_articles) < 10:
        print(f"  Only {len(new_articles)} new articles — using full set")
        new_articles = all_articles

    # 5. Update cache (register both URL and title)
    now_iso = datetime.now(timezone.utc).isoformat()
    for a in new_articles:
        cache[_article_id(a["url"])]   = now_iso
        cache[_title_id(a["title"])]   = now_iso
    _save_cache(cache)

    # 6. Ask Claude to select & summarize (avoid repeating recent themes)
    recent_topics = _load_recent_topics()
    session_label = f"{datetime.now().strftime('%Y-%m-%d')} {label}"
    print(f"  Sending {min(len(new_articles), MAX_TO_CLAUDE)} articles to Claude...")
    digest = select_and_summarize(new_articles, session_label, edition, market_data, recent_topics, maps, side)

    # 7. Build HTML
    html = build_html(digest, new_articles, label, market_data, maps, side)
    inline_images = [(f"map_{k}", m["png"]) for k, m in maps.items() if m.get("png")]
    if side.get("capital", {}).get("png"):
        inline_images.append(("sheet_capital", side["capital"]["png"]))
    if DRY_RUN:
        out = Path(os.environ.get("DIGEST_PREVIEW", "digest_preview.html"))
        out.write_text(html, encoding="utf-8")
        print(f"  [dry-run] wrote {out} - not sent")
        return

    # 8. Generate audio narration (optional — skipped gracefully if no OpenAI key)
    headline = digest.get("headline_word", "")
    attachments = None
    if OPENAI_API_KEY:
        try:
            print("  Synthesizing audio narration...")
            script = build_tts_script(digest, label, headline)
            audio = synthesize_audio(script)
            if audio:
                ext, subtype = _TTS_EXT.get(TTS_FORMAT, ("mp3", "mpeg"))
                fname = f"digest_{datetime.now().strftime('%Y%m%d')}_{edition}.{ext}"
                attachments = [(fname, audio, subtype)]
                print(f"  Audio ready: {len(audio)//1024} KB ({TTS_FORMAT})")
        except Exception as e:
            print(f"  [WARN] TTS failed, sending without audio: {e}")

    # 9. Send email — 各アカウントへ「自分から自分へ」配信
    subject = f"[Digest {label}] {datetime.now().strftime('%m/%d')} — {headline}"
    only = os.environ.get("DIGEST_ONLY", "").lower()   # テスト用：特定アカウントだけに送る
    for acct, pw in DELIVERY_ACCOUNTS:
        if only and acct.lower() != only:
            print(f"  (skip {acct}: DIGEST_ONLY={only})")
            continue
        try:
            send_email(subject, html, attachments,
                       to_addrs=[acct], sender=acct, sender_pw=pw,
                       inline_images=inline_images)
            print(f"  Sent to {acct}{' (+audio)' if attachments else ''}")
        except Exception as e:
            print(f"  [WARN] {acct} への送信に失敗: {e}")

    # 10. Record this edition's themes so upcoming editions don't rehash them
    selected_titles = [it.get("title_ja", "") for it in digest.get("articles", []) if it.get("title_ja")]
    recent_topics.append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "edition": label,
        "headline": headline,
        "titles": selected_titles,
    })
    _save_recent_topics(recent_topics)

# ── FRONTIER Digest (daily) ────────────────────────────────────────────────────

def fetch_future_articles() -> list[dict]:
    articles = []
    seen_urls: set[str] = set()  # deduplicate within the same batch
    for category, feeds in FUTURE_FEEDS.items():
        for source_name, url in feeds:
            try:
                feed = feedparser.parse(url, request_headers={"User-Agent": "NewsDigest/1.0"})
                for entry in feed.entries[:MAX_PER_FEED]:
                    entry_url = entry.get("link", "")
                    norm_url = _normalize_url(entry_url)
                    if not entry_url or norm_url in seen_urls:
                        continue
                    seen_urls.add(norm_url)
                    raw_summary = entry.get("summary", entry.get("description", ""))
                    clean_summary = re.sub(r"<[^>]+>", "", raw_summary)[:600]
                    articles.append({
                        "category": category,
                        "source":   source_name,
                        "title":    entry.get("title", "").strip(),
                        "url":      entry_url,
                        "summary":  clean_summary.strip(),
                    })
            except Exception as e:
                print(f"  [WARN] {source_name}: {e}")
    return articles

def _build_future_prompt(articles: list[dict]) -> str:
    article_blocks = "\n\n".join(
        f"[{i+1}] ({a['category']} / {a['source']})\n"
        f"Title: {a['title']}\n"
        f"Summary: {a['summary']}"
        for i, a in enumerate(articles)
    )
    return f"""
{USER_PROFILE}

あなたは今週の「ワクワクする未来の情報」をキュレーションします。
以下の記事の中から、読んだ人が思わず前のめりになるような、
テクノロジーの発見・革新的な事業・世界を変えうる理論・驚くべき科学的知見など、
未来への期待や好奇心を刺激する記事を{FUTURE_DIGEST_COUNT}件選んでください。

{article_blocks}

---

以下のJSON形式のみで返してください（前後の説明文は不要）：

{{
  "articles": [
    {{
      "index": <元の番号（整数）>,
      "title_ja": "記事タイトルの日本語訳（意訳可・簡潔に）",
      "spark": "なぜこれがワクワクするのか、何が革新的なのかを1〜2文で。読んだ人が『すごい、もっと知りたい』と感じるような書き方で",
      "summary_ja": "内容を平易な言葉で生き生きと説明する。専門知識がなくても理解できるよう、身近な比喩や具体例を使いながら、この発見・技術・事業が何を変えうるかを描く。物語風に、読んでいて楽しくなるように書く（5〜7文）",
      "implication": "これが実現したら・普及したら、世界や私たちの生活・仕事はどう変わるかを1〜2文で。『投資家として』『コンサルタントとして』のような特定の立場からの示唆は書かない。"
    }}
  ],
  "future_snapshot": "今週の5件を俯瞰して、『いま世界はどんな未来に向かっているか』を3〜4文でワクワクするように描写する",
  "headline_word": "今週の5件が束になって示す未来の方向性を、コンセプチュアルに15文字以内で表現する"
}}

選定の指針：
- テクノロジー・科学・事業・思想からバランスよく選ぶ
- すでに広く知られた話題より、まだ多くの人が知らない新鮮な発見・動向を優先
- 難解な専門的成果より、「社会・生活・思考を変えうる」インパクトを重視
"""

def build_future_html(digest: dict, articles: list[dict]) -> str:
    now_str  = datetime.now().strftime("%Y年%m月%d日")
    article_map = {i + 1: a for i, a in enumerate(articles)}

    cards_html = ""
    for item in digest.get("articles", []):
        idx  = item.get("index", 0)
        orig = article_map.get(idx, {})
        cards_html += f"""
        <div style="background:white;margin:0 0 20px;border-radius:10px;
                    padding:20px 24px;box-shadow:0 1px 5px rgba(0,0,0,0.07);">
          <div style="margin-bottom:8px;">
            <span style="font-size:11px;color:#888;">
              {orig.get('category','')} &nbsp;·&nbsp; {orig.get('source','')}
            </span>
          </div>
          <h2 style="margin:6px 0 10px;font-size:16px;font-weight:600;line-height:1.45;">
            <a href="{orig.get('url','#')}"
               style="color:#0f172a;text-decoration:none;">{item.get('title_ja', orig.get('title',''))}</a>
          </h2>
          <div style="background:#f0fdf4;border-left:3px solid #16a34a;
                      padding:10px 14px;margin-bottom:12px;border-radius:4px;">
            <p style="margin:0;font-size:13px;color:#14532d;font-weight:500;line-height:1.6;">
              ✨ {item.get('spark','')}
            </p>
          </div>
          <p style="margin:0 0 10px;font-size:13px;color:#444;line-height:1.85;white-space:pre-line;">
            {item.get('summary_ja','')}
          </p>
          <div style="border-top:1px solid #eee;padding-top:10px;margin-top:4px;">
            <p style="margin:0;font-size:12px;color:#666;">
              🔭 {item.get('implication','')}
            </p>
          </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f0fdf4;font-family:'Helvetica Neue',Arial,sans-serif;">
<div style="max-width:660px;margin:0 auto;padding:24px 16px;">

  <!-- Header -->
  <div style="background:#064e3b;color:white;padding:24px 28px;border-radius:10px 10px 0 0;">
    <div style="font-size:12px;letter-spacing:3px;color:#6ee7b7;text-transform:uppercase;">
      FRONTIER
    </div>
    <div style="font-size:22px;font-weight:300;margin-top:6px;letter-spacing:1px;">
      {now_str}
    </div>
  </div>

  <!-- Future Snapshot -->
  <div style="background:#dcfce7;border-left:4px solid #16a34a;
              padding:16px 20px;margin-bottom:20px;border-radius:0 0 4px 4px;">
    <div style="font-size:11px;font-weight:700;color:#16a34a;
                letter-spacing:1px;margin-bottom:6px;">🚀 今週の未来</div>
    <p style="margin:0;font-size:13px;color:#333;line-height:1.85;white-space:pre-line;">
      {digest.get('future_snapshot','')}
    </p>
  </div>

  <!-- Article Cards -->
  {cards_html}

  <!-- Footer -->
  <div style="text-align:center;padding:20px;font-size:11px;color:#aaa;">
    Generated by Claude
  </div>

</div>
</body>
</html>"""

def run_future_digest():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting FRONTIER (weekly)...")

    # 1. Load shared seen-article cache (deduplicate across days & with daily digest)
    cache = _load_cache()
    seen  = set(cache.keys())

    # 2. Fetch all future articles
    all_articles = fetch_future_articles()
    print(f"  Fetched {len(all_articles)} articles")

    # 3. Filter to new articles (by URL and title); fall back to all if too few
    new_articles = [
        a for a in all_articles
        if _article_id(a["url"]) not in seen and _title_id(a["title"]) not in seen
    ]
    if len(new_articles) < 8:
        print(f"  Only {len(new_articles)} new articles — using full set")
        new_articles = all_articles

    # 4. Register selected pool in cache so tomorrow's edition won't repeat them
    now_iso = datetime.now(timezone.utc).isoformat()
    for a in new_articles:
        cache[_article_id(a["url"])] = now_iso
        cache[_title_id(a["title"])] = now_iso
    _save_cache(cache)

    # 5. Ask Claude to curate & summarize
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = _build_future_prompt(new_articles[:MAX_TO_CLAUDE])
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text
    json_match = re.search(r"\{[\s\S]+\}", raw)
    if not json_match:
        raise ValueError(f"No JSON in response: {raw[:300]}")
    digest = json.loads(json_match.group())

    html     = build_future_html(digest, new_articles)
    headline = digest.get("headline_word", "")
    subject  = f"[FRONTIER] {datetime.now().strftime('%m/%d')} — {headline}"
    for acct, pw in DELIVERY_ACCOUNTS:
        try:
            send_email(subject, html, to_addrs=[acct], sender=acct, sender_pw=pw)
            print(f"  Sent to {acct}")
        except Exception as e:
            print(f"  [WARN] {acct} への送信に失敗: {e}")

# ── 対話ラリー（返信への調査・回答）─────────────────────────────────────────────

# 返信として処理する対象を、Botが送った件名の目印で判定する
REPLY_SUBJECT_MARKERS = ("[Digest", "[FRONTIER", "[Q&A")

def _decode_header(value: str) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value

def _extract_plain_text(msg) -> str:
    """メールから本文プレーンテキストを取り出す（text/plain優先、無ければHTMLを除タグ）。"""
    def _decode_part(part):
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                return ""
            return payload.decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            return ""

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                txt = _decode_part(part)
                if txt.strip():
                    return txt
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return re.sub(r"<[^>]+>", "", _decode_part(part))
        return ""
    txt = _decode_part(msg)
    if msg.get_content_type() == "text/html":
        txt = re.sub(r"<[^>]+>", "", txt)
    return txt

def _latest_message(body: str) -> str:
    """返信本文から、引用（前回メールの引用部分）を除いた『今回書かれた分』を抜く。"""
    lines = body.splitlines()
    out = []
    for ln in lines:
        s = ln.strip()
        if (s.startswith(">") or s.startswith("-----")
                or re.match(r"^On .*wrote:$", s)
                or re.match(r"^\d{4}年.*(書きました|wrote)", s)
                or s.startswith("From:") or s.startswith("差出人:")
                or "@gmail.com" in s and "wrote" in s):
            break
        out.append(ln)
    latest = "\n".join(out).strip()
    return latest or body.strip()

def _md_to_html(text: str) -> str:
    """回答テキスト（軽いMarkdown）を簡易HTMLに変換。"""
    esc = htmllib.escape(text)
    esc = re.sub(r"^\s*#{1,6}\s*(.+)$", r"<strong>\1</strong>", esc, flags=re.M)
    esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
    esc = re.sub(r"^\s*[-*・]\s+", "・", esc, flags=re.M)
    return esc.replace("\n", "<br>")

def answer_reply(subject: str, body: str) -> str:
    """読者の返信に対し、必要ならweb検索して回答本文（プレーン）を作る。"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = f"""{USER_PROFILE}

あなたはこのニュースダイジェストの筆者です。いま読者から返信メールが届きました。
読者はダイジェストを読んで浮かんだ疑問・感想・追加で知りたいことを書いています。
以下がそのメールです（本文の下部に前回までのやり取りが引用されていることがあります。
それは会話の文脈として使い、"今回の最新の問い"に答えてください）。

━━━ 件名 ━━━
{subject}
━━━ 本文 ━━━
{body[:6000]}
━━━━━━━━━━

これは配信後の数日にわたって続く"深掘りの対話"の一部です。単発のQ&Aで完結させるのではなく、
やり取りを重ねるごとに理解が一段ずつ深まっていくことを目指してください。

この読者の疑問・関心に、必要に応じてweb検索で最新の事実やデータを調べたうえで、
本質を押さえた分かりやすい回答をまとめてください。

- 文体：一文を短く、話し言葉に近い平易な日本語で。専門用語は直後にやさしく言い換える。ただし内容の深さ・分析の鋭さ・事実の正確さは落とさない。
- 引用された過去のやり取りがあれば、それを踏まえて『前回の続き』として一段深める。同じ説明の繰り返しは避け、新しい角度・より深い層を足す。
- 「投資家として」「コンサルタントとして」等、特定の立場に立った助言は書かない。事実と、その背後の構造・含意を示す。
- 調べて分かった事実には、出典（媒体名など）を軽く添える。
- 読者の意見・仮説には、賛否だけでなく『それはこういう前提に立っている』『こういう反例もある』と対話的に応答し、思考を前に進める。
- 会話が続くよう、最後に『さらに掘り下げるなら』という問い・論点を1〜2行、具体的に投げかけて締める。
- 冗長な前置き・定型の挨拶は不要。回答の中身から始める。

回答本文のみを、プレーンな文章（必要なら箇条書き可）で返してください。JSON形式にはしないこと。"""

    def _call(use_tools: bool):
        kwargs = dict(model="claude-sonnet-4-6", max_tokens=4000,
                      messages=[{"role": "user", "content": prompt}])
        if use_tools:
            kwargs["tools"] = [{"type": "web_search_20250305",
                                "name": "web_search", "max_uses": 5}]
        return client.messages.create(**kwargs)

    try:
        message = _call(use_tools=True)
    except Exception as e:
        print(f"  [WARN] web_search unavailable ({e}); answering without search")
        message = _call(use_tools=False)

    text = "".join(getattr(b, "text", "") for b in message.content
                   if getattr(b, "type", "") == "text").strip()
    return _strip_preamble(text)

def _strip_preamble(text: str) -> str:
    """『情報が揃いました。回答をまとめます。---』のような前置き・区切りを除去。"""
    meta = ("まとめます", "情報が揃", "回答します", "承知", "調べました",
            "以下にまとめ", "回答をまとめ", "整理します")
    lines = text.lstrip().splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s == "" or set(s) <= {"-", "—", "―", "="} or (len(s) < 40 and any(m in s for m in meta)):
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip()

def _build_reply_html(question: str, answer: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f3;font-family:'Helvetica Neue',Arial,sans-serif;">
<div style="max-width:660px;margin:0 auto;padding:24px 16px;">
  <div style="background:#1a1a2e;color:white;padding:18px 24px;border-radius:10px 10px 0 0;">
    <div style="font-size:11px;letter-spacing:3px;color:#7c86b5;text-transform:uppercase;">
      ご質問への回答
    </div>
  </div>
  <div style="background:#eef2ff;border-left:4px solid #4361ee;padding:12px 18px;">
    <div style="font-size:10px;font-weight:700;color:#4361ee;letter-spacing:1px;margin-bottom:4px;">
      いただいた問い
    </div>
    <p style="margin:0;font-size:12px;color:#555;line-height:1.7;white-space:pre-line;">{htmllib.escape(question[:500])}</p>
  </div>
  <div style="background:white;padding:20px 24px;border-radius:0 0 10px 10px;box-shadow:0 1px 5px rgba(0,0,0,0.07);">
    <p style="margin:0;font-size:14px;color:#333;line-height:1.9;">{_md_to_html(answer)}</p>
  </div>
  <div style="text-align:center;padding:16px;font-size:11px;color:#aaa;">
    このメールにそのまま返信すれば、続けて質問できます · Generated by Claude
  </div>
</div></body></html>"""

def _find_all_mail_folder(imap) -> str:
    """Gmailの「すべてのメール」フォルダ名（UI言語で変わる）を \\All フラグで特定。"""
    try:
        status, folders = imap.list()
        if status == "OK":
            for f in folders:
                decoded = f.decode(errors="replace") if isinstance(f, bytes) else f
                if "\\All" in decoded:
                    m = re.search(r'"([^"]*)"$', decoded)
                    if m:
                        return m.group(1)
    except Exception:
        pass
    return "INBOX"

def _check_account_replies(addr: str, pw: str, processed_ids: set) -> int:
    """1アカウントの「すべてのメール」を調べ、ユーザーの返信に回答する。返した件数を返す。"""
    recipients_lc = [a.lower() for a in RECIPIENT_EMAILS] + [addr.lower()]
    answered = 0
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
    with imap:
        imap.login(addr, pw)
        all_mail = _find_all_mail_folder(imap)
        imap.select(f'"{all_mail}"')
        # 件名にダイジェスト/FRONTIERの目印を含むものを収集（UNSEENに頼らない：
        # 自分宛の返信はGmailが既読で届くことがあり、INBOXにも入らないため）
        ids = []
        for term in ("Digest", "FRONTIER"):
            status, data = imap.search(None, "SUBJECT", f'"{term}"')
            if status == "OK" and data and data[0]:
                ids += data[0].split()
        print(f"  [{addr}] matched {len(set(ids))} message(s) in All Mail")
        # まず全件のヘッダだけ軽く確認し、本物の返信候補にだけ本文を取得する。
        # （全文取得は重いのでヘッダで絞る＝メールの位置に依存せず確実に拾える）
        unique_ids = list(dict.fromkeys(reversed(ids)))   # 新しい順・重複除去
        cand_count = 0
        for num in unique_ids[:250]:
            if answered >= MAX_REPLIES_PER_RUN:
                break
            try:
                typ, hd = imap.fetch(
                    num,
                    "(FLAGS BODY.PEEK[HEADER.FIELDS "
                    "(SUBJECT FROM MESSAGE-ID IN-REPLY-TO REFERENCES X-NEWS-BOT)])",
                )
                if typ != "OK" or not hd or not hd[0]:
                    continue
                flags = hd[0][0]
                flags = flags.decode(errors="replace") if isinstance(flags, bytes) else str(flags)
                is_draft = "\\Draft" in flags
                head = emaillib.message_from_bytes(hd[0][1])

                mid       = head.get("Message-ID", "")
                subject   = _decode_header(head.get("Subject", ""))
                from_addr = parseaddr(head.get("From", ""))[1].lower()
                is_bot    = bool(head.get("X-News-Bot"))
                is_reply  = bool(head.get("In-Reply-To") or head.get("References"))
                is_re     = subject.lstrip().lower().startswith("re:")
                has_mark  = any(m in subject for m in REPLY_SUBJECT_MARKERS)

                if not (is_re and has_mark):
                    continue    # 返信候補でなければ即スキップ（本文は取得しない）

                cand_count += 1
                print(f"    CAND subj='{subject[:42]}' from={from_addr} bot={is_bot} "
                      f"reply={is_reply} draft={is_draft} processed={mid in processed_ids}")

                if is_draft:
                    print("      -> skip: 下書き"); continue
                if not mid or mid in processed_ids:
                    print("      -> skip: 処理済み/ID無し"); continue
                if is_bot:
                    processed_ids.add(mid); print("      -> skip: 自分のBotメール"); continue
                if not is_reply:
                    print("      -> skip: In-Reply-To/References なし"); continue
                if from_addr not in recipients_lc:
                    print(f"      -> skip: from {from_addr} が配信先でない"); continue

                # 本物の返信 → 本文を取得して回答
                typ, full = imap.fetch(num, "(BODY.PEEK[])")
                if typ != "OK" or not full or not full[0]:
                    continue
                msg = emaillib.message_from_bytes(full[0][1])
                body = _extract_plain_text(msg)
                if not body.strip():
                    print("      -> skip: 本文が空"); processed_ids.add(mid); continue

                question = _latest_message(body)
                print(f"      -> Answering reply from {from_addr} ...")
                answer = answer_reply(subject, body)
                if not answer:
                    print("      -> WARN: empty answer"); continue

                refs = (msg.get("References", "") + " " + mid).strip()
                reply_subject = subject if subject.lower().startswith("re:") else "Re: " + subject
                send_email(
                    reply_subject,
                    _build_reply_html(question, answer),
                    to_addrs=[from_addr],
                    extra_headers={"In-Reply-To": mid, "References": refs},
                    sender=addr, sender_pw=pw,   # 届いたアカウントから返信（スレッド維持）
                )
                processed_ids.add(mid)
                answered += 1
                print(f"      -> Answered → {from_addr}")
            except Exception as e:
                print(f"  [WARN] 返信処理に失敗、スキップ: {e}")
                continue
        print(f"  [{addr}] reply candidates seen: {cand_count}")
    return answered

def run_reply_handler():
    """各アカウントのメールを調べ、ユーザーの返信にweb検索つきで回答する（メールのラリー）。"""
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Checking replies...")
    processed_ids = set(_load_reply_log())
    total = 0
    for addr, pw in SEND_ACCOUNTS.items():
        if not addr or not pw:
            continue
        # 1アカウントの失敗で全体を止めない（Actionsの失敗通知連発を防ぐ）
        try:
            total += _check_account_replies(addr, pw, processed_ids)
        except Exception as e:
            print(f"  [WARN] {addr} の返信チェックに失敗、スキップ: {e}")
            continue
    _save_reply_log(list(processed_ids))
    print(f"  Done. Answered {total} reply(ies).")

# ── Scheduler ─────────────────────────────────────────────────────────────────

def _validate_env():
    missing = [k for k in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "ANTHROPIC_API_KEY")
               if not os.environ.get(k)]
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        print("See .env.example for setup instructions.")
        sys.exit(1)

def main():
    _validate_env()

    args = sys.argv[1:]

    if "--now" in args or "--morning" in args:
        run_digest("morning")
        return
    if "--evening" in args:
        run_digest("evening")
        return
    if "--future" in args:
        run_future_digest()
        return
    if "--replies" in args:
        run_reply_handler()
        return

    # Default: start scheduler
    schedule.every().day.at("07:00").do(run_digest, edition="morning")
    schedule.every().day.at("21:00").do(run_digest, edition="evening")

    print("News Digest Scheduler running.")
    print("  Morning edition : 07:00")
    print("  Evening edition : 21:00")
    print("Press Ctrl+C to stop.\n")

    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    main()
