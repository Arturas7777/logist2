"""Валидация VIN: check digit + NHTSA decoder.

Используется для catching OCR-ошибок при обработке сканов титулов и
dock receipts. Если AI прочитал VIN с ошибкой и эта ошибка прошла все
другие проверки (точное совпадение между документами, отсутствие
похожих VIN в БД и т.д.), эти валидаторы — последняя линия защиты.

Архитектура:
  * vin_check_digit / is_vin_checksum_valid — чистая математика, ISO 3779.
    Для VIN из США/Канады (начинается с 1-5) — обязательно валидно.
    Для европейских/азиатских — может быть неактуально.
  * decode_vin_nhtsa — HTTP-запрос к https://vpic.nhtsa.dot.gov.
    Возвращает make/model/year + SuggestedVIN при ошибке. Бесплатно,
    без авторизации, rate limit ~5 req/s.
  * validate_vin — комбинированная функция, суммирующая обе проверки в
    один dict-результат, удобный для сохранения в extracted_data.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── ISO 3779 check digit ──────────────────────────────────────────────────

_TRANSLITERATION = {
    "A": 1,
    "B": 2,
    "C": 3,
    "D": 4,
    "E": 5,
    "F": 6,
    "G": 7,
    "H": 8,
    "J": 1,
    "K": 2,
    "L": 3,
    "M": 4,
    "N": 5,
    "P": 7,
    "R": 9,
    "S": 2,
    "T": 3,
    "U": 4,
    "V": 5,
    "W": 6,
    "X": 7,
    "Y": 8,
    "Z": 9,
}
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def vin_check_digit(vin: str) -> str | None:
    """Возвращает ожидаемую контрольную цифру (позиция 9, 0-индекс=8)."""
    if not vin or len(vin) != 17:
        return None
    total = 0
    for i, ch in enumerate(vin.upper()):
        if ch.isdigit():
            value = int(ch)
        elif ch in _TRANSLITERATION:
            value = _TRANSLITERATION[ch]
        else:
            return None  # I/O/Q или мусор — невалидный VIN
        total += value * _WEIGHTS[i]
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def is_vin_checksum_valid(vin: str) -> bool:
    expected = vin_check_digit(vin)
    if expected is None or len(vin) != 17:
        return False
    return vin[8].upper() == expected


def is_north_american_vin(vin: str) -> bool:
    """North American VIN (USA/Canada/Mexico) — check digit обязателен."""
    if not vin or len(vin) < 1:
        return False
    return vin[0].upper() in {"1", "2", "3", "4", "5"}


# ── NHTSA decode ──────────────────────────────────────────────────────────

_NHTSA_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/decodevin/{vin}?format=json"
_NHTSA_TIMEOUT = 5  # секунд


def decode_vin_nhtsa(vin: str, *, timeout: int = _NHTSA_TIMEOUT) -> dict[str, Any]:
    """Декодирует VIN через публичный NHTSA API.

    Возвращает dict (всегда):
      * ok: bool — VIN валиден по мнению NHTSA
      * make / model / year: str | None
      * error_code: str — '0' если нет ошибок
      * error_text: str — описание ошибки
      * suggested_vin: str — если NHTSA смог исправить (часто на 1 символ)
      * raw_failed: bool — если HTTP запрос упал

    На сетевые ошибки НЕ кидает исключений — возвращает raw_failed=True.
    """
    result: dict[str, Any] = {
        "ok": False,
        "make": None,
        "model": None,
        "year": None,
        "error_code": "",
        "error_text": "",
        "suggested_vin": "",
        "raw_failed": False,
    }
    if not vin or len(vin) != 17:
        result["error_text"] = "Invalid length"
        return result
    try:
        import requests
    except ImportError:
        logger.error("requests не установлен — NHTSA decode недоступен.")
        result["raw_failed"] = True
        return result

    try:
        resp = requests.get(_NHTSA_URL.format(vin=vin), timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("NHTSA decode failed for VIN=%s: %s", vin, e)
        result["raw_failed"] = True
        return result

    fields = {item.get("Variable"): item.get("Value") for item in data.get("Results") or []}
    result["error_code"] = fields.get("Error Code") or ""
    result["error_text"] = fields.get("Error Text") or ""
    result["make"] = fields.get("Make") or None
    result["model"] = fields.get("Model") or None
    year_str = fields.get("Model Year") or ""
    try:
        result["year"] = int(year_str) if year_str else None
    except ValueError:
        result["year"] = None
    result["suggested_vin"] = fields.get("Suggested VIN") or ""
    # ErrorCode '0' = no error. '1','2','3'... = разные виды проблем.
    # Также приемлем '6' (incomplete) — частично декодировано но make/model есть.
    # Считаем VIN "ok" только если error_code == '0'.
    result["ok"] = (result["error_code"] or "").strip() == "0"
    return result


# ── Технические характеристики по VIN ────────────────────────────────────

_NHTSA_VALUES_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"

# Ответ NHTSA по VIN не меняется, а проверка на санкции в кабинете клиента —
# сценарий «ввёл VIN, поправил поле, нажал ещё раз», поэтому кэшируем сутки.
_DETAILS_CACHE_TTL = 60 * 60 * 24


def decode_vin_details(vin: str, *, timeout: int = _NHTSA_TIMEOUT, use_cache: bool = True) -> dict[str, Any]:
    """Технические характеристики авто по VIN через NHTSA ``DecodeVinValues``.

    В отличие от :func:`decode_vin_nhtsa` (только make/model/year для проверки
    самого VIN) отдаёт то, что нужно для таможенной классификации: объём
    двигателя, тип топлива, уровень электрификации, тип кузова.

    Возвращает dict всегда; на сетевых ошибках — ``raw_failed=True``.
    """
    result: dict[str, Any] = {
        "ok": False,
        "raw_failed": False,
        "error_text": "",
        "make": None,
        "model": None,
        "year": None,
        "displacement_cc": None,
        "fuel_primary": "",
        "fuel_secondary": "",
        "electrification": "",
        "body_class": "",
        "vehicle_type": "",
        "engine_cylinders": None,
        "engine_hp": None,
    }
    vin_norm = (vin or "").strip().upper()
    if len(vin_norm) != 17:
        result["error_text"] = "VIN должен быть из 17 символов"
        return result

    from django.core.cache import cache

    cache_key = f"nhtsa:details:{vin_norm}"
    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return dict(cached)

    try:
        import requests
    except ImportError:
        logger.error("requests не установлен — NHTSA decode недоступен.")
        result["raw_failed"] = True
        return result

    try:
        resp = requests.get(_NHTSA_VALUES_URL.format(vin=vin_norm), timeout=timeout)
        resp.raise_for_status()
        rows = (resp.json() or {}).get("Results") or []
    except Exception as e:
        logger.warning("NHTSA details failed for VIN=%s: %s", vin_norm, e)
        result["raw_failed"] = True
        return result

    if not rows:
        result["error_text"] = "NHTSA не вернул данные по этому VIN"
        return result

    row = rows[0]
    result["error_text"] = (row.get("ErrorText") or "").strip()
    result["make"] = (row.get("Make") or "").strip() or None
    result["model"] = (row.get("Model") or "").strip() or None
    result["year"] = _as_int(row.get("ModelYear"))
    result["displacement_cc"] = _displacement_cc(row)
    result["fuel_primary"] = (row.get("FuelTypePrimary") or "").strip()
    result["fuel_secondary"] = (row.get("FuelTypeSecondary") or "").strip()
    result["electrification"] = (row.get("ElectrificationLevel") or "").strip()
    result["body_class"] = (row.get("BodyClass") or "").strip()
    result["vehicle_type"] = (row.get("VehicleType") or "").strip()
    result["engine_cylinders"] = _as_int(row.get("EngineCylinders"))
    result["engine_hp"] = _as_int(row.get("EngineHP"))
    # «ok» — хоть что-то полезное распознано: марка или год. Ошибки check
    # digit для нас здесь не важны, машину классифицируем по характеристикам.
    result["ok"] = bool(result["make"] or result["year"])

    if use_cache and result["ok"]:
        cache.set(cache_key, dict(result), _DETAILS_CACHE_TTL)
    return result


def _as_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(round(float(text)))
    except (TypeError, ValueError):
        return None


def _displacement_cc(row: dict[str, Any]) -> int | None:
    """Объём двигателя в см³: сначала ``DisplacementCC``, иначе из литров."""
    cc = _as_int(row.get("DisplacementCC"))
    if cc:
        return cc
    liters = str(row.get("DisplacementL") or "").strip()
    try:
        return int(round(float(liters) * 1000)) if liters else None
    except (TypeError, ValueError):
        return None


# ── Combined validation ──────────────────────────────────────────────────


def validate_vin(vin: str, *, use_nhtsa: bool = True) -> dict[str, Any]:
    """Собирает результаты check digit + NHTSA в единый dict.

    Подходит для сохранения в ScanProcessingJob.extracted_data['vin_validations'].

    use_nhtsa=False — для unit-тестов / offline режима.
    """
    vin_norm = (vin or "").strip().upper()
    out: dict[str, Any] = {
        "vin": vin_norm,
        "length_ok": len(vin_norm) == 17,
        "checksum_ok": is_vin_checksum_valid(vin_norm),
        "region_north_american": is_north_american_vin(vin_norm),
        "nhtsa": None,
        "warnings": [],
        "suggested_vin": "",
    }
    if not out["length_ok"]:
        out["warnings"].append("VIN не 17-символьный")
        return out
    # Check digit для NA-VIN — обязателен и важен.
    if out["region_north_american"] and not out["checksum_ok"]:
        out["warnings"].append(
            "Контрольная цифра VIN не сходится — для US/Canada VIN это почти наверняка ошибка чтения."
        )
    if use_nhtsa:
        nhtsa = decode_vin_nhtsa(vin_norm)
        out["nhtsa"] = nhtsa
        if nhtsa["raw_failed"]:
            out["warnings"].append("NHTSA API недоступен — пропустили проверку.")
        elif not nhtsa["ok"]:
            # NHTSA error_code != '0'. Но для не-NA VIN'ов "check digit
            # does not calculate" — известная норма (Audi/BMW/Porsche
            # не используют ISO check digit). Если make+year декодированы
            # успешно — не считаем это проблемой.
            err = (nhtsa.get("error_text") or "").strip()
            partial_decode_ok = bool(nhtsa.get("make") and nhtsa.get("year"))
            err_is_only_check_digit = "check digit" in err.lower() and "no detailed" not in err.lower()
            if not out["region_north_american"] and partial_decode_ok and err_is_only_check_digit:
                pass  # типичный EU/Asian VIN — пропускаем
            else:
                out["warnings"].append(f"NHTSA: VIN не валиден ({err or 'unknown'})")
                if nhtsa.get("suggested_vin"):
                    out["suggested_vin"] = nhtsa["suggested_vin"]
                    out["warnings"].append(f"NHTSA подсказывает правильный VIN: {nhtsa['suggested_vin']}")
    return out


def cross_check_with_ai_data(
    vin: str,
    *,
    ai_make: str | None = None,
    ai_model: str | None = None,
    ai_year: int | None = None,
    use_nhtsa: bool = True,
) -> dict[str, Any]:
    """validate_vin + сверка с make/model/year, которые AI извлёк отдельно.

    Главный value-add: если в одном из полей VIN AI ошибся, а make/model/year
    извлёк отдельно (с другого фрагмента документа), то NHTSA-декодинг
    кривого VIN даст другой год/модель → расхождение, которое и ловим.

    Пример:
      AI читает заголовок документа: "2024 GMC TERRAIN"
      AI читает VIN на наклейке:    "3GKALYEG5HL172044" (с ошибкой R->H)
      NHTSA декод VIN:               "GMC, year=2017" (а не 2024!)
      → mismatch_year warning.
    """
    result = validate_vin(vin, use_nhtsa=use_nhtsa)
    nhtsa = result.get("nhtsa") or {}
    if not nhtsa or nhtsa.get("raw_failed"):
        return result  # сравнивать не с чем

    n_year = nhtsa.get("year")
    n_make = nhtsa.get("make") or ""
    n_model = nhtsa.get("model") or ""

    if ai_year and n_year and int(ai_year) != int(n_year):
        result["warnings"].append(
            f"Год не совпадает: AI прочитал в документе {ai_year}, "
            f"но VIN декодируется как {n_year}-й год. Возможна ошибка в VIN."
        )
    # Модель с тайтла не сверяем: «EQUINOX LT» против «Equinox» — шум.
    # Марку сверяем через makes_match, чтобы CHEVY/CHEV/модель-в-поле-марки
    # не роняли уверенность VIN и не блокировали авто-применение.
    from core.services.vin_gate import extracted_make_agrees

    if ai_make and n_make and not extracted_make_agrees(ai_make, n_make, n_model):
        result["warnings"].append(f"Производитель не совпадает: AI={ai_make}, VIN→NHTSA={n_make}.")
    return result
