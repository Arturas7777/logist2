from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0150_add_container_labels_printed_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='container',
            name='booking_number',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                help_text='Booking number (букинг) — используется для сопоставления писем с контейнером, '
                          'когда номер контейнера ещё не известен.',
                max_length=50,
                verbose_name='Номер букинга',
            ),
        ),
    ]
