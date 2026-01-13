# Generated manually on 2025-09-16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0055_add_service_type_to_invoice'),
    ]

    operations = [
        # Добавляем поля услуг в модель Line
        migrations.AddField(
            model_name='line',
            name='ocean_freight_rate',
            field=models.DecimalField(decimal_places=2, default=0.00, max_digits=10, verbose_name='Стоимость перевозки (за авто)'),
        ),
        migrations.AddField(
            model_name='line',
            name='documentation_fee',
            field=models.DecimalField(decimal_places=2, default=0.00, max_digits=10, verbose_name='Стоимость документов'),
        ),
        migrations.AddField(
            model_name='line',
            name='handling_fee',
            field=models.DecimalField(decimal_places=2, default=0.00, max_digits=10, verbose_name='Стоимость обработки'),
        ),
        migrations.AddField(
            model_name='line',
            name='additional_fees',
            field=models.DecimalField(decimal_places=2, default=0.00, max_digits=10, verbose_name='Дополнительные сборы'),
        ),
        
        # Создаем модель Carrier
        migrations.CreateModel(
            name='Carrier',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, verbose_name='Название перевозчика')),
                ('short_name', models.CharField(blank=True, max_length=20, null=True, verbose_name='Короткое название')),
                ('contact_person', models.CharField(blank=True, max_length=100, null=True, verbose_name='Контактное лицо')),
                ('phone', models.CharField(blank=True, max_length=20, null=True, verbose_name='Телефон')),
                ('email', models.EmailField(blank=True, null=True, verbose_name='Email')),
                ('invoice_balance', models.DecimalField(decimal_places=2, default=0.00, max_digits=15, verbose_name='Инвойс-баланс')),
                ('cash_balance', models.DecimalField(decimal_places=2, default=0.00, max_digits=15, verbose_name='Наличные')),
                ('card_balance', models.DecimalField(decimal_places=2, default=0.00, max_digits=15, verbose_name='Безнал')),
                ('transport_rate', models.DecimalField(decimal_places=2, default=0.00, max_digits=10, verbose_name='Стоимость перевозки (за км)')),
                ('loading_fee', models.DecimalField(decimal_places=2, default=0.00, max_digits=10, verbose_name='Стоимость погрузки')),
                ('unloading_fee', models.DecimalField(decimal_places=2, default=0.00, max_digits=10, verbose_name='Стоимость разгрузки')),
                ('fuel_surcharge', models.DecimalField(decimal_places=2, default=0.00, max_digits=10, verbose_name='Топливная надбавка')),
                ('additional_fees', models.DecimalField(decimal_places=2, default=0.00, max_digits=10, verbose_name='Дополнительные сборы')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Дата обновления')),
            ],
            options={
                'verbose_name': 'Перевозчик',
                'verbose_name_plural': 'Перевозчики',
            },
        ),
    ]

