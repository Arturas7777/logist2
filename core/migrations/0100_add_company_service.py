from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0099_add_default_flags_line_carrier_services'),
    ]

    operations = [
        migrations.CreateModel(
            name='CompanyService',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='Название услуги')),
                ('description', models.TextField(blank=True, verbose_name='Описание')),
                ('default_price', models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name='Цена по умолчанию')),
                ('default_markup', models.DecimalField(decimal_places=2, default=0, help_text='Скрытая наценка, которая будет автоматически добавлена при создании услуги для авто', max_digits=10, verbose_name='Наценка по умолчанию')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активна')),
                ('add_by_default', models.BooleanField(default=False, help_text='Автоматически добавлять эту услугу при создании автомобиля для этой компании', verbose_name='Добавлять по умолчанию')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='services', to='core.company', verbose_name='Компания')),
            ],
            options={
                'verbose_name': 'Услуга компании',
                'verbose_name_plural': 'Услуги компаний',
            },
        ),
    ]
