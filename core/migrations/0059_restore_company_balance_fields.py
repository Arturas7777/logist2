# Generated manually on 2025-09-16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0058_merge_20250916_1700'),
    ]

    operations = [
        # Восстанавливаем поля балансов в Company
        migrations.AddField(
            model_name='company',
            name='invoice_balance',
            field=models.DecimalField(decimal_places=2, default=0.00, max_digits=15, verbose_name='Инвойс-баланс'),
        ),
        migrations.AddField(
            model_name='company',
            name='cash_balance',
            field=models.DecimalField(decimal_places=2, default=0.00, max_digits=15, verbose_name='Наличные'),
        ),
        migrations.AddField(
            model_name='company',
            name='card_balance',
            field=models.DecimalField(decimal_places=2, default=0.00, max_digits=15, verbose_name='Безнал'),
        ),
    ]

