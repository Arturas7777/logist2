"""Сигналы контрагентов: синк «общей почты» в модель «Контакты».

При сохранении контрагента с заполненной общей почтой (``general_email``,
у Carrier — ``email``) адрес автоматически появляется в «Контактах»:
создаётся/используется контакт «Общая почта» этого контрагента и к нему
привязывается email. Дубликаты не создаются — если адрес уже есть у
любого контакта этого контрагента, ничего не делаем.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models.carriers import Carrier
from core.models.clients import Client
from core.models.company import Company
from core.models.lines import Line
from core.models.warehouses import Warehouse

logger = logging.getLogger(__name__)

_GENERAL_CONTACT_POSITION = "Общая почта"


def _sync_general_email_to_contacts(instance, email: str) -> None:
    email = (email or "").strip()
    if not email or instance.pk is None:
        return

    from django.contrib.contenttypes.models import ContentType

    from core.models.contact import Contact, ContactEmail

    ct = ContentType.objects.get_for_model(type(instance))

    already_linked = ContactEmail.objects.filter(
        email__iexact=email,
        contact__content_type=ct,
        contact__object_id=instance.pk,
    ).exists()
    if already_linked:
        return

    contact = Contact.objects.filter(
        content_type=ct,
        object_id=instance.pk,
        position=_GENERAL_CONTACT_POSITION,
    ).first()
    if contact is None:
        contact = Contact.objects.create(
            content_type=ct,
            object_id=instance.pk,
            name=instance.name,
            position=_GENERAL_CONTACT_POSITION,
        )
    ContactEmail.objects.get_or_create(contact=contact, email=email)
    logger.info(
        "[partners] Общая почта %s добавлена в Контакты (%s #%s)",
        email,
        type(instance).__name__,
        instance.pk,
    )


@receiver(post_save, sender=Client, dispatch_uid="partners_sync_email_client")
@receiver(post_save, sender=Company, dispatch_uid="partners_sync_email_company")
@receiver(post_save, sender=Warehouse, dispatch_uid="partners_sync_email_warehouse")
@receiver(post_save, sender=Line, dispatch_uid="partners_sync_email_line")
def sync_general_email(sender, instance, **kwargs):
    _sync_general_email_to_contacts(instance, instance.general_email)


@receiver(post_save, sender=Carrier, dispatch_uid="partners_sync_email_carrier")
def sync_carrier_email(sender, instance, **kwargs):
    # У Carrier общая почта хранится в историческом поле ``email``.
    _sync_general_email_to_contacts(instance, instance.email)
