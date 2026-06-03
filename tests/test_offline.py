"""Оффлайн-тесты логики бота — БЕЗ единого запроса к partsapi.

Запуск (любой из вариантов):
    python tests/test_offline.py
    pytest tests/test_offline.py -q

Внешние модули (aiohttp, telegram, dotenv) подменяются стабами, поэтому
сеть/ключи не нужны. Проверяется парсинг, нормализация, классификация
ошибок, признаки качества OEM, уровни coverage и retry на временных сбоях.
"""
from __future__ import annotations
import os
import sys
import types
import asyncio
from pathlib import Path

# ── Запускаемся из корня репозитория, чтобы PartsResolver нашёл parts/*.csv ──
REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT))


# ── Стабы внешних модулей (нет в оффлайн-окружении) ──
def _install_stubs() -> None:
    aiohttp = types.ModuleType("aiohttp")

    class _ClientTimeout:
        def __init__(self, *a, **k):
            pass

    aiohttp.ClientTimeout = _ClientTimeout
    aiohttp.ClientSession = object
    aiohttp.ClientError = type("ClientError", (Exception,), {})
    aiohttp.ClientResponseError = type("ClientResponseError", (aiohttp.ClientError,), {})
    aiohttp.ServerTimeoutError = type("ServerTimeoutError", (aiohttp.ClientError,), {})
    sys.modules["aiohttp"] = aiohttp

    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = dotenv

    telegram = types.ModuleType("telegram")
    telegram.Update = object
    sys.modules["telegram"] = telegram

    tg_ext = types.ModuleType("telegram.ext")
    tg_ext.ApplicationBuilder = object
    tg_ext.CommandHandler = object
    tg_ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
    sys.modules["telegram.ext"] = tg_ext


_install_stubs()
import test_vin_bot as b  # noqa: E402

# Ускоряем retry в тестах (без реальных пауз) и делаем тесты герметичными:
# никакого чтения/записи реального cache.json на диске.
b.API_DELAY = 0
b._PARTS_RETRY_PAUSE = 0
b._CACHE = {}
b.cache_get = lambda key: None
b.cache_set = lambda *a, **k: None

MANU = "MITSUBISHI"

# Сырые ответы из реального лога пользователя (cat 8 / 281 / 282)
RAW_8 = [{
    "group": "Система подачи воздуха", "name": "Воздушный фильтр",
    "parts": ("ALFA ROMEO|1444.RT,CITROEN|1444 RT,PEUGEOT|1444VP,"
              "MITSUBISHI|1500A023,MITSUBISHI|1500A086,BOSCH|0986AF2661,"
              "SCANIA|1364283,MERITOR|MA-4613"),
}]
RAW_281 = [{
    "group": "Тормозная система", "name": "Тормозные колодки",
    "parts": ("CITROEN/PEUGEOT|4241N6,MITSUBISHI|4800A001,"
              "MITSUBISHI|MN161157,PEUGEOT|4241N6,SUBARU|26694FG010"),
    "shortname": "Тормозные колодки",
}]


# ── Фейковые HTTP session/response для теста retry ──
class _FakeResp:
    def __init__(self, status, text):
        self._status = status
        self._text = text

    @property
    def status(self):
        return self._status

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _RaisingCM:
    def __init__(self, exc):
        self.exc = exc

    async def __aenter__(self):
        raise self.exc

    async def __aexit__(self, *a):
        return False


class FakeSession:
    """session.get(...) проигрывает сценарий step-ов по порядку.

    step: ("raise", exc) | ("resp", status, text)
    Последний step повторяется, если попыток больше, чем шагов.
    """
    def __init__(self, script):
        self.script = script
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if step[0] == "raise":
            return _RaisingCM(step[1])
        return _FakeResp(step[1], step[2])


# ══════════════════════════════════════════════════════════════════
#  ТЕСТЫ: парсинг и нормализация
# ══════════════════════════════════════════════════════════════════

def test_extract_flat_items_from_parts_string():
    res = b.normalize_getparts_response(RAW_281, 200, cat="281", vin="X")
    assert res["api_status"] == "OK"
    # 5 пар brand|article распарсены в отдельные items
    assert res["total_raw"] == 5
    pairs = {(i["brand"], i["article"]) for i in res["items_raw"]}
    assert ("MITSUBISHI", "4800A001") in pairs
    assert ("SUBARU", "26694FG010") in pairs


def test_truck_brands_filtered_out():
    res = b.normalize_getparts_response(RAW_8, 200, cat="8", vin="X")
    brands = {i["brand"].upper() for i in res["items_raw"]}
    assert "SCANIA" not in brands and "MERITOR" not in brands
    assert "MITSUBISHI" in brands


def test_empty_array_is_no():
    res = b.normalize_getparts_response([], 200, cat="282", vin="X")
    assert res["api_status"] == "NO"
    assert res["total_raw"] == 0


def test_parts_as_list_of_dicts():
    raw = {"items": [{"group": "G", "name": "N",
                      "parts": [{"brand": "BOSCH", "article": "0986"},
                                {"manuName": "MANN", "number": "W914"}]}]}
    res = b.normalize_getparts_response(raw, 200, cat="7", vin="X")
    assert res["total_raw"] == 2


def test_none_and_garbage_are_err():
    assert b.normalize_getparts_response(None, 200, cat="x", vin="X")["api_status"] == "ERR"
    assert b.normalize_getparts_response("oops", 200, cat="x", vin="X")["api_status"] == "ERR"


# ══════════════════════════════════════════════════════════════════
#  ТЕСТЫ: классификация ошибок
# ══════════════════════════════════════════════════════════════════

def test_rate_limit_classification():
    res = b.normalize_getparts_response(
        {"error_code": 5000, "message": "Exceeded the number of requests", "status": 401},
        401, cat="7", vin="X")
    assert res["api_status"] == "ERR"
    assert res["error_code"] == "RATE_LIMIT"


def test_server_error_classification():
    res = b.normalize_getparts_response(
        {"error_code": 5007, "message": "Request error.", "status": 500},
        500, cat="9", vin="X")
    assert res["error_code"] == "SERVER_ERROR"


def test_timeout_exception_classification():
    res = b.normalize_getparts_response(None, None, exception=TimeoutError("slow"), cat="82", vin="X")
    assert res["api_status"] == "ERR"
    assert res["error_code"] == "TimeoutError"


# ══════════════════════════════════════════════════════════════════
#  ТЕСТЫ: качество OEM (raw_ok / precise_oem / noisy)
# ══════════════════════════════════════════════════════════════════

def test_quality_cat8_precise_but_noisy():
    res = b.filter_items_by_query_intent(
        b.normalize_getparts_response(RAW_8, 200, cat="8", vin="X"), "8")
    q = b.classify_oem_quality(b._parts_from_result(res), MANU)
    assert q["raw_ok"] is True
    assert q["precise_oem"] is True       # есть MITSUBISHI
    assert q["noisy"] is True             # но чужих марок больше
    assert b.coverage_quality(res, MANU)[0] == "OK"


def test_quality_cat281_weak_after_filter():
    # cat 281 (перёд): MITSUBISHI-артикулы — задние, отсеиваются фильтром
    res = b.filter_items_by_query_intent(
        b.normalize_getparts_response(RAW_281, 200, cat="281", vin="X"), "281")
    q = b.classify_oem_quality(b._parts_from_result(res), MANU)
    assert q["precise_oem"] is False
    assert b.coverage_quality(res, MANU)[0] == "WEAK"


def test_coverage_quality_levels():
    no_res = b.normalize_getparts_response([], 200, cat="282", vin="X")
    err_res = b.normalize_getparts_response(
        {"error_code": 5000, "message": "Exceeded the number of requests", "status": 401},
        401, cat="7", vin="X")
    assert b.coverage_quality(no_res, MANU)[0] == "NO"
    assert b.coverage_quality(err_res, MANU)[0] == "ERR"


# ══════════════════════════════════════════════════════════════════
#  ТЕСТЫ: выбор основного OEM
# ══════════════════════════════════════════════════════════════════

def test_pick_primary_prefers_vehicle_brand_known_oem():
    res = b.filter_items_by_query_intent(
        b.normalize_getparts_response(RAW_8, 200, cat="8", vin="X"), "8")
    parts = b._parts_from_result(res)
    primary, _others = b.pick_primary_oem(parts, "8", MANU)
    assert primary is not None
    assert primary[0].upper() == "MITSUBISHI"
    # 1500A023 — эталонный OEM из OEM_FALLBACK_ARTICLES, должен быть основным
    assert b.normalize_article(primary[1]) == b.normalize_article("1500A023")


def test_pick_primary_never_foreign_vehicle_brand():
    # только чужие марки авто -> primary не выбирается
    parts = [("CITROEN", "4241N6"), ("PEUGEOT", "4241N6"), ("SUBARU", "26694FG010")]
    primary, _ = b.pick_primary_oem(parts, "281", MANU)
    assert primary is None


# ══════════════════════════════════════════════════════════════════
#  ТЕСТЫ: retry на временных сбоях
# ══════════════════════════════════════════════════════════════════

def test_retry_timeout_then_success():
    session = FakeSession([
        ("raise", TimeoutError("slow")),                 # попытка 1 — таймаут
        ("resp", 200, str(RAW_8).replace("'", '"')),     # попытка 2 — успех
    ])
    res = asyncio.run(b.api_get_parts_by_vin(session, "VINX", "8", use_cache=False))
    assert res["api_status"] == "OK"
    assert res["_attempts"] == 2
    assert res["_retried"] is True


def test_retry_5xx_then_success():
    session = FakeSession([
        ("resp", 500, '{"error_code":5007,"message":"Request error.","status":500}'),
        ("resp", 200, str(RAW_281).replace("'", '"')),
    ])
    res = asyncio.run(b.api_get_parts_by_vin(session, "VINX", "281", use_cache=False))
    assert res["api_status"] == "OK"
    assert res["_attempts"] == 2
    assert res["_retried"] is True


def test_rate_limit_not_retried():
    session = FakeSession([
        ("resp", 401, '{"error_code":5000,"message":"Exceeded the number of requests","status":401}'),
        ("resp", 200, str(RAW_8).replace("'", '"')),     # не должно быть достигнуто
    ])
    res = asyncio.run(b.api_get_parts_by_vin(session, "VINX", "8", use_cache=False))
    assert res["api_status"] == "ERR"
    assert res["error_code"] == "RATE_LIMIT"
    assert res["_attempts"] == 1          # без повторов
    assert session.calls == 1


def test_persistent_timeout_exhausts_retries():
    session = FakeSession([("raise", TimeoutError("slow"))])  # всегда таймаут
    res = asyncio.run(b.api_get_parts_by_vin(session, "VINX", "7", use_cache=False))
    assert res["api_status"] == "ERR"
    assert res["error_code"] == "TimeoutError"
    assert res["_attempts"] == 3          # 1 основная + 2 повтора
    assert session.calls == 3


def test_max_retries_zero_single_attempt():
    session = FakeSession([("raise", TimeoutError("slow"))])
    res = asyncio.run(b.api_get_parts_by_vin(session, "VINX", "7", use_cache=False, max_retries=0))
    assert res["api_status"] == "ERR"
    assert res["_attempts"] == 1          # без повторов
    assert session.calls == 1


# ── Сессия, отвечающая по params["cat"] (для проверки перебора cat) ──
class CatSession:
    def __init__(self, mapping):
        self.mapping = mapping            # cat -> ("raise", exc) | ("resp", status, text)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        cat = (params or {}).get("cat")
        self.calls.append(cat)
        step = self.mapping[cat]
        if step[0] == "raise":
            return _RaisingCM(step[1])
        return _FakeResp(step[1], step[2])


def test_fallback_tries_alternative_cat_on_timeout():
    # cat 7 (масляный) таймаутит -> должен перейти на альтернативный cat 10
    assert "7" in b.ALTERNATIVE_CATS
    session = CatSession({
        "7": ("raise", TimeoutError("slow")),
        "10": ("resp", 200, str(RAW_8).replace("'", '"')),
        "774": ("resp", 200, "[]"),
    })
    actual_cat, res = asyncio.run(b.fetch_parts_with_cat_fallback(session, "VINX", "7"))
    assert res["api_status"] == "OK"
    assert actual_cat == "10"
    assert session.calls == ["7", "10"]   # 774 не понадобился


def test_fallback_stops_on_rate_limit_no_alt():
    # при RATE_LIMIT альтернативные cat НЕ перебираем (бережём ключ/IP)
    session = CatSession({
        "7": ("resp", 401, '{"error_code":5000,"message":"Exceeded the number of requests","status":401}'),
        "10": ("resp", 200, str(RAW_8).replace("'", '"')),
        "774": ("resp", 200, "[]"),
    })
    _actual_cat, res = asyncio.run(b.fetch_parts_with_cat_fallback(session, "VINX", "7"))
    assert res["api_status"] == "ERR"
    assert res["error_code"] == "RATE_LIMIT"
    assert session.calls == ["7"]         # на лимите остановились сразу


# ══════════════════════════════════════════════════════════════════
#  ТЕСТЫ: coverage (OK/NO/WEAK/ERR + ранняя остановка)
# ══════════════════════════════════════════════════════════════════

def test_build_coverage_levels_and_stop():
    b.PARTSAPI_KEY_VINDECODE = "x"

    async def fake_vd(session, vin):
        return {"manuName": MANU}

    async def fake_api(session, vin, cat, *, timeout_sec=15.0, use_cache=True, max_retries=None):
        cat = str(cat)
        if cat == "8":
            return b.normalize_getparts_response(RAW_8, 200, cat=cat, vin=vin)      # OK
        if cat == "281":
            return b.normalize_getparts_response(RAW_281, 200, cat=cat, vin=vin)    # WEAK
        if cat == "282":
            return b.normalize_getparts_response([], 200, cat=cat, vin=vin)         # NO
        if cat == "9":
            return b.normalize_getparts_response(
                {"error_code": 5000, "message": "Exceeded the number of requests", "status": 401},
                401, cat=cat, vin=vin)                                             # ERR -> стоп
        return b.normalize_getparts_response([], 200, cat=cat, vin=vin)

    orig_vd, orig_api = b.api_vindecode, b.api_get_parts_by_vin
    b.api_vindecode, b.api_get_parts_by_vin = fake_vd, fake_api
    try:
        rep = asyncio.run(b.build_coverage_report(
            None, "VINX",
            cats=[("8", "возд"), ("281", "колодки"), ("282", "задн"), ("9", "топл"), ("7", "масл")]))
    finally:
        b.api_vindecode, b.api_get_parts_by_vin = orig_vd, orig_api

    assert rep["manu_name"] == MANU
    assert [c["catid"] for c in rep["ok_list"]] == ["8"]
    assert [c["catid"] for c in rep["weak_list"]] == ["281"]
    assert [c["catid"] for c in rep["no_list"]] == ["282"]
    assert rep["stopped"] is True
    assert rep["stopped_reason"] == "RATE_LIMIT"
    assert rep["checked"] < rep["total_cats"]   # остановились раньше конца


# ══════════════════════════════════════════════════════════════════
#  Самостоятельный запуск без pytest
# ══════════════════════════════════════════════════════════════════

def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} тестов прошло")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())