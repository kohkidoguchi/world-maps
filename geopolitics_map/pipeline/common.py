"""Shared plumbing for the geopolitics map pipeline.

* HTTP with on-disk caching (works behind the corporate SSL proxy via truststore)
* Country reference table: ISO3 -> ISO numeric (topojson id), FIPS 10-4 (GDELT geo codes), names
* CAMEO event-code labels (Japanese)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import time

import requests

try:  # corporate proxy: trust the Windows certificate store
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
VINTAGE_DIR = os.path.join(DATA_DIR, "vintages")
for _d in (RAW_DIR, VINTAGE_DIR):
    os.makedirs(_d, exist_ok=True)

log = logging.getLogger("geopolitics")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) geopolitics-map/0.1", "Accept": "*/*"}


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- HTTP
def http_get(url: str, *, ttl_hours: float = 6, timeout: int = 120, headers: dict | None = None,
             retries: int = 3, ok404: bool = False) -> bytes | None:
    """GET with a simple file cache keyed by URL. Raises on HTTP errors (returns None on 404 if ok404)."""
    key = hashlib.sha1(url.encode()).hexdigest()
    path = os.path.join(RAW_DIR, key + ".bin")
    meta = os.path.join(RAW_DIR, key + ".json")
    if os.path.exists(path) and os.path.exists(meta):
        age_h = (time.time() - os.path.getmtime(path)) / 3600
        if age_h < ttl_hours:
            return open(path, "rb").read()
    t0 = time.time()
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout, headers={**UA, **(headers or {})})
            break
        except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError,
                requests.exceptions.ReadTimeout) as e:
            last_exc = e
            log.warning("retry %d for %s: %s", attempt + 1, url[:100], str(e)[:80])
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"network failure for {url[:160]} :: {last_exc}")
    if r.status_code == 404 and ok404:
        return None
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} for {url[:160]} :: {r.text[:200]}")
    open(path, "wb").write(r.content)
    json.dump({"url": url, "fetched_at": now_iso(), "status": r.status_code, "bytes": len(r.content),
               "seconds": round(time.time() - t0, 1)}, open(meta, "w"))
    log.info("GET %s -> %d bytes in %.1fs", url[:110], len(r.content), time.time() - t0)
    return r.content


def fetched_at(url: str) -> str:
    key = hashlib.sha1(url.encode()).hexdigest()
    meta = os.path.join(RAW_DIR, key + ".json")
    if os.path.exists(meta):
        return json.load(open(meta)).get("fetched_at", "")
    return ""


def purge_raw(older_than_days: float, prefix_url: str) -> int:
    """Delete cached files for URLs starting with prefix_url that are older than N days (immutable feeds)."""
    n = 0
    for f in os.listdir(RAW_DIR):
        if not f.endswith(".json"):
            continue
        p = os.path.join(RAW_DIR, f)
        try:
            m = json.load(open(p))
        except Exception:
            continue
        if not m.get("url", "").startswith(prefix_url):
            continue
        if (time.time() - os.path.getmtime(p)) / 86400 > older_than_days:
            for ext in (".json", ".bin"):
                q = os.path.join(RAW_DIR, f[:-5] + ext)
                if os.path.exists(q):
                    os.remove(q)
            n += 1
    return n


# --------------------------------------------------------------------------- countries
# ISO3: (ISO numeric as in world-atlas topojson or None, FIPS 10-4 code used by GDELT geo fields, name_ja, name_en,
#        optional (lat, lon) for territories without a topojson shape)
COUNTRIES: dict[str, tuple] = {
    "AFG": (4, "AF", "アフガニスタン", "Afghanistan"), "ALB": (8, "AL", "アルバニア", "Albania"),
    "DZA": (12, "AG", "アルジェリア", "Algeria"), "AGO": (24, "AO", "アンゴラ", "Angola"),
    "ARG": (32, "AR", "アルゼンチン", "Argentina"), "ARM": (51, "AM", "アルメニア", "Armenia"),
    "AUS": (36, "AS", "オーストラリア", "Australia"), "AUT": (40, "AU", "オーストリア", "Austria"),
    "AZE": (31, "AJ", "アゼルバイジャン", "Azerbaijan"), "BHS": (44, "BF", "バハマ", "Bahamas"),
    "BHR": (48, "BA", "バーレーン", "Bahrain", (26.1, 50.6)), "BGD": (50, "BG", "バングラデシュ", "Bangladesh"),
    "BLR": (112, "BO", "ベラルーシ", "Belarus"), "BEL": (56, "BE", "ベルギー", "Belgium"),
    "BLZ": (84, "BH", "ベリーズ", "Belize"), "BEN": (204, "BN", "ベナン", "Benin"),
    "BTN": (64, "BT", "ブータン", "Bhutan"), "BOL": (68, "BL", "ボリビア", "Bolivia"),
    "BIH": (70, "BK", "ボスニア・ヘルツェゴビナ", "Bosnia and Herzegovina"), "BWA": (72, "BC", "ボツワナ", "Botswana"),
    "BRA": (76, "BR", "ブラジル", "Brazil"), "BRN": (96, "BX", "ブルネイ", "Brunei"),
    "BGR": (100, "BU", "ブルガリア", "Bulgaria"), "BFA": (854, "UV", "ブルキナファソ", "Burkina Faso"),
    "BDI": (108, "BY", "ブルンジ", "Burundi"), "KHM": (116, "CB", "カンボジア", "Cambodia"),
    "CMR": (120, "CM", "カメルーン", "Cameroon"), "CAN": (124, "CA", "カナダ", "Canada"),
    "CAF": (140, "CT", "中央アフリカ", "Central African Republic"), "TCD": (148, "CD", "チャド", "Chad"),
    "CHL": (152, "CI", "チリ", "Chile"), "CHN": (156, "CH", "中国", "China"),
    "COL": (170, "CO", "コロンビア", "Colombia"), "COG": (178, "CF", "コンゴ共和国", "Republic of the Congo"),
    "COD": (180, "CG", "コンゴ民主共和国", "DR Congo"), "CRI": (188, "CS", "コスタリカ", "Costa Rica"),
    "CIV": (384, "IV", "コートジボワール", "Côte d'Ivoire"), "HRV": (191, "HR", "クロアチア", "Croatia"),
    "CUB": (192, "CU", "キューバ", "Cuba"), "CYP": (196, "CY", "キプロス", "Cyprus"),
    "CZE": (203, "EZ", "チェコ", "Czechia"), "DNK": (208, "DA", "デンマーク", "Denmark"),
    "DJI": (262, "DJ", "ジブチ", "Djibouti"), "DOM": (214, "DR", "ドミニカ共和国", "Dominican Republic"),
    "ECU": (218, "EC", "エクアドル", "Ecuador"), "EGY": (818, "EG", "エジプト", "Egypt"),
    "SLV": (222, "ES", "エルサルバドル", "El Salvador"), "GNQ": (226, "EK", "赤道ギニア", "Equatorial Guinea"),
    "ERI": (232, "ER", "エリトリア", "Eritrea"), "EST": (233, "EN", "エストニア", "Estonia"),
    "SWZ": (748, "WZ", "エスワティニ", "Eswatini"), "ETH": (231, "ET", "エチオピア", "Ethiopia"),
    "FJI": (242, "FJ", "フィジー", "Fiji"), "FIN": (246, "FI", "フィンランド", "Finland"),
    "FRA": (250, "FR", "フランス", "France"), "GAB": (266, "GB", "ガボン", "Gabon"),
    "GMB": (270, "GA", "ガンビア", "Gambia"), "GEO": (268, "GG", "ジョージア", "Georgia"),
    "DEU": (276, "GM", "ドイツ", "Germany"), "GHA": (288, "GH", "ガーナ", "Ghana"),
    "GRC": (300, "GR", "ギリシャ", "Greece"), "GRL": (304, "GL", "グリーンランド", "Greenland"),
    "GTM": (320, "GT", "グアテマラ", "Guatemala"), "GIN": (324, "GV", "ギニア", "Guinea"),
    "GNB": (624, "PU", "ギニアビサウ", "Guinea-Bissau"), "GUY": (328, "GY", "ガイアナ", "Guyana"),
    "HTI": (332, "HA", "ハイチ", "Haiti"), "HND": (340, "HO", "ホンジュラス", "Honduras"),
    "HKG": (None, "HK", "香港", "Hong Kong", (22.3, 114.2)), "HUN": (348, "HU", "ハンガリー", "Hungary"),
    "ISL": (352, "IC", "アイスランド", "Iceland"), "IND": (356, "IN", "インド", "India"),
    "IDN": (360, "ID", "インドネシア", "Indonesia"), "IRN": (364, "IR", "イラン", "Iran"),
    "IRQ": (368, "IZ", "イラク", "Iraq"), "IRL": (372, "EI", "アイルランド", "Ireland"),
    "ISR": (376, "IS", "イスラエル", "Israel"), "ITA": (380, "IT", "イタリア", "Italy"),
    "JAM": (388, "JM", "ジャマイカ", "Jamaica"), "JPN": (392, "JA", "日本", "Japan"),
    "JOR": (400, "JO", "ヨルダン", "Jordan"), "KAZ": (398, "KZ", "カザフスタン", "Kazakhstan"),
    "KEN": (404, "KE", "ケニア", "Kenya"), "PRK": (408, "KN", "北朝鮮", "North Korea"),
    "KOR": (410, "KS", "韓国", "South Korea"), "XKX": (None, "KV", "コソボ", "Kosovo", (42.6, 20.9)),
    "KWT": (414, "KU", "クウェート", "Kuwait"), "KGZ": (417, "KG", "キルギス", "Kyrgyzstan"),
    "LAO": (418, "LA", "ラオス", "Laos"), "LVA": (428, "LG", "ラトビア", "Latvia"),
    "LBN": (422, "LE", "レバノン", "Lebanon"), "LSO": (426, "LT", "レソト", "Lesotho"),
    "LBR": (430, "LI", "リベリア", "Liberia"), "LBY": (434, "LY", "リビア", "Libya"),
    "LTU": (440, "LH", "リトアニア", "Lithuania"), "LUX": (442, "LU", "ルクセンブルク", "Luxembourg"),
    "MDG": (450, "MA", "マダガスカル", "Madagascar"), "MWI": (454, "MI", "マラウイ", "Malawi"),
    "MYS": (458, "MY", "マレーシア", "Malaysia"), "MDV": (None, "MV", "モルディブ", "Maldives", (4.2, 73.5)),
    "MLI": (466, "ML", "マリ", "Mali"), "MLT": (None, "MT", "マルタ", "Malta", (35.9, 14.5)),
    "MRT": (478, "MR", "モーリタニア", "Mauritania"), "MUS": (None, "MP", "モーリシャス", "Mauritius", (-20.2, 57.5)),
    "MEX": (484, "MX", "メキシコ", "Mexico"), "MDA": (498, "MD", "モルドバ", "Moldova"),
    "MNG": (496, "MG", "モンゴル", "Mongolia"), "MNE": (499, "MJ", "モンテネグロ", "Montenegro"),
    "MAR": (504, "MO", "モロッコ", "Morocco"), "MOZ": (508, "MZ", "モザンビーク", "Mozambique"),
    "MMR": (104, "BM", "ミャンマー", "Myanmar"), "NAM": (516, "WA", "ナミビア", "Namibia"),
    "NPL": (524, "NP", "ネパール", "Nepal"), "NLD": (528, "NL", "オランダ", "Netherlands"),
    "NZL": (554, "NZ", "ニュージーランド", "New Zealand"), "NIC": (558, "NU", "ニカラグア", "Nicaragua"),
    "NER": (562, "NG", "ニジェール", "Niger"), "NGA": (566, "NI", "ナイジェリア", "Nigeria"),
    "MKD": (807, "MK", "北マケドニア", "North Macedonia"), "NOR": (578, "NO", "ノルウェー", "Norway"),
    "OMN": (512, "MU", "オマーン", "Oman"), "PAK": (586, "PK", "パキスタン", "Pakistan"),
    "PSE": (275, "WE", "パレスチナ", "Palestine"), "PAN": (591, "PM", "パナマ", "Panama"),
    "PNG": (598, "PP", "パプアニューギニア", "Papua New Guinea"), "PRY": (600, "PA", "パラグアイ", "Paraguay"),
    "PER": (604, "PE", "ペルー", "Peru"), "PHL": (608, "RP", "フィリピン", "Philippines"),
    "POL": (616, "PL", "ポーランド", "Poland"), "PRT": (620, "PO", "ポルトガル", "Portugal"),
    "QAT": (634, "QA", "カタール", "Qatar"), "ROU": (642, "RO", "ルーマニア", "Romania"),
    "RUS": (643, "RS", "ロシア", "Russia"), "RWA": (646, "RW", "ルワンダ", "Rwanda"),
    "SAU": (682, "SA", "サウジアラビア", "Saudi Arabia"), "SEN": (686, "SG", "セネガル", "Senegal"),
    "SRB": (688, "RI", "セルビア", "Serbia"), "SLE": (694, "SL", "シエラレオネ", "Sierra Leone"),
    "SGP": (None, "SN", "シンガポール", "Singapore", (1.35, 103.8)), "SVK": (703, "LO", "スロバキア", "Slovakia"),
    "SVN": (705, "SI", "スロベニア", "Slovenia"), "SLB": (90, "BP", "ソロモン諸島", "Solomon Islands"),
    "SOM": (706, "SO", "ソマリア", "Somalia"), "ZAF": (710, "SF", "南アフリカ", "South Africa"),
    "SSD": (728, "OD", "南スーダン", "South Sudan"), "ESP": (724, "SP", "スペイン", "Spain"),
    "LKA": (144, "CE", "スリランカ", "Sri Lanka"), "SDN": (729, "SU", "スーダン", "Sudan"),
    "SUR": (740, "NS", "スリナム", "Suriname"), "SWE": (752, "SW", "スウェーデン", "Sweden"),
    "CHE": (756, "SZ", "スイス", "Switzerland"), "SYR": (760, "SY", "シリア", "Syria"),
    "TWN": (158, "TW", "台湾", "Taiwan"), "TJK": (762, "TI", "タジキスタン", "Tajikistan"),
    "TZA": (834, "TZ", "タンザニア", "Tanzania"), "THA": (764, "TH", "タイ", "Thailand"),
    "TLS": (626, "TT", "東ティモール", "Timor-Leste"), "TGO": (768, "TO", "トーゴ", "Togo"),
    "TTO": (780, "TD", "トリニダード・トバゴ", "Trinidad and Tobago"), "TUN": (788, "TS", "チュニジア", "Tunisia"),
    "TUR": (792, "TU", "トルコ", "Türkiye"), "TKM": (795, "TX", "トルクメニスタン", "Turkmenistan"),
    "UGA": (800, "UG", "ウガンダ", "Uganda"), "UKR": (804, "UP", "ウクライナ", "Ukraine"),
    "ARE": (784, "AE", "アラブ首長国連邦", "United Arab Emirates"), "GBR": (826, "UK", "英国", "United Kingdom"),
    "USA": (840, "US", "米国", "United States"), "URY": (858, "UY", "ウルグアイ", "Uruguay"),
    "UZB": (860, "UZ", "ウズベキスタン", "Uzbekistan"), "VUT": (548, "NH", "バヌアツ", "Vanuatu"),
    "VEN": (862, "VE", "ベネズエラ", "Venezuela"), "VNM": (704, "VM", "ベトナム", "Vietnam"),
    "YEM": (887, "YM", "イエメン", "Yemen"), "ZMB": (894, "ZA", "ザンビア", "Zambia"),
    "ZWE": (716, "ZI", "ジンバブエ", "Zimbabwe"), "PRI": (630, "RQ", "プエルトリコ", "Puerto Rico"),
    "NCL": (540, "NC", "ニューカレドニア", "New Caledonia"), "ESH": (732, "WI", "西サハラ", "Western Sahara"),
    "CPV": (None, "CV", "カーボベルデ", "Cabo Verde", (15.1, -23.6)), "COM": (None, "CN", "コモロ", "Comoros", (-11.7, 43.3)),
}
FIPS_TO_ISO3 = {v[1]: k for k, v in COUNTRIES.items()}
FIPS_TO_ISO3.update({"GZ": "PSE", "MC": "HKG"})  # Gaza Strip -> Palestine; Macau -> shown with Hong Kong
NUM_TO_ISO3 = {v[0]: k for k, v in COUNTRIES.items() if v[0] is not None}
EN_TO_ISO3 = {v[3].lower(): k for k, v in COUNTRIES.items()}
EN_TO_ISO3.update({
    "united states of america": "USA", "u.s.": "USA", "us": "USA", "america": "USA", "britain": "GBR", "uk": "GBR",
    "great britain": "GBR", "turkey": "TUR", "russian federation": "RUS", "south korea": "KOR", "republic of korea": "KOR",
    "korea, rep.": "KOR", "korea, dem. people's rep.": "PRK", "dprk": "PRK", "north korea": "PRK",
    "iran, islamic rep.": "IRN", "egypt, arab rep.": "EGY", "venezuela, rb": "VEN", "yemen, rep.": "YEM",
    "syrian arab republic": "SYR", "lao pdr": "LAO", "viet nam": "VNM", "congo, dem. rep.": "COD", "congo, rep.": "COG",
    "democratic republic of the congo": "COD", "gambia, the": "GMB", "bahamas, the": "BHS", "czech republic": "CZE",
    "slovak republic": "SVK", "kyrgyz republic": "KGZ", "hong kong sar, china": "HKG", "macedonia": "MKD",
    "west bank and gaza": "PSE", "palestinian territories": "PSE", "gaza": "PSE", "cote d'ivoire": "CIV",
    "ivory coast": "CIV", "myanmar (burma)": "MMR", "burma": "MMR", "east timor": "TLS", "swaziland": "SWZ",
    "cape verde": "CPV", "the netherlands": "NLD", "holland": "NLD", "uae": "ARE", "emirates": "ARE",
    "brunei darussalam": "BRN", "micronesia, fed. sts.": None, "taiwan, china": "TWN", "republic of china": "TWN",
    "türkiye": "TUR", "turkiye": "TUR", "ussr": "RUS", "eu": None, "european union": None,
})


def iso3_from_en(name: str) -> str | None:
    n = (name or "").strip().lower()
    n = n.replace("&", "and").replace("the ", "") if n.startswith("the ") else n.replace("&", "and")
    return EN_TO_ISO3.get(n)


# --------------------------------------------------------------------------- CAMEO labels (Japanese)
CAMEO_ROOT_JA = {
    "01": "公式声明", "02": "要請・アピール", "03": "協力の意思表明", "04": "協議・会談", "05": "外交協力",
    "06": "実質的協力", "07": "援助提供", "08": "譲歩・緩和", "09": "調査", "10": "要求", "11": "非難",
    "12": "拒否", "13": "威嚇", "14": "抗議・デモ", "15": "軍事態勢の誇示", "16": "関係縮小", "17": "強制",
    "18": "襲撃・攻撃", "19": "戦闘", "20": "大量暴力",
}
CAMEO_BASE_JA = {
    "010": "声明", "011": "否定・拒否のコメント", "012": "悲観的コメント", "013": "楽観的コメント", "014": "政策検討の表明",
    "015": "責任の認容", "016": "否認", "017": "象徴的行為", "018": "賛辞・支持", "019": "不快感の表明",
    "020": "要請", "021": "実質協力の要請", "022": "外交協力の要請", "023": "援助の要請", "024": "政治改革の要請",
    "025": "譲歩の要請", "026": "会談の要請", "027": "和解の要請", "028": "停戦の要請",
    "030": "協力の意思表明", "031": "実質協力の意思", "032": "外交協力の意思", "033": "援助の意思", "034": "政治改革の意思",
    "035": "譲歩の意思", "036": "会談の意思", "037": "和解の意思", "038": "停戦の意思", "039": "調停受入の意思",
    "040": "協議", "041": "電話協議", "042": "訪問", "043": "訪問の受入", "044": "第三地での会談", "045": "交渉", "046": "交渉",
    "050": "外交協力", "051": "称賛・支持", "052": "擁護", "053": "支持表明", "054": "外交関係の樹立", "055": "謝罪",
    "056": "許し", "057": "正式合意の署名",
    "060": "実質協力", "061": "経済協力", "062": "軍事協力", "063": "司法協力", "064": "情報共有",
    "070": "援助提供", "071": "経済援助", "072": "軍事援助", "073": "人道援助", "074": "軍事保護・平和維持", "075": "亡命受入",
    "080": "譲歩", "081": "行政制裁の緩和", "082": "政治的抑圧の緩和", "083": "政治改革の受入", "084": "返還・解放",
    "085": "経済制裁・禁輸の緩和", "086": "国際関与の容認", "087": "軍事的緊張の緩和（停戦等）",
    "090": "調査", "091": "犯罪捜査", "092": "人権調査", "093": "軍事行動の調査", "094": "戦争犯罪の調査",
    "100": "要求", "101": "実質協力の要求", "102": "外交協力の要求", "103": "援助の要求", "104": "政治改革の要求",
    "105": "譲歩の要求", "106": "会談の要求", "107": "調停の要求", "108": "停戦の要求",
    "110": "非難", "111": "批判・糾弾", "112": "告発", "113": "反対運動", "114": "苦情申立", "115": "提訴", "116": "有罪認定",
    "120": "拒否", "121": "実質協力の拒否", "122": "外交協力の拒否", "123": "援助の拒否", "124": "政治改革の拒否",
    "125": "譲歩の拒否", "126": "調停の拒否", "127": "計画・合意の拒否", "128": "規範の無視", "129": "拒否権行使",
    "130": "威嚇", "131": "非軍事的威嚇", "132": "行政制裁の威嚇", "133": "政治的反対の威嚇", "134": "交渉停止の威嚇",
    "135": "調停停止の威嚇", "136": "国際関与停止の威嚇", "137": "弾圧の威嚇", "138": "軍事力行使の威嚇", "139": "最後通牒",
    "140": "抗議・デモ", "141": "デモ・集会", "142": "ハンガーストライキ", "143": "ストライキ・ボイコット", "144": "通行妨害・封鎖", "145": "暴動",
    "150": "軍事態勢の誇示", "151": "警察力の増強", "152": "軍事警戒態勢", "153": "軍の動員・展開", "154": "軍事演習",
    "160": "関係縮小", "161": "外交関係の縮小・断絶", "162": "実質協力の縮小・停止", "163": "援助の停止", "164": "交渉の停止",
    "165": "調停の停止", "166": "国際関与の停止・追放",
    "170": "強制", "171": "財産の押収・差押え", "172": "行政制裁", "173": "逮捕・拘束", "174": "追放・国外退去",
    "175": "暴力的弾圧", "176": "サイバー攻撃",
    "180": "襲撃・攻撃", "181": "拉致・人質", "182": "身体的暴行", "183": "爆弾テロ", "184": "拷問", "185": "暗殺未遂", "186": "暗殺",
    "190": "戦闘", "191": "封鎖・移動制限", "192": "領土の占領", "193": "小火器による戦闘", "194": "砲撃・重火器戦闘",
    "195": "空爆・ミサイル攻撃", "196": "停戦違反",
    "200": "大量暴力", "201": "大量追放", "202": "大量殺戮", "203": "民族浄化", "204": "大量破壊兵器の使用",
}
QUAD_JA = {1: "言語的協調", 2: "実質的協調", 3: "言語的対立", 4: "実質的対立"}


def cameo_label(base: str, root: str) -> str:
    return CAMEO_BASE_JA.get(base) or CAMEO_ROOT_JA.get(root) or f"CAMEO {base}"
