from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0098_alter_container_unloaded_status_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='lineservice',
            name='add_by_default',
            field=models.BooleanField(
                default=False,
                help_text='Автоматически добавлять эту услугу при создании автомобиля для этой линии',
                verbose_name='Добавлять по умолчанию',
            ),
        ),
        migrations.AddField(
            model_name='carrierservice',
            name='add_by_default',
            field=models.BooleanField(
                default=False,
                help_text='Автоматически добавлять эту услугу при создании автомобиля для этого перевозчика',
                verbose_name='Добавлять по умолчанию',
            ),
        ),
    ]
