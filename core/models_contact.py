"""Модели Contact / ContactEmail / ContactPhone — универсальные контактные
лица, привязанные к любому контрагенту (Line / Carrier / Client / Warehouse /
Company) через GenericForeignKey.

Используются:
 * в карточках контрагентов (GenericTabularInline)
 * на странице «Контакты» (группировка по типу → наименованию → должности)
 * в composer'е писем для autocomplete (начинаешь вводить — подсказки)

Авто-создаваемые контакты при отправке письма на email, которого нет в базе,
помечаются ``is_orphan=True`` и показываются отдельной группой «Осиротевшие»,
чтобы потом их можно было вручную привязать к нужному контрагенту.
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Contact(models.Model):
    """Контактное лицо.

    Может быть привязано к любому «контрагенту» через GenericFK, либо быть
    «осиротевшим» (is_orphan=True) — такие создаются автоматически, когда мы
    пишем письмо на email, не привязанный к существующему контакту.
    """

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='Тип контрагента',
        help_text='Модель контрагента (Линия / Перевозчик / Клиент / Склад / Компания).',
    )
    object_id = models.PositiveIntegerField(
        null=True, blank=True,
        verbose_name='ID контрагента',
    )
    counterparty = GenericForeignKey('content_type', 'object_id')

    name = models.CharField(
        max_length=200,
        verbose_name='Имя',
        help_text='ФИО или имя контактного лица.',
    )
    position = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name='Должность',
        help_text='Например "Import Manager", "Таможенный брокер", "Водитель".',
    )
    comment = models.TextField(
        blank=True, default='',
        verbose_name='Комментарий',
    )

    is_primary = models.BooleanField(
        default=False,
        verbose_name='Основной',
        help_text='Если у контрагента несколько контактов — используйте для главного.',
    )
    is_orphan = models.BooleanField(
        default=False,
        verbose_name='Осиротевший',
        help_text='Создан автоматически при отправке email — не привязан к контрагенту. '
                  'Привяжите вручную через админку.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Контакт'
        verbose_name_plural = 'Контакты'
        ordering = ['name']
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
            models.Index(fields=['is_orphan']),
        ]

    def __str__(self) -> str:
        parts = [self.name]
        if self.position:
            parts.append(f'({self.position})')
        return ' '.join(parts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def primary_email(self) -> str:
        """Первый email (is_primary → position → id)."""
        em = self.emails.order_by('-is_primary', 'position', 'id').first()
        return em.email if em else ''

    @property
    def primary_phone(self) -> str:
        ph = self.phones.order_by('-is_primary', 'position', 'id').first()
        return ph.phone if ph else ''

    @property
    def emails_preview(self) -> str:
        return ', '.join(e.email for e in self.emails.all()[:5])

    @property
    def phones_preview(self) -> str:
        return ', '.join(p.phone for p in self.phones.all()[:5])

    @property
    def counterparty_name(self) -> str:
        """Человекочитаемое имя контрагента или '(Осиротевший)'."""
        cp = self.counterparty
        if cp is None:
            return '(Осиротевший)'
        return str(cp)

    @property
    def counterparty_type(self) -> str:
        """Verbose name модели контрагента (для группировки)."""
        if self.content_type_id is None:
            return 'Осиротевшие'
        return str(self.content_type.model_class()._meta.verbose_name_plural).capitalize()

    @property
    def as_header_format(self) -> str:
        """RFC 5322-формат для composer ("Имя <email>")."""
        email = self.primary_email
        if not email:
            return ''
        name = (self.name or '').strip()
        if not name:
            return email
        if any(ch in name for ch in ',;<>"'):
            name = '"' + name.replace('"', '\\"') + '"'
        return f'{name} <{email}>'


class ContactEmail(models.Model):
    """Один email у контакта. Отдельная таблица → проще индексировать/искать."""

    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name='emails',
        verbose_name='Контакт',
    )
    email = models.EmailField(
        max_length=254,
        db_index=True,
        verbose_name='Email',
    )
    is_primary = models.BooleanField(
        default=False,
        verbose_name='Основной',
    )
    position = models.PositiveIntegerField(
        default=0,
        verbose_name='Порядок',
    )

    class Meta:
        verbose_name = 'Email контакта'
        verbose_name_plural = 'Emails контактов'
        ordering = ['contact', '-is_primary', 'position', 'email']
        constraints = [
            models.UniqueConstraint(
                fields=['contact', 'email'],
                name='contactemail_unique_email_per_contact',
            ),
        ]

    def __str__(self) -> str:
        return self.email


class ContactPhone(models.Model):
    """Один телефон у контакта."""

    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name='phones',
        verbose_name='Контакт',
    )
    phone = models.CharField(
        max_length=50,
        verbose_name='Телефон',
    )
    is_primary = models.BooleanField(
        default=False,
        verbose_name='Основной',
    )
    position = models.PositiveIntegerField(
        default=0,
        verbose_name='Порядок',
    )

    class Meta:
        verbose_name = 'Телефон контакта'
        verbose_name_plural = 'Телефоны контактов'
        ordering = ['contact', '-is_primary', 'position', 'phone']

    def __str__(self) -> str:
        return self.phone
