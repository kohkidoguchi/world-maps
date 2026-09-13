"""out/index.html（トップページ）を生成。各地図へのリンクと最新PNG、更新時刻を並べる。"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
CARDS = [
    ("capital",  "資産クラス別の評価額変動（Nowcast）", "資金循環統計の残高 × 直近の株価・利回り・為替 ＝ 公表を待たずに推計した評価変動", "sheet.png"),
    ("geo",      "世界の政治・地政学マップ", "GDELTイベント × 予測市場 × 制裁 × 構造指標", "map.png"),
    ("corp",     "世界の企業活動マップ",     "各国ニュースからClaudeが抽出した企業イベント（規模×資本×影響で重み付け）", "map.png"),
    ("research", "世界の研究マップ",         "OpenAlex：各分野の上位1%論文と新着（被引用の重要度で重み付け）", "map.png"),
]

def main():
    parts = []
    for key, title, sub, img_name in CARDS:
        s = {}
        sp = OUT / key / "summary.json"
        if sp.exists():
            s = json.loads(sp.read_text(encoding="utf-8"))
        gen = s.get("generated") or s.get("date") or ""
        img = ""
        if (OUT / key / img_name).exists():
            img = (f'<a href="{key}/"><img src="{key}/{img_name}" alt="{title}" '
                   f'style="width:100%;border-radius:8px;border:1px solid #ddd"></a>')
        parts.append(
            f'<section style="margin:0 0 40px">'
            f'<h2 style="margin:0 0 4px;font-size:20px"><a href="{key}/" style="color:#1a1a2e;text-decoration:none">{title} →</a></h2>'
            f'<div style="color:#777;font-size:13px;margin-bottom:10px">{sub}　<span style="color:#aaa">更新: {gen}</span></div>'
            f'{img}</section>'
        )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = "".join(parts)
    html = (
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>World Maps</title></head>'
        '<body style="margin:0;background:#f5f5f3;font-family:\'Helvetica Neue\',Arial,sans-serif;color:#222">'
        '<div style="max-width:960px;margin:0 auto;padding:32px 20px">'
        '<h1 style="font-size:24px;font-weight:600;margin:0 0 6px">World Maps</h1>'
        f'<div style="color:#888;font-size:12px;margin-bottom:28px">自動生成 · {now}</div>'
        f'{body}</div></body></html>'
    )
    (OUT / "index.html").write_text(html, encoding="utf-8")
    print("wrote out/index.html")

if __name__ == "__main__":
    main()
