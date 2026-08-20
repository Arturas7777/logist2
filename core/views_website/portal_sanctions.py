"""Кабинет клиента: «Проверка на санкции» (транзит через Литву).

Клиент вводит VIN, страница подтягивает характеристики авто из NHTSA, клиент
при необходимости правит их (NHTSA не знает ни клиренса, ни таможенной
категории) — и получает вердикт по памятке литовской таможни
(:mod:`core.services.sanctions_check`).

Раздел виден только клиентам, которым в админке проставлена страна
«Беларусь»: памятка описывает транзит через Литву именно туда.
"""

from __future__ import annotations

from django import forms
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.models.website import ClientUser
from core.services import sanctions_check
from core.services.vin_validator import decode_vin_details


def client_sees_sanctions_check(client) -> bool:
    """Показывать ли клиенту раздел «Проверка на санкции»."""
    return bool(client) and sanctions_check.is_available_for_country(client.country)


class SanctionsCheckForm(forms.Form):
    """Данные об авто для вердикта. Поля намеренно необязательные.

    Клиент часто знает только VIN, а чего не хватает — движок скажет сам
    («не хватает данных»), это понятнее, чем ошибка валидации.
    """

    vin = forms.CharField(label="VIN", max_length=17, required=False)
    category = forms.ChoiceField(
        label="Категория",
        choices=sanctions_check.CATEGORIES,
        initial="CAR",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    engine_type = forms.ChoiceField(
        label="Двигатель",
        choices=sanctions_check.ENGINE_TYPES,
        initial="PETROL",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    displacement_cc = forms.IntegerField(
        label="Объём двигателя, см³",
        required=False,
        min_value=0,
        max_value=20000,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "placeholder": "2000"}),
    )
    clearance = forms.ChoiceField(
        label="Просвет (клиренс)",
        choices=sanctions_check.CLEARANCE_CHOICES,
        initial="UNKNOWN",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    year = forms.IntegerField(
        label="Год выпуска",
        required=False,
        min_value=1900,
        max_value=2100,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "placeholder": "2019"}),
    )
    price_eur = forms.DecimalField(
        label="Стоимость, EUR",
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "placeholder": "25000"}),
    )

    def to_check_input(self) -> sanctions_check.CheckInput:
        data = self.cleaned_data
        return sanctions_check.CheckInput(
            category=data.get("category") or "CAR",
            engine_type=data.get("engine_type") or "PETROL",
            displacement_cc=data.get("displacement_cc"),
            clearance=data.get("clearance") or "UNKNOWN",
            year=data.get("year"),
            price_eur=data.get("price_eur"),
            vin=(data.get("vin") or "").strip().upper(),
        )


def _get_client(request):
    try:
        return request.user.clientuser.client
    except ClientUser.DoesNotExist:
        return None


def _forbidden(request):
    return render(request, "website/not_authorized.html", status=403)


@login_required
def sanctions_check_page(request):
    """Страница проверки: форма и вердикт по отправленным данным."""
    client = _get_client(request)
    if client is None:
        return _forbidden(request)
    if not client_sees_sanctions_check(client):
        return _forbidden(request)

    result = None
    if request.method == "POST":
        form = SanctionsCheckForm(request.POST)
        if form.is_valid():
            result = sanctions_check.check(form.to_check_input())
    else:
        form = SanctionsCheckForm()

    return render(
        request,
        "website/client_sanctions_check.html",
        {
            "client": client,
            "form": form,
            "result": result,
            "base_documents": sanctions_check.BASE_DOCUMENTS,
        },
    )


@login_required
@require_POST
def sanctions_vin_lookup(request):
    """AJAX: характеристики авто по VIN из NHTSA для подстановки в форму."""
    client = _get_client(request)
    if client is None or not client_sees_sanctions_check(client):
        return JsonResponse({"ok": False, "error": "Раздел недоступен."}, status=403)

    vin = (request.POST.get("vin") or "").strip().upper()
    if len(vin) != 17:
        return JsonResponse({"ok": False, "error": "VIN должен быть из 17 символов."}, status=400)

    details = decode_vin_details(vin)
    if details.get("raw_failed"):
        return JsonResponse(
            {"ok": False, "error": "Справочник NHTSA сейчас недоступен — заполните данные вручную."},
            status=502,
        )
    if not details.get("ok"):
        return JsonResponse(
            {"ok": False, "error": details.get("error_text") or "NHTSA не знает этот VIN — заполните данные вручную."},
            status=404,
        )

    guess = sanctions_check.guess_from_nhtsa(details)
    return JsonResponse(
        {
            "ok": True,
            "car": f"{details.get('make') or ''} {details.get('model') or ''}".strip(),
            "fields": guess,
            "source": {
                "body_class": details.get("body_class") or "",
                "fuel": details.get("fuel_primary") or "",
                "electrification": details.get("electrification") or "",
            },
        }
    )
