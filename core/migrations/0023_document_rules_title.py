"""Тайтл — обязательный документ в любом пакете.

Дописываем «TITLE» в уже заведённые правила «страна + процедура», чтобы
чекбоксы в админке совпадали с фактическим требованием: сам код требует
тайтл всегда (см. ``core.services.transport_request_check``), независимо
от содержимого правила.
"""

from django.db import migrations


def add_title(apps, schema_editor):
    TransportDocumentRule = apps.get_model("core", "TransportDocumentRule")
    for rule in TransportDocumentRule.objects.all():
        codes = list(rule.required_doc_types or [])
        if "TITLE" in codes:
            continue
        rule.required_doc_types = ["TITLE", *codes]
        rule.save(update_fields=["required_doc_types"])


def remove_title(apps, schema_editor):
    TransportDocumentRule = apps.get_model("core", "TransportDocumentRule")
    for rule in TransportDocumentRule.objects.all():
        codes = [code for code in (rule.required_doc_types or []) if code != "TITLE"]
        if codes != list(rule.required_doc_types or []):
            rule.required_doc_types = codes
            rule.save(update_fields=["required_doc_types"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0022_alter_transportrequestdocument_doc_type"),
    ]

    operations = [
        migrations.RunPython(add_title, remove_title),
    ]
