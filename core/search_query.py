"""Нормализация поисковой строки.

При копировании VIN из Excel, браузера или мессенджера в буфер часто
попадает невидимый символ: ZERO WIDTH SPACE (U+200B), LTR-метка (U+200E),
BOM (U+FEFF). ``str.strip()`` их не снимает — это не whitespace, — и
``vin__icontains`` ищет 18 символов вместо 17. Совпадения нет.
"""

from __future__ import annotations

import re
import unicodedata

# Типичные «невидимые» символы из буфера обмена.
_INVISIBLE_RE = re.compile(
    r"[\u00ad"  # soft hyphen
    r"\u180e"  # mongolian vowel separator
    r"\u200b-\u200f"  # zwsp, zwnj, zwj, lrm, rlm
    r"\u202a-\u202e"  # bidi embeddings
    r"\u2060-\u206f"  # word joiner, invisible operators
    r"\ufeff]"  # BOM / zwnbsp
)

# Неразрывные/узкие пробелы: в поиск как обычный пробел, иначе VIN «ломается».
_NBSP_RE = re.compile(r"[\u00a0\u202f\u2007\u2009]")


def normalize_search_query(value: str | None) -> str:
    """Убрать невидимые символы и обрезать края поискового запроса."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = _INVISIBLE_RE.sub("", text)
    text = _NBSP_RE.sub(" ", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return text.strip()
