"""web/ を Artifact 公開用の単一 HTML（dist/index.html）に変換する。

- <!DOCTYPE>/<html>/<head>/<body> の枠を外す（公開時に付け直される）
- lib/*.js は cdnjs の同一バージョンに差し替える（Artifact の CSP で許可されたホスト）
- style.css / app.js / data.json / world-110m.json はすべて HTML に埋め込む（同梱ファイルへの依存を無くす）

    python build_dist.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent
WEB, DIST = ROOT / "web", ROOT / "dist"
CDN = {
    "lib/d3.min.js": "https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js",
    "lib/topojson.min.js": "https://cdnjs.cloudflare.com/ajax/libs/topojson/3.0.2/topojson.min.js",
}
def inline_json(name: str) -> str:
    # </script> を閉じてしまわないよう "</" をエスケープ
    return (WEB / name).read_text(encoding="utf-8").replace("</", r"<\/")

DIST.mkdir(exist_ok=True)
html = (WEB / "index.html").read_text(encoding="utf-8")
for local, cdn in CDN.items():
    html = html.replace(f'src="{local}"', f'src="{cdn}"')
head = re.search(r"<head>(.*?)</head>", html, re.S).group(1)
body = re.search(r"<body>(.*?)</body>", html, re.S).group(1)
head = re.sub(r"<meta[^>]*>\s*", "", head)
head = head.replace('<link rel="stylesheet" href="style.css">', "<style>\n" + (WEB / "style.css").read_text(encoding="utf-8") + "</style>")
body = body.replace('<script src="app.js"></script>',
    f"<script>window.__DATA__={inline_json('data.json')};window.__WORLD__={inline_json('world-110m.json')};</script>\n"
    "<script>\n" + (WEB / "app.js").read_text(encoding="utf-8") + "</script>")
out = DIST / "index.html"
out.write_text(head.strip() + "\n" + body.strip() + "\n", encoding="utf-8")
for stale in ("app.js", "style.css", "data.json", "world-110m.json"):
    (DIST / stale).unlink(missing_ok=True)
print(f"dist/index.html: {out.stat().st_size/1024:.0f} KB")
