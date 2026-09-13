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
                if (OUT / m / "index.html").exists():
                    if not shot_page(browser, f"{m}/", OUT / m / "map.png"):
                        rc = 1
                else:
                    print(f"[WARN] {m}: no index.html, skip")
            if not capital(browser):
                rc = 1
            browser.close()
        return rc
    finally:
        srv.terminate()

if __name__ == "__main__":
    sys.exit(main())
