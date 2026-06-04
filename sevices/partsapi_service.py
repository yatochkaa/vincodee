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
PARTSAPI_KEY_TECDOC    = os.getenv("PARTSAPI_KEY_TECDOC")   # getMakes/getModels/getCars/getSearchTree/getArticles
PARTSAPI_KEY_OE        = os.getenv("PARTSAPI_KEY_OE")       # getOEApplicability
PARTSAPI_KEY_TO        = os.getenv("PARTSAPI_KEY_TO")       # toParts / toOils

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

# ── Известные makeId для быстрого поиска без запроса к getMakes ───────────────
KNOWN_MAKE_IDS: dict[str, int] = {
"MITSUBISHI":    77,
"AUDI":           5,
"TOYOTA":        None,   # заполнить при первом запросе
"VOLKSWAGEN":    None,
"BMW":           None,
"MERCEDES-BENZ": None,
"NISSAN":        None,
"HONDA":         None,
"KIA":           None,
"HYUNDAI":       None,
"MAZDA":         None,
"SUBARU":        None,
"FORD":          None,
"OPEL":          None,
"RENAULT":       None,
"PEUGEOT":       None,
"CITROEN":       None,
"SKODA":         None,
"VOLVO":         None,
"INFINITI":      None,
"LEXUS":         None,
}


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
    + все сырые поля из arr (_raw)
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
# 4. getMakes / getModels / getCars — навигация по каталогу TecDoc
# ВАЖНО: параметр называется carType (не vehicleType!)
# ══════════════════════════════════════════════════════════════════════════════

async def get_makes(
session: aiohttp.ClientSession,
car_type: str = "PC",
) -> list[dict]:
"""
getMakes — список производителей.
Каждый элемент: {"makeId": int, "makeName": str}
"""
data = await _get(session, {
    "method": "getMakes",
    "key": PARTSAPI_KEY_TECDOC,
    "carType": car_type,
    "lang": LANG_RU,
})
return data if isinstance(data, list) else []


async def get_models(
session: aiohttp.ClientSession,
make_id: int,
car_type: str = "PC",
) -> list[dict]:
"""
getModels — список моделей производителя.
Каждый элемент: {"makeId": int, "makeName": str, "modelId": int, "modelName": str}
"""
data = await _get(session, {
    "method": "getModels",
    "key": PARTSAPI_KEY_TECDOC,
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
getCars — список модификаций модели.
Каждый элемент содержит carId (числовой), нужный для getSearchTree/getArticles.
Поля: carId, carName, makeId, makeName, modelId, modelName,
      CAPACITY, POWER_KW, POWER_PS, yearStart, yearEnd,
      BODY_TYPE_RU, ENGINE_TYPE_RU
"""
data = await _get(session, {
    "method": "getCars",
    "key": PARTSAPI_KEY_TECDOC,
    "makeId": make_id,
    "modelId": model_id,
    "carType": car_type,
    "lang": LANG_RU,
})
return data if isinstance(data, list) else []


# ══════════════════════════════════════════════════════════════════════════════
# 5. getSearchTree / getArticles — дерево групп и артикулы по carId
# ══════════════════════════════════════════════════════════════════════════════

async def get_search_tree(
session: aiohttp.ClientSession,
car_id: int | str,
car_type: str = "PC",
) -> list[dict]:
"""
getSearchTree — дерево товарных групп для модификации авто.

car_id   — числовой идентификатор из getCars (например 111480 для CW6W)
car_type — "PC" | "CV" | "Motorcycle"

Каждый узел содержит: STR_ID, STR_ID_PARENT, STR_LEVEL, STR_NODE_NAME, STR_PATH
Возвращает список узлов или [] при ошибке.
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
car_id: int | str,
str_id: int | str,
car_type: str = "PC",
) -> list[dict]:
"""
getArticles — артикулы для выбранной группы дерева.

car_id  — числовой идентификатор модификации (из getCars)
str_id  — STR_ID узла дерева (из CAT_TO_STR_ID или getSearchTree)
car_type — "PC" | "CV" | "Motorcycle"

Каждый элемент содержит: SUP_BRAND, ART_ARTICLE_NR, ART_ID, PRODUCT_GROUP, PT_ID
Возвращает список артикулов или [] при ошибке.
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


def get_str_id_for_cat(cat: str) -> int | None:
"""
Возвращает STR_ID дерева TecDoc для заданного cat_id бота.
Возвращает None если маппинг не найден.
"""
return CAT_TO_STR_ID.get(str(cat))


# ══════════════════════════════════════════════════════════════════════════════
# 6. resolve_car_id — получить числовой carId по modely из VINdecodeOE
# ══════════════════════════════════════════════════════════════════════════════

async def resolve_car_id(
session: aiohttp.ClientSession,
manu_name: str,
modely: str,
year: int | None = None,
) -> int | None:
"""
Находит числовой carId для getSearchTree по данным из VINdecodeOE.

manu_name — марка (например "MITSUBISHI")
modely    — код модификации из VINdecodeOE (например "CW6W")
year      — год выпуска для уточнения при нескольких совпадениях

Алгоритм:
  1. getMakes → найти makeId по manu_name
  2. getModels → найти modelId по вхождению modely в modelName
  3. getCars → найти carId по вхождению modely в carName

Возвращает числовой carId или None если не найдено.
Результат кэшируется в KNOWN_MAKE_IDS для makeId.
"""
manu_upper = manu_name.upper().strip()
modely_upper = modely.upper().strip()

# Шаг 1: makeId
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

# Шаг 2: modelId — ищем модель где modely встречается в названии
models = await get_models(session, make_id)
model_id = None
for m in models:
    name = m.get("modelName", "").upper()
    # modely типа "CW6W" должен встречаться в скобках: "OUTLANDER II (CW_W)"
    # ищем по первым двум символам кода (CW) чтобы покрыть CW0W/CW6W/CW8W
    if len(modely_upper) >= 2 and modely_upper[:2] in name:
        model_id = m["modelId"]
        break
if model_id is None:
    logger.warning("[resolve_car_id] modelId не найден для %s modely=%s", manu_upper, modely_upper)
    return None

# Шаг 3: carId — ищем модификацию где modely точно совпадает в carName
cars = await get_cars(session, make_id, model_id)
best_car_id = None
for c in cars:
    car_name = c.get("carName", "").upper()
    if modely_upper in car_name:
        car_id_val = c.get("carId")
        if car_id_val is None:
            continue
        # Если год известен — выбираем наиболее подходящий по дате
        if year and best_car_id is None:
            year_start = c.get("yearStart", "")
            year_end = c.get("yearEnd", "")
            try:
                ys = int(str(year_start)[:4]) if year_start else 0
                ye = int(str(year_end)[:4]) if year_end else 9999
                if ys <= year <= ye:
                    best_car_id = int(car_id_val)
                    continue
            except (ValueError, TypeError):
                pass
        if best_car_id is None:
            best_car_id = int(car_id_val)

if best_car_id is None:
    logger.warning("[resolve_car_id] carId не найден для %s modely=%s", manu_upper, modely_upper)
return best_car_id


# ══════════════════════════════════════════════════════════════════════════════
# 7. getOEApplicability — проверка применяемости OEM артикула
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
# 8. toParts / toOils — детали и жидкости для ТО
# ══════════════════════════════════════════════════════════════════════════════

async def get_to_parts(
session: aiohttp.ClientSession,
type_id: str | int,
) -> list[dict]:
"""
toParts — список деталей для ТО модификации.
Стабильная альтернатива getPartsbyVIN для ТО-категорий.

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
