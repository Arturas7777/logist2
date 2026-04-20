"""Phase 2: заменяем FK ContainerEmail.container → M2M containers (через
ContainerEmailLink) и добавляем FK sent_from_container для OUTGOING.

Логика:
  1. Освобождаем related_name='emails' у старого FK container
     (ставим '+' — реверса не будет, это временно).
  2. Создаём сквозную модель ContainerEmailLink.
  3. Добавляем M2M ContainerEmail.containers through=ContainerEmailLink,
     related_name='emails' (получаем ту же reverse-ссылку).
  4. RunPython — копируем существующие container_id в ContainerEmailLink.
  5. Добавляем FK sent_from_container.
  6. RunPython — для OUTGOING копируем container_id → sent_from_container.
  7. Удаляем старый FK container.

Старые письма НЕ реanchматчим через тело/тему — остаётся ровно та же связь,
что была в старом FK. Новые письма матчер заведёт сам (см. email_matcher).
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


def _copy_container_to_links(apps, schema_editor):
    ContainerEmail = apps.get_model('core', 'ContainerEmail')
    ContainerEmailLink = apps.get_model('core', 'ContainerEmailLink')

    rows = (
        ContainerEmail.objects
        .exclude(container__isnull=True)
        .values_list('id', 'container_id', 'matched_by')
    )

    links = [
        ContainerEmailLink(
            email_id=email_id,
            container_id=container_id,
            matched_by=matched_by or 'UNMATCHED',
        )
        for (email_id, container_id, matched_by) in rows
    ]
    if links:
        ContainerEmailLink.objects.bulk_create(
            links, batch_size=500, ignore_conflicts=True,
        )


def _copy_outgoing_origin(apps, schema_editor):
    ContainerEmail = apps.get_model('core', 'ContainerEmail')
    (
        ContainerEmail.objects
        .filter(direction='OUTGOING', container__isnull=False)
        .update(sent_from_container_id=models.F('container_id'))
    )


def _noop_reverse(apps, schema_editor):
    # Обратный код не заполняем — старый FK удаляется на следующей
    # миграции, так что откатывать RunPython смысла нет.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0155_add_contacts'),
    ]

    operations = [
        # 1. Освобождаем related_name='emails' у старого FK
        migrations.AlterField(
            model_name='containeremail',
            name='container',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='core.container',
                verbose_name='Контейнер (устар.)',
            ),
        ),

        # Сбрасываем старый индекс, где упоминался container
        migrations.RemoveIndex(
            model_name='containeremail',
            name='core_contai_contain_6add97_idx',
        ),

        # 2. Создаём through-модель
        migrations.CreateModel(
            name='ContainerEmailLink',
                fields=[
                    ('id', models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False,
                        verbose_name='ID',
                    )),
                ('matched_by', models.CharField(
                    max_length=20,
                    choices=[
                        ('CONTAINER_NUMBER', 'По номеру контейнера'),
                        ('BOOKING_NUMBER', 'По номеру букинга'),
                        ('THREAD', 'По треду'),
                        ('MANUAL', 'Привязано вручную'),
                        ('UNMATCHED', 'Не привязано'),
                    ],
                    default='UNMATCHED',
                    help_text='Причина связи именно с этим контейнером. '
                              'Может отличаться от ContainerEmail.matched_by '
                              '(первичная причина).',
                    verbose_name='Как сопоставлено',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('container', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='email_links',
                    to='core.container',
                )),
                ('email', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='container_links',
                    to='core.containeremail',
                )),
            ],
            options={
                'verbose_name': 'Связь письма с контейнером',
                'verbose_name_plural': 'Связи писем с контейнерами',
            },
        ),
        migrations.AddConstraint(
            model_name='containeremaillink',
            constraint=models.UniqueConstraint(
                fields=('email', 'container'),
                name='containeremaillink_unique_email_container',
            ),
        ),
        migrations.AddIndex(
            model_name='containeremaillink',
            index=models.Index(
                fields=['container', 'email'],
                name='core_contai_contain_bf07cb_idx',
            ),
        ),

        # 3. Добавляем M2M
        migrations.AddField(
            model_name='containeremail',
            name='containers',
            field=models.ManyToManyField(
                blank=True,
                related_name='emails',
                through='core.ContainerEmailLink',
                to='core.container',
                verbose_name='Контейнеры',
            ),
        ),

        # 4. Копируем существующие связи
        migrations.RunPython(_copy_container_to_links, _noop_reverse),

        # 5. FK sent_from_container
        migrations.AddField(
            model_name='containeremail',
            name='sent_from_container',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='sent_emails_origin',
                to='core.container',
                verbose_name='Отправлено из карточки',
                help_text='Контейнер, из карточки которого было отправлено '
                          'письмо (только для OUTGOING).',
            ),
        ),

        # 6. Заполняем sent_from_container для OUTGOING
        migrations.RunPython(_copy_outgoing_origin, _noop_reverse),

        # 7. Удаляем старый FK
        migrations.RemoveField(
            model_name='containeremail',
            name='container',
        ),

        # Новый индекс по received_at (вместо старого container+received_at)
        migrations.AddIndex(
            model_name='containeremail',
            index=models.Index(
                fields=['-received_at'],
                name='core_contai_receive_a2a740_idx',
            ),
        ),
    ]
