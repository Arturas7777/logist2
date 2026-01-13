# Generated manually on 2025-09-16

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0054_add_invoice_entity_fields_simple'),
    ]

    operations = [
        # Эта миграция ничего не делает, но помечает состояние как исправленное
        migrations.RunSQL(
            sql="SELECT 1;",
            reverse_sql="SELECT 1;"
        ),
    ]

