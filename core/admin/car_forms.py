"""Форма машины с проверкой VIN на входе.

Раньше единственной защитой при ручном вводе была проверка длины в
``Car.clean()``: опечатка в одном символе спокойно попадала в базу, а
всплывала позже — когда AI читал Dock Receipt и «не находил» машину.

Здесь тот же набор проверок, что применяется к сканам (контрольная цифра,
расшифровка NHTSA, поиск похожих VIN), подключён к форме оператора.
Сохранение не запрещается: система не всегда права (бывают VIN без
контрольной цифры и действительно похожие номера соседних машин), поэтому
при находках форма просит подтвердить ввод.

Поле подтверждения скрытое, а видимую галочку рисует ``vin_guard.js``
рядом с проблемным VIN — иначе в табличном инлайне контейнера появилась бы
лишняя колонка, пустая в девяноста девяти случаях из ста.
"""

from __future__ import annotations

import logging

from django import forms

from core.models import Car
from core.services.vin_gate import check_vin, normalize_vin_input, schedule_vin_check

logger = logging.getLogger(__name__)

CONFIRM_FIELD = "vin_confirmed"
CONFIRM_LABEL = "Я сверил VIN с документом, он верный"


class VinGuardForm(forms.ModelForm):
    """Проверка VIN с подтверждением — общая для карточки авто и инлайна."""

    vin_confirmed = forms.BooleanField(
        required=False,
        label=CONFIRM_LABEL,
        widget=forms.HiddenInput(attrs={"data-vin-confirm": "1"}),
    )

    class Meta:
        model = Car
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "vin" in self.fields:
            self.fields["vin"].widget.attrs["data-vin-guard"] = "1"
            self.fields["vin"].widget.attrs["autocapitalize"] = "characters"

    def clean_vin(self):
        """Приводит VIN к каноническому виду — это делается всегда и молча."""
        return normalize_vin_input(self.cleaned_data.get("vin"))

    def clean(self):
        cleaned = super().clean()
        vin = cleaned.get("vin") or ""
        if not vin or "vin" in self.errors:
            return cleaned

        verdict = check_vin(
            vin,
            exclude_car_id=self.instance.pk,
            brand=cleaned.get("brand"),
            year=cleaned.get("year"),
        )
        self.vin_verdict = verdict
        if verdict.ok:
            return cleaned

        if cleaned.get(CONFIRM_FIELD):
            logger.info("VIN %s сохранён с подтверждением оператора: %s", vin, verdict.summary)
            return cleaned

        for issue in verdict.issues:
            message = issue.message
            if issue.suggestion:
                message += f" Вероятно, верный вариант: {issue.suggestion}."
            self.add_error("vin", message)
        return cleaned

    def save(self, commit=True):
        car = super().save(commit=commit)
        if car.vin:
            # Кэш проверки нужен аудиту контейнера и панели сверки, а сеть
            # в момент сохранения ждать незачем — обновляем в фоне.
            schedule_vin_check(car.vin)
        return car
