"""Коррекция OCR-ошибок в VIN и оценка уверенности распознавания.

Проблема: AI-vision читает VIN со сканов с типичными OCR-ошибками
(O вместо 0, S вместо 5, B вместо 8 и т.д.). Этот модуль — детерминированная
пост-обработка результата OCR:

  1. ``normalize_vin`` — замена символов, запрещённых в VIN по ISO 3779
     (I/O/Q не используются вообще, их появление = гарантированная ошибка).
  2. ``correct_vin_by_checksum`` — для североамериканских VIN с невалидной
     контрольной цифрой перебираем однобуквенные подмены из таблицы
     типичных OCR-путаниц; если РОВНО один кандидат проходит checksum —
     это почти наверняка правильный VIN.
  3. ``assess_vin_confidence`` — сводит все сигналы (checksum, NHTSA,
     повторное посимвольное чтение, факт автокоррекции) в уровень
     уверенности high / medium / low. На уровне high задача может
     применяться автоматически без ручного review.
  4. ``process_extracted_vin`` — полный конвейер для одного VIN из OCR,
     используется scan_extractor'ом.

Модуль не ходит в сеть сам по себе (NHTSA вызывается только внутри
``process_extracted_vin`` при use_nhtsa=True) — всё остальное чистая логика,
покрытая unit-тестами.
"""

from __future__ import annotations

import logging
from typing import Any

from core.services.vin_validator import (
    is_north_american_vin,
    is_vin_checksum_valid,
)

logger = logging.getLogger(__name__)

# Допустимые символы VIN (ISO 3779): цифры + латиница без I, O, Q.
VALID_VIN_CHARS = frozenset("0123456789ABCDEFGHJKLMNPRSTUVWXYZ")

# Запрещённые в VIN символы → их визуальные двойники.
_FORBIDDEN_MAP = {"I": "1", "O": "0", "Q": "0"}

# Таблица типичных OCR-путаниц (двунаправленная): если контрольная цифра
# не сходится, перебираем эти подмены по одной позиции.
_CONFUSION_MAP: dict[str, tuple[str, ...]] = {
    "0": ("D",),
    "D": ("0",),
    "1": ("L", "7"),
    "L": ("1",),
    "7": ("1",),
    "5": ("S",),
    "S": ("5",),
    "8": ("B",),
    "B": ("8",),
    "2": ("Z",),
    "Z": ("2",),
    "6": ("G",),
    "G": ("6",),
    "4": ("A",),
    "A": ("4",),
}


def normalize_vin(vin) -> tuple[str, list[str]]:
    """Upper + strip + замена запрещённых I/O/Q на визуальные двойники.

    Возвращает ``(normalized_vin, changes)``, где changes — человекочитаемый
    список замен вида ``"поз. 4: O → 0"`` (позиции 1-индексные).
    """
    raw = (vin or "").strip().upper().replace(" ", "")
    changes: list[str] = []
    out_chars: list[str] = []
    for i, ch in enumerate(raw):
        repl = _FORBIDDEN_MAP.get(ch)
        if repl is not None:
            changes.append(f"поз. {i + 1}: {ch} → {repl}")
            out_chars.append(repl)
        else:
            out_chars.append(ch)
    return "".join(out_chars), changes


def hamming_distance(a: str, b: str) -> int | None:
    """Число несовпадающих символов для строк одинаковой длины (иначе None)."""
    if not a or not b or len(a) != len(b):
        return None
    return sum(1 for x, y in zip(a, b, strict=True) if x != y)


def correct_vin_by_checksum(vin: str) -> dict[str, Any]:
    """Пытается исправить NA-VIN с невалидной контрольной цифрой.

    Перебирает однобуквенные подмены из ``_CONFUSION_MAP`` по каждой позиции
    и собирает кандидатов, у которых контрольная цифра сходится.

    Возвращает dict:
      * corrected: str | None — исправленный VIN, если кандидат РОВНО один.
      * candidates: list[str] — все кандидаты, прошедшие checksum.
      * applicable: bool — False, если коррекция неприменима (не 17 симв.,
        не североамериканский VIN или checksum уже валиден).
    """
    result: dict[str, Any] = {"corrected": None, "candidates": [], "applicable": False}
    if not vin or len(vin) != 17:
        return result
    if not is_north_american_vin(vin):
        # Для EU/Asian VIN контрольная цифра не обязана сходиться —
        # «исправление» по ней только навредит.
        return result
    if is_vin_checksum_valid(vin):
        return result

    result["applicable"] = True
    candidates: list[str] = []
    for pos, ch in enumerate(vin):
        for alt in _CONFUSION_MAP.get(ch, ()):
            candidate = vin[:pos] + alt + vin[pos + 1 :]
            if is_vin_checksum_valid(candidate):
                candidates.append(candidate)
    # Убираем дубликаты, сохраняя порядок.
    seen: set[str] = set()
    candidates = [c for c in candidates if not (c in seen or seen.add(c))]
    result["candidates"] = candidates
    if len(candidates) == 1:
        result["corrected"] = candidates[0]
    return result


def assess_vin_confidence(
    validation: dict[str, Any] | None,
    *,
    second_pass_agrees: bool | None = None,
    was_corrected: bool = False,
) -> dict[str, Any]:
    """Сводит сигналы валидации в уровень уверенности high / medium / low.

    Параметры:
      * validation — результат ``vin_validator.validate_vin`` /
        ``cross_check_with_ai_data`` (length_ok, checksum_ok, nhtsa, warnings).
      * second_pass_agrees — совпало ли повторное посимвольное чтение VIN
        (None = второй проход не выполнялся).
      * was_corrected — VIN был автоматически исправлен (нормализация I/O/Q
        не считается — только checksum-коррекция).

    Правила:
      * low — любое расхождение: невалидная длина, blocking-warnings
        (checksum NA, NHTSA invalid, mismatch make/year), несовпадение
        повторного чтения.
      * high — подтверждение NHTSA (декод make+year) И, для NA-VIN,
        валидная контрольная цифра. Автокоррекция требует NHTSA-подтверждения.
      * medium — всё, что подтверждено лишь частично (например NHTSA
        недоступен, но checksum сходится).
    """
    if not validation:
        return {"level": "low", "reasons": ["нет данных валидации VIN"]}
    if not validation.get("length_ok"):
        return {"level": "low", "reasons": ["VIN не 17-символьный"]}
    if second_pass_agrees is False:
        return {"level": "low", "reasons": ["повторное посимвольное чтение дало другой VIN"]}

    warnings = validation.get("warnings") or []
    # «NHTSA недоступен» — не ошибка VIN, а деградация проверки.
    blocking = [w for w in warnings if "недоступен" not in w]
    if blocking:
        return {"level": "low", "reasons": blocking}

    nhtsa = validation.get("nhtsa") or {}
    is_na = bool(validation.get("region_north_american"))
    checksum_ok = bool(validation.get("checksum_ok"))

    nhtsa_confirmed = bool(nhtsa) and not nhtsa.get("raw_failed") and bool(nhtsa.get("make") and nhtsa.get("year"))
    checksum_confirmed = checksum_ok  # для non-NA сходится редко, но если сошлось — плюс

    reasons: list[str] = []
    if nhtsa_confirmed:
        reasons.append(f"NHTSA декодировал VIN: {nhtsa.get('make')} {nhtsa.get('year')}")
    if checksum_confirmed:
        reasons.append("контрольная цифра сходится")
    if second_pass_agrees is True:
        reasons.append("повторное посимвольное чтение совпало")

    if nhtsa_confirmed and (checksum_confirmed or not is_na):
        level = "high"
    elif nhtsa_confirmed or checksum_confirmed:
        level = "medium"
        if not nhtsa_confirmed:
            reasons.append("NHTSA-подтверждения нет — уверенность ограничена")
    else:
        return {"level": "low", "reasons": ["ни контрольная цифра, ни NHTSA не подтвердили VIN"]}

    if was_corrected:
        reasons.append("VIN был автоматически исправлен по контрольной цифре")
        if not nhtsa_confirmed:
            level = "medium"

    return {"level": level, "reasons": reasons}


def process_extracted_vin(
    vin,
    *,
    ai_make: str | None = None,
    ai_model: str | None = None,
    ai_year=None,
    second_pass_vin: str | None = None,
    use_nhtsa: bool = True,
) -> dict[str, Any]:
    """Полный конвейер обработки одного VIN, прочитанного OCR.

    Шаги: нормализация I/O/Q → checksum-коррекция → валидация (checksum +
    NHTSA + cross-check с make/year из документа) → оценка уверенности.

    ``second_pass_vin`` — VIN из повторного посимвольного чтения (если было);
    сравнивается после такой же нормализации.

    Возвращает dict:
      * vin — итоговый (исправленный) VIN,
      * original — как прочитал OCR,
      * changes — список замен (нормализация + автокоррекция),
      * was_corrected — была ли checksum-коррекция,
      * checksum_candidates — кандидаты, если коррекция неоднозначна,
      * validation — результат vin_validator,
      * confidence — {"level": ..., "reasons": [...]}.
    """
    from core.services.vin_validator import cross_check_with_ai_data

    original = (vin or "").strip().upper()
    normalized, changes = normalize_vin(original)

    was_corrected = False
    checksum_candidates: list[str] = []
    final_vin = normalized
    if len(normalized) == 17:
        correction = correct_vin_by_checksum(normalized)
        checksum_candidates = correction["candidates"]
        if correction["corrected"]:
            final_vin = correction["corrected"]
            was_corrected = True
            diff_pos = next(
                (i for i, (a, b) in enumerate(zip(normalized, final_vin, strict=True)) if a != b),
                None,
            )
            if diff_pos is not None:
                changes.append(
                    f"поз. {diff_pos + 1}: {normalized[diff_pos]} → {final_vin[diff_pos]} (по контрольной цифре)"
                )

    second_pass_agrees: bool | None = None
    if second_pass_vin:
        second_normalized, _ = normalize_vin(second_pass_vin)
        # Если автокоррекция изменила VIN, сравниваем со ВТОРЫМ прочтением
        # оба варианта: совпадение с любым из них — согласие.
        second_pass_agrees = second_normalized in (final_vin, normalized)

    try:
        validation = cross_check_with_ai_data(
            final_vin,
            ai_make=ai_make,
            ai_model=ai_model,
            ai_year=ai_year,
            use_nhtsa=use_nhtsa,
        )
    except Exception as e:  # сеть/парсинг — не роняем пайплайн
        logger.warning("VIN validation failed for %s: %s", final_vin, e)
        validation = None

    confidence = assess_vin_confidence(
        validation,
        second_pass_agrees=second_pass_agrees,
        was_corrected=was_corrected,
    )

    return {
        "vin": final_vin,
        "original": original,
        "changes": changes,
        "was_corrected": was_corrected,
        "checksum_candidates": checksum_candidates,
        "second_pass_agrees": second_pass_agrees,
        "validation": validation,
        "confidence": confidence,
    }
