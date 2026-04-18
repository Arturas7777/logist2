"""Модель ContainerEmail — письма Gmail, привязанные к контейнерам.

Вынесено в отдельный модуль, чтобы не раздувать core/models.py. Импортируется
в core/models.py для автодетекта Django (через AppConfig.ready() / миграции).
"""

from __future__ import annotations

from django.db import models


class ContainerEmail(models.Model):
    """Одно письмо Gmail, подтянутое через OAuth API и сопоставленное с контейнером.

    Идемпотентность поддерживается двумя уникальными ключами:
      * ``message_id`` — RFC 5322 Message-ID из заголовков (стабилен между серверами)
      * ``gmail_id``   — внутренний id Gmail API (нужен для history API и аттачей)

    Один из них может быть пустым в пограничных кейсах (письма без Message-ID),
    поэтому оба покрыты unique-constraint по-отдельности.
    """

    DIRECTION_INCOMING = 'INCOMING'
    DIRECTION_OUTGOING = 'OUTGOING'
    DIRECTION_CHOICES = [
        (DIRECTION_INCOMING, 'Входящее'),
        (DIRECTION_OUTGOING, 'Исходящее'),
    ]

    MATCHED_BY_CONTAINER_NUMBER = 'CONTAINER_NUMBER'
    MATCHED_BY_BOOKING_NUMBER = 'BOOKING_NUMBER'
    MATCHED_BY_THREAD = 'THREAD'
    MATCHED_BY_MANUAL = 'MANUAL'
    MATCHED_BY_UNMATCHED = 'UNMATCHED'
    MATCHED_BY_CHOICES = [
        (MATCHED_BY_CONTAINER_NUMBER, 'По номеру контейнера'),
        (MATCHED_BY_BOOKING_NUMBER, 'По номеру букинга'),
        (MATCHED_BY_THREAD, 'По треду'),
        (MATCHED_BY_MANUAL, 'Привязано вручную'),
        (MATCHED_BY_UNMATCHED, 'Не привязано'),
    ]

    container = models.ForeignKey(
        'Container',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='emails',
        verbose_name='Контейнер',
    )

    message_id = models.CharField(
        max_length=500, unique=True, db_index=True,
        verbose_name='Message-ID',
        help_text='RFC 5322 Message-ID из заголовков письма.',
    )
    thread_id = models.CharField(
        max_length=500, db_index=True,
        verbose_name='Thread ID',
        help_text='Gmail threadId (или первый Message-ID в треде, если threadId нет).',
    )
    in_reply_to = models.CharField(max_length=500, blank=True, default='')
    references = models.TextField(
        blank=True, default='',
        help_text='Пробел-разделённый список Message-ID из заголовка References.',
    )

    direction = models.CharField(
        max_length=10, choices=DIRECTION_CHOICES, default=DIRECTION_INCOMING,
        verbose_name='Направление',
    )
    from_addr = models.CharField(max_length=500, verbose_name='От')
    to_addrs = models.TextField(blank=True, default='', verbose_name='Кому')
    cc_addrs = models.TextField(blank=True, default='', verbose_name='Копия')
    subject = models.CharField(max_length=1000, blank=True, default='', verbose_name='Тема')
    body_text = models.TextField(blank=True, default='', verbose_name='Тело (text)')
    body_html = models.TextField(blank=True, default='', verbose_name='Тело (HTML)')
    snippet = models.CharField(
        max_length=500, blank=True, default='',
        help_text='Короткое превью от Gmail API.',
    )

    received_at = models.DateTimeField(db_index=True, verbose_name='Получено')

    gmail_id = models.CharField(
        max_length=64, blank=True, default='', db_index=True,
        help_text='Внутренний id Gmail API (messages.get → id).',
    )
    gmail_history_id = models.BigIntegerField(
        null=True, blank=True,
        help_text='historyId на момент получения письма — нужен для incremental sync.',
    )
    labels_json = models.JSONField(
        default=list, blank=True,
        help_text='Массив Gmail labels (INBOX, SENT, STARRED и т.д.).',
    )
    attachments_json = models.JSONField(
        default=list, blank=True,
        help_text='[{filename, size, content_type, storage_path, attachment_id}]. '
                  'storage_path пуст, если вложение пропущено из-за размера.',
    )

    matched_by = models.CharField(
        max_length=20, choices=MATCHED_BY_CHOICES, default=MATCHED_BY_UNMATCHED,
        verbose_name='Как сопоставлено',
    )
    is_read = models.BooleanField(default=False, verbose_name='Прочитано в UI')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Письмо контейнера'
        verbose_name_plural = 'Письма контейнеров'
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['container', '-received_at']),
            models.Index(fields=['thread_id', 'received_at']),
            models.Index(fields=['matched_by', '-received_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['gmail_id'],
                condition=~models.Q(gmail_id=''),
                name='containeremail_unique_gmail_id_nonempty',
            ),
        ]

    def __str__(self) -> str:
        who = self.from_addr[:50]
        subj = (self.subject or '(без темы)')[:60]
        return f'{who} — {subj}'

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @property
    def is_incoming(self) -> bool:
        return self.direction == self.DIRECTION_INCOMING

    @property
    def has_attachments(self) -> bool:
        return bool(self.attachments_json)


class GmailSyncState(models.Model):
    """Храним последний обработанный historyId, чтобы тянуть инкрементально.

    Одна строка на аккаунт (обычно у нас один корпоративный ящик). Если в
    будущем появится мульти-ящик режим — добавим поле user_email в PK.
    """

    user_email = models.CharField(
        max_length=254, unique=True,
        help_text='Gmail-адрес ящика, к которому выдан refresh_token.',
    )
    last_history_id = models.BigIntegerField(
        null=True, blank=True,
        help_text='Максимальный обработанный historyId. NULL → первый прогон.',
    )
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Состояние Gmail-синка'
        verbose_name_plural = 'Состояние Gmail-синка'

    def __str__(self) -> str:
        return f'{self.user_email} (hid={self.last_history_id})'
