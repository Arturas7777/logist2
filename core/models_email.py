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

    # Phase 2: одно письмо может быть привязано к нескольким контейнерам
    # (например, в теме упомянуты MSKU9754460 и MRSU7341005 — оба контейнера
    # должны видеть этот тред в карточке «Переписка»). Связь идёт через
    # through-модель ``ContainerEmailLink``, чтобы хранить ``matched_by``
    # отдельно для каждой пары (email, container).
    containers = models.ManyToManyField(
        'Container',
        through='ContainerEmailLink',
        related_name='emails',
        blank=True,
        verbose_name='Контейнеры',
    )

    # Для исходящих писем — из какой карточки контейнера их отправили.
    # Помогает UI подсветить «исходящее из этой карточки» vs «просто связано»
    # и позволяет сохранять источник даже если позже пользователь вручную
    # отвяжет контейнер от M2M.
    sent_from_container = models.ForeignKey(
        'Container',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='sent_emails_origin',
        verbose_name='Отправлено из карточки',
        help_text='Контейнер, из карточки которого было отправлено письмо '
                  '(только для OUTGOING).',
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

    # ── Phase 2: отслеживаем отправку исходящих писем из админки ─────────
    SEND_STATUS_SENT = 'SENT'
    SEND_STATUS_FAILED = 'FAILED'
    SEND_STATUS_PENDING = 'PENDING'
    SEND_STATUS_CHOICES = [
        (SEND_STATUS_SENT, 'Отправлено'),
        (SEND_STATUS_FAILED, 'Ошибка'),
        (SEND_STATUS_PENDING, 'В очереди'),
    ]

    sent_by_user = models.ForeignKey(
        'auth.User',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='sent_container_emails',
        verbose_name='Отправил',
        help_text='Пользователь, отправивший письмо из админки (только для OUTGOING).',
    )
    send_status = models.CharField(
        max_length=10, choices=SEND_STATUS_CHOICES,
        blank=True, default='',
        help_text='Статус отправки для исходящих писем из админки.',
    )
    send_error = models.TextField(
        blank=True, default='',
        help_text='Текст последней ошибки Gmail API при попытке отправить.',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Письмо контейнера'
        verbose_name_plural = 'Письма контейнеров'
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['-received_at']),
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


class ContainerEmailLink(models.Model):
    """Связь «письмо ↔ контейнер» (through для ``ContainerEmail.containers``).

    В отдельной модели, чтобы хранить ``matched_by`` per-ссылка: одно и то же
    письмо может быть привязано к контейнеру A «по номеру», а к контейнеру B
    «по букингу» или «вручную».

    Удаляется каскадом вместе с любым из концов (email или container) —
    это не данные письма, это просто граф связей.
    """

    email = models.ForeignKey(
        ContainerEmail,
        on_delete=models.CASCADE,
        related_name='container_links',
    )
    container = models.ForeignKey(
        'Container',
        on_delete=models.CASCADE,
        related_name='email_links',
    )
    matched_by = models.CharField(
        max_length=20,
        choices=ContainerEmail.MATCHED_BY_CHOICES,
        default=ContainerEmail.MATCHED_BY_UNMATCHED,
        verbose_name='Как сопоставлено',
        help_text='Причина связи именно с этим контейнером. Может отличаться '
                  'от ContainerEmail.matched_by (первичная причина).',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Связь письма с контейнером'
        verbose_name_plural = 'Связи писем с контейнерами'
        constraints = [
            models.UniqueConstraint(
                fields=['email', 'container'],
                name='containeremaillink_unique_email_container',
            ),
        ]
        indexes = [
            models.Index(fields=['container', 'email']),
        ]

    def __str__(self) -> str:
        return f'link<email={self.email_id}, container={self.container_id}, {self.matched_by}>'


class EmailGroup(models.Model):
    """Общая группа контактов для быстрой вставки в поле получателей.

    Общая для всех админов (scope нет). Администрируется через Django admin
    (``/admin/core/emailgroup/``). В composer карточки контейнера доступна
    через кнопку «📇 Группы» — клик по группе разворачивает участников в
    активное поле (``To`` / ``Cc`` / ``Bcc``).

    Если у участника заполнено ``display_name``, в заголовок подставится
    в формате RFC 5322 ``Имя <email@host>``; иначе просто ``email@host``.
    """

    name = models.CharField(
        max_length=120, unique=True,
        verbose_name='Название',
        help_text='Например "Таможня Klaipeda" или "MSC Agents".',
    )
    description = models.CharField(
        max_length=255, blank=True, default='',
        verbose_name='Описание',
        help_text='Опционально — контекст, когда использовать группу.',
    )
    created_by = models.ForeignKey(
        'auth.User', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
        verbose_name='Создал',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Email-группа'
        verbose_name_plural = 'Email-группы'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name

    @property
    def members_count(self) -> int:
        # Используется в API/admin. Инкапсулирует prefetch-agnostic вариант.
        return self.members.count()


class EmailGroupMember(models.Model):
    """Один участник email-группы.

    ``display_name`` опционален: если задан — используется формат
    ``Имя <email>`` при подстановке, иначе только ``email``.
    """

    group = models.ForeignKey(
        EmailGroup,
        on_delete=models.CASCADE,
        related_name='members',
        verbose_name='Группа',
    )
    email = models.EmailField(
        max_length=254,
        verbose_name='Email',
    )
    display_name = models.CharField(
        max_length=120, blank=True, default='',
        verbose_name='Имя (опционально)',
        help_text='Если заполнено, в поле «Кому» подставится как '
                  '"Имя <email@...>". Иначе — только email.',
    )
    position = models.PositiveIntegerField(
        default=0,
        verbose_name='Порядок',
        help_text='Меньше → выше в списке. При равных — по email.',
    )

    class Meta:
        verbose_name = 'Участник email-группы'
        verbose_name_plural = 'Участники email-группы'
        ordering = ['group', 'position', 'email']
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'email'],
                name='emailgroupmember_unique_email_per_group',
            ),
        ]

    def __str__(self) -> str:
        if self.display_name:
            return f'{self.display_name} <{self.email}>'
        return self.email

    @property
    def as_header_format(self) -> str:
        """Возвращает RFC 5322-формат для вставки в поле получателей."""
        name = (self.display_name or '').strip()
        if not name:
            return self.email
        # Кавычим имя, если содержит символы, требующие экранирования
        # (запятая, точка с запятой, угловые скобки, кавычки).
        if any(ch in name for ch in ',;<>"'):
            name = '"' + name.replace('"', '\\"') + '"'
        return f'{name} <{self.email}>'


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
