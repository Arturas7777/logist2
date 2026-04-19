# Generated manually for Phase 2 (email sending from container card).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0152_add_container_email'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='containeremail',
            name='sent_by_user',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='sent_container_emails',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Отправил',
                help_text='Пользователь, отправивший письмо из админки (только для OUTGOING).',
            ),
        ),
        migrations.AddField(
            model_name='containeremail',
            name='send_status',
            field=models.CharField(
                blank=True, default='', max_length=10,
                choices=[
                    ('SENT', 'Отправлено'),
                    ('FAILED', 'Ошибка'),
                    ('PENDING', 'В очереди'),
                ],
                help_text='Статус отправки для исходящих писем из админки.',
            ),
        ),
        migrations.AddField(
            model_name='containeremail',
            name='send_error',
            field=models.TextField(
                blank=True, default='',
                help_text='Текст последней ошибки Gmail API при попытке отправить.',
            ),
        ),
    ]
