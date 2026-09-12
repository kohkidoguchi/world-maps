"""out/ を一時的に配信し、各地図を Playwright(Chromium) で撮影して out/<map>/map.png に保存。

    python tools/render.py
"""
from __future__ import annotations
import subprocess, sys, time, socket
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
MAPS = ["geo", "corp"]
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

def main() -> int:
    from playwright.sync_api import sync_playwright
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1"],
                           cwd=OUT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_port(PORT):
            print("[WARN] static server did not start")
            return 1
        rc = 0
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for m in MAPS:
                if not (OUT / m / "index.html").exists():
                    print(f"[WARN] {m}: no index.html, skip")
                    continue
                page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1.5)
                try:
                    page.goto(f"http://127.0.0.1:{PORT}/{m}/", wait_until="networkidle", timeout=60000)
                    page.wait_for_timeout(4000)   # D3 の描画・アニメーション待ち
                    png = OUT / m / "map.png"
                    page.screenshot(path=str(png), full_page=False)
                    print(f"wrote {png.relative_to(ROOT)} ({png.stat().st_size//1024} KB)")
                except Exception as e:
                    rc = 1
                    print(f"[WARN] {m}: render failed: {e}")
                finally:
                    page.close()
            browser.close()
        return rc
    finally:
        srv.terminate()

if __name__ == "__main__":
    sys.exit(main())
