"""out/ を一時的に配信し、Playwright(Chromium) で各地図を撮影・抽出する。

  geo / corp   : ページ全体を map.png に保存
  research     : ページ全体を map.png に保存
  capital      : matrix.html を開き、保有主体=全部門にして JS が計算した Nowcast
                 (window.__NOWCAST__) を summary.json に書き出し、Nowcast セクションを sheet.png に保存

    python tools/render.py
"""
from __future__ import annotations
import json, subprocess, sys, time, socket
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
PORT = 8765
VIEWPORT = {"width": 1400, "height": 900}

def wait_port(port: int, t: float = 15.0) -> bool:
    end = time.time() + t
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False

def shot_page(browser, rel: str, png: Path, wait_ms: int = 4000) -> bool:
    page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1.5)
    try:
        page.goto(f"http://127.0.0.1:{PORT}/{rel}", wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(wait_ms)
        page.screenshot(path=str(png), full_page=False)
        print(f"wrote {png.relative_to(ROOT)} ({png.stat().st_size//1024} KB)")
        return True
    except Exception as e:
        print(f"[WARN] {rel}: render failed: {e}")
        return False
    finally:
        page.close()

def shot_map(browser, rel: str, outdir: Path, wait_ms: int = 4500) -> bool:
    """地図パネル（#map の SVG）だけを撮る。ページ全体を撮ると、見出し・操作パネル・凡例に
    画素の6割を取られ、スマホでメールを開いたとき地図が読めないため。
    あわせて、左右に少し重なる2枚（西半分・東半分）を出す — 細い画面ではこの2枚を縦に積む。"""
    page = browser.new_page(viewport={"width": 1500, "height": 1200}, device_scale_factor=2)
    try:
        page.goto(f"http://127.0.0.1:{PORT}/{rel}", wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(wait_ms)
        el = page.locator("#map").first
        box = el.bounding_box() if el.count() else None
        if not box or box["width"] < 200 or box["height"] < 120:
            print(f"[WARN] {rel}: #map not found, falling back to full page")
            page.screenshot(path=str(outdir / "map.png"), full_page=False)
            return True
        el.screenshot(path=str(outdir / "map.png"))
        ov = 0.06                                     # 端の出来事が切れないよう6%重ねる
        half = box["width"] * (0.5 + ov)
        for name, x in (("map_w.png", box["x"]), ("map_e.png", box["x"] + box["width"] - half)):
            page.screenshot(path=str(outdir / name),
                            clip={"x": x, "y": box["y"], "width": half, "height": box["height"]})
        sizes = ", ".join(f"{n} {(outdir / n).stat().st_size//1024}KB" for n in ("map.png", "map_w.png", "map_e.png"))
        print(f"wrote {outdir.relative_to(ROOT)}/: {sizes}")
        return True
    except Exception as e:
        print(f"[WARN] {rel}: render failed: {e}")
        return False
    finally:
        page.close()

def capital(browser) -> bool:
    """資本フロー: Nowcast の数値と表の画像を取り出す。"""
    d = OUT / "capital"
    if not (d / "matrix.html").exists() or not (d / "matrix_data.json").exists():
        print("[WARN] capital: matrix.html / matrix_data.json missing, skip"); return False
    page = browser.new_page(viewport={"width": 1500, "height": 1000}, device_scale_factor=1.5)
    try:
        page.goto(f"http://127.0.0.1:{PORT}/capital/matrix.html", wait_until="networkidle", timeout=120000)
        page.wait_for_function("() => window.__NOWCAST__ !== undefined", timeout=60000)
        page.select_option("#sel-rsector", "ALL")            # 全部門
        page.wait_for_timeout(1500)
        nc = page.evaluate("() => window.__NOWCAST__")
        if not nc or nc.get("error"):
            print(f"[WARN] capital: __NOWCAST__ error: {nc}"); return False
        gen = json.loads((d / "matrix_data.json").read_text(encoding="utf-8")).get("generated_at")
        summary = {"map": "capital", "title": "資産クラス別の評価額変動（Nowcast）",
                   "generated": gen, "exported": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   **nc}
        (d / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote out/capital/summary.json (period {nc.get('stat_period_ja')}, sector {nc.get('sector')})")
        page.locator("#recent").screenshot(path=str(d / "sheet.png"))
        print(f"wrote out/capital/sheet.png ({(d / 'sheet.png').stat().st_size//1024} KB)")
        page.screenshot(path=str(d / "map.png"), full_page=False)
        return True
    except Exception as e:
        print(f"[WARN] capital: {e}"); return False
    finally:
        page.close()

def main() -> int:
    from playwright.sync_api import sync_playwright
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1"],
                           cwd=OUT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_port(PORT):
            print("[WARN] static server did not start"); return 1
        rc = 0
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for m in ("geo", "corp", "research"):
                if not (OUT / m / "index.html").exists():
                    print(f"[WARN] {m}: no index.html, skip")
                    continue
                # geo/corp はメールに載るので地図だけを大きく撮る。research は全景のまま。
                ok = (shot_map(browser, f"{m}/", OUT / m) if m in ("geo", "corp")
                      else shot_page(browser, f"{m}/", OUT / m / "map.png"))
                if not ok:
                    rc = 1
            if not capital(browser):
                rc = 1
            browser.close()
        return rc
    finally:
        srv.terminate()

if __name__ == "__main__":
    sys.exit(main())
