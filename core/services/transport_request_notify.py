"""Уведомление клиента о нашем сообщении в заявке на автовоз.

Дублирует оба канала уведомлений о разгрузке (``email_service`` +
``telegram_service``) и пишет тот же ``NotificationLog``, но привязывается к
заявке (``NotificationLog.transport_request``), а не к контейнеру/ТС.

Дедупликации здесь нет — в отличие от разгрузки, каждое сообщение сотрудника
должно дойти до клиента, даже если по этой заявке уже писали.
"""

from __future__ import annotations

import html
import json
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags

from core.services.telegram_service import _telegram_enabled, send_telegram_message

logger = logging.getLogger(__name__)


def notify_client_about_message(message, user=None) -> dict:
    """Шлёт клиенту email и Telegram о новом сообщении сотрудника по заявке.

    Возвращает ``{"email": <кол-во успешных>, "telegram": <кол-во успешных>}``.
    Исключения не пробрасываются: сообщение в переписке уже сохранено, и
    падение внешнего канала не должно ломать ответ вьюхи.
    """
    if message.author_kind != message.AUTHOR_STAFF:
        return {"email": 0, "telegram": 0}

    transport_request = message.request
    client = transport_request.client
    is_doc_request = message.kind == message.KIND_DOC_REQUEST
    notification_type = "REQUEST_DOCS" if is_doc_request else "REQUEST_MESSAGE"

    context = _build_context(message)
    subject = context["subject"]

    sent_email = _send_email(
        message=message,
        client=client,
        subject=subject,
        context=context,
        notification_type=notification_type,
        user=user,
    )
    sent_tg = _send_telegram(
        message=message,
        client=client,
        subject=subject,
        context=context,
        notification_type=notification_type,
        user=user,
    )
    return {"email": sent_email, "telegram": sent_tg}


def _build_context(message) -> dict:
    transport_request = message.request
    is_doc_request = message.kind == message.KIND_DOC_REQUEST
    doc_labels = message.requested_doc_labels

    if is_doc_request:
        subject = f"Нужны документы по заявке {transport_request.number}"
    else:
        subject = f"Сообщение по заявке {transport_request.number}"

    portal_path = reverse("website:transport_requests")
    site_url = (getattr(settings, "SITE_URL", "") or "").rstrip("/")

    return {
        "subject": subject,
        "request_number": transport_request.number,
        "client_name": transport_request.client.name,
        "body": message.body or "",
        "is_doc_request": is_doc_request,
        "requested_doc_labels": doc_labels,
        "car": message.car,
        "portal_url": f"{site_url}{portal_path}#req-{transport_request.pk}" if site_url else "",
        "company_name": getattr(settings, "COMPANY_NAME", "Caromoto Lithuania"),
        "company_phone": getattr(settings, "COMPANY_PHONE", ""),
        "company_email": getattr(settings, "COMPANY_EMAIL", ""),
        "company_website": getattr(settings, "COMPANY_WEBSITE", ""),
    }


def _send_email(*, message, client, subject, context, notification_type, user) -> int:
    if not (client.has_notification_emails() and client.notification_enabled):
        logger.info(
            "[transport_request_notify] клиент %s без email/выключен — пропуск %s",
            client.name,
            notification_type,
        )
        return 0

    html_content = render_to_string("email/transport_request_message.html", context)
    text_content = strip_tags(html_content)

    sent = 0
    for email_to in client.get_notification_emails():
        success = True
        error_message = ""
        try:
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[email_to],
            )
            email.attach_alternative(html_content, "text/html")
            email.send(fail_silently=False)
            sent += 1
        except Exception as exc:
            success = False
            error_message = str(exc)
            logger.error(
                "[transport_request_notify] email %s → %s не отправлен: %s",
                notification_type,
                email_to,
                exc,
            )
        _log(
            message=message,
            client=client,
            notification_type=notification_type,
            channel="EMAIL",
            recipient=email_to,
            subject=subject,
            success=success,
            error_message=error_message,
            user=user,
        )
    return sent


def _send_telegram(*, message, client, subject, context, notification_type, user) -> int:
    if not _telegram_enabled() or not client.has_telegram():
        return 0

    text = _build_telegram_text(context)
    sent = 0
    for chat_id in client.get_telegram_chat_ids():
        success, error_message = send_telegram_message(chat_id, text)
        if success:
            sent += 1
        else:
            logger.error(
                "[transport_request_notify] telegram %s → %s: %s",
                notification_type,
                chat_id,
                error_message,
            )
        _log(
            message=message,
            client=client,
            notification_type=notification_type,
            channel="TELEGRAM",
            recipient=str(chat_id or ""),
            subject=subject,
            success=success,
            error_message=error_message,
            user=user,
        )
    return sent


def _build_telegram_text(context) -> str:
    icon = "📄" if context["is_doc_request"] else "💬"
    lines = [
        f"{icon} <b>{html.escape(context['subject'])}</b>",
        "",
        f"Здравствуйте, {html.escape(context['client_name'])}!",
    ]
    if context["body"]:
        lines += ["", html.escape(context["body"])]
    if context["requested_doc_labels"]:
        lines += ["", "<b>Просим загрузить:</b>"]
        lines += [f"• {html.escape(label)}" for label in context["requested_doc_labels"]]
    car = context.get("car")
    if car is not None:
        lines += ["", f"По автомобилю: {html.escape(str(car.brand or ''))} (VIN: {html.escape(str(car.vin or ''))})"]
    if context["portal_url"]:
        lines += ["", f'<a href="{context["portal_url"]}">Открыть заявку в кабинете</a>']
    lines += ["", f"<b>{html.escape(context['company_name'])}</b>"]
    if context["company_phone"]:
        lines.append(html.escape(context["company_phone"]))
    return "\n".join(lines)


def _log(*, message, client, notification_type, channel, recipient, subject, success, error_message, user) -> None:
    from core.models.website import NotificationLog

    cars_info = []
    if message.car_id:
        cars_info.append({"vin": message.car.vin, "brand": message.car.brand, "year": message.car.year})
    try:
        NotificationLog.objects.create(
            transport_request=message.request,
            client=client,
            notification_type=notification_type,
            channel=channel,
            email_to=recipient,
            subject=subject,
            cars_info=json.dumps(cars_info, ensure_ascii=False),
            success=success,
            error_message=error_message,
            created_by=user if (user and getattr(user, "is_authenticated", False)) else None,
        )
    except Exception as exc:
        logger.error("[transport_request_notify] не удалось записать NotificationLog: %s", exc)
