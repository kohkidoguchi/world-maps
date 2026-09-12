"""Bundle the map into one self-contained HTML file (dist/index.html).

Data (web/data.json) and the world topology are inlined; D3 / topojson load from cdnjs.
Use this to publish the map as a hosted page (e.g. a claude.ai Artifact) or to mail it around.
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
DIST = os.path.join(ROOT, "dist")
CDN = {
    "lib/d3.min.js": "https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js",
    "lib/topojson.min.js": "https://cdnjs.cloudflare.com/ajax/libs/topojson/3.0.2/topojson.min.js",
}


def main(artifact_mode: bool = True):
    html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
    css = open(os.path.join(WEB, "style.css"), encoding="utf-8").read()
    js = open(os.path.join(WEB, "app.js"), encoding="utf-8").read()
    data = open(os.path.join(WEB, "data.json"), encoding="utf-8").read()
    world = open(os.path.join(WEB, "world-110m.json"), encoding="utf-8").read()
    js = js.replace('fetch("data.json").then(r => r.json())', "Promise.resolve(window.__DATA__)")
    js = js.replace('fetch("world-110m.json").then(r => r.json())', "Promise.resolve(window.__WORLD__)")
    inline = f"<script>window.__DATA__={data};window.__WORLD__={world};</script>\n<script>{js}</script>"
    html = html.replace('<link rel="stylesheet" href="style.css">', f"<style>{css}</style>")
    for local, cdn in CDN.items():
        html = html.replace(f'<script src="{local}"></script>', f'<script src="{cdn}"></script>')
    html = html.replace('<script src="app.js"></script>', inline)
    if artifact_mode:  # the Artifact host supplies the document skeleton
        html = re.sub(r"^.*?<head>\s*", "", html, flags=re.S)
        html = html.replace("</head>\n<body>", "").replace("</body>\n</html>", "")
        html = re.sub(r'<meta charset="utf-8">\s*<meta name="viewport"[^>]*>\s*', "", html)
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "index.html")
    open(out, "w", encoding="utf-8").write(html)
    print(f"wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    import sys
    main(artifact_mode="--full" not in sys.argv)
