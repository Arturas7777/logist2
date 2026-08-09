"""Вход/выход/регистрация клиента на сайте (вместо /admin/login/)."""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView, LogoutView
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from core.models import Client
from core.models.website import ClientUser

from .forms import ClientRegistrationForm


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


def client_register(request):
    """Регистрация клиента: User + новый Client + ClientUser (не верифицирован).

    Новый Client создаётся пустым — сотрудник в админке привязывает доступ
    к реальному клиенту CRM (меняет FK у ClientUser) и ставит is_verified.
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
            login(request, user)
            messages.success(
                request,
                "Регистрация завершена. Мы свяжем ваш аккаунт с вашими автомобилями "
                "в ближайшее время — после этого они появятся в кабинете.",
            )
            return redirect("website:dashboard")
    else:
        form = ClientRegistrationForm()

    return render(request, "website/register.html", {"form": form})
