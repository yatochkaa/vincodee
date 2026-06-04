from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://api.partsapi.ru"

PARTSAPI_KEY_VINDECODE = os.getenv("PARTSAPI_KEY_VINDECODE")
PARTSAPI_KEY_CROSSES   = os.getenv("PARTSAPI_KEY_CROSSES")
PARTSAPI_KEY_MAKES     = os.getenv("PARTSAPI_KEY_MAKES")
PARTSAPI_KEY_MODELS    = os.getenv("PARTSAPI_KEY_MODELS")
PARTSAPI_KEY_CARS      = os.getenv("PARTSAPI_KEY_CARS")
PARTSAPI_KEY_TREE      = os.getenv("PARTSAPI_KEY_TREE")
PARTSAPI_KEY_ARTICLES  = os.getenv("PARTSAPI_KEY_ARTICLES")

DEFAULT_TIMEOUT = 15.0
LANG_RU = 16

CAT_TO_STR_ID: dict[str, int] = {
"7":    100259,
"10":   100259,
"774":  100259,
"8":    100260,
"9":    100261,
"424":  100263,
"281":  100030,
"282":  100030,
"82":   100032,
"84":   100032,
"1041": 100121,
"1042": 100121,
"686":  100151,
"685":  100151,
"689":  100153,
"188":  100113,
"189":  100113,
"273":  100110,
"274":  100110,
"1037": 100112,
"306":  100170,
"307":  100170,
"470":  100060,
"655":  100130,
"5":    100100,
}

KNOWN_MAKE_IDS: dict[str, int | None] = {
"MITSUBISHI": 77,
"AUDI": 5,
}


async def _get(
session: aiohttp.ClientSession,
params: dict[str, Any],
timeout: float = DEFAULT_TIMEOUT,
) -> Any:
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
    logger.warning("[partsapi] error: method=%s %s", params.get("method"), e)
    return None


async def vin_decode_oe(session: aiohttp.ClientSession, vin: str) -> dict | None:
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
    "brend":        arr.get("brend", ""),
    "naimenovanie": arr.get("naimenovanie", ""),
    "modifikaciya": arr.get("modifikaciya", ""),
    "katalog":      arr.get("katalog", ""),
    "modely":       arr.get("modely", ""),
    "rynok":        arr.get("rynok", ""),
    "data_vypuska": arr.get("data_vypuska", ""),
    "manuName":     arr.get("brend", ""),
    "modelName":    arr.get("naimenovanie", ""),
    "typeName":     arr.get("modifikaciya", ""),
    "_raw":         arr,
}


async def get_crosses(session: aiohttp.ClientSession, article: str) -> list:
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


async def get_makes(
session: aiohttp.ClientSession,
car_type: str = "PC",
) -> list[dict]:
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
data = await _get(session, {
    "method": "getCars",
    "key": PARTSAPI_KEY_CARS,
    "makeId": make_id,
    "modelId": model_id,
    "carType": car_type,
    "lang": LANG_RU,
})
return data if isinstance(data, list) else []


async def get_search_tree(
session: aiohttp.ClientSession,
car_id: int | str,
car_type: str = "PC",
) -> list[dict]:
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
return CAT_TO_STR_ID.get(str(cat))


async def resolve_car_id(
session: aiohttp.ClientSession,
manu_name: str,
modely: str,
year: int | None = None,
) -> int | None:
manu_upper   = manu_name.upper().strip()
modely_upper = modely.upper().strip()

make_id = KNOWN_MAKE_IDS.get(manu_upper)
if make_id is None:
    makes = await get_makes(session)
    for m in makes:
        if m.get("makeName", "").upper() == manu_upper:
            make_id = m["makeId"]
            KNOWN_MAKE_IDS[manu_upper] = make_id
            break
if make_id is None:
    logger.warning("[resolve_car_id] makeId not found for %s", manu_upper)
    return None

models = await get_models(session, make_id)
model_id = None
prefix = modely_upper[:2] if len(modely_upper) >= 2 else modely_upper
for m in models:
    if prefix in m.get("modelName", "").upper():
        model_id = m["modelId"]
        break
if model_id is None:
    logger.warning("[resolve_car_id] modelId not found for %s modely=%s", manu_upper, modely_upper)
    return None

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
    logger.warning("[resolve_car_id] carId not found for %s modely=%s", manu_upper, modely_upper)
return result
