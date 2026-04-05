"""
Rename Python-level field names without changing database columns.

Fields renamed:
  Container.sklad  → Container.warehouse_fee  (db_column='sklad')
  Container.dekl   → Container.declaration_fee (db_column='dekl')
  Container.proft  → Container.markup          (db_column='proft')
  Car.dekl         → Car.declaration_fee       (db_column='dekl')
  Car.proft        → Car.markup                (db_column='proft')

Using db_column preserves the existing column names so this migration
is a no-op at the database level.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0130_alter_bankaccount_unique_together_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='container',
            old_name='sklad',
            new_name='warehouse_fee',
        ),
        migrations.RenameField(
            model_name='container',
            old_name='dekl',
            new_name='declaration_fee',
        ),
        migrations.RenameField(
            model_name='container',
            old_name='proft',
            new_name='markup',
        ),
        migrations.RenameField(
            model_name='car',
            old_name='dekl',
            new_name='declaration_fee',
        ),
        migrations.RenameField(
            model_name='car',
            old_name='proft',
            new_name='markup',
        ),
    ]
