"""Sanctions lists: the "coercive economic statecraft" layer.

* OFAC SDN list (US Treasury) — full CSV, counted by program and mapped to target countries; daily vintages are
  diffed so new / removed designations show up as flows. The OFAC "recent actions" page gives dated headlines.
* EU consolidated financial sanctions list (CSV) — counted by programme; recent publication dates as flows.
* UN Security Council consolidated list (XML) — counted by list type; LISTED_ON dates as flows.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import io
import json
import logging
import os
import re
from collections import Counter, defaultdict

from .common import VINTAGE_DIR, fetched_at, http_get

log = logging.getLogger("geopolitics.sanctions")

OFAC_SDN = "https://www.treasury.gov/ofac/downloads/sdn.csv"
OFAC_RECENT = "https://ofac.treasury.gov/recent-actions"
EU_CSV = "https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
UN_XML = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"

# OFAC program prefix -> (ISO3 target or None for thematic, label_ja)
OFAC_PROGRAMS = [
    ("RUSSIA", "RUS", "ロシア"), ("UKRAINE", "RUS", "ロシア（ウクライナ関連）"), ("CAATSA", "RUS", "ロシア（CAATSA）"),
    ("IRAN", "IRN", "イラン"), ("IRGC", "IRN", "イラン（革命防衛隊）"), ("IFSR", "IRN", "イラン（金融）"),
    ("DPRK", "PRK", "北朝鮮"), ("NKOREA", "PRK", "北朝鮮"), ("VENEZUELA", "VEN", "ベネズエラ"), ("SYRIA", "SYR", "シリア"),
    ("BELARUS", "BLR", "ベラルーシ"), ("BURMA", "MMR", "ミャンマー"), ("CUBA", "CUB", "キューバ"), ("DRCONGO", "COD", "コンゴ民主共和国"),
    ("SOUTH SUDAN", "SSD", "南スーダン"), ("YEMEN", "YEM", "イエメン"), ("LIBYA", "LBY", "リビア"), ("SUDAN", "SDN", "スーダン"),
    ("DARFUR", "SDN", "スーダン（ダルフール）"), ("CAR", "CAF", "中央アフリカ"), ("MALI", "MLI", "マリ"), ("NICARAGUA", "NIC", "ニカラグア"),
    ("ZIMBABWE", "ZWE", "ジンバブエ"), ("HK", "HKG", "香港"), ("BALKANS", "SRB", "西バルカン"), ("IRAQ", "IRQ", "イラク"),
    ("LEBANON", "LBN", "レバノン"), ("SOMALIA", "SOM", "ソマリア"), ("ETHIOPIA", "ETH", "エチオピア"), ("WEST BANK", "PSE", "ヨルダン川西岸"),
    ("HAITI", "HTI", "ハイチ"), ("AFGHANISTAN", "AFG", "アフガニスタン"), ("CHINA", "CHN", "中国（軍産複合体）"), ("CMIC", "CHN", "中国（軍産複合体）"),
    ("SDGT", None, "テロ（SDGT）"), ("FTO", None, "テロ組織（FTO）"), ("SDNTK", None, "麻薬（SDNTK）"), ("SDNT", None, "麻薬（SDNT）"),
    ("ILLICIT-DRUGS", None, "麻薬"), ("TCO", None, "国際犯罪組織"), ("NPWMD", None, "大量破壊兵器拡散"), ("CYBER", None, "サイバー"),
    ("GLOMAG", None, "人権・腐敗（グローバル・マグニツキー）"), ("MAGNIT", None, "人権（マグニツキー）"), ("ELECTION", None, "選挙介入"),
    ("HOSTAGES", None, "人質・不当拘束"), ("PAARSSR", None, "ロシア有害活動"), ("HRIT", None, "人権侵害・検閲"),
]


def _program_target(prog: str) -> tuple[str | None, str]:
    p = prog.strip().upper()
    for prefix, iso, label in OFAC_PROGRAMS:
        if p.startswith(prefix):
            return iso, label
    return None, p or "その他"


def _ofac() -> dict:
    blob = http_get(OFAC_SDN, ttl_hours=6, timeout=120)
    text = blob.decode("latin-1")
    rows = list(csv.reader(io.StringIO(text)))
    by_prog: Counter = Counter()
    by_iso: Counter = Counter()
    label_of: dict[str, str] = {}
    entries: dict[str, tuple[str, str, str]] = {}  # ent_num -> (name, type, programs)
    for r in rows:
        if len(r) < 4 or not r[0].strip().isdigit():
            continue
        ent, name, typ, progs = r[0].strip(), r[1].strip(), r[2].strip(), r[3].strip()
        entries[ent] = (name, typ, progs)
        seen = set()
        for prog in re.split(r"[;\]\[]+", progs):
            prog = prog.strip()
            if not prog:
                continue
            iso, label = _program_target(prog)
            by_prog[label] += 1
            label_of[label] = label
            if iso and iso not in seen:
                by_iso[iso] += 1
                seen.add(iso)
    # vintage diff
    today = dt.date.today().isoformat()
    vint_path = os.path.join(VINTAGE_DIR, f"ofac_{today}.json")
    prev = sorted(f for f in os.listdir(VINTAGE_DIR) if f.startswith("ofac_") and f < f"ofac_{today}.json")
    added, removed, prev_date = [], [], ""
    if prev:
        prev_date = prev[-1][5:15]
        old = json.load(open(os.path.join(VINTAGE_DIR, prev[-1]), encoding="utf-8"))
        old_ids = set(old)
        for ent, (name, typ, progs) in entries.items():
            if ent not in old_ids:
                added.append({"name": name, "type": typ, "programs": progs, "iso": _program_target(progs.split(";")[0])[0]})
        for ent, v in old.items():
            if ent not in entries:
                removed.append({"name": v[0], "type": v[1], "programs": v[2], "iso": _program_target(v[2].split(";")[0])[0]})
    json.dump(entries, open(vint_path, "w", encoding="utf-8"))
    # recent actions page
    actions = []
    try:
        page = http_get(OFAC_RECENT, ttl_hours=3, timeout=60).decode("utf-8", "replace")
        seen_dates = set()
        for m in re.finditer(r'<a href="/recent-actions/(\d{8})"[^>]*>([^<]+)</a>', page):
            d, title = m.group(1), html.unescape(m.group(2)).strip()
            if d in seen_dates:
                continue
            seen_dates.add(d)
            isos = []
            for prefix, iso, _ in OFAC_PROGRAMS:
                if iso and prefix.title() in title and iso not in isos:
                    isos.append(iso)
            for kw, iso in (("Iran", "IRN"), ("Russia", "RUS"), ("North Korea", "PRK"), ("DPRK", "PRK"), ("Venezuela", "VEN"),
                            ("Syria", "SYR"), ("Belarus", "BLR"), ("Burma", "MMR"), ("Cuba", "CUB"), ("China", "CHN"), ("Hong Kong", "HKG"),
                            ("Sudan", "SDN"), ("Yemen", "YEM"), ("Houthi", "YEM"), ("Hizballah", "LBN"), ("Hezbollah", "LBN"), ("Hamas", "PSE"),
                            ("Mexico", "MEX"), ("Colombia", "COL"), ("Haiti", "HTI"), ("Libya", "LBY"), ("Iraq", "IRQ"), ("Nicaragua", "NIC")):
                if kw in title and iso not in isos:
                    isos.append(iso)
            actions.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "title": title, "countries": isos,
                            "url": f"https://ofac.treasury.gov/recent-actions/{d}"})
            if len(actions) >= 25:
                break
    except Exception as e:
        log.warning("OFAC recent actions failed: %s", str(e)[:100])
    return {"entries": len(entries), "by_program": sorted(by_prog.items(), key=lambda kv: -kv[1]),
            "by_iso": dict(by_iso), "added": added[:200], "removed": removed[:200], "prev_vintage": prev_date,
            "actions": actions, "fetched": fetched_at(OFAC_SDN)}


EU_PROG = {  # EU programme codes -> ISO3 (thematic ones -> None)
    "RUS": "RUS", "UKR": "RUS", "BLR": "BLR", "IRN": "IRN", "PRK": "PRK", "SYR": "SYR", "MMR": "MMR", "VEN": "VEN",
    "LBY": "LBY", "YEM": "YEM", "SDN": "SDN", "SSD": "SSD", "SOM": "SOM", "COD": "COD", "CAF": "CAF", "MLI": "MLI",
    "NIC": "NIC", "ZWE": "ZWE", "TUR": "TUR", "IRQ": "IRQ", "LBN": "LBN", "GIN": "GIN", "GNB": "GNB", "BDI": "BDI",
    "TUN": "TUN", "EGY": "EGY", "HTI": "HTI", "AFG": "AFG", "MDA": "MDA", "BIH": "BIH", "CHN": "CHN", "SRB": "SRB",
    "PSE": "PSE", "ISR": "ISR", "TAQA": None, "TERR": None, "CHEM": None, "CYB": None, "HR": None, "HUR": None,
}


def _eu() -> dict:
    blob = http_get(EU_CSV, ttl_hours=12, timeout=180)
    text = blob.decode("utf-8-sig", "replace")
    rd = csv.DictReader(io.StringIO(text), delimiter=";")
    by_prog: Counter = Counter()
    by_iso: Counter = Counter()
    recent: dict[str, dict] = {}
    seen_entities = set()
    gen_date = ""
    cutoff = None
    for row in rd:
        if not gen_date:
            gen_date = row.get("fileGenerationDate", "")
            m0 = re.match(r"(\d{2})/(\d{2})/(\d{4})", gen_date)
            gen = dt.date(int(m0.group(3)), int(m0.group(2)), int(m0.group(1))) if m0 else dt.date.today()
            cutoff = (gen - dt.timedelta(days=60)).isoformat()  # "recent" relative to the list's own generation date
        ent = row.get("Entity_LogicalId")
        if not ent or ent in seen_entities:
            continue
        seen_entities.add(ent)
        prog = (row.get("Entity_Regulation_Programme") or "").strip()
        by_prog[prog or "?"] += 1
        iso = EU_PROG.get(prog)
        if iso:
            by_iso[iso] += 1
        pub = (row.get("Entity_Regulation_PublicationDate") or "").strip()
        if pub >= cutoff:
            r = recent.setdefault(pub + "|" + prog, {"date": pub, "programme": prog, "iso": iso, "n": 0,
                                                     "reg": row.get("Entity_Regulation_NumberTitle", ""),
                                                     "names": []})
            r["n"] += 1
            if len(r["names"]) < 5:
                r["names"].append(row.get("NameAlias_WholeName") or row.get("NameAlias_LastName") or "")
    rec = sorted(recent.values(), key=lambda r: r["date"], reverse=True)[:40]
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", gen_date)
    gen_iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else gen_date
    return {"entities": len(seen_entities), "generated": gen_iso, "by_programme": sorted(by_prog.items(), key=lambda kv: -kv[1]),
            "by_iso": dict(by_iso), "recent": rec, "fetched": fetched_at(EU_CSV)}


UN_LIST = {"Al-Qaida": None, "Taliban": "AFG", "DPRK": "PRK", "Iraq": "IRQ", "Somalia": "SOM", "Libya": "LBY", "DRC": "COD",
           "Sudan": "SDN", "South Sudan": "SSD", "CAR": "CAF", "Yemen": "YEM", "Mali": "MLI", "Haiti": "HTI", "Guinea-Bissau": "GNB",
           "ISIL (Da'esh) & Al-Qaida": None, "Iran": "IRN", "Lebanon": "LBN"}


def _un() -> dict:
    blob = http_get(UN_XML, ttl_hours=12, timeout=120)
    text = blob.decode("utf-8", "replace")
    by_list: Counter = Counter()
    recent: list[dict] = []
    gen = re.search(r'dateGenerated="([^"]+)"', text)
    cutoff = (dt.date.today() - dt.timedelta(days=60)).isoformat()
    for block in re.finditer(r"<(INDIVIDUAL|ENTITY)>(.*?)</\1>", text, re.S):
        kind, body = block.group(1), block.group(2)
        lt = re.search(r"<UN_LIST_TYPE>([^<]*)</UN_LIST_TYPE>", body)
        lst = (lt.group(1).strip() if lt else "?")
        by_list[lst] += 1
        lo = re.search(r"<LISTED_ON>([^<]*)</LISTED_ON>", body)
        listed = lo.group(1).strip()[:10] if lo else ""
        if listed >= cutoff:
            names = re.findall(r"<(?:FIRST_NAME|SECOND_NAME|THIRD_NAME)>([^<]*)<", body)
            recent.append({"date": listed, "list": lst, "kind": kind.lower(), "name": " ".join(n for n in names if n)[:80]})
    by_iso: Counter = Counter()
    for lst, n in by_list.items():
        iso = None
        for k, v in UN_LIST.items():
            if k.lower() in lst.lower():
                iso = v
                break
        if iso:
            by_iso[iso] += n
    recent.sort(key=lambda r: r["date"], reverse=True)
    return {"entries": sum(by_list.values()), "generated": (gen.group(1)[:10] if gen else ""),
            "by_list": sorted(by_list.items(), key=lambda kv: -kv[1]), "by_iso": dict(by_iso), "recent": recent[:40],
            "fetched": fetched_at(UN_XML)}


def fetch_all() -> dict:
    out: dict = {}
    for name, fn in (("ofac", _ofac), ("eu", _eu), ("un", _un)):
        try:
            out[name] = fn()
            log.info("%s sanctions ok", name)
        except Exception as e:
            log.exception("%s sanctions failed", name)
            out[name] = {"error": str(e)[:200]}
    return out
