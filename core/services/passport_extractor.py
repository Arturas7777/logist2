"""AI-распознавание паспорта РБ и транслитерация адреса (пакет автовоза).

После загрузки фото/скана главной страницы белорусского паспорта Claude Vision
извлекает номер паспорта, ФИО латиницей и даты (рождения/выдачи) — они всегда
хорошо читаются, в том числе из MRZ-строки внизу страницы. Вручную клиент
вводит только ФИО и адрес кириллицей; латинский вариант адреса для инвойса
и платёжки транслитерируется автоматически.

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

PASSPORT_PROMPT = """Ты — система распознавания главной страницы паспорта гражданина
Республики Беларусь (фото или скан).

Правила:
- passport_number: серия и номер паспорта — 2 латинские буквы + 7 цифр
  (например MC3902087). Он напечатан в правом верхнем углу страницы и
  продублирован в MRZ-строке внизу. Сверь оба места.
- surname_latin / given_name_latin: фамилия и имя ЛАТИНИЦЕЙ — в паспорте РБ
  они напечатаны под кириллическим вариантом и продублированы в MRZ-строке
  (формат P<BLRSURNAME<<GIVENNAME<...). MRZ — самый надёжный источник.
- birth_date: дата рождения в формате YYYY-MM-DD.
- issue_date: дата выдачи паспорта в формате YYYY-MM-DD (поле «Дата выдачи /
  Date of issue»). Не путай с датой окончания срока действия.
- Если поле не читается — ставь null, НЕ выдумывай.

Верни ТОЛЬКО валидный JSON (без markdown):
{
  "passport_number": "MC3902087",
  "surname_latin": "ZIZIKA",
  "given_name_latin": "ULADZIMIR",
  "birth_date": "1967-01-29",
  "issue_date": "2025-10-22"
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

_PASSPORT_NUMBER_RE = re.compile(r"^[A-Z]{2}\d{7}$")


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


def extract_passport(path: str) -> dict[str, str]:
    """Распознать главную страницу паспорта РБ.

    Возвращает dict с ключами данных пакета (``buyer_name``,
    ``buyer_passport_number``, ``buyer_birth_date``,
    ``buyer_passport_issue_date``); нечитаемые поля опущены.
    Пустой dict — распознать не удалось.
    """
    images = render_document_images(path)
    if not images:
        return {}
    data = _call_claude_vision(
        images,
        system_prompt=PASSPORT_PROMPT,
        user_text="Это главная страница паспорта гражданина Республики Беларусь. Извлеки данные по схеме.",
    )
    if not isinstance(data, dict):
        return {}

    result: dict[str, str] = {}

    number = str(data.get("passport_number") or "").replace(" ", "").upper()
    if _PASSPORT_NUMBER_RE.match(number):
        result["buyer_passport_number"] = number

    surname = str(data.get("surname_latin") or "").strip().upper()
    given = str(data.get("given_name_latin") or "").strip().upper()
    if surname or given:
        result["buyer_name"] = " ".join(filter(None, (surname, given)))

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
