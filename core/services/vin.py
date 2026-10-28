"""Каноническая форма VIN и поиск VIN в тексте — единственное место.

``normalize_vin`` — только безопасные преобразования (пробелы, регистр).
Замена похожих символов I/O/Q — не нормализация, а коррекция OCR
(``vin_corrector.normalize_vin``) или подсказка пользователю (``vin_gate``).
"""

from __future__ import annotations

import re

# 17 символов VIN по ISO 3779 (без I, O, Q); \b — чтобы не цеплять подстроки.
VIN_SEARCH_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)


def normalize_vin(value) -> str:
    """VIN без пробельных символов, заглавными буквами."""
    return "".join(str(value or "").split()).upper()


def find_vins(text) -> list[str]:
    """VIN-ы из текста в порядке первого появления, без повторов."""
    return list(dict.fromkeys(m.group(0).upper() for m in VIN_SEARCH_RE.finditer(text or "")))
