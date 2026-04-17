"""Add warehouse/operation-type/series/location ID fields to SiteProConnection.

site.pro API теперь требует warehouseId, operationTypeId и clientId при создании
sale, а также locationId при создании клиента. Эти поля добавляются в настройки
подключения, плюс data-migration заполняет разумные дефолты для уже существующих
подключений Caromoto (warehouse=1, operationType=23/PPP, series=12/PARDP, location=1/LT).
"""
from django.db import migrations, models


def set_default_reference_ids(apps, schema_editor):
    """Apply sensible defaults for Caromoto's existing SiteProConnection(s).

    Defaults correspond to what was discovered on 2026-04-17 via reference-book probes:
    - warehouse_id=1  (Pagrindinis, primary warehouse)
    - operation_type_id=23  (PPP - Pardavimas paslaugos, aligns with PARDP series)
    - series_id=12  (PARDP - matches the textual invoice_series='PARDP')
    - location_id=1  (Lietuva)
    """
    SiteProConnection = apps.get_model('core', 'SiteProConnection')
    SiteProConnection.objects.filter(
        default_warehouse_id__isnull=True,
    ).update(
        default_warehouse_id=1,
        default_operation_type_id=23,
        default_series_id=12,
        default_location_id=1,
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0146_performance_indexes_linked_paid'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteproconnection',
            name='default_warehouse_id',
            field=models.IntegerField(
                blank=True,
                help_text=(
                    'ID склада в site.pro (обязательно для sales/create). '
                    'Узнать через action "Загрузить справочники". Обычно 1 = Pagrindinis.'
                ),
                null=True,
                verbose_name='Warehouse ID',
            ),
        ),
        migrations.AddField(
            model_name='siteproconnection',
            name='default_operation_type_id',
            field=models.IntegerField(
                blank=True,
                help_text=(
                    'ID типа операции (обязательно для sales/create). '
                    'Для продаж обычно 2 = Pardavimai (isSale=True).'
                ),
                null=True,
                verbose_name='Operation Type ID',
            ),
        ),
        migrations.AddField(
            model_name='siteproconnection',
            name='default_series_id',
            field=models.IntegerField(
                blank=True,
                help_text=(
                    'ID серии в site.pro (опционально). '
                    'Если не задан, серия шлётся только текстом через invoice_series.'
                ),
                null=True,
                verbose_name='Series ID (опц.)',
            ),
        ),
        migrations.AddField(
            model_name='siteproconnection',
            name='default_location_id',
            field=models.IntegerField(
                blank=True,
                help_text=(
                    'Тип налогового резидентства по умолчанию для новых клиентов. '
                    '1 = Lietuva, 2 = Europos Sąjunga, 3 = Trečiosios šalys. '
                    'Обязательно для clients/create.'
                ),
                null=True,
                verbose_name='Default Tax Residency',
            ),
        ),
        migrations.RunPython(set_default_reference_ids, noop),
    ]
