"""Вход/выход/регистрация клиента на сайте (вместо /admin/login/)
и восстановление пароля (C4)."""

import logging

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import Client, Task
from core.models.website import ClientUser

from .forms import ClientRegistrationForm

logger = logging.getLogger(__name__)


class ClientLoginView(LoginView):
    """Страница входа в кабинет клиента в стиле сайта."""

    template_name = "website/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        ClientUser.objects.filter(user=self.request.user).update(last_login=timezone.now())
        return response

    def get_success_url(self):
        redirect_to = self.get_redirect_url()
        if redirect_to:
            return redirect_to
        user = self.request.user
        if ClientUser.objects.filter(user=user).exists():
            return reverse("website:dashboard")
        if user.is_staff:
            return "/admin/"
        return reverse("website:home")


class ClientLogoutView(LogoutView):
    next_page = reverse_lazy("website:home")


# ── Восстановление пароля (C4) ─────────────────────────────────────────────
# Стандартные вьюхи Django с шаблонами сайта. Письмо — lt/en/ru через
# {% trans %} в templates/email/password_reset_*; язык берётся из запроса
# того, кто запрашивает сброс (cookie сайта).


class ClientPasswordResetView(PasswordResetView):
    template_name = "website/password_reset_form.html"
    email_template_name = "email/password_reset_email.txt"
    html_email_template_name = "email/password_reset_email.html"
    subject_template_name = "email/password_reset_subject.txt"
    success_url = reverse_lazy("website:password_reset_done")
    extra_email_context = {"company_name": "Caromoto Lithuania"}


class ClientPasswordResetDoneView(PasswordResetDoneView):
    template_name = "website/password_reset_done.html"


class ClientPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "website/password_reset_confirm.html"
    success_url = reverse_lazy("website:password_reset_complete")


class ClientPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "website/password_reset_complete.html"


def _notify_manager_about_registration(client, user, phone):
    """Дело менеджеру «Новый клиент зарегистрировался» (C4).

    Кабинет без привязки к реальному клиенту CRM бесполезен — кто-то должен
    это сделать руками; дело на доске напоминает об этом. Ошибка создания
    дела не должна ломать регистрацию.
    """
    lines = [f"Имя / компания: {client.name}", f"Email: {user.email}"]
    if phone:
        lines.append(f"Телефон: {phone}")
    lines += [
        f"Логин: {user.username}",
        "",
        "Нужно: привязать доступ к реальному клиенту CRM (ClientUser → client) "
        "и поставить галочку «Верифицирован».",
    ]
    description = "\n".join(lines)
    try:
        Task.objects.create(
            title=f"Новый клиент зарегистрировался: {client.name}"[:200],
            description=description,
            priority="MEDIUM",
            auto_created=True,
            origin=Task.ORIGIN_MANUAL,
            created_by="website",
        )
    except Exception as exc:  # дело — вспомогательное, регистрацию не ломаем
        logger.warning("Не удалось создать дело о регистрации клиента %s: %s", client.pk, exc)


def client_register(request):
    """Регистрация клиента: User + новый Client + ClientUser (не верифицирован).

    Новый Client создаётся пустым — сотрудник в админке привязывает доступ
    к реальному клиенту CRM (меняет FK у ClientUser) и ставит is_verified.
    До этого в кабинете показывается онбординг-баннер (см. client_dashboard).
    """
    if request.user.is_authenticated:
        return redirect("website:dashboard")

    if request.method == "POST":
        form = ClientRegistrationForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            with transaction.atomic():
                user = User.objects.create_user(
                    username=data["username"],
                    email=data["email"],
                    password=data["password1"],
                )
                client = Client.objects.create(name=data["name"], email=data["email"])
                ClientUser.objects.create(
                    user=user,
                    client=client,
                    phone=data.get("phone", ""),
                    is_verified=False,
                    last_login=timezone.now(),
                )
                _notify_manager_about_registration(client, user, data.get("phone", ""))
            login(request, user)
            messages.success(
                request,
                _(
                    "Регистрация завершена. Мы свяжем ваш аккаунт с вашими автомобилями "
                    "в ближайшее время — после этого они появятся в кабинете."
                ),
            )
            return redirect("website:dashboard")
    else:
        form = ClientRegistrationForm()

    return render(request, "website/register.html", {"form": form})
