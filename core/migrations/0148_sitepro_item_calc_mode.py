"""Add default_item_id and default_calculation_mode to SiteProConnection.

site.pro /warehouse/sale-items/create требует itemId (ссылка на reference-book
item), warehouseId и calculationMode. Добавляем эти настройки в connection.
"""
from django.db import migrations, models


def set_item_defaults(apps, schema_editor):
    """For logistic services default to 'Paslauga (vnt.)' (id=24) and mode=1 (без НДС)."""
    SiteProConnection = apps.get_model('core', 'SiteProConnection')
    SiteProConnection.objects.filter(default_item_id__isnull=True).update(
        default_item_id=24,
        default_calculation_mode=1,
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0147_sitepro_reference_ids'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteproconnection',
            name='default_item_id',
            field=models.IntegerField(
                blank=True,
                help_text=(
                    'ID справочного товара/услуги для позиций инвойса (обязательно для '
                    'sale-items/create). Для логистических услуг обычно 24 = Paslauga (vnt.).'
                ),
                null=True,
                verbose_name='Default Item ID',
            ),
        ),
        migrations.AddField(
            model_name='siteproconnection',
            name='default_calculation_mode',
            field=models.IntegerField(
                default=1,
                help_text=(
                    'Режим расчёта позиций инвойса. 1 = без НДС (стандарт для Caromoto).'
                ),
                verbose_name='Calculation Mode',
            ),
        ),
        migrations.RunPython(set_item_defaults, noop),
    ]
