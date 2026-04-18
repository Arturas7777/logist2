"""Сопоставление письма с контейнером.

Порядок приоритетов (побеждает первый сработавший):
  1. По треду — если в БД уже есть письмо с тем же ``thread_id`` и привязкой.
  2. По In-Reply-To — отвечаем на уже сохранённое письмо.
  3. По номеру контейнера (ISO 6346: `[A-Z]{4}\\d{7}`) в теме или теле.
  4. По номеру букинга (``Container.booking_number``) в теме или теле.
  5. Иначе — UNMATCHED.

Функция чистая: не меняет БД (только читает). Сохранение — забота ingest-слоя.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import Container
    from core.models_email import ContainerEmail
    from core.services.gmail_client import ParsedMessage

logger = logging.getLogger(__name__)

# ISO 6346: 4 заглавные буквы + 7 цифр. Контейнер без контрольной суммы,
# но для матчинга достаточно — коллизий практически не бывает.
_CONTAINER_NUMBER_RE = re.compile(r'\b([A-Z]{4}\d{7})\b')

# Чтобы не ловить "U1", "UV3" как букинги — нижний предел длины.
_MIN_BOOKING_LEN = 4


@dataclass(frozen=True)
class MatchResult:
    container_id: int | None
    matched_by: str  # одно из ContainerEmail.MATCHED_BY_*

    @property
    def is_matched(self) -> bool:
        return self.container_id is not None


def match_email_to_container(
    msg: 'ParsedMessage',
    *,
    booking_index: dict[str, int] | None = None,
) -> MatchResult:
    """Определяет, к какому контейнеру привязать письмо.

    ``booking_index`` — опциональный заранее построенный dict
    ``{booking_lower: container_id}``. Если не передать, функция построит его
    сама при каждом вызове (дорого для большого количества писем).
    """
    from core.models_email import ContainerEmail

    # 1) По треду
    tid = (msg.thread_id or '').strip()
    if tid:
        existing = (
            ContainerEmail.objects
            .filter(thread_id=tid, container__isnull=False)
            .only('container_id')
            .first()
        )
        if existing and existing.container_id:
            return MatchResult(existing.container_id, ContainerEmail.MATCHED_BY_THREAD)

    # 2) По In-Reply-To (указывает на Message-ID родителя)
    irt = (msg.in_reply_to or '').strip()
    if irt:
        parent = (
            ContainerEmail.objects
            .filter(message_id=irt, container__isnull=False)
            .only('container_id')
            .first()
        )
        if parent and parent.container_id:
            return MatchResult(parent.container_id, ContainerEmail.MATCHED_BY_THREAD)

    haystack = f'{msg.subject}\n{msg.body_text}'

    # 3) По номеру контейнера (ISO 6346)
    container_id = _match_by_container_number(haystack)
    if container_id is not None:
        return MatchResult(container_id, ContainerEmail.MATCHED_BY_CONTAINER_NUMBER)

    # 4) По номеру букинга
    if booking_index is None:
        booking_index = build_booking_index()
    container_id = _match_by_booking(haystack, booking_index)
    if container_id is not None:
        return MatchResult(container_id, ContainerEmail.MATCHED_BY_BOOKING_NUMBER)

    return MatchResult(None, ContainerEmail.MATCHED_BY_UNMATCHED)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def build_booking_index() -> dict[str, int]:
    """``{booking_lower: container_id}`` — единоразово на прогоне задачи.

    Игнорируем пустые booking_number. Если у двух контейнеров одинаковый
    букинг — остаётся последний по порядку (Django queryset order по pk asc);
    в реальности такое маловероятно, но лог пишем.
    """
    from core.models import Container

    index: dict[str, int] = {}
    rows = Container.objects.exclude(booking_number='').values_list('id', 'booking_number')
    for cid, booking in rows:
        if not booking:
            continue
        key = booking.strip().lower()
        if len(key) < _MIN_BOOKING_LEN:
            continue
        if key in index and index[key] != cid:
            logger.warning(
                "Booking collision: '%s' used by containers %d and %d. "
                "Will attach email to %d (first seen).",
                booking, index[key], cid, index[key],
            )
            continue
        index[key] = cid
    return index


def _match_by_container_number(text: str) -> int | None:
    from core.models import Container

    if not text:
        return None
    candidates = set(_CONTAINER_NUMBER_RE.findall(text.upper()))
    if not candidates:
        return None
    # Берём первый найденный контейнер, который есть в БД. Если их несколько —
    # Phase 1 привязывает к первому; массовый матч на несколько контейнеров
    # реализуется в Phase 2 через M2M.
    qs = Container.objects.filter(number__in=candidates).values_list('id', 'number')
    for cid, _number in qs:
        return cid
    return None


def _match_by_booking(text: str, booking_index: dict[str, int]) -> int | None:
    if not text or not booking_index:
        return None
    lowered = text.lower()
    # Сначала быстрый substring-скан; затем — проверка границ слов,
    # чтобы букинг "ABC123" не сработал на "ABC1234".
    for booking_lower, cid in booking_index.items():
        if booking_lower not in lowered:
            continue
        pattern = rf'(?<![A-Za-z0-9]){re.escape(booking_lower)}(?![A-Za-z0-9])'
        if re.search(pattern, lowered):
            return cid
    return None
