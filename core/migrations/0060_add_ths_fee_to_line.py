# Generated manually on 2025-09-16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0059_restore_company_balance_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='line',
            name='ths_fee',
            field=models.DecimalField(decimal_places=2, default=0.00, max_digits=10, verbose_name='THS сбор (оплата линиям)'),
        ),
    ]

