# Generated manually on 2025-09-16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0054_add_invoice_entity_fields_simple'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='service_type',
            field=models.CharField(
                choices=[
                    ('WAREHOUSE_SERVICES', 'Услуги склада'),
                    ('LINE_SERVICES', 'Услуги линий'),
                    ('TRANSPORT_SERVICES', 'Транспортные услуги'),
                    ('OTHER_SERVICES', 'Прочие услуги'),
                ],
                default='WAREHOUSE_SERVICES',
                max_length=20,
                verbose_name='Тип услуг'
            ),
        ),
    ]

