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
PARTSAPI_KEY_VINDECODE = os.getenv("PARTSAPI_KEY_VINDECODE")
PARTSAPI_KEY_VIN       = os.getenv("PARTSAPI_KEY_VIN")
PARTSAPI_KEY_CROSSES   = os.getenv("PARTSAPI_KEY_CROSSES")
PARTSAPI_KEY_TECDOC    = os.getenv("PARTSAPI_KEY_TECDOC")    # getMakes/getModels/getCars/getSearchTree/getArticles
PARTSAPI_KEY_OE        = os.getenv("PARTSAPI_KEY_OE")        # getOEApplicability
PARTSAPI_KEY_TO        = os.getenv("PARTSAPI_KEY_TO")        # toParts / toOils

DEFAULT_TIMEOUT = 15.0
LANG_RU = 16  # язык ответов — русский


# ══════════════════════════════════════════════════════════════════════════════
# Низкоуровневый HTTP-хелпер
# ══════════════════════════════════════════════════════════════════════════════

async def _get(
session: aiohttp.ClientSession,
params: dict[str, Any],
timeout: float = DEFAULT_TIMEOUT,
) -> Any:
"""
Выполняет GET-запрос к BASE_URL с заданными params.
Возвращает распарсенный JSON (dict | list) или None при ошибке.
"""
try:
    async with session.get(
        BASE_URL,
        params=params,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as r:
        r.raise_for_status()
        text = await r.text()
        return json.loads(text)
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
# 1. VINdecodeOE — расшифровка VIN по оригинальным каталогам
# ══════════════════════════════════════════════════════════════════════════════

async def vin_decode_oe(session: aiohttp.ClientSession, vin: str) -> dict | None:
"""
Возвращает dict с полями:
    manuName, modelName, typeName, katalog, modely, rynok,
    + все сырые поля из arr (carId, ktype и др. если есть)
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
    # стандартные поля
    "manuName":  arr.get("brend", ""),
    "modelName": arr.get("naimenovanie", ""),
    "typeName":  arr.get("modifikaciya", ""),
    "katalog":   arr.get("katalog", ""),
    "modely":    arr.get("modely", ""),
    "rynok":     arr.get("rynok", ""),
    # сохраняем весь сырой ответ — пригодится для отладки и новых полей
    "_raw": arr,
}


# ══════════════════════════════════════════════════════════════════════════════
# 2. getPartsbyVIN — OEM запчасти по VIN и категории
# ══════════════════════════════════════════════════════════════════════════════

async def get_parts_by_vin(
session: aiohttp.ClientSession,
vin: str,
cat: str,
timeout: float = DEFAULT_TIMEOUT,
) -> dict | None:
"""
Возвращает сырой JSON-ответ или None при ошибке/таймауте.
Нормализация и кэш — на стороне вызывающего кода (test_vin_bot.py).
"""
return await _get(session, {
    "method": "getPartsbyVIN",
    "key": PARTSAPI_KEY_VIN,
    "vin": vin,
    "type": "oem",
    "cat": cat,
}, timeout=timeout)


# ══════════════════════════════════════════════════════════════════════════════
# 3. tecdocCrosses — аналоги по базе TecDoc
# ══════════════════════════════════════════════════════════════════════════════

async def get_crosses(session: aiohttp.ClientSession, article: str) -> list:
"""
Возвращает список аналогов (list of dict) или [] при ошибке.
"""
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
# 4. TecDoc: дерево групп и артикулы по carId
# ══════════════════════════════════════════════════════════════════════════════

async def get_search_tree(
session: aiohttp.ClientSession,
car_id: str | int,
car_type: str = "PC",
) -> list[dict]:
"""
getSearchTree — дерево товарных групп для модификации авто.

car_id   — идентификатор модификации (carId из getCars или modely из VINdecodeOE)
car_type — "PC" (легковые) | "CV" (грузовые) | "Motorcycle"

Возвращает список узлов дерева или [] при ошибке.
"""
data = await _get(session, {
    "method": "getSearchTree",
    "key": PARTSAPI_KEY_TECDOC,
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
car_id: str | int,
str_id: str | int,
car_type: str = "PC",
) -> list[dict]:
"""
getArticles — артикулы для выбранной группы дерева.

car_id  — идентификатор модификации
str_id  — идентификатор узла дерева (NODE_1_STR_ID / NODE_2_STR_ID / NODE_3_STR_ID)
car_type — "PC" | "CV" | "Motorcycle"

Возвращает список артикулов или [] при ошибке.
Каждый элемент содержит: SUP_BRAND, ART_ARTICLE_NR, ART_ID, PRODUCT_GROUP, PT_ID.
"""
data = await _get(session, {
    "method": "getArticles",
    "key": PARTSAPI_KEY_TECDOC,
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


# ══════════════════════════════════════════════════════════════════════════════
# 5. getOEApplicability — проверка применяемости OEM артикула
# ══════════════════════════════════════════════════════════════════════════════

async def get_oe_applicability(
session: aiohttp.ClientSession,
oe_number: str,
brand: str,
) -> list[dict]:
"""
Проверяет, к каким автомобилям подходит данный OEM артикул.
Используется для валидации артикула перед показом пользователю.

Возвращает список применяемостей или [] если артикул не найден / ошибка.
"""
data = await _get(session, {
    "method": "getOEApplicability",
    "key": PARTSAPI_KEY_OE,
    "oeNumber": oe_number,
    "brand": brand,
    "lang": LANG_RU,
})
if isinstance(data, list):
    return data
if isinstance(data, dict) and data.get("error_code"):
    logger.warning("[getOEApplicability] %s %s error: %s", brand, oe_number, data.get("message"))
return []


# ══════════════════════════════════════════════════════════════════════════════
# 6. toParts — детали для ТО по модификации
# ══════════════════════════════════════════════════════════════════════════════

async def get_to_parts(
session: aiohttp.ClientSession,
type_id: str | int,
) -> list[dict]:
"""
toParts — список деталей для технического обслуживания модификации.
Стабильная альтернатива getPartsbyVIN для ТО-категорий (фильтры, свечи, масло).

type_id — идентификатор модификации ТС (из toTypes)

Возвращает список деталей или [] при ошибке.
"""
data = await _get(session, {
    "method": "toParts",
    "key": PARTSAPI_KEY_TO,
    "typeId": type_id,
    "lang": LANG_RU,
})
if isinstance(data, list):
    return data
if isinstance(data, dict) and data.get("error_code"):
    logger.warning("[toParts] typeId=%s error: %s", type_id, data.get("message"))
return []


async def get_to_oils(
session: aiohttp.ClientSession,
type_id: str | int,
) -> list[dict]:
"""
toOils — заправочные объёмы жидкостей для модификации.

Возвращает список объёмов или [] при ошибке.
"""
data = await _get(session, {
    "method": "toOils",
    "key": PARTSAPI_KEY_TO,
    "typeId": type_id,
    "lang": LANG_RU,
})
if isinstance(data, list):
    return data
if isinstance(data, dict) and data.get("error_code"):
    logger.warning("[toOils] typeId=%s error: %s", type_id, data.get("message"))
return []


# ══════════════════════════════════════════════════════════════════════════════
# 7. getMakes / getModels / getCars — навигация по каталогу TecDoc
# ══════════════════════════════════════════════════════════════════════════════

async def get_makes(
session: aiohttp.ClientSession,
vehicle_type: str = "PC",
) -> list[dict]:
"""
getMakes — список производителей.
Каждый элемент: {"makeId": int, "makeName": str}
"""
data = await _get(session, {
    "method": "getMakes",
    "key": PARTSAPI_KEY_TECDOC,
    "vehicleType": vehicle_type,
    "lang": LANG_RU,
})
return data if isinstance(data, list) else []


async def get_models(
session: aiohttp.ClientSession,
make_id: int,
vehicle_type: str = "PC",
) -> list[dict]:
"""
getModels — список моделей производителя.
Каждый элемент: {"makeId": int, "makeName": str, "modelId": int, "modelName": str}
"""
data = await _get(session, {
    "method": "getModels",
    "key": PARTSAPI_KEY_TECDOC,
    "makeId": make_id,
    "vehicleType": vehicle_type,
    "lang": LANG_RU,
})
return data if isinstance(data, list) else []


async def get_cars(
session: aiohttp.ClientSession,
make_id: int,
model_id: int,
vehicle_type: str = "PC",
) -> list[dict]:
"""
getCars — список модификаций модели.
Содержит carId, который нужен для getSearchTree и getArticles.
"""
data = await _get(session, {
    "method": "getCars",
    "key": PARTSAPI_KEY_TECDOC,
    "makeId": make_id,
    "modelId": model_id,
    "vehicleType": vehicle_type,
    "lang": LANG_RU,
})
return data if isinstance(data, list) else []
