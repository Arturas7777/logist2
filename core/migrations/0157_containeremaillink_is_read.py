"""Phase 3: переносим ContainerEmail.is_read → ContainerEmailLink.is_read.

Мотивация: одно и то же письмо может быть привязано к нескольким карточкам
(M2M через ContainerEmailLink). Глобальный ``ContainerEmail.is_read`` не
отражает реальность: если мы отправили письмо из карточки A, оно сразу
помечается прочитанным; но если в теле упомянут контейнер B, карточка B
не должна показывать это письмо прочитанным — пользователь его ещё не видел.

Миграция данных:
  * INCOMING: все links наследуют email.is_read (как было).
  * OUTGOING: только origin-link (container == sent_from_container) наследует
    email.is_read=True; остальные links остаются is_read=False (чтобы
    бейдж «непрочитанное» показывался в тех карточках, где пользователь
    ещё не видел переписку).

Затем удаляем поле ``ContainerEmail.is_read``.
"""

from __future__ import annotations

from django.db import migrations, models


def _copy_is_read_to_links(apps, schema_editor):
    ContainerEmail = apps.get_model('core', 'ContainerEmail')
    ContainerEmailLink = apps.get_model('core', 'ContainerEmailLink')

    # 1) Incoming и всё не-исходящее: копируем is_read на ВСЕ links письма.
    incoming_read_ids = list(
        ContainerEmail.objects
        .exclude(direction='OUTGOING')
        .filter(is_read=True)
        .values_list('id', flat=True)
    )
    if incoming_read_ids:
        ContainerEmailLink.objects.filter(
            email_id__in=incoming_read_ids,
        ).update(is_read=True)

    # 2) Outgoing: только origin-link получает is_read=True.
    outgoing = list(
        ContainerEmail.objects
        .filter(
            direction='OUTGOING',
            is_read=True,
            sent_from_container__isnull=False,
        )
        .values_list('id', 'sent_from_container_id')
    )
    for email_id, origin_cid in outgoing:
        ContainerEmailLink.objects.filter(
            email_id=email_id, container_id=origin_cid,
        ).update(is_read=True)


def _restore_is_read_on_email(apps, schema_editor):
    """Обратная операция: email.is_read = OR(link.is_read for link in email.links)."""
    ContainerEmail = apps.get_model('core', 'ContainerEmail')
    ContainerEmailLink = apps.get_model('core', 'ContainerEmailLink')
    read_email_ids = set(
        ContainerEmailLink.objects
        .filter(is_read=True)
        .values_list('email_id', flat=True)
    )
    if read_email_ids:
        ContainerEmail.objects.filter(id__in=read_email_ids).update(is_read=True)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0156_containeremail_m2m_containers'),
    ]

    operations = [
        migrations.AddField(
            model_name='containeremaillink',
            name='is_read',
            field=models.BooleanField(
                default=False,
                verbose_name='Прочитано в этой карточке',
                help_text='Хранится per-ссылка: одно и то же письмо может '
                          'быть «прочитано» в карточке-источнике и '
                          '«непрочитано» в карточке, где оно появилось по '
                          'упоминанию в теме/теле.',
            ),
        ),
        migrations.RunPython(_copy_is_read_to_links, _restore_is_read_on_email),
        migrations.RemoveField(
            model_name='containeremail',
            name='is_read',
        ),
    ]
