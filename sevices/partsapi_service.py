from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://api.partsapi.ru"

# ── API ключи (каждый метод — свой ключ) ──────────────────────────────────────
PARTSAPI_KEY_VINDECODE = os.getenv("PARTSAPI_KEY_VINDECODE")   # VINdecodeOE
PARTSAPI_KEY_VIN       = os.getenv("PARTSAPI_KEY_VIN")         # getPartsbyVIN
PARTSAPI_KEY_CROSSES   = os.getenv("PARTSAPI_KEY_CROSSES")     # tecdocCrosses
PARTSAPI_KEY_MAKES     = os.getenv("PARTSAPI_KEY_MAKES")        # getMakes
PARTSAPI_KEY_MODELS    = os.getenv("PARTSAPI_KEY_MODELS")       # getModels
PARTSAPI_KEY_CARS      = os.getenv("PARTSAPI_KEY_CARS")         # getCars
PARTSAPI_KEY_TREE      = os.getenv("PARTSAPI_KEY_TREE")         # getSearchTree
PARTSAPI_KEY_ARTICLES  = os.getenv("PARTSAPI_KEY_ARTICLES")     # getArticles

DEFAULT_TIMEOUT = 15.0
LANG_RU = 16  # язык ответов — русский

# ── Маппинг cat_id бота → STR_ID дерева TecDoc ────────────────────────────────
# Получен из getSearchTree(carId=111480) для MITSUBISHI OUTLANDER II CW6W.
# STR_ID стабильны между модификациями — используем как универсальный маппинг.
CAT_TO_STR_ID: dict[str, int] = {
"7":    100259,   # Масляный фильтр
"10":   100259,   # Масляный фильтр (альт. кат)
"774":  100259,   # Масляный фильтр (альт. кат)
"8":    100260,   # Воздушный фильтр
"424":  100263,   # Фильтр салона
"281":  100030,   # Тормозные колодки (передние)
"282":  100030,   # Тормозные колодки (задние)
"82":   100032,   # Тормозной диск (передний)
"84":   100032,   # Тормозной диск (задний)
"1041": 100121,   # Амортизатор (передний)
"1042": 100121,   # Амортизатор (задний)
"686":  100151,   # Свеча зажигания
"685":  100151,   # Свеча зажигания (альт. кат)
"188":  100113,   # Пружина подвески (передняя)
"189":  100113,   # Пружина подвески (задняя)
}

# ── Кэш makeId чтобы не тратить запросы к getMakes ───────────────────────────
KNOWN_MAKE_IDS: dict[str, int | None] = {
"MITSUBISHI": 77,
"AUDI":        5,
}


# ══════════════════════════════════════════════════════════════════════════════
# Низкоуровневый HTTP-хелпер
# ══════════════════════════════════════════════════════════════════════════════

async def _get(
session: aiohttp.ClientSession,
params: dict[str, Any],
timeout: float = DEFAULT_TIMEOUT,
) -> Any:
"""GET к BASE_URL. Возвращает dict | list или None при ошибке."""
try:
    async with session.get(
        BASE_URL,
        params=params,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as r:
        r.raise_for_status()
        return json.loads(await r.text())
except asyncio.TimeoutError:
    logger.warning("[partsapi] timeout: method=%s", params.get("method"))
    return None
except aiohttp.ClientResponseError as e:
    logger.warning("[partsapi] HTTP %s: method=%s", e.status, params.get("method"))
    return None
except Exception as e:
    logger.warning("[partsapi] error: method=%s — %s", params.get("method"), e)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 1. VINdecodeOE
# ══════════════════════════════════════════════════════════════════════════════

async def vin_decode_oe(session: aiohttp.ClientSession, vin: str) -> dict | None:
"""
Расшифровка VIN по оригинальным каталогам.
Возвращает dict: manuName, modelName, typeName, katalog, modely, rynok, _raw
или None при ошибке.
"""
data = await _get(session, {
    "method": "VINdecodeOE",
    "key": PARTSAPI_KEY_VINDECODE,
    "vin": vin,
    "lang": "ru",
})
if not isinstance(data, dict):
    return None
arr = data.get("data", {}).get("array", {})
if not arr:
    return None
return {
    "manuName":  arr.get("brend", ""),
    "modelName": arr.get("naimenovanie", ""),
    "typeName":  arr.get("modifikaciya", ""),
    "katalog":   arr.get("katalog", ""),
    "modely":    arr.get("modely", ""),
    "rynok":     arr.get("rynok", ""),
    "_raw":      arr,
}


# ══════════════════════════════════════════════════════════════════════════════
# 2. getPartsbyVIN — основной метод, остаётся как primary
# ══════════════════════════════════════════════════════════════════════════════

async def get_parts_by_vin(
session: aiohttp.ClientSession,
vin: str,
cat: str,
timeout: float = DEFAULT_TIMEOUT,
) -> dict | None:
"""
OEM запчасти по VIN и категории.
Возвращает сырой JSON или None. Нормализация и кэш — в test_vin_bot.py.
"""
return await _get(session, {
    "method": "getPartsbyVIN",
    "key": PARTSAPI_KEY_VIN,
    "vin": vin,
    "type": "oem",
    "cat": cat,
}, timeout=timeout)


# ══════════════════════════════════════════════════════════════════════════════
# 3. tecdocCrosses
# ══════════════════════════════════════════════════════════════════════════════

async def get_crosses(session: aiohttp.ClientSession, article: str) -> list:
"""Аналоги по базе TecDoc. Возвращает list или []."""
data = await _get(session, {
    "method": "tecdocCrosses",
    "key": PARTSAPI_KEY_CROSSES,
    "number": article,
})
if isinstance(data, list):
    return data
if isinstance(data, dict) and data.get("error_code"):
    logger.warning("[tecdocCrosses] error: %s", data.get("message"))
return []


# ══════════════════════════════════════════════════════════════════════════════
# 4. getMakes / getModels / getCars — у каждого свой ключ!
# ВАЖНО: параметр carType (не vehicleType!)
# ══════════════════════════════════════════════════════════════════════════════

async def get_makes(
session: aiohttp.ClientSession,
car_type: str = "PC",
) -> list[dict]:
"""Список производителей. Каждый элемент: {makeId, makeName}"""
data = await _get(session, {
    "method": "getMakes",
    "key": PARTSAPI_KEY_MAKES,
    "carType": car_type,
    "lang": LANG_RU,
})
return data if isinstance(data, list) else []


async def get_models(
session: aiohttp.ClientSession,
make_id: int,
car_type: str = "PC",
) -> list[dict]:
"""Список моделей производителя. Каждый элемент: {makeId, makeName, modelId, modelName}"""
data = await _get(session, {
    "method": "getModels",
    "key": PARTSAPI_KEY_MODELS,
    "makeId": make_id,
    "carType": car_type,
    "lang": LANG_RU,
})
return data if isinstance(data, list) else []


async def get_cars(
session: aiohttp.ClientSession,
make_id: int,
model_id: int,
car_type: str = "PC",
) -> list[dict]:
"""
Список модификаций модели.
carId — числовой, нужен для getSearchTree/getArticles.
Поля: carId, carName, makeId, makeName, modelId, modelName,
      CAPACITY, POWER_KW, POWER_PS, yearStart, yearEnd
"""
data = await _get(session, {
    "method": "getCars",
    "key": PARTSAPI_KEY_CARS,
    "makeId": make_id,
    "modelId": model_id,
    "carType": car_type,
    "lang": LANG_RU,
})
return data if isinstance(data, list) else []


# ══════════════════════════════════════════════════════════════════════════════
# 5. getSearchTree / getArticles — у каждого свой ключ!
# ══════════════════════════════════════════════════════════════════════════════

async def get_search_tree(
session: aiohttp.ClientSession,
car_id: int | str,
car_type: str = "PC",
) -> list[dict]:
"""
Дерево товарных групп для модификации.
car_id — числовой из getCars (например 111480 для CW6W).
Каждый узел: {STR_ID, STR_ID_PARENT, STR_LEVEL, STR_NODE_NAME, STR_PATH}
"""
data = await _get(session, {
    "method": "getSearchTree",
    "key": PARTSAPI_KEY_TREE,
    "carId": car_id,
    "carType": car_type,
    "lang": LANG_RU,
})
if isinstance(data, list):
    return data
if isinstance(data, dict) and data.get("error_code"):
    logger.warning("[getSearchTree] carId=%s error: %s", car_id, data.get("message"))
return []


async def get_articles(
session: aiohttp.ClientSession,
car_id: int | str,
str_id: int | str,
car_type: str = "PC",
) -> list[dict]:
"""
Артикулы для группы дерева.
car_id  — числовой из getCars
str_id  — STR_ID из CAT_TO_STR_ID или getSearchTree
Каждый элемент: {SUP_BRAND, ART_ARTICLE_NR, ART_ID, PRODUCT_GROUP, PT_ID}
"""
data = await _get(session, {
    "method": "getArticles",
    "key": PARTSAPI_KEY_ARTICLES,
    "carId": car_id,
    "strId": str_id,
    "carType": car_type,
    "lang": LANG_RU,
})
if isinstance(data, list):
    return data
if isinstance(data, dict) and data.get("error_code"):
    logger.warning("[getArticles] carId=%s strId=%s error: %s", car_id, str_id, data.get("message"))
return []


def get_str_id_for_cat(cat: str | int) -> int | None:
"""STR_ID дерева TecDoc для cat_id бота. None если маппинг не найден."""
return CAT_TO_STR_ID.get(str(cat))


# ══════════════════════════════════════════════════════════════════════════════
# 6. resolve_car_id — modely из VINdecodeOE → числовой carId для getSearchTree
# ══════════════════════════════════════════════════════════════════════════════

async def resolve_car_id(
session: aiohttp.ClientSession,
manu_name: str,
modely: str,
year: int | None = None,
) -> int | None:
"""
Находит числовой carId по данным из VINdecodeOE.

manu_name — марка ("MITSUBISHI")
modely    — код модификации ("CW6W")
year      — год выпуска для уточнения при нескольких совпадениях

Алгоритм: getMakes → getModels → getCars → carId
makeId кэшируется в KNOWN_MAKE_IDS — экономит 1 запрос из лимита.
Расходует до 3 запросов (makes + models + cars).
Возвращает числовой carId или None.
"""
manu_upper = manu_name.upper().strip()
modely_upper = modely.upper().strip()

# Шаг 1: makeId (из кэша или getMakes)
make_id = KNOWN_MAKE_IDS.get(manu_upper)
if make_id is None:
    makes = await get_makes(session)
    for m in makes:
        if m.get("makeName", "").upper() == manu_upper:
            make_id = m["makeId"]
            KNOWN_MAKE_IDS[manu_upper] = make_id
            break
if make_id is None:
    logger.warning("[resolve_car_id] makeId не найден для %s", manu_upper)
    return None

# Шаг 2: modelId — ищем по первым 2 символам modely в modelName
# "CW6W" → ищем "CW" в "OUTLANDER II (CW_W)"
models = await get_models(session, make_id)
model_id = None
prefix = modely_upper[:2] if len(modely_upper) >= 2 else modely_upper
for m in models:
    if prefix in m.get("modelName", "").upper():
        model_id = m["modelId"]
        break
if model_id is None:
    logger.warning("[resolve_car_id] modelId не найден для %s modely=%s", manu_upper, modely_upper)
    return None

# Шаг 3: carId — точное совпадение modely в carName, уточняем по году
cars = await get_cars(session, make_id, model_id)
best: int | None = None
fallback: int | None = None
for c in cars:
    car_name = c.get("carName", "").upper()
    if modely_upper not in car_name:
        continue
    raw_id = c.get("carId")
    if raw_id is None:
        continue
    cid = int(raw_id)
    if year and best is None:
        try:
            ys = int(str(c.get("yearStart", "0"))[:4])
            ye = int(str(c.get("yearEnd", "9999"))[:4])
            if ys <= year <= ye:
                best = cid
                continue
        except (ValueError, TypeError):
            pass
    if fallback is None:
        fallback = cid

result = best if best is not None else fallback
if result is None:
    logger.warning("[resolve_car_id] carId не найден для %s modely=%s", manu_upper, modely_upper)
return result
