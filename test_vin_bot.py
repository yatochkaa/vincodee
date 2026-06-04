import asyncio
import logging
import os
import aiohttp
import json
import hashlib
import re
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from parts_resolver import PartsResolver
from sevices.partsapi_service import (
    resolve_car_id,
    get_search_tree,
    get_articles,
    CAT_TO_STR_ID,
    get_str_id_for_cat,
)

resolver = PartsResolver("parts")

# ══════════════════════════════════════════════════════════════════
#  ПОСТОЯННЫЙ КЕШ (JSON на диске)
# ══════════════════════════════════════════════════════════════════
CACHE_FILE = "cache.json"

def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_cache(cache: dict) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Не удалось сохранить кеш: %s", e)

# Загружаем кеш при старте
_CACHE: dict = _load_cache()

def cache_get(key: str):
    return _CACHE.get(key)

def cache_set(key: str, value, *, allow_err: bool = False) -> None:
    if not allow_err and isinstance(value, dict) and value.get("api_status") == "ERR":
        logger.debug("cache_set: пропускаем кеширование ERR для key=%r", key)
        return
    _CACHE[key] = value
    _save_cache(_CACHE)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PARTSAPI_KEY_CROSSES = os.getenv("PARTSAPI_KEY_CROSSES")
PARTSAPI_KEY_VINDECODE = os.getenv("PARTSAPI_KEY_VINDECODE")
PARTSAPI_KEY_MAKES = os.getenv("PARTSAPI_KEY_MAKES")
PARTSAPI_KEY_MODELS = os.getenv("PARTSAPI_KEY_MODELS")
PARTSAPI_KEY_CARS = os.getenv("PARTSAPI_KEY_CARS")
PARTSAPI_KEY_TREE = os.getenv("PARTSAPI_KEY_TREE")
PARTSAPI_KEY_ARTICLES = os.getenv("PARTSAPI_KEY_ARTICLES")

try:
    API_DELAY = float(os.getenv("API_DELAY", "0.5"))
except (TypeError, ValueError):
    API_DELAY = 0.5

BASE_URL = "https://api.partsapi.ru"
print(f"BASE_URL = {BASE_URL}")

# ══════════════════════════════════════════════════════════════════
#  УТИЛИТЫ
# ══════════════════════════════════════════════════════════════════

def normalize_article(article: str) -> str:
    return article.replace("-", "").replace(" ", "").replace(".", "").strip().upper()

def brand_root(brand: str) -> str:
    b = brand.upper()
    for suffix in ("FILTERS", "FILTER", "AUTO", "PARTS", "GROUP"):
        if b.endswith(suffix) and len(b) > len(suffix) + 2:
            b = b[: -len(suffix)].strip()
    return b

# ══════════════════════════════════════════════════════════════════
#  ГРУЗОВЫЕ БРЕНДЫ
# ══════════════════════════════════════════════════════════════════

TRUCK_BRANDS = {
    "MERITOR", "RENAULT TRUCKS", "VOLVO TRUCKS", "DAF", "SCANIA", "MAN", "IVECO",
    "BERLIET", "IKARUS", "INTERNATIONAL HARV.", "INTERNATIONAL", "KENWORTH",
    "PETERBILT", "FREIGHTLINER", "MACK", "NAVISTAR", "CATERPILLAR", "CUMMINS",
    "PACCAR", "WABCO", "KNORR-BREMSE", "HALDEX", "BPW", "SAF-HOLLAND",
    "SETRA", "NEOPLAN", "TATRA", "KAMAZ", "MAZ", "URAL", "ZIL",
}

# ══════════════════════════════════════════════════════════════════
#  МАРКИ ЛЕГКОВЫХ АВТОМОБИЛЕЙ
# ══════════════════════════════════════════════════════════════════

VEHICLE_BRANDS = {
    "ACURA", "ALFA ROMEO", "ASTON MARTIN", "AUDI", "BENTLEY", "BMW",
    "BUICK", "CADILLAC", "CHERY", "CHEVROLET", "CHRYSLER", "CITROEN",
    "DACIA", "DAEWOO", "DAIHATSU", "DODGE", "DS", "FERRARI", "FIAT",
    "FORD", "GAZ", "GEELY", "GENESIS", "GMC", "GREAT WALL", "HAVAL",
    "HONDA", "HUMMER", "HYUNDAI", "INFINITI", "ISUZU", "JAGUAR", "JEEP",
    "KIA", "LADA", "LAMBORGHINI", "LANCIA", "LAND ROVER", "LEXUS",
    "LIFAN", "LINCOLN", "MASERATI", "MAYBACH", "MAZDA", "MCLAREN",
    "MERCEDES-BENZ", "MERCEDES", "MG", "MINI", "MITSUBISHI", "NISSAN",
    "OPEL", "PEUGEOT", "PONTIAC", "PORSCHE", "RAM", "RANGE ROVER",
    "RENAULT", "ROLLS-ROYCE", "ROVER", "SAAB", "SATURN", "SCION",
    "SEAT", "SKODA", "SMART", "SSANGYONG", "SUBARU", "SUZUKI", "TESLA",
    "TOYOTA", "UAZ", "VAUXHALL", "VOLKSWAGEN", "VW", "VOLVO", "ZAZ",
}

# ══════════════════════════════════════════════════════════════════
#  OEM-ПОСТАВЩИКИ (надёжные бренды)
# ══════════════════════════════════════════════════════════════════

OEM_SUPPLIER_BRANDS = {
    "BOSCH", "MANN-FILTER", "MANN", "MAHLE", "KNECHT",
    "NGK", "DENSO", "BERU", "CHAMPION",
    "GATES", "CONTITECH", "DAYCO",
    "SACHS", "BILSTEIN", "KAYABA", "KYB", "MONROE", "BOGE",
    "FEBI BILSTEIN", "FEBI", "SWAG", "MEYLE", "LEMFOERDER", "LEMFORDER",
    "VALEO", "HELLA", "MAGNETI MARELLI",
    "BREMBO", "ATE", "TRW", "FERODO", "TEXTAR", "PAGID", "JURID",
    "LUCAS", "DELPHI", "VEMO", "VAICO",
    "LUK", "EXEDY", "AISIN",
    "SKF", "FAG", "INA", "NSK", "KOYO", "NTN",
    "FILTRON", "HENGST", "PURFLUX", "UFI", "WIX",
    "ELRING", "VICTOR REINZ", "CORTECO",
}

# ══════════════════════════════════════════════════════════════════
#  ПРИОРИТЕТ ИЗВЕСТНЫХ AFTERMARKET-БРЕНДОВ
# ══════════════════════════════════════════════════════════════════

TOP_AFTERMARKET_BRANDS = [
    "BOSCH", "MANN-FILTER", "MANN", "MAHLE", "KNECHT",
    "NGK", "DENSO", "BERU", "CHAMPION",
    "GATES", "CONTITECH", "DAYCO",
    "SACHS", "BILSTEIN", "KAYABA", "KYB", "MONROE", "BOGE",
    "FEBI BILSTEIN", "FEBI", "SWAG", "MEYLE", "LEMFOERDER", "LEMFORDER",
    "VALEO", "HELLA", "MAGNETI MARELLI",
    "BREMBO", "ATE", "TRW", "FERODO", "TEXTAR", "PAGID", "JURID",
    "LUCAS", "DELPHI", "VEMO", "VAICO",
    "LUK", "EXEDY", "AISIN",
    "SKF", "FAG", "INA", "NSK", "KOYO", "NTN",
    "FILTRON", "HENGST", "PURFLUX", "UFI", "WIX",
    "ELRING", "VICTOR REINZ", "CORTECO",
]

# ══════════════════════════════════════════════════════════════════
#  OEM-ПРЕФИКСЫ ПО МАРКАМ И КАТЕГОРИЯМ
# ══════════════════════════════════════════════════════════════════

OEM_PREFIXES: dict[str, dict[str, list[str]]] = {
    "TOYOTA": {
        "5": ["43040", "43041", "43470", "04427", "04428"],
        "7": ["90915", "15601", "15607"],
        "8": ["17801", "17800"],
        "9": ["23300", "23301", "23303"],
        "424": ["87139"],
        "281": ["04465", "04466", "04491", "04492"],
        "282": ["04465", "04466", "04491", "04492"],
        "82": ["43512", "43512"],
        "84": ["42431", "42431"],
        "1041": ["48510", "48520", "48530", "48540"],
        "1042": ["48510", "48520", "48530", "48540"],
        "686": ["90919", "90080"],
        "306": ["13568"],
        "307": ["13506", "13507"],
    },
    "MITSUBISHI": {
        "5": ["MN", "MB", "MR"],
        "7": ["MD", "MZ"],
        "8": ["MD", "MR"],
        "9": ["MR", "MD"],
        "424": ["7803A", "MZ"],
        "281": ["MN", "MB", "4605"],
        "282": ["MN", "MB", "4605"],
        "82": ["MN", "MB", "4615"],
        "84": ["MN", "MB", "4615"],
        "1041": ["MR", "MB"],
        "1042": ["MR", "MB"],
        "686": ["MD", "MZ"],
        "306": ["MD"],
        "307": ["MD"],
    },
    "KIA": {
        "7": ["26300", "26310"],
        "8": ["28113"],
        "281": ["58101", "58301"],
        "282": ["58101", "58301"],
        "686": ["18846", "18843"],
    },
    "HYUNDAI": {
        "7": ["26300", "26310"],
        "8": ["28113"],
        "281": ["58101", "58301"],
        "282": ["58101", "58301"],
        "686": ["18846", "18843"],
    },
    "NISSAN": {
        "7": ["15208", "15209"],
        "8": ["16546"],
        "281": ["41060", "D1060"],
        "282": ["44060", "D4060"],
        "686": ["22401", "22400"],
    },
    "HONDA": {
        "7": ["15400", "15410"],
        "8": ["17220"],
        "281": ["45022", "43022"],
        "282": ["43022", "45022"],
        "686": ["98079", "98056"],
    },
    "LADA": {
        "7": ["2101", "2103", "2108", "2110"],
        "8": ["2112", "2108"],
        "281": ["2108", "2110"],
        "282": ["2108", "2110"],
        "686": ["2108", "2110"],
    },
    "AUDI": {
        "7": ["06J", "06H", "07L", "03L", "04L"],
        "8": ["1K0", "8K0", "4F0"],
        "281": ["8E0", "4B0", "8K0"],
        "282": ["8E0", "4B0", "8K0"],
        "686": ["101", "905"],
    },
    "VOLKSWAGEN": {
        "7": ["06J", "06H", "07L", "03L", "04L"],
        "8": ["1K0", "6Q0"],
        "281": ["1K0", "6Q0"],
        "282": ["1K0", "6Q0"],
        "686": ["101", "905"],
    },
    "BMW": {
        "7": ["11427", "11428"],
        "8": ["13717", "13718"],
        "281": ["34116", "34216"],
        "282": ["34116", "34216"],
        "686": ["12120", "12122"],
    },
    "MERCEDES-BENZ": {
        "7": ["A000", "A001", "A002", "A003"],
        "8": ["A000", "A001"],
        "281": ["A000", "A001"],
        "282": ["A000", "A001"],
        "686": ["A000", "A001"],
    },
    "FORD": {
        "7": ["1S7J", "1L2J", "YS4J"],
        "8": ["1S7A", "98AB"],
        "281": ["1S71", "6C11"],
        "282": ["1S71", "6C11"],
        "686": ["AGRF", "AGSF"],
    },
    "OPEL": {
        "7": ["93", "55", "650"],
        "8": ["93", "13"],
        "281": ["93", "13"],
        "282": ["93", "13"],
        "686": ["55", "93"],
    },
    "MAZDA": {
        "7": ["LF", "L3", "AJ"],
        "8": ["RF", "WL"],
        "281": ["GJ6A", "GHY1"],
        "282": ["GJ6A", "GHY1"],
        "686": ["ZZY1", "L3G1"],
    },
    "SUBARU": {
        "7": ["15208", "15209"],
        "8": ["16546"],
        "281": ["26296", "26697"],
        "282": ["26296", "26697"],
        "686": ["22401"],
    },
    "RENAULT": {
        "7": ["8200", "7700"],
        "8": ["7700", "8200"],
        "281": ["7701", "8200"],
        "282": ["7701", "8200"],
        "686": ["7700", "8200"],
    },
    "PEUGEOT": {
        "7": ["1109", "1175"],
        "8": ["1444", "1457"],
        "281": ["4253", "4254"],
        "282": ["4253", "4254"],
        "686": ["5960", "5962"],
    },
    "CITROEN": {
        "7": ["1109", "1175"],
        "8": ["1444", "1457"],
        "281": ["4253", "4254"],
        "282": ["4253", "4254"],
        "686": ["5960", "5962"],
    },
    "SKODA": {
        "7": ["06J", "06H", "03L"],
        "8": ["1K0", "6Q0"],
        "281": ["1K0", "6Q0"],
        "282": ["1K0", "6Q0"],
        "686": ["101", "905"],
    },
    "SEAT": {
        "7": ["06J", "06H", "03L"],
        "8": ["1K0", "6Q0"],
        "281": ["1K0", "6Q0"],
        "282": ["1K0", "6Q0"],
        "686": ["101", "905"],
    },
    "VOLVO": {
        "7": ["30650", "31272"],
        "8": ["30636", "30680"],
        "281": ["30793", "31341"],
        "282": ["30793", "31341"],
        "686": ["30583", "31316"],
    },
    "CHEVROLET": {
        "7": ["25162997", "12612350"],
        "8": ["25161433"],
        "281": ["13502049", "13579698"],
        "282": ["13502049", "13579698"],
        "686": ["12571164", "41101"],
    },
    "LEXUS": {
        "5": ["43040", "43041", "43470"],
        "7": ["90915", "15601"],
        "8": ["17801"],
        "281": ["04465", "04466"],
        "282": ["04465", "04466"],
        "686": ["90919"],
    },
}

# ══════════════════════════════════════════════════════════════════
#  FALLBACK OEM BLACKLIST
# ══════════════════════════════════════════════════════════════════

FALLBACK_OEM_BLACKLIST: dict[str, list[str]] = {
    "амортизатор": ["MR992330", "MR992459"],
}

# ══════════════════════════════════════════════════════════════════
#  NON-POSITIONAL CATS (безопасный fallback)
# ══════════════════════════════════════════════════════════════════

NON_POSITIONAL_CATS = {"7", "8", "9", "424", "686", "689"}

# ══════════════════════════════════════════════════════════════════
#  NO-FALLBACK HINTS
# ══════════════════════════════════════════════════════════════════

NO_FALLBACK_HINTS: dict[str, str] = {
    "306": (
        "💡 Возможно на данном авто <b>цепной привод ГРМ</b> — ремень не предусмотрен.\n"
        "Попробуй запросить <b>цепь ГРМ</b> или проверь в каталоге: "
        "<code>/debug parts VIN 306</code>"
    ),
    "307": (
        "💡 Возможно на данном авто <b>цепной привод ГРМ</b> — комплект ремня не предусмотрен.\n"
        "Попробуй запросить <b>цепь ГРМ</b> или <code>/debug parts VIN 307</code>"
    ),
}

# ══════════════════════════════════════════════════════════════════
#  STOP-ON-ERROR CODES (для coverage report)
# ══════════════════════════════════════════════════════════════════

_STOP_ON_ERROR_CODES = {"RATE_LIMIT", "SERVER_ERROR", "AUTH"}

# ══════════════════════════════════════════════════════════════════
#  POSITION / SIDE LABELS
# ══════════════════════════════════════════════════════════════════

POSITION_KEYWORDS = {
    "левый": "Лев.", "левая": "Лев.", "лев": "Лев.",
    "правый": "Пр.", "правая": "Пр.", "прав": "Пр.",
}

SIDE_LABEL_RU = {"front": "Передний", "rear": "Задний"}

# ══════════════════════════════════════════════════════════════════
#  SEMANTIC FILTER (передние/задние позиционные категории)
# ══════════════════════════════════════════════════════════════════

_REAR_STOP_RE = re.compile(
    r"\b(rear|задн|parking|стояноч|shoe|drum|барабан)\b",
    re.IGNORECASE | re.UNICODE,
)
_FRONT_STOP_RE = re.compile(
    r"\b(front|передн)\b",
    re.IGNORECASE | re.UNICODE,
)
_FRONT_CATS = {"281", "82", "1041", "188", "273"}
_REAR_CATS  = {"282", "84", "1042", "189", "274"}
_KNOWN_REAR_ARTICLES = {"4800A001", "MN161157"}

# ══════════════════════════════════════════════════════════════════
#  CURATED DEBUG CATS
# ══════════════════════════════════════════════════════════════════

CURATED_DEBUG_CATS = [
    ("7",    "Масляный фильтр"),
    ("8",    "Воздушный фильтр"),
    ("9",    "Топливный фильтр"),
    ("424",  "Салонный фильтр"),
    ("281",  "Колодки передние"),
    ("282",  "Колодки задние"),
    ("82",   "Тормозной диск передний"),
    ("84",   "Тормозной диск задний"),
    ("1041", "Амортизатор передний"),
    ("1042", "Амортизатор задний"),
    ("188",  "Пружина передняя"),
    ("189",  "Пружина задняя"),
    ("273",  "Рычаг подвески передний"),
    ("274",  "Рычаг подвески задний"),
    ("1037", "Шаровая опора"),
    ("686",  "Свеча зажигания"),
    ("689",  "Катушка зажигания"),
    ("306",  "Ремень ГРМ"),
    ("307",  "Комплект ГРМ"),
    ("470",  "Радиатор"),
    ("655",  "Подшипник ступицы"),
    ("5",    "ШРУС"),
]

# ══════════════════════════════════════════════════════════════════
#  GROUP NAMES (для /help)
# ══════════════════════════════════════════════════════════════════

GROUP_NAMES = {
    1:  ("🔧", "Двигатель и ГРМ"),
    2:  ("🌡️", "Система охлаждения"),
    3:  ("⛽", "Топливная система"),
    4:  ("💨", "Впуск и выпуск"),
    5:  ("🛑", "Тормозная система"),
    6:  ("🔩", "Подвеска"),
    7:  ("🎯", "Рулевое управление"),
    8:  ("⚙️", "Трансмиссия и сцепление"),
    9:  ("⚡", "Электрика и зажигание"),
    10: ("❄️", "Салон и климат"),
    11: ("🛞", "Колёса и ступицы"),
    12: ("🔆", "Дворники и свет"),
    13: ("📡", "Датчики"),
}

# ══════════════════════════════════════════════════════════════════
#  PARTS MAP
# ══════════════════════════════════════════════════════════════════
# Импортируем из parts_resolver через resolver.find_part()
# PARTS_MAP хранится внутри PartsResolver

# ══════════════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════════════════════════════

def find_part(text: str):
    """Делегируем в PartsResolver."""
    return resolver.find_part(text)

def format_car_info(vin_info: dict) -> str:
    parts = []
    brand = vin_info.get("brend") or vin_info.get("manuName") or ""
    model = vin_info.get("naimenovanie") or vin_info.get("modelName") or ""
    mod   = vin_info.get("modifikaciya") or vin_info.get("typeName") or ""
    year  = vin_info.get("data_vypuska") or vin_info.get("year") or ""
    if brand:
        parts.append(brand)
    if model:
        parts.append(model)
    if mod:
        parts.append(mod)
    if year:
        parts.append(str(year))
    return " ".join(parts).strip()

def dedupe_name(name: str) -> str:
    words = name.split()
    seen = set()
    result = []
    for w in words:
        wl = w.lower()
        if wl not in seen:
            seen.add(wl)
            result.append(w)
    return " ".join(result)

def parse_parts_string(parts_str: str) -> list[tuple[str, str]]:
    """Парсит строку вида 'BRAND ART1; BRAND ART2' в список (brand, article)."""
    result = []
    if not parts_str:
        return result
    for chunk in parts_str.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        tokens = chunk.split()
        if len(tokens) >= 2:
            brand = tokens[0]
            art = " ".join(tokens[1:])
            result.append((brand, art))
    return result

def _item_text(item: dict) -> str:
    return " ".join(filter(None, [
        item.get("brand", ""),
        item.get("article", ""),
        item.get("name", ""),
        item.get("group", ""),
        item.get("ART_ARTICLE_NR", ""),
        item.get("SUP_BRAND", ""),
        item.get("PRODUCT_GROUP", ""),
    ])).lower()

def _is_supplier_brand(brand: str) -> bool:
    return brand.upper().strip() in OEM_SUPPLIER_BRANDS

def normalize_tecdoc_article(raw: dict) -> tuple[str, str] | None:
    """Нормализует один артикул из getArticles в (brand, article)."""
    brand = (raw.get("SUP_BRAND") or raw.get("brand") or "").strip()
    art   = (raw.get("ART_ARTICLE_NR") or raw.get("article") or "").strip()
    if brand and art:
        return (brand, art)
    return None

# ══════════════════════════════════════════════════════════════════
#  VIN DECODE
# ══════════════════════════════════════════════════════════════════

async def api_vindecode(session: aiohttp.ClientSession, vin: str) -> dict | None:
    if not PARTSAPI_KEY_VINDECODE:
        return None
    url = f"{BASE_URL}/api/v1/VINdecodeOE"
    params = {"key": PARTSAPI_KEY_VINDECODE, "vin": vin}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning("VINdecodeOE HTTP %s для VIN %s", resp.status, vin)
                return None
            data = await resp.json(content_type=None)
            if not data or not isinstance(data, dict):
                return None
            # Нормализуем поля
            result = dict(data)
            result.setdefault("manuName", data.get("brend", ""))
            result.setdefault("modelName", data.get("naimenovanie", ""))
            result.setdefault("typeName", data.get("modifikaciya", ""))
            return result
    except Exception as e:
        logger.warning("VINdecodeOE ошибка: %s", e)
        return None

# ══════════════════════════════════════════════════════════════════
#  CROSSES
# ══════════════════════════════════════════════════════════════════

async def api_get_crosses(session: aiohttp.ClientSession, article: str) -> list[dict]:
    if not PARTSAPI_KEY_CROSSES:
        return []
    url = f"{BASE_URL}/api/v1/tecdocCrosses"
    params = {"key": PARTSAPI_KEY_CROSSES, "article": article}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data", data.get("items", []))
            return []
    except Exception as e:
        logger.warning("tecdocCrosses ошибка: %s", e)
        return []

def filter_crosses(raw_crosses: list, oem_article: str, oem_brand: str) -> list[tuple[str, str]]:
    norm_oem = normalize_article(oem_article)
    oem_brand_root = brand_root(oem_brand)
    seen_brands: set = set()
    result = []
    for item in raw_crosses:
        if not isinstance(item, dict):
            continue
        b = (item.get("brand") or item.get("SUP_BRAND") or "").strip()
        a = (item.get("article") or item.get("ART_ARTICLE_NR") or "").strip()
        if not b or not a:
            continue
        if normalize_article(a) == norm_oem:
            continue
        if brand_root(b) == oem_brand_root:
            continue
        br = brand_root(b)
        if br in seen_brands:
            continue
        seen_brands.add(br)
        result.append((b, a))
    return result

def sort_crosses_by_priority(crosses: list[tuple[str, str]]) -> list[tuple[str, str]]:
    priority = {b: i for i, b in enumerate(TOP_AFTERMARKET_BRANDS)}
    def key(pair):
        b, _ = pair
        return priority.get(b.upper(), len(TOP_AFTERMARKET_BRANDS))
    return sorted(crosses, key=key)

# ══════════════════════════════════════════════════════════════════
#  TECDOC CHAIN: основной метод получения артикулов
# ══════════════════════════════════════════════════════════════════

async def fetch_parts_tecdoc(
    session: aiohttp.ClientSession,
    vin_info: dict,
    cat_id: str,
) -> list[tuple[str, str]]:
    """
    Основная цепочка: resolve_car_id → get_search_tree → get_articles.
    Возвращает список (brand, article). Пустой список если не найдено.
    """
    manu_name = (
        vin_info.get("brend") or vin_info.get("manuName") or ""
    ).strip()
    modely = (vin_info.get("modely") or "").strip()
    year_raw = vin_info.get("data_vypuska") or vin_info.get("year")
    year: int | None = None
    if year_raw:
        try:
            year = int(str(year_raw)[:4])
        except (ValueError, TypeError):
            year = None

    if not manu_name or not modely:
        logger.warning("fetch_parts_tecdoc: нет manu_name или modely в vin_info")
        return []

    # Шаг 1: resolve carId
    car_id = await resolve_car_id(session, manu_name, modely, year)
    if not car_id:
        logger.info("fetch_parts_tecdoc: carId не найден для %s %s", manu_name, modely)
        return []

    # Шаг 2: получаем STR_ID для категории
    str_id = get_str_id_for_cat(cat_id)
    if not str_id:
        logger.info("fetch_parts_tecdoc: нет STR_ID для cat_id=%s", cat_id)
        return []

    # Шаг 3: getArticles
    raw_articles = await get_articles(session, car_id, str_id)
    if not raw_articles:
        logger.info(
            "fetch_parts_tecdoc: getArticles вернул пусто для carId=%s str_id=%s",
            car_id, str_id,
        )
        return []

    # Нормализуем
    result: list[tuple[str, str]] = []
    seen: set = set()
    for raw in raw_articles:
        pair = normalize_tecdoc_article(raw)
        if not pair:
            continue
        brand, art = pair
        key = f"{brand.upper()}|{normalize_article(art)}"
        if key not in seen:
            seen.add(key)
            result.append((brand, art))

    return result

# ══════════════════════════════════════════════════════════════════
#  PICK PRIMARY OEM
# ══════════════════════════════════════════════════════════════════

def pick_primary_oem(
    parts: list[tuple[str, str]],
    cat_id: str,
    manu_name: str,
    shrus_filter: str | None = None,
) -> tuple[tuple[str, str] | None, list[tuple[str, str]]]:
    """
    Выбирает основной OEM из списка (brand, article).
    Возвращает (primary, other_oem_same_brand).
    """
    if not parts:
        return None, []

    manu_upper = (manu_name or "").upper().strip()
    prefixes = []
    if manu_upper and manu_upper in OEM_PREFIXES:
        prefixes = OEM_PREFIXES[manu_upper].get(str(cat_id), [])

    # 1. Ищем точный OEM марки по префиксам
    own_brand_parts = [
        (b, a) for b, a in parts
        if b.upper().strip() == manu_upper
    ]
    if own_brand_parts and prefixes:
        for b, a in own_brand_parts:
            norm = normalize_article(a)
            for pfx in prefixes:
                if norm.startswith(pfx.upper().replace("-", "").replace(" ", "")):
                    other = [(bb, aa) for bb, aa in own_brand_parts if aa != a]
                    return (b, a), other

    # 2. Любой артикул своей марки
    if own_brand_parts:
        primary = own_brand_parts[0]
        other = own_brand_parts[1:]
        return primary, other

    # 3. OEM-поставщик
    supplier_parts = [(b, a) for b, a in parts if _is_supplier_brand(b)]
    if supplier_parts:
        return supplier_parts[0], supplier_parts[1:]

    return None, []

# ══════════════════════════════════════════════════════════════════
#  CLASSIFY OEM QUALITY
# ══════════════════════════════════════════════════════════════════

def classify_oem_quality(
    parts: list[tuple[str, str]],
    manu_name: str,
) -> dict:
    manu_upper = (manu_name or "").upper().strip()
    own = 0
    supplier = 0
    foreign_vehicle = 0
    other = 0
    for b, a in parts:
        bu = b.upper().strip()
        if bu == manu_upper:
            own += 1
        elif _is_supplier_brand(b):
            supplier += 1
        elif bu in VEHICLE_BRANDS:
            foreign_vehicle += 1
        else:
            other += 1
    total = len(parts)
    raw_ok = total > 0
    precise_oem = own > 0 or supplier > 0
    noisy = foreign_vehicle > 0 and own == 0 and supplier == 0
    return {
        "raw_ok": raw_ok,
        "precise_oem": precise_oem,
        "noisy": noisy,
        "own": own,
        "supplier": supplier,
        "foreign_vehicle": foreign_vehicle,
        "other": other,
        "total": total,
    }

def coverage_quality(parts: list[tuple[str, str]], manu_name: str) -> tuple[str, dict]:
    if not parts:
        return "NO", {"raw_ok": False, "precise_oem": False, "noisy": False}
    q = classify_oem_quality(parts, manu_name)
    if q["precise_oem"]:
        return "OK", q
    if q["noisy"]:
        return "WEAK", q
    if q["raw_ok"]:
        return "WEAK", q
    return "NO", q

# ══════════════════════════════════════════════════════════════════
#  SEMANTIC FILTER
# ══════════════════════════════════════════════════════════════════

def filter_items_by_position(
    parts: list[tuple[str, str]],
    cat_id: str,
) -> list[tuple[str, str]]:
    """Фильтрует артикулы по позиции (перед/зад) для позиционных категорий."""
    cat = str(cat_id)
    if cat not in _FRONT_CATS and cat not in _REAR_CATS:
        return parts

    is_front = cat in _FRONT_CATS
    result = []
    for b, a in parts:
        text = f"{b} {a}".lower()
        article_norm = normalize_article(a)
        blocked = False
        if is_front:
            if article_norm in {normalize_article(x) for x in _KNOWN_REAR_ARTICLES}:
                blocked = True
            elif _REAR_STOP_RE.search(text):
                blocked = True
        else:
            if _FRONT_STOP_RE.search(text):
                blocked = True
        if not blocked:
            result.append((b, a))
    return result

# ══════════════════════════════════════════════════════════════════
#  TRY OEM FALLBACK (резерв для беспозиционных расходников)
# ══════════════════════════════════════════════════════════════════

def is_nonpositional_fallback_allowed(
    cat_id: str,
    api_status: str,
    error_code: str | None,
    explicit_side: bool,
) -> bool:
    cat = str(cat_id)
    if cat not in NON_POSITIONAL_CATS:
        return False
    if cat in NO_FALLBACK_HINTS:
        return False
    if explicit_side:
        return False
    if api_status == "ERR" and error_code in {"RATE_LIMIT", "AUTH", "JSON_ERROR", "MALFORMED"}:
        return False
    return True

async def try_oem_fallback(
    session: aiohttp.ClientSession,
    manu_name: str,
    cat_id: str,
    part_name: str = "",
) -> tuple[tuple[str, str], list[tuple[str, str]]] | None:
    """
    Резервный поиск OEM через TecDoc crosses для беспозиционных расходников.
    Возвращает ((brand, article), crosses) или None.
    """
    manu_upper = (manu_name or "").upper().strip()
    prefixes = []
    if manu_upper and manu_upper in OEM_PREFIXES:
        prefixes = OEM_PREFIXES[manu_upper].get(str(cat_id), [])

    if not prefixes:
        return None

    # Пробуем первый известный префикс как артикул-затравку
    # (в реальности нужна БД типовых OEM — здесь заглушка)
    return None

# ══════════════════════════════════════════════════════════════════
#  BUILD NONPOSITIONAL FALLBACK MESSAGE
# ══════════════════════════════════════════════════════════════════

def build_nonpositional_fallback_message(
    *,
    group_name: str,
    car_str: str,
    vin: str,
    cat_id: str,
    manu_name: str,
    fb_brand: str,
    fb_art: str,
    fb_crosses: list[tuple[str, str]],
    reason: str,
) -> str:
    cross_block = "\n".join(
        f"  • <b>{b}</b>  <code>{a}</code>" for b, a in fb_crosses
    )
    lines = [
        f"⚠️ <b>{group_name}</b> — fallback-поиск",
        f"<i>{reason}. Использую типовой OEM для {fb_brand}.</i>",
        f"VIN: <code>{vin}</code>",
        *([f"🚗 {car_str}"] if car_str else []),
        "─" * 28,
        "🔵 <b>OEM (типовой, требует проверки!):</b>",
        f"  ⚠️ <b>{fb_brand}</b>  <code>{fb_art}</code>",
        "",
        f"🔄 <b>Аналоги (топ-{len(fb_crosses)}) — {fb_brand} <code>{fb_art}</code>:</b>",
        cross_block,
        "",
        "⚠️ <i>Артикул типовой, сверь с каталогом перед заказом.</i>",
    ]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════
#  SUMMARIZE PARTS ITEMS
# ══════════════════════════════════════════════════════════════════

def summarize_parts_items(
    parts: list[tuple[str, str]],
    limit: int = 5,
) -> tuple[int, list[tuple[str, str]]]:
    return len(parts), parts[:limit]

# ══════════════════════════════════════════════════════════════════
#  FORMAT VIN INFO BLOCK
# ══════════════════════════════════════════════════════════════════

def _fmt_vin_info_block(info: dict) -> list[str]:
    def _v(key: str) -> str:
        val = info.get(key)
        if val in (None, ""):
            return "<i>—</i>"
        return str(val)
    return [
        f"<b>Марка:</b>        {_v('brend') or _v('manuName')}",
        f"<b>Модель:</b>       {_v('naimenovanie') or _v('modelName')}",
        f"<b>Модификация:</b>  {_v('modifikaciya') or _v('typeName')}",
        f"<b>Каталог:</b>      {_v('katalog')}",
        f"<b>Модели (TecDoc):</b> {_v('modely')}",
        f"<b>Рынок:</b>        {_v('rynok')}",
    ]

# ══════════════════════════════════════════════════════════════════
#  DEBUG VIN INFO
# ══════════════════════════════════════════════════════════════════

async def debug_vin_info(session, vin: str) -> dict | None:
    return await api_vindecode(session, vin)

# ══════════════════════════════════════════════════════════════════
#  DEBUG GET PARTS (переключён на TecDoc)
# ══════════════════════════════════════════════════════════════════

async def debug_get_parts(session, vin: str, cat: str) -> str:
    vin_info = await api_vindecode(session, vin)
    if not vin_info:
        return (
            f"🔧 TecDoc chain\nVIN: {vin} | cat: {cat}\n"
            "❌ VINdecodeOE не вернул данные — невозможно определить carId."
        )

    parts = await fetch_parts_tecdoc(session, vin_info, cat)

    lines = [
        "🔧 TecDoc chain (resolve_car_id → getSearchTree → getArticles)",
        f"VIN: {vin} | cat: {cat}",
        f"manu: {vin_info.get('brend') or vin_info.get('manuName')} | "
        f"modely: {vin_info.get('modely')}",
        "────────────────────────────",
    ]

    if not parts:
        lines.append("⚠️ Артикулы не найдены (carId не определён или STR_ID не в CAT_TO_STR_ID).")
    else:
        lines.append(f"✅ Найдено артикулов: {len(parts)}")
        for i, (b, a) in enumerate(parts[:10], 1):
            lines.append(f"{i}. {b} | {a}")
        if len(parts) > 10:
            lines.append(f"... и ещё {len(parts) - 10}")

    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════
#  BUILD COVERAGE REPORT (переключён на TecDoc)
# ══════════════════════════════════════════════════════════════════

async def build_coverage_report(
    session: aiohttp.ClientSession,
    vin: str,
    vin_info: dict | None = None,
    cats: list[tuple[str, str]] | None = None,
) -> dict:
    if cats is None:
        cats = CURATED_DEBUG_CATS

    if vin_info is None:
        vin_info = await api_vindecode(session, vin) or {}

    manu_name = (
        vin_info.get("brend") or vin_info.get("manuName") or ""
    ).strip()

    total_cats = len(cats)
    per_cat = []
    ok_list = []
    no_list = []
    weak_list = []
    err_list = []
    stopped = False
    stopped_reason = None

    for cat_id, cat_name in cats:
        parts = await fetch_parts_tecdoc(session, vin_info, str(cat_id))
        quality, q = coverage_quality(parts, manu_name)

        samples = parts[:2]

        row = {
            "catid": str(cat_id),
            "cat_id": str(cat_id),
            "name": cat_name,
            "api_status": "OK" if parts else "NO",
            "quality": quality,
            "raw_ok": q.get("raw_ok", False),
            "precise_oem": q.get("precise_oem", False),
            "noisy": q.get("noisy", False),
            "count": len(parts),
            "samples": samples,
            "error_code": None,
            "message": None,
        }
        per_cat.append(row)

        if quality == "OK":
            ok_list.append(row)
        elif quality == "WEAK":
            weak_list.append(row)
        elif quality == "NO":
            no_list.append(row)
        else:
            err_list.append(row)

        if API_DELAY > 0:
            await asyncio.sleep(API_DELAY)

    checked = len(per_cat)
    ok_count = len(ok_list)
    weak_count = len(weak_list)

    if err_list:
        level = "error"
    elif checked >= 15 and ok_count >= 8:
        level = "ok"
    elif ok_count >= 3:
        level = "partial"
    elif ok_count + weak_count >= 3:
        level = "weak"
    else:
        level = "weak"

    return {
        "level": level,
        "manu_name": manu_name,
        "per_cat": per_cat,
        "ok_list": ok_list,
        "no_list": no_list,
        "weak_list": weak_list,
        "err_list": err_list,
        "checked": checked,
        "total_cats": total_cats,
        "stopped": stopped,
        "stopped_reason": stopped_reason,
    }

# ══════════════════════════════════════════════════════════════════
#  CMD /vin
# ══════════════════════════════════════════════════════════════════

async def cmd_vin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ Формат: <code>/vin VIN запчасть</code>\n\n"
            "Примеры:\n"
            "<code>/vin XW7BF4FK60S145161 воздушный фильтр</code>\n"
            "<code>/vin XW7BF4FK60S145161 тормозные колодки передние</code>\n"
            "<code>/vin XW7BF4FK60S145161 амортизатор задний</code>\n"
            "<code>/vin XW7BF4FK60S145161 шрус внутренний</code>\n\n"
            "/help — полный список запчастей",
            parse_mode="HTML",
        )
        return

    vin = context.args[0].strip().upper()
    part_text = " ".join(context.args[1:]).strip()

    if len(vin) != 17:
        await update.message.reply_text(
            f"⚠️ VIN <code>{vin}</code> — неверная длина.", parse_mode="HTML"
        )
        return

    found = find_part(part_text)
    if not found:
        await update.message.reply_text(
            f"🤔 Не распознаю: <b>{part_text}</b>\n/help — список запчастей",
            parse_mode="HTML",
        )
        return

    cats_list, part_name, position, side, explicit_side = found

    if not cats_list:
        side_ru = SIDE_LABEL_RU.get(side or "", "указанной стороне")
        await update.message.reply_text(
            f"❌ <b>{part_name}</b> — нет категорий для запрошенной стороны "
            f"({side_ru}). Подбор отключён, чтобы не подменить деталь "
            f"противоположной стороной.",
            parse_mode="HTML",
        )
        return

    async with aiohttp.ClientSession() as session:
        # Шаг 1: VINdecode
        vin_info: dict = {}
        manu_name = ""
        car_str = ""
        if PARTSAPI_KEY_VINDECODE:
            raw_info = await api_vindecode(session, vin)
            if raw_info:
                vin_info = raw_info
                manu_name = (
                    vin_info.get("brend") or vin_info.get("manuName") or ""
                ).strip()
                car_str = format_car_info(vin_info)

        for cat_id, cat_label in cats_list:
            shrus_filter = None
            if "Наружний" in cat_label:
                shrus_filter = "Наружний"
            elif "Внутренний" in cat_label:
                shrus_filter = "Внутренний"

            pos_pfx    = f"{position} " if position else ""
            cat_pfx    = f"{cat_label} " if cat_label else ""
            group_name = dedupe_name(f"{pos_pfx}{cat_pfx}{part_name}".strip())

            header = [f"⏳ Ищу: <b>{group_name}</b>"]
            if car_str:
                header.append(f"🚗 {car_str}")
            header.append(f"VIN: <code>{vin}</code> | cat: <code>{cat_id}</code>")
            await update.message.reply_text("\n".join(header), parse_mode="HTML")

            print(f"[DEBUG] cat_id={cat_id} position={position!r} part_name={part_name!r}")

            # Шаг 2: TecDoc chain
            all_parts = await fetch_parts_tecdoc(session, vin_info, str(cat_id))

            # Семантический фильтр по позиции
            all_parts = filter_items_by_position(all_parts, str(cat_id))

            # ── Выбираем OEM ──
            primary, other_oem = (None, [])
            if all_parts:
                primary, other_oem = pick_primary_oem(
                    all_parts, str(cat_id), manu_name, shrus_filter
                )

            # ── СЦЕНАРИЙ A: OEM найден ──
            if primary:
                p_brand, p_art = primary
                is_supplier = _is_supplier_brand(p_brand)

                if is_supplier:
                    src_label = "OEM поставщика (надёжный)"
                    oem_emoji = "✅"
                elif manu_name and p_brand.upper().strip() == manu_name.upper().strip():
                    src_label = "OEM марки авто"
                    oem_emoji = "✅"
                else:
                    src_label = "OEM (из TecDoc)"
                    oem_emoji = "✅"

                oem_block = (
                    f" {oem_emoji} <b>{p_brand}</b>\n"
                    f"<code>{p_art}</code>\n\n"
                    f"Источник: {src_label}"
                )

                if other_oem:
                    others_str = "\n".join(
                        f" • <code>{a}</code>" for _b, a in other_oem[:5]
                    )
                    oem_block += f"\n\nДругие арт. {p_brand} ({len(other_oem)}):\n{others_str}"
                    if len(other_oem) > 5:
                        oem_block += f"\n... и ещё {len(other_oem) - 5}"

                raw_crosses = await api_get_crosses(session, p_art)
                crosses_all = filter_crosses(raw_crosses, p_art, p_brand) if raw_crosses else []
                crosses = sort_crosses_by_priority(crosses_all)[:10]

                if crosses:
                    cross_block = "\n".join(
                        f" • <b>{b}</b> — <code>{a}</code>" for b, a in crosses
                    )
                    crosses_header = (
                        f"🔄 Аналоги (топ-{len(crosses)} из {len(crosses_all)}) — {p_brand} {p_art}:"
                    )
                    cross_footer = "\n\n⚠️ Проверяй соответствие перед заказом"
                else:
                    cross_block = f"Нет аналогов для <code>{p_art}</code>"
                    crosses_header = f"🔄 Аналоги для <code>{p_art}</code>:"
                    cross_footer = ""

                msg = "\n".join([
                    f"✅ Найдено: <b>{group_name}</b>",
                    *([f"🚗 {car_str}"] if car_str else []),
                    f"VIN: <code>{vin}</code>",
                    f"Категория: <code>{cat_id}</code>",
                    "─" * 28,
                    f"🔵 Основной артикул:\n{oem_block}",
                    "─" * 28,
                    f"{crosses_header}\n{cross_block}{cross_footer}",
                ])
                await update.message.reply_text(msg, parse_mode="HTML")
                continue

            # ── СЦЕНАРИЙ B: данные есть, но OEM не выделен ──
            if all_parts:
                foreign_lines = []
                for b, a in all_parts[:8]:
                    foreign_lines.append(f" • <b>{b}</b> — <code>{a}</code>")

                msg = "\n".join([
                    f"⚠️ Найдены данные по <b>{group_name}</b>, но основной OEM не удалось выделить.",
                    *([f"🚗 {car_str}"] if car_str else []),
                    f"VIN: <code>{vin}</code>",
                    f"Категория: <code>{cat_id}</code>",
                    "─" * 28,
                    "Что вернул TecDoc:",
                    *foreign_lines,
                    "",
                    "Для проверки аналогов: <code>/crosses ARTICLE</code>",
                ])
                await update.message.reply_text(msg, parse_mode="HTML")
                continue

            # ── СЦЕНАРИЙ C: ничего не найдено ──
            # Пробуем fallback только для беспозиционных расходников
            allow_fb = is_nonpositional_fallback_allowed(
                str(cat_id), "NO", None, explicit_side
            )
            fallback = (
                await try_oem_fallback(session, manu_name, str(cat_id), part_name=part_name)
                if allow_fb else None
            )

            if fallback:
                (fb_brand, fb_art), fb_crosses = fallback
                msg = build_nonpositional_fallback_message(
                    group_name=group_name, car_str=car_str, vin=vin,
                    cat_id=str(cat_id), manu_name=manu_name,
                    fb_brand=fb_brand, fb_art=fb_art, fb_crosses=fb_crosses,
                    reason="TecDoc не вернул данные",
                )
                await update.message.reply_text(msg, parse_mode="HTML")
                continue

            hint = NO_FALLBACK_HINTS.get(str(cat_id), "")
            if explicit_side:
                side_ru = SIDE_LABEL_RU.get(side or "", "указанной стороне")
                await update.message.reply_text(
                    f"❌ <b>{group_name}</b> — не удалось подтвердить артикул "
                    f"для запрошенной стороны ({side_ru}).\n"
                    f"<i>TecDoc не вернул данные по этому VIN/cat.</i>\n"
                    f"💡 Проверь в каталоге: <code>/debug parts {vin} {cat_id}</code>",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(
                    f"ℹ️ По категории <b>{group_name}</b> данные не найдены в TecDoc."
                    + (f"\n\n{hint}" if hint else ""),
                    parse_mode="HTML",
                )

# ══════════════════════════════════════════════════════════════════
#  CMD /debug
# ══════════════════════════════════════════════════════════════════

async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "<code>/debug vin VIN</code> — декодировать VIN\n"
            "<code>/debug parts VIN CAT</code> — TecDoc артикулы по категории\n"
            "<code>/debug coverage VIN</code> — покрытие по всем категориям\n"
            "<code>/debug crosses ARTICLE</code> — аналоги артикула",
            parse_mode="HTML",
        )
        return

    sub = context.args[0].lower()

    async with aiohttp.ClientSession() as session:
        if sub == "vin":
            if len(context.args) < 2:
                await update.message.reply_text("❌ Укажи VIN: /debug vin VIN")
                return
            vin = context.args[1].strip().upper()
            info = await debug_vin_info(session, vin)
            if not info:
                await update.message.reply_text(f"❌ VINdecodeOE не вернул данные для {vin}")
                return
            lines = [f"🔍 VIN: <code>{vin}</code>", ""] + _fmt_vin_info_block(info)
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")

        elif sub == "parts":
            if len(context.args) < 3:
                await update.message.reply_text("❌ Формат: /debug parts VIN CAT")
                return
            vin = context.args[1].strip().upper()
            cat = context.args[2].strip()
            result = await debug_get_parts(session, vin, cat)
            await update.message.reply_text(result, parse_mode="HTML")

        elif sub == "coverage":
            if len(context.args) < 2:
                await update.message.reply_text("❌ Укажи VIN: /debug coverage VIN")
                return
            vin = context.args[1].strip().upper()
            await update.message.reply_text(
                f"⏳ Проверяю покрытие TecDoc для VIN <code>{vin}</code>...",
                parse_mode="HTML",
            )
            vin_info = await api_vindecode(session, vin)
            report = await build_coverage_report(session, vin, vin_info=vin_info)

            lines = [
                f"📊 Покрытие TecDoc: <b>{report['level'].upper()}</b>",
                f"Марка: {report['manu_name'] or '—'}",
                f"Проверено категорий: {report['checked']} / {report['total_cats']}",
                f"OK: {len(report['ok_list'])} | WEAK: {len(report['weak_list'])} | "
                f"NO: {len(report['no_list'])} | ERR: {len(report['err_list'])}",
                "─" * 28,
            ]
            for row in report["per_cat"]:
                emoji = {"OK": "✅", "WEAK": "⚠️", "NO": "❌", "ERR": "🔴"}.get(row["quality"], "❓")
                samples_str = ""
                if row["samples"]:
                    samples_str = " | " + ", ".join(f"{b} {a}" for b, a in row["samples"][:2])
                lines.append(
                    f"{emoji} <b>{row['name']}</b> (cat {row['cat_id']}): "
                    f"{row['count']} арт.{samples_str}"
                )

            await update.message.reply_text("\n".join(lines), parse_mode="HTML")

        elif sub == "crosses":
            if len(context.args) < 2:
                await update.message.reply_text("❌ Укажи артикул: /debug crosses ARTICLE")
                return
            article = context.args[1].strip()
            raw = await api_get_crosses(session, article)
            if not raw:
                await update.message.reply_text(f"Нет аналогов для <code>{article}</code>", parse_mode="HTML")
                return
            crosses = filter_crosses(raw, article, "")
            crosses = sort_crosses_by_priority(crosses)[:15]
            lines = [f"🔄 Аналоги для <code>{article}</code>:"]
            for b, a in crosses:
                lines.append(f" • <b>{b}</b> — <code>{a}</code>")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")

        else:
            await update.message.reply_text(
                f"❓ Неизвестная подкоманда: {sub}\n"
                "Доступно: vin, parts, coverage, crosses"
            )

# ══════════════════════════════════════════════════════════════════
#  CMD /crosses
# ══════════════════════════════════════════════════════════════════

async def cmd_crosses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ Формат: <code>/crosses ARTICLE</code>\n"
            "Пример: <code>/crosses 90915-YZZD4</code>",
            parse_mode="HTML",
        )
        return
    article = context.args[0].strip()
    async with aiohttp.ClientSession() as session:
        raw = await api_get_crosses(session, article)
    if not raw:
        await update.message.reply_text(
            f"Нет аналогов для <code>{article}</code>", parse_mode="HTML"
        )
        return
    crosses = filter_crosses(raw, article, "")
    crosses = sort_crosses_by_priority(crosses)[:10]
    if not crosses:
        await update.message.reply_text(
            f"Нет аналогов для <code>{article}</code>", parse_mode="HTML"
        )
        return
    lines = [f"🔄 Аналоги для <code>{article}</code>:"]
    for b, a in crosses:
        lines.append(f" • <b>{b}</b> — <code>{a}</code>")
    lines.append("\n⚠️ Проверяй соответствие перед заказом")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

# ══════════════════════════════════════════════════════════════════
#  CMD /help
# ══════════════════════════════════════════════════════════════════

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [
        "📋 <b>Поддерживаемые запчасти</b>\n",
        "Формат: <code>/vin VIN название_запчасти</code>\n",
    ]
    # Получаем список из resolver
    try:
        parts_list = resolver.get_all_parts()
        by_group: dict[int, list[str]] = {}
        for keyword, (cats, name, group_num) in parts_list.items():
            by_group.setdefault(group_num, [])
            if name not in by_group[group_num]:
                by_group[group_num].append(name)
        for group_num in sorted(by_group.keys()):
            emoji, title = GROUP_NAMES.get(group_num, ("🔹", f"Группа {group_num}"))
            lines.append(f"\n{emoji} <b>{title}</b>")
            for name in by_group[group_num]:
                lines.append(f"  • {name}")
    except Exception:
        lines.append("(список недоступен)")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

# ══════════════════════════════════════════════════════════════════
#  CMD /start
# ══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>VIN-бот запущен!</b>\n\n"
        "Команды:\n"
        "<code>/vin VIN запчасть</code> — подобрать OEM-артикул\n"
        "<code>/crosses ARTICLE</code> — аналоги артикула\n"
        "<code>/debug vin VIN</code> — декодировать VIN\n"
        "<code>/debug parts VIN CAT</code> — TecDoc по категории\n"
        "<code>/debug coverage VIN</code> — покрытие TecDoc\n"
        "<code>/help</code> — список запчастей\n\n"
        "Источник данных: TecDoc API (partsapi.ru)",
        parse_mode="HTML",
    )

# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not PARTSAPI_KEY_VINDECODE:
        logger.warning("PARTSAPI_KEY_VINDECODE не задан — VINdecodeOE отключён")
    if not PARTSAPI_KEY_MAKES:
        missing.append("PARTSAPI_KEY_MAKES")
    if not PARTSAPI_KEY_MODELS:
        missing.append("PARTSAPI_KEY_MODELS")
    if not PARTSAPI_KEY_CARS:
        missing.append("PARTSAPI_KEY_CARS")
    if not PARTSAPI_KEY_TREE:
        missing.append("PARTSAPI_KEY_TREE")
    if not PARTSAPI_KEY_ARTICLES:
        missing.append("PARTSAPI_KEY_ARTICLES")
    if not PARTSAPI_KEY_CROSSES:
        logger.warning("PARTSAPI_KEY_CROSSES не задан — аналоги отключены")

    if missing:
        logger.error("Не заданы обязательные переменные окружения: %s", ", ".join(missing))
        raise SystemExit(1)

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("vin", cmd_vin))
    app.add_handler(CommandHandler("crosses", cmd_crosses))
    app.add_handler(CommandHandler("debug", cmd_debug))

    logger.info("Бот запущен. TecDoc chain активен.")
    app.run_polling()

if __name__ == "__main__":
    main()
