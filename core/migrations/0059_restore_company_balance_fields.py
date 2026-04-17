# Generated manually on 2025-09-16

from django.db import migrations, models


def add_field_if_not_exists(apps, schema_editor):
    """Add balance fields to Company only if they don't exist yet (idempotent).

    Используется только на Postgres в продакшене; на SQLite (тесты) — no-op,
    так как тестовая схема создаётся с нуля и колонок там и так нет в модели.
    """
    if schema_editor.connection.vendor != 'postgresql':
        return
    cursor = schema_editor.connection.cursor()
    cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'core_company'"
    )
    existing_columns = {row[0] for row in cursor.fetchall()}

    fields = {
        'invoice_balance': "numeric(15, 2) NOT NULL DEFAULT 0",
        'cash_balance': "numeric(15, 2) NOT NULL DEFAULT 0",
        'card_balance': "numeric(15, 2) NOT NULL DEFAULT 0",
    }
    for col, definition in fields.items():
        if col not in existing_columns:
            cursor.execute(
                f'ALTER TABLE "core_company" ADD COLUMN "{col}" {definition};'
            )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0058_merge_20250916_1700'),
    ]

    operations = [
        migrations.RunPython(add_field_if_not_exists, migrations.RunPython.noop),
    ]

