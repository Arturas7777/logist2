"""Таймлайн статусов груза для кабинета клиента (C1).

Единый компонент FLOATING → IN_PORT → UNLOADED → TRANSFERRED для карточки
авто и контейнера (``templates/website/_status_timeline.html``). Публичный
трекинг на главной рисует тот же таймлайн на JS по данным ``/api/track/``.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

STATUS_ORDER = ("FLOATING", "IN_PORT", "UNLOADED", "TRANSFERRED")

STEP_LABELS = {
    "FLOATING": _("В пути"),
    "IN_PORT": _("В порту"),
    "UNLOADED": _("Разгружен"),
    "TRANSFERRED": _("Передан"),
}

STEP_ICONS = {
    "FLOATING": "bi-water",
    "IN_PORT": "bi-geo-alt",
    "UNLOADED": "bi-box-seam",
    "TRANSFERRED": "bi-check2-circle",
}


def build_status_timeline(status, *, eta=None, unload_date=None, transfer_date=None, history=None):
    """Шаги таймлайна для шаблона.

    ``history`` — ``{status: date}`` из ``CarStatusHistory`` (C2). Для
    UNLOADED / TRANSFERRED приоритет у бизнес-дат (``unload_date`` /
    ``transfer_date`` — их вводит оператор), журнал — запасной источник;
    для FLOATING / IN_PORT других дат нет, берём журнал.
    ETA показывается только пока груз ещё не прибыл.
    """
    history = history or {}
    try:
        current_idx = STATUS_ORDER.index(status)
    except ValueError:
        current_idx = -1

    steps = []
    for idx, code in enumerate(STATUS_ORDER):
        if idx < current_idx:
            state = "done"
        elif idx == current_idx:
            state = "current"
        else:
            state = "upcoming"

        date = history.get(code)
        date_prefix = ""
        if code == "UNLOADED":
            date = unload_date or date
        elif code == "TRANSFERRED":
            date = transfer_date or date
        elif code == "IN_PORT" and not date and state != "done" and eta and current_idx <= 0:
            # Прибытие в порт ещё впереди — показываем ожидаемую дату.
            date = eta
            date_prefix = "ETA"
        if state == "upcoming" and not date_prefix:
            # Шаг ещё впереди — старые даты (откат статуса) только путают.
            date = None

        steps.append(
            {
                "code": code,
                "label": STEP_LABELS[code],
                "icon": STEP_ICONS[code],
                "state": state,
                "date": date,
                "date_prefix": date_prefix,
            }
        )
    return steps
