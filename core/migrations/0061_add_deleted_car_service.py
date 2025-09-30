# Generated manually

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0060_add_ths_fee_to_line'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeletedCarService',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('service_type', models.CharField(choices=[('LINE', 'Линия'), ('CARRIER', 'Перевозчик'), ('WAREHOUSE', 'Склад')], max_length=20, verbose_name='Тип поставщика')),
                ('service_id', models.PositiveIntegerField(verbose_name='ID услуги')),
                ('deleted_at', models.DateTimeField(auto_now_add=True, verbose_name='Дата удаления')),
                ('car', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deleted_services', to='core.car', verbose_name='Автомобиль')),
            ],
            options={
                'verbose_name': 'Удаленная услуга автомобиля',
                'verbose_name_plural': 'Удаленные услуги автомобилей',
            },
        ),
        migrations.AlterUniqueTogether(
            name='deletedcarservice',
            unique_together={('car', 'service_type', 'service_id')},
        ),
    ]

