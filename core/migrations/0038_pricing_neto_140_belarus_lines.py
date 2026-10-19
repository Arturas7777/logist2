"""Цены с 2026-10-19.

* NETO: «Разгрузка/ Погрузка / Декларация» 160 → 140 € — в каталоге и у ещё
  не переданных авто. Переданные авто и выставленные счета не трогаем.
* CAROMOTO BELARUS: легковые по линии — MAERSK 290 €, остальные линии 260 €
  при любом числе авто; ставка мотоциклов не меняется.

Наценки тарифа и total_price после миграции пересчитывает
``manage.py recalculate_client_tariffs --with-totals``.
На пустой базе (тесты) миграция ничего не делает.
"""

from decimal import Decimal

from django.db import migrations

NETO_SERVICE_NAME = "Разгрузка/ Погрузка / Декларация"
OLD_PRICE = Decimal("160")
NEW_PRICE = Decimal("140")


def forwards(apps, schema_editor):
    WarehouseService = apps.get_model("core", "WarehouseService")
    CarService = apps.get_model("core", "CarService")
    Client = apps.get_model("core", "Client")
    ClientTariffRate = apps.get_model("core", "ClientTariffRate")
    Line = apps.get_model("core", "Line")

    for service in WarehouseService.objects.filter(warehouse__name__iexact="NETO", name=NETO_SERVICE_NAME):
        if service.default_price == OLD_PRICE:
            service.default_price = NEW_PRICE
            service.save(update_fields=["default_price"])
        CarService.objects.filter(
            service_type="WAREHOUSE",
            service_id=service.pk,
            custom_price=OLD_PRICE,
            car__warehouse_id=service.warehouse_id,
        ).exclude(car__status="TRANSFERRED").update(custom_price=NEW_PRICE)

    client = Client.objects.filter(name__iexact="CAROMOTO BELARUS").first()
    maersk = Line.objects.filter(name__iexact="MAERSK").first()
    if client and maersk:
        ClientTariffRate.objects.filter(client=client, vehicle_type="SEDAN").delete()
        ClientTariffRate.objects.create(
            client=client, vehicle_type="SEDAN", line=maersk, min_cars=1, max_cars=None, agreed_total_price=290
        )
        ClientTariffRate.objects.create(
            client=client, vehicle_type="SEDAN", line=None, min_cars=1, max_cars=None, agreed_total_price=260
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0037_clienttariffrate_line"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
