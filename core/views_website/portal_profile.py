"""Кабинет клиента: профиль — настройки уведомлений (C5)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from core.models.website import ClientUser
from core.services.client_notifications import CHANNEL_EMAIL, CHANNEL_TELEGRAM, EVENTS


def _get_client_user(request):
    try:
        return request.user.clientuser
    except ClientUser.DoesNotExist:
        return None


@login_required
@require_http_methods(["GET", "POST"])
def notification_settings(request):
    """Чекбоксы «событие × канал». Отсутствующий ключ в prefs = включено,
    поэтому сохраняем только явные значения по всем событиям."""
    client_user = _get_client_user(request)
    if client_user is None:
        return render(request, "website/not_authorized.html", status=403)
    client = client_user.client

    if request.method == "POST":
        prefs = {}
        for code, _label, _help in EVENTS:
            prefs[code] = {
                "email": f"{code}__email" in request.POST,
                "telegram": f"{code}__telegram" in request.POST,
            }
        client_user.notification_prefs = prefs
        client_user.save(update_fields=["notification_prefs"])
        messages.success(request, _("Настройки уведомлений сохранены."))
        return redirect("website:notification_settings")

    rows = [
        {
            "code": code,
            "label": label,
            "help": help_text,
            "email": client_user.wants_notification(code, "email"),
            "telegram": client_user.wants_notification(code, "telegram"),
        }
        for code, label, help_text in EVENTS
    ]
    context = {
        "client": client,
        "rows": rows,
        "email_available": client.has_notification_emails() and client.notification_enabled,
        "telegram_available": client.has_telegram(),
        "notification_emails": client.get_notification_emails(),
        "channel_email": CHANNEL_EMAIL,
        "channel_telegram": CHANNEL_TELEGRAM,
    }
    return render(request, "website/client_notification_settings.html", context)
