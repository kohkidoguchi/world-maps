"""The slow clock: structural facts that move yearly or slower.

* World Bank API — military expenditure, armed forces, population, GDP (WDI) and the Worldwide Governance
  Indicators (political stability, voice & accountability, rule of law, government effectiveness, corruption).
* Alliances and blocs — a hand-maintained membership table (the deep structure of the map).
* Election calendar — parsed from Wikipedia's national electoral calendar pages (this year and next).
* UN press releases RSS — Security Council / General Assembly meetings coverage.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import re

from .common import COUNTRIES, fetched_at, http_get, iso3_from_en

log = logging.getLogger("geopolitics.structure")

WB = "https://api.worldbank.org/v2/country/all/indicator/{ind}?format=json&mrv={mrv}&per_page=500{extra}"
WB_INDICATORS = {  # key: (indicator id, source, label_ja, unit)
    "milex_gdp": ("MS.MIL.XPND.GD.ZS", "", "軍事費（対GDP比）", "%GDP"),
    "milex_usd": ("MS.MIL.XPND.CD", "", "軍事費（米ドル）", "USD"),
    "forces": ("MS.MIL.TOTL.P1", "", "兵員数", "人"),
    "pop": ("SP.POP.TOTL", "", "人口", "人"),
    "gdp_usd": ("NY.GDP.MKTP.CD", "", "名目GDP（米ドル）", "USD"),
    "wgi_pv": ("GOV_WGI_PV.EST", "3", "政治的安定・非暴力（WGI）", "-2.5〜+2.5"),
    "wgi_va": ("GOV_WGI_VA.EST", "3", "発言力と説明責任（WGI）", "-2.5〜+2.5"),
    "wgi_rl": ("GOV_WGI_RL.EST", "3", "法の支配（WGI）", "-2.5〜+2.5"),
    "wgi_ge": ("GOV_WGI_GE.EST", "3", "政府の有効性（WGI）", "-2.5〜+2.5"),
    "wgi_cc": ("GOV_WGI_CC.EST", "3", "腐敗の抑制（WGI）", "-2.5〜+2.5"),
}

BLOCS = {  # id: (name_ja, note, members)
    "NATO": ("NATO", "北大西洋条約機構（32か国）", ["ALB", "BEL", "BGR", "CAN", "HRV", "CZE", "DNK", "EST", "FIN", "FRA", "DEU", "GRC", "HUN", "ISL", "ITA", "LVA", "LTU", "LUX", "MNE", "NLD", "MKD", "NOR", "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE", "TUR", "GBR", "USA"]),
    "EU": ("EU", "欧州連合（27か国）", ["AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA", "DEU", "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD", "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE"]),
    "US_ALLY_ASIA": ("米国の二国間同盟（アジア太平洋）", "日米・米韓・米比・米泰・ANZUS", ["JPN", "KOR", "PHL", "THA", "AUS", "NZL"]),
    "QUAD": ("QUAD", "日米豪印戦略対話", ["USA", "JPN", "AUS", "IND"]),
    "AUKUS": ("AUKUS", "米英豪の安全保障パートナーシップ", ["AUS", "GBR", "USA"]),
    "FIVE_EYES": ("ファイブ・アイズ", "情報共有枠組み", ["USA", "GBR", "CAN", "AUS", "NZL"]),
    "G7": ("G7", "主要7か国", ["USA", "JPN", "DEU", "GBR", "FRA", "ITA", "CAN"]),
    "BRICS": ("BRICS+", "2024年以降の拡大メンバー（サウジは参加を正式表明せず）", ["BRA", "RUS", "IND", "CHN", "ZAF", "EGY", "ETH", "IRN", "ARE", "IDN"]),
    "SCO": ("上海協力機構", "正式加盟国", ["CHN", "RUS", "IND", "PAK", "KAZ", "KGZ", "TJK", "UZB", "IRN", "BLR"]),
    "CSTO": ("CSTO", "集団安全保障条約機構（アルメニアは2024年から参加凍結）", ["RUS", "BLR", "KAZ", "KGZ", "TJK", "ARM"]),
    "ASEAN": ("ASEAN", "東南アジア諸国連合（東ティモールは2025年加盟）", ["BRN", "KHM", "IDN", "LAO", "MYS", "MMR", "PHL", "SGP", "THA", "VNM", "TLS"]),
    "GCC": ("GCC", "湾岸協力会議", ["SAU", "ARE", "QAT", "KWT", "BHR", "OMN"]),
    "NUCLEAR": ("核保有国", "核兵器を保有する9か国（イスラエルは未公表）", ["USA", "RUS", "CHN", "FRA", "GBR", "IND", "PAK", "PRK", "ISR"]),
    "UNSC_P5": ("国連安保理 常任理事国", "拒否権を持つ5か国", ["USA", "RUS", "CHN", "FRA", "GBR"]),
    "OPEC": ("OPEC", "石油輸出国機構（2024年以降の加盟国）", ["SAU", "IRN", "IRQ", "KWT", "ARE", "DZA", "LBY", "NGA", "VEN", "COG", "GNQ", "GAB"]),
    "RU_AXIS": ("ロシアと安全保障条約を結ぶ国", "露朝包括的戦略パートナーシップ条約（2024）・露イラン条約（2025）・ベラルーシ連合国家", ["RUS", "PRK", "IRN", "BLR"]),
}

MONTHS = {m: i + 1 for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                          "September", "October", "November", "December"])}


def _wb(ind: str, source: str, mrv: int = 1) -> tuple[dict, str]:
    extra = f"&source={source}" if source else ""
    url = WB.format(ind=ind, mrv=mrv, extra=extra)
    doc = json.loads(http_get(url, ttl_hours=24 * 7, timeout=120))
    if not isinstance(doc, list) or len(doc) < 2 or not doc[1]:
        raise RuntimeError(f"WB {ind}: {str(doc)[:120]}")
    out = {}
    for row in doc[1]:
        iso = row.get("countryiso3code")
        if iso in COUNTRIES and row.get("value") is not None:
            out[iso] = (row["value"], row["date"])
    return out, doc[0].get("lastupdated", "")


def world_bank() -> dict:
    data: dict[str, dict] = {k: {} for k in WB_INDICATORS}
    meta = {}
    for key, (ind, src, label, unit) in WB_INDICATORS.items():
        try:
            vals, upd = _wb(ind, src)
            data[key] = vals
            meta[key] = {"label": label, "unit": unit, "indicator": ind, "updated": upd, "n": len(vals),
                         "year": max((v[1] for v in vals.values()), default="")}
            log.info("WB %s: %d countries (latest %s)", ind, len(vals), meta[key]["year"])
        except Exception as e:
            log.warning("WB %s failed: %s", ind, str(e)[:120])
            meta[key] = {"label": label, "unit": unit, "indicator": ind, "error": str(e)[:120]}
    return {"data": data, "meta": meta}


def power_index(wb: dict) -> dict[str, float]:
    """0-1 index of state 'size': average of GDP share and military-spend share, each normalised by the maximum."""
    gdp = {k: v[0] for k, v in wb["data"].get("gdp_usd", {}).items()}
    mil = {k: v[0] for k, v in wb["data"].get("milex_usd", {}).items()}
    gmax = max(gdp.values(), default=1) or 1
    mmax = max(mil.values(), default=1) or 1
    out = {}
    for iso in COUNTRIES:
        g = gdp.get(iso, 0) / gmax
        m = mil.get(iso, 0) / mmax
        out[iso] = round(0.5 * g + 0.5 * m, 4)
    out.setdefault("TWN", 0.12)  # not in World Bank data
    return out


def _strip_wiki(s: str) -> str:
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"'{2,}", "", s)
    return html.unescape(s).strip(" ,;")


def elections(years: list[int]) -> list[dict]:
    out = []
    for y in years:
        url = f"https://en.wikipedia.org/w/api.php?action=parse&page={y}_national_electoral_calendar&prop=wikitext&format=json&formatversion=2"
        try:
            doc = json.loads(http_get(url, ttl_hours=24, timeout=60))
            wt = doc["parse"]["wikitext"]
        except Exception as e:
            log.warning("wikipedia calendar %d failed: %s", y, str(e)[:100])
            continue
        month = None
        pending_date = None  # "* 27 September:" followed by "** [[...]]" sub-bullets
        for line in wt.split("\n"):
            m = re.match(r"^==+\s*([A-Z][a-z]+)\s*==+", line)
            if m and m.group(1) in MONTHS:
                month = MONTHS[m.group(1)]
                continue
            if not line.startswith("*") or month is None:
                continue
            if line.startswith("**"):
                if not pending_date:
                    continue
                date, rest = pending_date, line.lstrip("*").strip()
            else:
                body = line.lstrip("*").strip()
                md = re.match(r"^(\d{1,2})(?:\s*[–-]\s*(\d{1,2}))?(?:\s+([A-Z][a-z]+))?\s*(?:\(.*?\))?\s*:\s*(.*)$", body)
                if not md:
                    continue
                day = int(md.group(1))
                mon = MONTHS.get(md.group(3) or "", month)
                try:
                    date = dt.date(y, mon, day).isoformat()
                except ValueError:
                    continue
                rest = md.group(4)
                if not rest.strip():
                    pending_date = date
                    continue
                pending_date = None
            cm = re.search(r"\[\[(?:Elections|Politics) in (?:the )?([^|\]]+)\|([^\]]+)\]\]", rest) or re.search(r"\[\[([^|\]]+)\|([^\]]+)\]\]", rest)
            country_en = cm.group(2) if cm else _strip_wiki(rest.split(",")[0])
            iso = iso3_from_en(country_en) or iso3_from_en(cm.group(1) if cm else "")
            what = re.sub(r"<br\s*/?>", " ", _strip_wiki(rest))
            what = re.sub(r"^\s*" + re.escape(country_en) + r"\s*,?\s*", "", what).strip()
            if not country_en and not what:
                continue
            tentative = rest.lstrip().startswith("''")
            out.append({"date": date, "iso": iso, "country": country_en, "what": what[:120], "tentative": tentative})
    out.sort(key=lambda e: e["date"])
    return out


def un_press() -> list[dict]:
    try:
        xml = http_get("https://press.un.org/en/rss.xml", ttl_hours=2, timeout=60).decode("utf-8", "replace")
    except Exception as e:
        log.warning("UN press RSS failed: %s", str(e)[:100])
        return []
    items = []
    for it in re.finditer(r"<item>(.*?)</item>", xml, re.S):
        b = it.group(1)
        g = lambda tag: html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", (re.search(f"<{tag}>(.*?)</{tag}>", b, re.S) or [None, ""])[1]).strip())
        link = g("link")
        kind = "安保理" if "/sc" in link else "総会" if "/ga" in link else "事務総長" if "/sg" in link else "国連"
        pub = g("pubDate")
        try:
            d = dt.datetime.strptime(pub[:25].strip(), "%a, %d %b %Y %H:%M:%S").date().isoformat()
        except Exception:
            d = pub[:16]
        items.append({"date": d, "kind": kind, "title": g("title"), "url": link, "desc": re.sub(r"\s+", " ", g("description"))[:300]})
    return items[:30]


def fetch_all() -> dict:
    wb = world_bank()
    now = dt.date.today()
    return {
        "wb": wb,
        "power": power_index(wb),
        "blocs": {k: {"name": v[0], "note": v[1], "members": v[2]} for k, v in BLOCS.items()},
        "elections": elections([now.year, now.year + 1]),
        "un_press": un_press(),
        "fetched": {"wb": fetched_at(WB.format(ind="SP.POP.TOTL", mrv=1, extra="")), "un_press": fetched_at("https://press.un.org/en/rss.xml")},
    }
