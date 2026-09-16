"""ローカルの news_digest/digest.py（正本）を、公開リポジトリ用にサニタイズしてコピーする。

    python tools/sync_digest.py

個人情報の既定値（プロフィール文・2人目のアドレス）を空にし、値は GitHub Secrets 経由の環境変数
（USER_PROFILE, DUNSRI_ADDRESS）に委ねる。コピー後に個人情報が残っていないか検査する。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT.parent / "news_digest"
DST = ROOT / "news_digest"

FORBIDDEN = ("BCGに勤める", "kidoguchi@", "dunsri0921", "waseda")

def main():
    s = (SRC / "digest.py").read_text(encoding="utf-8")
    i = s.index('_DEFAULT_PROFILE = """')
    j = s.index('"""', i + 22) + 3
    s = s[:i] + '_DEFAULT_PROFILE = ""   # 公開用：プロフィールは USER_PROFILE 環境変数（Secret）で渡す' + s[j:]
    s = s.replace('os.environ.get("DUNSRI_ADDRESS", "dunsri0921@gmail.com")', 'os.environ.get("DUNSRI_ADDRESS", "")')
    bad = [k for k in FORBIDDEN if k in s]
    if bad:
        raise SystemExit(f"personal data still present: {bad}")
    DST.mkdir(exist_ok=True)
    (DST / "digest.py").write_text(s, encoding="utf-8")
    (DST / "requirements.txt").write_text((SRC / "requirements.txt").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"synced -> {DST / 'digest.py'} ({len(s)//1024} KB), clean")

if __name__ == "__main__":
    main()
