"""Сопоставление письма с контейнерами.

Phase 2: возвращает список хитов, а не одну привязку — одно письмо может
упоминать несколько контейнеров, и в новой модели они все линкуются через
``ContainerEmail.containers`` (M2M).

Порядок приоритетов (побеждает первый сработавший источник):
  1. По треду — если в БД уже есть письма с тем же ``thread_id``, привязанные
     к контейнерам, — наследуем ВСЕ их привязки.
  2. По In-Reply-To — отвечаем на уже сохранённое письмо, наследуем его
     привязки.
  3. По номеру контейнера (ISO 6346: `[A-Z]{4}\\d{7}`) в теме или теле —
     ВСЕ найденные совпадения (а не только первое).
  4. По номеру букинга (``Container.booking_number``) в теме или теле —
     тоже все найденные.
  5. Иначе — UNMATCHED.

Функция чистая: не меняет БД (только читает). Сохранение связей — забота
ingest-слоя.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.services.gmail_client import ParsedMessage

logger = logging.getLogger(__name__)

# ISO 6346: 4 заглавные буквы + 7 цифр. Контейнер без контрольной суммы,
# но для матчинга достаточно — коллизий практически не бывает.
_CONTAINER_NUMBER_RE = re.compile(r'\b([A-Z]{4}\d{7})\b')

# Чтобы не ловить "U1", "UV3" как букинги — нижний предел длины.
_MIN_BOOKING_LEN = 4


@dataclass(frozen=True)
class MatchHit:
    """Одна привязка: контейнер + причина."""
    container_id: int
    matched_by: str  # одно из ContainerEmail.MATCHED_BY_*


@dataclass
class MatchResult:
    """Результат матчинга: все найденные контейнеры + «первичная» причина.

    ``hits`` — уникальный по container_id список, в порядке обнаружения
    (первый — наиболее приоритетный, он же идёт в ``ContainerEmail.matched_by``).
    """
    hits: list[MatchHit] = field(default_factory=list)

    @property
    def is_matched(self) -> bool:
        return bool(self.hits)

    @property
    def primary(self) -> MatchHit | None:
        return self.hits[0] if self.hits else None

    @property
    def primary_container_id(self) -> int | None:
        hit = self.primary
        return hit.container_id if hit else None

    @property
    def primary_matched_by(self) -> str:
        from core.models_email import ContainerEmail
        hit = self.primary
        return hit.matched_by if hit else ContainerEmail.MATCHED_BY_UNMATCHED


def match_email_to_containers(
    msg: 'ParsedMessage',
    *,
    booking_index: dict[str, int] | None = None,
) -> MatchResult:
    """Определяет, к каким контейнерам привязать письмо (может быть >1).

    ``booking_index`` — опциональный заранее построенный dict
    ``{booking_lower: container_id}``. Если не передать, функция построит его
    сама при каждом вызове (дорого для большого количества писем).
    """
    from core.models_email import ContainerEmail, ContainerEmailLink

    hits: list[MatchHit] = []
    seen: set[int] = set()

    def _add(cid: int, matched_by: str) -> None:
        if cid in seen:
            return
        seen.add(cid)
        hits.append(MatchHit(container_id=cid, matched_by=matched_by))

    # 1) По треду — наследуем все привязки из сохранённых писем того же треда
    tid = (msg.thread_id or '').strip()
    if tid:
        thread_links = (
            ContainerEmailLink.objects
            .filter(email__thread_id=tid)
            .values_list('container_id', flat=True)
            .distinct()
        )
        for cid in thread_links:
            _add(cid, ContainerEmail.MATCHED_BY_THREAD)

    # 2) По In-Reply-To (указывает на Message-ID родителя) — привязки родителя
    irt = (msg.in_reply_to or '').strip()
    if irt and not hits:
        parent_links = (
            ContainerEmailLink.objects
            .filter(email__message_id=irt)
            .values_list('container_id', flat=True)
            .distinct()
        )
        for cid in parent_links:
            _add(cid, ContainerEmail.MATCHED_BY_THREAD)

    haystack = f'{msg.subject}\n{msg.body_text}'

    # 3) По номеру контейнера (ISO 6346) — ВСЕ найденные
    for cid in _match_by_container_numbers(haystack):
        _add(cid, ContainerEmail.MATCHED_BY_CONTAINER_NUMBER)

    # 4) По номеру букинга — тоже все
    if booking_index is None:
        booking_index = build_booking_index()
    for cid in _match_by_bookings(haystack, booking_index):
        _add(cid, ContainerEmail.MATCHED_BY_BOOKING_NUMBER)

    return MatchResult(hits=hits)


# ── Backward-compat-обёртка (может ещё где-то использоваться) ───────────


def match_email_to_container(
    msg: 'ParsedMessage',
    *,
    booking_index: dict[str, int] | None = None,
) -> MatchResult:
    """Старое имя, возвращает тот же MatchResult.

    Оставлено для обратной совместимости: старый код дёргал
    ``match_email_to_container(...)`` и обращался к ``.container_id`` /
    ``.matched_by``. Эти атрибуты теперь доступны через свойства
    ``primary_container_id`` / ``primary_matched_by``. Код,
    использовавший старые имена, нужно подправить — модуль публикует оба API,
    чтобы миграция была плавной.
    """
    return match_email_to_containers(msg, booking_index=booking_index)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def build_booking_index(queryset=None) -> dict[str, int]:
    """``{booking_lower: container_id}`` — единоразово на прогоне задачи.

    Если ``queryset`` не передан, берутся все ``Container`` с непустым
    ``booking_number``. Передай уже отфильтрованный QuerySet, чтобы
    построить индекс только по «активным» контейнерам и не раздувать его.

    Игнорируем пустые booking_number. Если у двух контейнеров одинаковый
    букинг — остаётся первый по порядку; в реальности такое маловероятно,
    но лог пишем.
    """
    from core.models import Container

    index: dict[str, int] = {}
    if queryset is None:
        queryset = Container.objects.exclude(booking_number='')
    rows = queryset.values_list('id', 'booking_number')
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


def _match_by_container_numbers(text: str) -> list[int]:
    """Возвращает id ВСЕХ контейнеров, номера которых упомянуты в тексте."""
    from core.models import Container

    if not text:
        return []
    candidates = set(_CONTAINER_NUMBER_RE.findall(text.upper()))
    if not candidates:
        return []
    # Сохраним порядок появления в тексте, чтобы primary был «первым в теме».
    order_map: dict[str, int] = {}
    for idx, m in enumerate(_CONTAINER_NUMBER_RE.finditer(text.upper())):
        order_map.setdefault(m.group(1), idx)

    rows = list(
        Container.objects
        .filter(number__in=candidates)
        .values_list('id', 'number')
    )
    rows.sort(key=lambda row: order_map.get(row[1], 1_000_000))
    return [cid for cid, _number in rows]


def _match_by_bookings(text: str, booking_index: dict[str, int]) -> list[int]:
    """Возвращает id всех контейнеров по букингам, упомянутым в тексте."""
    if not text or not booking_index:
        return []
    lowered = text.lower()
    result: list[int] = []
    seen: set[int] = set()
    for booking_lower, cid in booking_index.items():
        if booking_lower not in lowered:
            continue
        pattern = rf'(?<![A-Za-z0-9]){re.escape(booking_lower)}(?![A-Za-z0-9])'
        if re.search(pattern, lowered):
            if cid not in seen:
                seen.add(cid)
                result.append(cid)
    return result
