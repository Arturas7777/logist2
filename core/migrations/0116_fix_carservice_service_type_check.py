"""
Fix outdated CHECK constraint on core_carservice.service_type.

The original migration (0062) was created under an older Django version that
auto-generated a DB-level CHECK constraint for CharField choices.
That constraint only allows ('LINE', 'CARRIER', 'WAREHOUSE').

Migration 0101 added 'COMPANY' to the Python choices but Django 5.x no longer
manages these constraints automatically, so the old constraint was never updated.

This migration drops the outdated constraint and replaces it with the correct one.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0115_container_container_unloaded_requires_warehouse_and_date'),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE core_carservice DROP CONSTRAINT IF EXISTS core_carservice_service_type_check;",
            reverse_sql="ALTER TABLE core_carservice ADD CONSTRAINT core_carservice_service_type_check CHECK (service_type IN ('LINE', 'CARRIER', 'WAREHOUSE'));",
        ),
        migrations.RunSQL(
            sql="""
                ALTER TABLE core_carservice ADD CONSTRAINT core_carservice_service_type_check
                CHECK (service_type IN ('LINE', 'CARRIER', 'WAREHOUSE', 'COMPANY'));
            """,
            reverse_sql="ALTER TABLE core_carservice DROP CONSTRAINT IF EXISTS core_carservice_service_type_check;",
        ),
    ]
