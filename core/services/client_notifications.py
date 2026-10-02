"""Уведомления клиента о событиях кабинета (C5) и напоминания о просрочке (B8).

Один конвейер для всех новых событий — email + Telegram, с дедупом через
``NotificationLog`` и учётом подписок (``ClientUser.notification_prefs``):

* ``PHOTOS_READY``     — появились публичные фото контейнера / авто;
* ``INVOICE_ISSUED``   — выставлен официальный счёт (PARDP) с PDF, если есть;
* ``REQUEST_STATUS``   — заявка на автовоз сменила статус;
* ``CAR_TRANSFERRED``  — авто передано клиенту;
* ``INVOICE_DUE_SOON`` — срок оплаты через 3 дня;
* ``INVOICE_OVERDUE``  — инвойс просрочен.

Существующие PLANNED / UNLOADED / CAR_UNLOADED остаются в
``email_service`` / ``telegram_service`` — здесь только новые события.

Язык письма — язык первого пользователя портала клиента (``ClientUser.language``),
иначе ``settings.LANGUAGE_CODE``. Шаблоны писем — ``templates/email/client_*.html``,
все строки через ``{% trans %}``.

Дедуп: запись ``NotificationLog`` с ``success=True`` по ключу
(клиент, тип, канал, контейнер/авто/заявка, ``cars_info``). У ``NotificationLog``
нет FK на инвойс, поэтому для инвойсов и статусов заявок ключ события хранится
в ``cars_info`` как детерминированный JSON (см. ``invoice_ref`` / ``status_ref``).
"""

from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import translation
from django.utils.html import strip_tags
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from core.services.telegram_service import _telegram_enabled, send_telegram_message

logger = logging.getLogger(__name__)

CHANNEL_EMAIL = "EMAIL"
CHANNEL_TELEGRAM = "TELEGRAM"
CHANNELS = (CHANNEL_EMAIL, CHANNEL_TELEGRAM)

EVENT_PHOTOS_READY = "PHOTOS_READY"
EVENT_INVOICE_ISSUED = "INVOICE_ISSUED"
EVENT_REQUEST_STATUS = "REQUEST_STATUS"
EVENT_CAR_TRANSFERRED = "CAR_TRANSFERRED"
EVENT_INVOICE_DUE_SOON = "INVOICE_DUE_SOON"
EVENT_INVOICE_OVERDUE = "INVOICE_OVERDUE"

# События, которыми клиент управляет в профиле (код, подпись, описание).
EVENTS = (
    (EVENT_PHOTOS_READY, gettext_lazy("Фото готовы"), gettext_lazy("Появились фотографии контейнера или автомобиля")),
    (EVENT_INVOICE_ISSUED, gettext_lazy("Выставлен счёт"), gettext_lazy("Новый счёт на оплату (с PDF во вложении)")),
    (EVENT_REQUEST_STATUS, gettext_lazy("Статус заявки"), gettext_lazy("Заявка на автовоз сменила статус")),
    (EVENT_CAR_TRANSFERRED, gettext_lazy("Авто передано"), gettext_lazy("Автомобиль передан / выехал со склада")),
    (
        EVENT_INVOICE_DUE_SOON,
        gettext_lazy("Напоминание об оплате"),
        gettext_lazy("За 3 дня до срока оплаты счёта и при просрочке"),
    ),
)
EVENT_CODES = tuple(code for code, _label, _help in EVENTS)
# Просрочка — та же подписка, что и напоминание (одна галочка в профиле).
_PREF_ALIASES = {EVENT_INVOICE_OVERDUE: EVENT_INVOICE_DUE_SOON}


def _site_url():
    return (getattr(settings, "SITE_URL", "") or getattr(settings, "COMPANY_WEBSITE", "") or "").rstrip("/")


def portal_url(viewname, *args):
    """Абсолютная ссылка в кабинет (пусто, если SITE_URL не настроен)."""
    base = _site_url()
    if not base:
        return ""
    return f"{base}{reverse(viewname, args=args)}"


def company_context():
    return {
        "company_name": getattr(settings, "COMPANY_NAME", "Caromoto Lithuania"),
        "company_phone": getattr(settings, "COMPANY_PHONE", ""),
        "company_email": getattr(settings, "COMPANY_EMAIL", ""),
        "company_website": getattr(settings, "COMPANY_WEBSITE", ""),
    }


def client_language(client):
    """Язык уведомлений: язык первого пользователя портала или дефолт сайта."""
    lang = client.portal_users.order_by("id").values_list("language", flat=True).first()
    return lang or settings.LANGUAGE_CODE


def client_wants(client, event, channel):
    """Подписка клиента на событие в канале.

    Клиент = компания, пользователей портала может быть несколько; отписка
    любого из них отключает канал — иначе «отписаться» не работало бы.
    Клиент без пользователей портала получает всё (как и раньше).
    """
    pref_event = _PREF_ALIASES.get(event, event)
    key = channel.lower()
    for prefs in client.portal_users.values_list("notification_prefs", flat=True):
        event_prefs = (prefs or {}).get(pref_event)
        if isinstance(event_prefs, dict) and not event_prefs.get(key, True):
            return False
    return True


def invoice_ref(invoice):
    """Ключ дедупа для инвойсных событий (хранится в ``NotificationLog.cars_info``)."""
    return json.dumps({"invoice_id": invoice.pk, "number": invoice.number}, ensure_ascii=False)


def status_ref(status):
    """Ключ дедупа для смены статуса заявки."""
    return json.dumps({"status": status}, ensure_ascii=False)


def cars_ref(cars):
    return json.dumps(
        [{"vin": c.vin, "brand": c.brand, "year": c.year} for c in cars],
        ensure_ascii=False,
    )


@dataclass
class ClientEvent:
    """Описание одного уведомления (общее для обоих каналов)."""

    event: str
    client: object
    subject: str
    template: str
    context: dict
    telegram_text: str
    container: object = None
    car: object = None
    transport_request: object = None
    dedup_key: str = ""
    attachments: list = field(default_factory=list)  # [(filename, bytes, mimetype)]
    user: object = None


def _already_sent(ev: ClientEvent, channel):
    from core.models.website import NotificationLog

    return NotificationLog.objects.filter(
        client=ev.client,
        notification_type=ev.event,
        channel=channel,
        success=True,
        container=ev.container,
        car=ev.car,
        transport_request=ev.transport_request,
        cars_info=ev.dedup_key,
    ).exists()


def _log(ev: ClientEvent, channel, recipient, success, error_message):
    from core.models.website import NotificationLog

    try:
        NotificationLog.objects.create(
            container=ev.container,
            car=ev.car,
            transport_request=ev.transport_request,
            client=ev.client,
            notification_type=ev.event,
            channel=channel,
            email_to=recipient,
            subject=ev.subject[:255],
            cars_info=ev.dedup_key,
            success=success,
            error_message=error_message,
            created_by=ev.user if (ev.user and getattr(ev.user, "is_authenticated", False)) else None,
        )
    except Exception as exc:
        logger.error("[client_notifications] не удалось записать NotificationLog: %s", exc)


def _send_email(ev: ClientEvent) -> int:
    client = ev.client
    if not (client.has_notification_emails() and client.notification_enabled):
        return 0
    if not client_wants(client, ev.event, CHANNEL_EMAIL):
        logger.info("[client_notifications] %s: email отключён клиентом %s", ev.event, client.name)
        return 0
    if _already_sent(ev, CHANNEL_EMAIL):
        return 0

    html_content = render_to_string(ev.template, ev.context)
    text_content = strip_tags(html_content)
    sent = 0
    for email_to in client.get_notification_emails():
        success, error_message = True, ""
        try:
            message = EmailMultiAlternatives(
                subject=ev.subject,
                body=text_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[email_to],
            )
            message.attach_alternative(html_content, "text/html")
            for filename, content, mimetype in ev.attachments:
                message.attach(filename, content, mimetype)
            message.send(fail_silently=False)
            sent += 1
        except Exception as exc:
            success, error_message = False, str(exc)
            logger.error("[client_notifications] email %s → %s не отправлен: %s", ev.event, email_to, exc)
        _log(ev, CHANNEL_EMAIL, email_to, success, error_message)
    return sent


def _send_telegram(ev: ClientEvent) -> int:
    client = ev.client
    if not _telegram_enabled() or not client.has_telegram():
        return 0
    if not client_wants(client, ev.event, CHANNEL_TELEGRAM):
        logger.info("[client_notifications] %s: telegram отключён клиентом %s", ev.event, client.name)
        return 0
    if _already_sent(ev, CHANNEL_TELEGRAM):
        return 0

    sent = 0
    for chat_id in client.get_telegram_chat_ids():
        success, error_message = send_telegram_message(chat_id, ev.telegram_text)
        if success:
            sent += 1
        else:
            logger.error("[client_notifications] telegram %s → %s: %s", ev.event, chat_id, error_message)
        _log(ev, CHANNEL_TELEGRAM, str(chat_id or ""), success, error_message)
    return sent


def dispatch(ev: ClientEvent) -> dict:
    """Отправляет событие в оба канала. Исключения каналов не пробрасываются
    выше уровня канала — падение Telegram не должно блокировать email и наоборот."""
    result = {"email": 0, "telegram": 0}
    try:
        result["email"] = _send_email(ev)
    except Exception:
        logger.exception("[client_notifications] email %s для %s упал", ev.event, ev.client.name)
    try:
        result["telegram"] = _send_telegram(ev)
    except Exception:
        logger.exception("[client_notifications] telegram %s для %s упал", ev.event, ev.client.name)
    return result


def _tg_footer():
    ctx = company_context()
    lines = ["", f"<b>{html.escape(ctx['company_name'])}</b>"]
    if ctx["company_phone"]:
        lines.append(html.escape(ctx["company_phone"]))
    return "\n".join(lines)


def _tg_cars(cars):
    rows = []
    for car in cars:
        label = " ".join(p for p in (str(car.brand or ""), str(car.year or "")) if p)
        rows.append(f"• {html.escape(label)} (VIN: {html.escape(str(car.vin or ''))})")
    return "\n".join(rows)


# ── События ─────────────────────────────────────────────────────────────────


def notify_photos_ready(*, container=None, car=None, user=None) -> dict:
    """«Фото готовы»: по контейнеру — всем клиентам его авто, по авто — его клиенту.

    Отправляется один раз на контейнер/авто и клиента (дедуп по FK в логе).
    """
    if container is None and car is None:
        return {"email": 0, "telegram": 0}

    totals = {"email": 0, "telegram": 0}
    if container is not None:
        photos_count = container.photos.filter(is_public=True).count()
        if not photos_count:
            return totals
        cars = list(container.container_cars.select_related("client").filter(client__isnull=False))
        clients = {c.client_id: c.client for c in cars}
        for client_id, client in clients.items():
            client_cars = [c for c in cars if c.client_id == client_id]
            result = _dispatch_photos_ready(
                client=client,
                container=container,
                car=None,
                cars=client_cars,
                photos_count=photos_count,
                gallery_number=container.number,
                user=user,
            )
            totals = {k: totals[k] + result[k] for k in totals}
        return totals

    if not car.client_id:
        return totals
    photos_count = car.photos.filter(is_public=True).count()
    if not photos_count:
        return totals
    return _dispatch_photos_ready(
        client=car.client,
        container=None,
        car=car,
        cars=[car],
        photos_count=photos_count,
        gallery_number=car.container.number if car.container_id else "",
        user=user,
    )


def _dispatch_photos_ready(*, client, container, car, cars, photos_count, gallery_number, user):
    with translation.override(client_language(client)):
        if container is not None:
            subject = _("Фотографии контейнера %(number)s готовы") % {"number": container.number}
            link = portal_url("website:container_detail", container.pk)
        else:
            subject = _("Фотографии автомобиля %(vin)s готовы") % {"vin": car.vin}
            link = portal_url("website:car_detail", car.pk)
        context = {
            **company_context(),
            "subject": subject,
            "client_name": client.name,
            "container": container,
            "car": car,
            "cars": cars,
            "photos_count": photos_count,
            "portal_url": link,
            "gallery_url": f"{_site_url()}/?track={gallery_number}&photos=1" if gallery_number and _site_url() else "",
        }
        tg = [f"📷 <b>{html.escape(subject)}</b>", "", _("Здравствуйте, %(name)s!") % {"name": html.escape(client.name)}]
        tg += ["", _("Доступно фотографий: %(count)s") % {"count": photos_count}]
        if cars:
            tg += ["", _("Ваши автомобили:"), _tg_cars(cars)]
        if link:
            tg += ["", f'<a href="{link}">{_("Открыть в кабинете")}</a>']
        tg.append(_tg_footer())
        return dispatch(
            ClientEvent(
                event=EVENT_PHOTOS_READY,
                client=client,
                subject=subject,
                template="email/client_photos_ready.html",
                context=context,
                telegram_text="\n".join(tg),
                container=container,
                car=car,
                dedup_key=cars_ref(cars),
                user=user,
            )
        )


def _invoice_attachment(invoice):
    """PDF инвойса для вложения, если файл есть и это PDF."""
    attachment = getattr(invoice, "attachment", None)
    if not attachment or not getattr(attachment, "name", ""):
        return []
    name = attachment.name.rsplit("/", 1)[-1]
    if not name.lower().endswith(".pdf"):
        return []
    try:
        with attachment.open("rb") as fh:
            content = fh.read()
    except Exception as exc:
        logger.warning("[client_notifications] не удалось прочитать вложение инвойса %s: %s", invoice.number, exc)
        return []
    return [(f"{invoice.number}.pdf", content, "application/pdf")]


def _invoice_context(invoice, client):
    cars = list(invoice.cars.all()) if hasattr(invoice, "cars") else []
    return {
        **company_context(),
        "client_name": client.name,
        "invoice": invoice,
        "cars": cars,
        "amount_due": (invoice.total or 0) - (invoice.paid_amount or 0),
        "portal_url": portal_url("website:invoices"),
    }


def notify_invoice_issued(invoice, user=None) -> dict:
    """«Инвойс выставлен»: PARDP перешёл в ISSUED → письмо с PDF (если прикреплён)."""
    client = invoice.recipient_client
    if client is None:
        return {"email": 0, "telegram": 0}
    with translation.override(client_language(client)):
        subject = _("Выставлен счёт %(number)s на %(total)s EUR") % {
            "number": invoice.number,
            "total": f"{invoice.total:.2f}",
        }
        context = {**_invoice_context(invoice, client), "subject": subject}
        tg = [
            f"🧾 <b>{html.escape(subject)}</b>",
            "",
            _("Здравствуйте, %(name)s!") % {"name": html.escape(client.name)},
            "",
            _("Сумма: <b>%(total)s EUR</b>") % {"total": f"{invoice.total:.2f}"},
        ]
        if invoice.due_date:
            tg.append(_("Срок оплаты: %(date)s") % {"date": invoice.due_date.strftime("%d.%m.%Y")})
        if context["portal_url"]:
            tg += ["", f'<a href="{context["portal_url"]}">{_("Открыть счета в кабинете")}</a>']
        tg.append(_tg_footer())
        return dispatch(
            ClientEvent(
                event=EVENT_INVOICE_ISSUED,
                client=client,
                subject=subject,
                template="email/client_invoice_issued.html",
                context=context,
                telegram_text="\n".join(tg),
                dedup_key=invoice_ref(invoice),
                attachments=_invoice_attachment(invoice),
                user=user,
            )
        )


def notify_invoice_due_soon(invoice, user=None) -> dict:
    """Напоминание за 3 дня до срока оплаты (B8)."""
    return _notify_invoice_reminder(invoice, EVENT_INVOICE_DUE_SOON, user=user)


def notify_invoice_overdue(invoice, user=None) -> dict:
    """Уведомление о просрочке (B8), один раз на инвойс."""
    return _notify_invoice_reminder(invoice, EVENT_INVOICE_OVERDUE, user=user)


def _notify_invoice_reminder(invoice, event, user=None) -> dict:
    client = invoice.recipient_client
    if client is None:
        return {"email": 0, "telegram": 0}
    overdue = event == EVENT_INVOICE_OVERDUE
    with translation.override(client_language(client)):
        if overdue:
            subject = _("Счёт %(number)s просрочен") % {"number": invoice.number}
            icon = "⚠️"
        else:
            subject = _("Напоминание: срок оплаты счёта %(number)s — %(date)s") % {
                "number": invoice.number,
                "date": invoice.due_date.strftime("%d.%m.%Y") if invoice.due_date else "",
            }
            icon = "⏰"
        context = {**_invoice_context(invoice, client), "subject": subject, "overdue": overdue}
        tg = [
            f"{icon} <b>{html.escape(subject)}</b>",
            "",
            _("Здравствуйте, %(name)s!") % {"name": html.escape(client.name)},
            "",
            _("К оплате: <b>%(amount)s EUR</b>") % {"amount": f"{context['amount_due']:.2f}"},
        ]
        if invoice.due_date:
            tg.append(_("Срок оплаты: %(date)s") % {"date": invoice.due_date.strftime("%d.%m.%Y")})
        if context["portal_url"]:
            tg += ["", f'<a href="{context["portal_url"]}">{_("Открыть счета в кабинете")}</a>']
        tg.append(_tg_footer())
        return dispatch(
            ClientEvent(
                event=event,
                client=client,
                subject=subject,
                template="email/client_invoice_reminder.html",
                context=context,
                telegram_text="\n".join(tg),
                dedup_key=invoice_ref(invoice),
                user=user,
            )
        )


def notify_request_status(transport_request, user=None) -> dict:
    """«Заявка сменила статус»: один раз на пару (заявка, статус)."""
    client = transport_request.client
    status_display = transport_request.get_status_display()
    with translation.override(client_language(client)):
        subject = _("Заявка %(number)s: %(status)s") % {
            "number": transport_request.number,
            "status": status_display,
        }
        cars = list(transport_request.cars.all())
        link = portal_url("website:transport_requests")
        if link:
            link = f"{link}?docs_req={transport_request.pk}#req-{transport_request.pk}"
        context = {
            **company_context(),
            "subject": subject,
            "client_name": client.name,
            "transport_request": transport_request,
            "status_display": status_display,
            "cars": cars,
            "portal_url": link,
        }
        tg = [
            f"🚚 <b>{html.escape(subject)}</b>",
            "",
            _("Здравствуйте, %(name)s!") % {"name": html.escape(client.name)},
            "",
            _("Новый статус заявки: <b>%(status)s</b>") % {"status": html.escape(str(status_display))},
        ]
        if cars:
            tg += ["", _tg_cars(cars)]
        if link:
            tg += ["", f'<a href="{link}">{_("Открыть заявку в кабинете")}</a>']
        tg.append(_tg_footer())
        return dispatch(
            ClientEvent(
                event=EVENT_REQUEST_STATUS,
                client=client,
                subject=subject,
                template="email/client_request_status.html",
                context=context,
                telegram_text="\n".join(tg),
                transport_request=transport_request,
                dedup_key=status_ref(transport_request.status),
                user=user,
            )
        )


def notify_car_transferred(car, user=None) -> dict:
    """«Авто передано»: Car перешёл в TRANSFERRED."""
    client = car.client
    if client is None:
        return {"email": 0, "telegram": 0}
    with translation.override(client_language(client)):
        subject = _("Автомобиль %(brand)s (%(vin)s) передан") % {"brand": car.brand or "", "vin": car.vin}
        link = portal_url("website:car_detail", car.pk)
        context = {
            **company_context(),
            "subject": subject,
            "client_name": client.name,
            "car": car,
            "transfer_date": car.transfer_date,
            "portal_url": link,
        }
        tg = [
            f"✅ <b>{html.escape(subject)}</b>",
            "",
            _("Здравствуйте, %(name)s!") % {"name": html.escape(client.name)},
            "",
            _tg_cars([car]),
        ]
        if car.transfer_date:
            tg.append(_("Дата передачи: <b>%(date)s</b>") % {"date": car.transfer_date.strftime("%d.%m.%Y")})
        if link:
            tg += ["", f'<a href="{link}">{_("Открыть в кабинете")}</a>']
        tg.append(_tg_footer())
        return dispatch(
            ClientEvent(
                event=EVENT_CAR_TRANSFERRED,
                client=client,
                subject=subject,
                template="email/client_car_transferred.html",
                context=context,
                telegram_text="\n".join(tg),
                car=car,
                dedup_key=cars_ref([car]),
                user=user,
            )
        )
