"""Наполнение матрицы «страна + процедура → обязательные документы».

Значения повторяют набор, который до этого был зашит в коде (транзит —
полный пакет, остальные процедуры — паспорт и инвойс), поэтому поведение
не меняется: дальше сотрудник правит строки в админке под требования
таможни каждой страны.

Заодно проставляем страну назначения существующим заявкам: до сих пор пакет
документов собирался под оформление на Беларусь.
"""

from django.db import migrations

COUNTRIES = ["BY", "MD", "UA"]

TRANSIT = ["PASSPORT", "INVOICE", "PAYMENT_ORDER", "LETTER_USA", "OBLIGATION", "CONTRACT"]
BASE = ["PASSPORT", "INVOICE"]

REQUIRED_BY_PROCEDURE = {
    "TRANSIT": TRANSIT,
    "EXPORT": BASE,
    "IMPORT": BASE,
    "REEXPORT": BASE,
}


def seed(apps, schema_editor):
    TransportDocumentRule = apps.get_model("core", "TransportDocumentRule")
    TransportRequest = apps.get_model("core", "TransportRequest")

    for country in COUNTRIES:
        for procedure, doc_types in REQUIRED_BY_PROCEDURE.items():
            TransportDocumentRule.objects.get_or_create(
                country=country,
                procedure=procedure,
                defaults={"required_doc_types": list(doc_types)},
            )

    TransportRequest.objects.filter(destination_country="").update(destination_country="BY")


def unseed(apps, schema_editor):
    TransportDocumentRule = apps.get_model("core", "TransportDocumentRule")
    TransportDocumentRule.objects.filter(country__in=COUNTRIES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0019_transportdocumentrule"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
