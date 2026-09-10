"""AI-распознавание паспорта РБ / ID-карты и транслитерация адреса.

После загрузки фото/скана Claude Vision извлекает номер документа, ФИО
латиницей и кириллицей, даты рождения и выдачи. Паспорт и ID-карта
(идентификационная карта РБ и похожие пластиковые удостоверения) идут
в один слот пакета: номер ID подставляется вместо номера паспорта.

Адрес с фото не читаем — клиент вводит его кириллицей, латинский вариант
для инвойса и платёжки транслитерируется автоматически.

Переиспользует рендер и вызов Claude Vision из :mod:`.scan_extractor`.
"""

from __future__ import annotations

import datetime
import logging
import os
import re
from typing import Any

from core.services.scan_extractor import _call_claude_vision, render_document_images

logger = logging.getLogger(__name__)

PASSPORT_PROMPT = """Ты — система распознавания удостоверения личности.
На фото/скане может быть ОДНО из двух:

1) Главная страница паспорта гражданина Республики Беларусь
   (книжка, MRZ вида P<BLR...).
2) Пластиковая ID-карта / идентификационная карта
   (РБ: «ИДЕНТИФИКАЦИОННАЯ КАРТА / IDENTITY CARD», или похожая карта
   другой страны — литовская asmens tapatybės kortelė и т.п.).

Правила:
- document_kind: "passport" или "id_card". Определи по виду документа.
- document_number: НОМЕР ДОКУМЕНТА, не личный/идентификационный номер.
  * Паспорт РБ: 2 латинские буквы + 7 цифр (MC3902087) — правый верх
    и дубль в MRZ.
  * ID-карта РБ: поле «НОМЕР КАРТЫ / DOCUMENT NUMBER». НЕ бери поле
    «ИДЕНТИФИКАЦИОННЫЙ № / IDENTIFICATION No» (это личный номер вида
    3121073A013PB2) — он не подходит вместо паспорта.
  * Другая ID-карта: номер карты / document number / kortelės numeris.
- surname_latin / given_name_latin: фамилия и имя ЛАТИНИЦЕЙ
  (в паспорте РБ — под кириллицей и в MRZ P<BLRSURNAME<<GIVEN...).
- surname_cyrillic / given_name_cyrillic / patronymic_cyrillic:
  ФИО КИРИЛЛИЦЕЙ (белорусский или русский текст на документе).
  Если на карте несколько кириллических вариантов — бери русский.
  Отчество — если напечатано, иначе null.
  Если кириллицы нет (европейская карта) — все три поля null.
- birth_date: дата рождения YYYY-MM-DD.
- issue_date: дата выдачи YYYY-MM-DD (Date of issue / Дата выдачи).
  Не путай со сроком действия.
- Если поле не читается — ставь null, НЕ выдумывай.

Верни ТОЛЬКО валидный JSON (без markdown):
{
  "document_kind": "id_card",
  "document_number": "KH1234567",
  "surname_latin": "VISLOBOKOV",
  "given_name_latin": "VALERY",
  "surname_cyrillic": "Вислобоков",
  "given_name_cyrillic": "Валерий",
  "patronymic_cyrillic": "Иванович",
  "birth_date": "1985-03-12",
  "issue_date": "2023-06-01"
}
"""

TRANSLIT_PROMPT = """Ты транслитерируешь белорусские адреса с кириллицы на латиницу
для международных платёжных документов (инвойс, SWIFT-платёж).

Правила:
- Используй общепринятую транслитерацию: ул. → ul., д. (деревня) → d.,
  г. → g., р-н → r-on, обл. → obl.
- Порядок частей сохраняй естественным для латинского адреса:
  сначала улица и дом, затем населённый пункт и район.
- В конце добавь ", Belarus", если страна не указана.
- Верни ТОЛЬКО одну строку адреса, без пояснений и кавычек.

Пример:
Вход: д. Большая Лысица, Несвижского р-на, ул. Гая 5
Выход: ul. Gaya 5, d.Bolshaya lysitsa, Nesvizhskiy r-on, Belarus
"""

# Паспорт РБ: две латинские буквы + 7 цифр.
_PASSPORT_NUMBER_RE = re.compile(r"^[A-Z]{2}\d{7}$")
# Номер пластиковой ID-карты (РБ / LT / похожие): не путать с личным номером.
_ID_CARD_NUMBER_RE = re.compile(r"^(?:[A-Z]{1,3}\d{5,9}|\d{8,10})$")
# Личный номер РБ (идентификационный №) — в документы не подставляем.
_PERSONAL_NUMBER_RE = re.compile(r"^\d{7}[A-Z]\d{3}[A-Z]{2}\d$")

_KIND_PASSPORT = "passport"
_KIND_ID_CARD = "id_card"


def ai_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", ""))


def _clean_date(value: Any) -> str:
    """ISO-дата из ответа модели или пустая строка."""
    if not value or not isinstance(value, str):
        return ""
    try:
        return datetime.date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return ""


def _clean_number(value: Any) -> str:
    """Нормализовать номер документа; пусто если это не номер карты/паспорта."""
    raw = str(value or "").replace(" ", "").replace("-", "").upper()
    if not raw or _PERSONAL_NUMBER_RE.match(raw):
        return ""
    if _PASSPORT_NUMBER_RE.match(raw) or _ID_CARD_NUMBER_RE.match(raw):
        return raw
    return ""


def _cyrillic_word(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "null":
        return ""
    if text.isupper():
        return text.capitalize()
    return text


def _latin_word(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "null":
        return ""
    return text.upper()


def extract_passport(path: str) -> dict[str, str]:
    """Распознать паспорт РБ или ID-карту.

    Возвращает dict с ключами данных пакета (``buyer_name``,
    ``buyer_name_ru``, ``buyer_passport_number``, ``buyer_birth_date``,
    ``buyer_passport_issue_date``, ``buyer_id_kind``); нечитаемые поля
    опущены. Пустой dict — распознать не удалось.
    """
    images = render_document_images(path)
    if not images:
        return {}
    data = _call_claude_vision(
        images,
        system_prompt=PASSPORT_PROMPT,
        user_text="Это паспорт или ID-карта. Извлеки данные по схеме.",
    )
    if not isinstance(data, dict):
        return {}

    result: dict[str, str] = {}

    number = _clean_number(data.get("document_number") or data.get("passport_number"))
    if number:
        result["buyer_passport_number"] = number

    kind = str(data.get("document_kind") or "").strip().lower()
    if kind == _KIND_ID_CARD or (kind != _KIND_PASSPORT and number and not _PASSPORT_NUMBER_RE.match(number)):
        result["buyer_id_kind"] = _KIND_ID_CARD
    elif number or kind == _KIND_PASSPORT:
        result["buyer_id_kind"] = _KIND_PASSPORT

    surname = _latin_word(data.get("surname_latin"))
    given = _latin_word(data.get("given_name_latin"))
    if surname or given:
        result["buyer_name"] = " ".join(filter(None, (surname, given)))

    cyr = [
        _cyrillic_word(data.get("surname_cyrillic")),
        _cyrillic_word(data.get("given_name_cyrillic")),
        _cyrillic_word(data.get("patronymic_cyrillic")),
    ]
    name_ru = " ".join(part for part in cyr if part)
    if name_ru:
        result["buyer_name_ru"] = name_ru

    birth = _clean_date(data.get("birth_date"))
    if birth:
        result["buyer_birth_date"] = birth
    issue = _clean_date(data.get("issue_date"))
    if issue:
        result["buyer_passport_issue_date"] = issue

    return result


def transliterate_address(address_ru: str) -> str:
    """Латинский вариант белорусского адреса (для инвойса/платёжки)."""
    data_text = address_ru.strip()
    if not data_text:
        return ""
    try:
        import anthropic

        from core.services.llm_text import anthropic_response_text
        from core.services.scan_extractor import _get_model_name

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
        response = client.messages.create(
            model=_get_model_name(),
            max_tokens=1000,
            system=TRANSLIT_PROMPT,
            messages=[{"role": "user", "content": data_text}],
        )
        line = anthropic_response_text(response).strip().strip('"')
        # Модель должна вернуть одну строку; всё лишнее — признак сбоя.
        if line and "\n" not in line:
            return line
    except Exception as exc:
        logger.warning("passport_extractor: транслитерация адреса не удалась: %s", exc)
    return ""
