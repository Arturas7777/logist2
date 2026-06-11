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
    n_make = (nhtsa.get("make") or "").strip().upper()
    (nhtsa.get("model") or "").strip().upper()

    if ai_year and n_year and int(ai_year) != int(n_year):
        result["warnings"].append(
            f"Год не совпадает: AI прочитал в документе {ai_year}, "
            f"но VIN декодируется как {n_year}-й год. Возможна ошибка в VIN."
        )
    if ai_make and n_make and ai_make.strip().upper() not in n_make and n_make not in ai_make.strip().upper():
        result["warnings"].append(f"Производитель не совпадает: AI={ai_make}, VIN→NHTSA={n_make}.")
    return result
