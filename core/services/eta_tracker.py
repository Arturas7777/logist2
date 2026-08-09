"""Обновление Container.eta из Track & Trace API морских линий.

Maersk и CMA CGM отдают события контейнера в формате стандарта DCSA
Track & Trace: список событий, где TRANSPORT-события с
``transportEventTypeCode=ARRI`` и ``eventClassifierCode=PLN/EST`` — это
плановые/расчётные прибытия судна. Самое позднее из них — прибытие в
конечный порт выгрузки (промежуточные ARRI при трансшипментах раньше).

Доступ:
  * Maersk  — https://developer.maersk.com, ключ приложения в заголовке
    ``Consumer-Key`` (env ``MAERSK_CONSUMER_KEY``).
  * CMA CGM — https://api-portal.cma-cgm.com, API-ключ в заголовке
    ``KeyId`` (env ``CMA_CGM_API_KEY``).
  * MSC — доступ к API выдаётся по заявке на их портале; адаптер добавим,
    когда будут реквизиты.

Вызывается из Celery-задач (``update_container_eta_task`` после применения
dock receipt и ``update_container_etas_task`` ежедневно по beat) — см.
core/tasks.py.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20


# ── Парсинг DCSA-событий ───────────────────────────────────────────────────


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def extract_eta_from_events(payload) -> date | None:
    """Достаёт плановую дату прибытия из DCSA Track & Trace событий.

    ``payload`` — список событий либо dict с ключом ``events``.
    Берём TRANSPORT-события ARRI с классификатором PLN (план) или EST
    (оценка) и возвращаем самую позднюю дату: при трансшипментах это
    прибытие в конечный порт.
    """
    events = payload.get("events") if isinstance(payload, dict) else payload
    best: datetime | None = None
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        if (ev.get("eventType") or "").upper() != "TRANSPORT":
            continue
        if (ev.get("transportEventTypeCode") or "").upper() != "ARRI":
            continue
        if (ev.get("eventClassifierCode") or "").upper() not in ("PLN", "EST"):
            continue
        dt = _parse_dt(ev.get("eventDateTime"))
        if dt and (best is None or dt > best):
            best = dt
    return best.date() if best else None


# ── Адаптеры линий ─────────────────────────────────────────────────────────


def _fetch_maersk(container_number: str) -> tuple[object | None, str]:
    """DCSA-события Maersk. Возвращает (payload, error_message)."""
    key = (getattr(settings, "MAERSK_CONSUMER_KEY", "") or "").strip()
    if not key:
        return None, "MAERSK_CONSUMER_KEY не задан в .env"
    resp = requests.get(
        "https://api.maersk.com/track-and-trace-private/events",
        params={"equipmentReference": container_number},
        headers={"Consumer-Key": key, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 404:
        return None, "контейнер не найден в системе Maersk"
    if resp.status_code in (401, 403):
        return None, f"Maersk отклонил ключ (HTTP {resp.status_code}) — проверьте MAERSK_CONSUMER_KEY"
    resp.raise_for_status()
    return resp.json(), ""


def _fetch_cma_cgm(container_number: str) -> tuple[object | None, str]:
    """DCSA-события CMA CGM (покрывает также ANL/APL/CNC)."""
    key = (getattr(settings, "CMA_CGM_API_KEY", "") or "").strip()
    if not key:
        return None, "CMA_CGM_API_KEY не задан в .env"
    resp = requests.get(
        "https://apis.cma-cgm.net/operation/trackandtrace/v1/events",
        params={"equipmentReference": container_number},
        headers={"KeyId": key, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 404:
        return None, "контейнер не найден в системе CMA CGM"
    if resp.status_code in (401, 403):
        return None, f"CMA CGM отклонил ключ (HTTP {resp.status_code}) — проверьте CMA_CGM_API_KEY"
    resp.raise_for_status()
    return resp.json(), ""


# Имя Line в БД → адаптер. MSC/Hapag/COSCO/ONE/OOCL подключим по мере
# получения доступов.
LINE_FETCHERS = {
    "MAERSK": _fetch_maersk,
    "CMA": _fetch_cma_cgm,
}


# ── Обновление контейнера ──────────────────────────────────────────────────


def update_container_eta(container) -> dict:
    """Запрашивает ETA у линии контейнера и обновляет ``container.eta``.

    Возвращает dict с результатом (все значения JSON-сериализуемые —
    результат уходит в Celery backend):
      * updated — bool, поменяли ли дату;
      * old_eta / new_eta — ISO-строки или None;
      * message — человекочитаемое пояснение.
    """
    result = {
        "container": container.number,
        "updated": False,
        "old_eta": container.eta.isoformat() if container.eta else None,
        "new_eta": None,
        "message": "",
    }

    line_name = (container.line.name if container.line_id else "").strip().upper()
    fetcher = LINE_FETCHERS.get(line_name)
    if fetcher is None:
        result["message"] = f"линия «{line_name or '—'}» не поддерживается (есть: {', '.join(LINE_FETCHERS)})"
        return result

    try:
        payload, error = fetcher(container.number)
    except requests.RequestException as e:
        result["message"] = f"ошибка запроса к {line_name}: {e}"
        logger.warning("ETA %s (%s): %s", container.number, line_name, e)
        return result

    if payload is None:
        result["message"] = error
        logger.info("ETA %s (%s): %s", container.number, line_name, error)
        return result

    eta = extract_eta_from_events(payload)
    if eta is None:
        result["message"] = "в ответе линии нет плановой даты прибытия"
        return result

    result["new_eta"] = eta.isoformat()
    if container.eta == eta:
        result["message"] = "ETA не изменился"
        return result

    old = container.eta
    container.eta = eta
    container.save(update_fields=["eta"])
    result["updated"] = True
    result["message"] = f"ETA обновлён: {old or '—'} → {eta}"
    logger.info("ETA %s (%s): %s → %s", container.number, line_name, old, eta)
    return result
