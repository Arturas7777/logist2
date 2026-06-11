# P3 (AUDIT_ROUND3): pg_trgm + GIN-индексы для icontains-поиска.
#
# search_invoices / search_counterparties / поиск VIN делают OR по многим
# `icontains` — на PostgreSQL это seq scan по LIKE '%...%'. Триграммные
# GIN-индексы позволяют планировщику использовать индекс для таких LIKE.
#
# Индексы создаются только на PostgreSQL (RunPython с проверкой vendor):
# тестовый профиль (SQLite) миграции не запускает, но защита нужна для
# любых нестандартных сценариев. В состояние моделей индексы не вносятся
# (makemigrations их не отслеживает — дрифта нет).

from django.db import migrations

# (table, column, index_name)
TRGM_INDEXES = [
    ("core_newinvoice", "number", "inv_number_trgm_idx"),
    ("core_newinvoice", "external_number", "inv_extnumber_trgm_idx"),
    ("core_client", "name", "client_name_trgm_idx"),
    ("core_car", "vin", "car_vin_trgm_idx"),
    ("core_car", "brand", "car_brand_trgm_idx"),
]


def create_trgm_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        for table, column, name in TRGM_INDEXES:
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {name} "
                f"ON {table} USING gin ({column} gin_trgm_ops)"
            )


def drop_trgm_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for _table, _column, name in TRGM_INDEXES:
            cursor.execute(f"DROP INDEX IF EXISTS {name}")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0185_currency_eur_constraint"),
    ]

    operations = [
        migrations.RunPython(create_trgm_indexes, drop_trgm_indexes),
    ]
