"""Сигналы для ``AutoTransport``:

* ``post_save`` — при переходе в ``FORMED`` ставит генерацию инвойсов в
  Celery; при переходе в ``LOADED/IN_TRANSIT/DELIVERED`` помечает все
  привязанные машины как ``TRANSFERRED``.
* ``m2m_changed (cars)`` — блокирует добавление в автовоз авто с пометкой
  «Важное» (см. большой коммент внутри :func:`autotransport_cars_changed_handler`).

m2m-сигнал требует доступа к ``AutoTransport.cars.through`` (динамический
класс через-таблицы) — для него отдельная функция :func:`connect_autotransport_signals`,
которая вызывается из ``core.signals.__init__``.

Также здесь живёт :func:`_update_container_status_if_all_transferred` —
тонкая обёртка над методом модели. Её импортируют
:mod:`core.signals.car` (на TRANSFERRED-переходе авто) и сам этот модуль
(на массовом TRANSFERRED через автовоз).
"""

import logging

from django.db import transaction
from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver

from core.models import Car, Container

logger = logging.getLogger(__name__)


def _queue_or_run_generate_invoices(autotransport):
    """Ставит задачу в Celery; при недоступности брокера — выполняет синхронно."""
    from core.tasks import generate_autotransport_invoices_task

    try:
        transaction.on_commit(lambda: generate_autotransport_invoices_task.delay(autotransport.pk))
    except Exception as exc:
        logger.warning(
            "AutoTransport %s: Celery unavailable, generating invoices inline: %s",
            autotransport.number,
            exc,
        )
        try:
            invoices = autotransport.generate_invoices()
            if invoices:
                logger.info(
                    "AutoTransport %s: created/updated %d invoices (sync fallback)",
                    autotransport.number,
                    len(invoices),
                )
        except Exception as e:
            logger.error("AutoTransport %s invoice error: %s", autotransport.number, e)


@receiver(pre_save, sender="core.AutoTransport")
def autotransport_pre_save(sender, instance, **kwargs):
    """Снимок старого статуса — чтобы ``post_save`` отличал ПЕРЕХОД в FORMED
    от повторного сохранения уже сформированного автовоза."""
    if not instance.pk:
        instance._pre_save_status = None
        return
    instance._pre_save_status = sender.objects.filter(pk=instance.pk).values_list("status", flat=True).first()


@receiver(post_save, sender="core.AutoTransport")
def autotransport_post_save(sender, instance, created, **kwargs):
    old_status = getattr(instance, "_pre_save_status", None)
    instance._pre_save_status = None

    # Генерация инвойсов только при ПЕРЕХОДЕ в FORMED (новый автовоз сразу в
    # FORMED или смена статуса на FORMED). Раньше срабатывало на КАЖДОМ
    # сохранении сформированного автовоза — лишняя нагрузка и риск перезаписи
    # DRAFT/ISSUED. Изменение состава машин обрабатывается отдельно через
    # m2m_changed (post_add/post_remove).
    if instance.status == "FORMED" and old_status != "FORMED":
        _queue_or_run_generate_invoices(instance)

    if instance.status in ("LOADED", "IN_TRANSIT", "DELIVERED"):
        transfer_date = getattr(instance, "_transfer_date_override", None)
        _mark_cars_as_transferred(instance, transfer_date)


def _mark_cars_as_transferred(autotransport, transfer_date=None):
    from django.utils import timezone as tz

    if transfer_date is None:
        transfer_date = tz.now().date()
    affected_cars = list(autotransport.cars.exclude(status="TRANSFERRED").values_list("id", "container_id"))
    if not affected_cars:
        return
    car_ids = [c[0] for c in affected_cars]
    container_ids = {c[1] for c in affected_cars if c[1]}
    Car.objects.filter(id__in=car_ids).update(status="TRANSFERRED", transfer_date=transfer_date)
    logger.info(
        "AutoTransport %s: %d cars -> TRANSFERRED (date: %s)",
        autotransport.number,
        len(car_ids),
        transfer_date,
    )
    for cid in container_ids:
        _update_container_status_if_all_transferred(cid)


def _update_container_status_if_all_transferred(container_id):
    """Тонкая обёртка над ``Container.check_and_update_status_from_cars()``.

    Логика «контейнер передан, если все его авто TRANSFERRED» единым
    методом живёт на модели Container — здесь только подгрузка по id.
    Раньше эту же логику дублировал отдельный код на 12 строк.
    """
    try:
        container = Container.objects.only("id", "status", "number").get(pk=container_id)
    except Container.DoesNotExist:
        return
    container.check_and_update_status_from_cars()


def autotransport_cars_changed_handler(sender, instance, action, **kwargs):
    # Блокируем добавление авто, помеченных как «Важное»: пока галочка
    # стоит, машину нельзя включать в автовоз. Снятие галочки = ручное
    # завершение связанного «Дела» (см. _handle_car_important_transition).
    #
    # ВАЖНО про admin save flow: Django ModelForm при сохранении m2m делает
    # `instance.cars = [<id>, ...]` (Manager.set), а это последовательность
    # pre_clear → post_clear → pre_add → post_add со всеми текущими ID.
    # Если просто блокировать pre_add по is_important, существующие
    # автовозы с авто, которые УЖЕ были добавлены до выставления галочки,
    # перестанут сохраняться. Поэтому в pre_clear запоминаем старый
    # набор cars на инстансе, а в pre_add сравниваем и блокируем
    # ТОЛЬКО реально новые добавления.
    if action == "pre_clear":
        try:
            instance._existing_cars_before_clear = set(instance.cars.values_list("pk", flat=True))
        except Exception:
            instance._existing_cars_before_clear = set()
        return

    if action == "pre_add":
        pk_set = kwargs.get("pk_set") or set()
        if not pk_set:
            return
        existing = getattr(instance, "_existing_cars_before_clear", None)
        # Если pre_clear не было (точечный add()) — все pk_set считаются новыми.
        if existing is None:
            # При точечном add() старого набора в нашем атрибуте нет,
            # но БД уже не отражает добавляемые pk (post_add ещё впереди),
            # поэтому "уже привязанные" = текущий cars.
            try:
                existing = set(instance.cars.values_list("pk", flat=True))
            except Exception:
                existing = set()
        new_ids = pk_set - existing
        if not new_ids:
            return
        blocked = list(Car.objects.filter(pk__in=new_ids, is_important=True).values_list("vin", flat=True))
        if blocked:
            from django.core.exceptions import ValidationError

            raise ValidationError(
                "Нельзя добавить в автовоз авто с пометкой «Важное»: "
                + ", ".join(blocked)
                + ". Сначала снимите галочку «Важное» в карточке авто."
            )
        return

    if action == "post_clear":
        # Сохраняем атрибут до pre_add — не очищаем здесь.
        return

    if action in ("post_add", "post_remove"):
        # Чистим временный атрибут после успешного цикла set().
        if hasattr(instance, "_existing_cars_before_clear"):
            try:
                del instance._existing_cars_before_clear
            except AttributeError:
                pass
        if instance.status == "FORMED":
            _queue_or_run_generate_invoices(instance)


def connect_autotransport_signals():
    """Подключает m2m-сигнал к ``AutoTransport.cars.through``.

    Через-таблица — динамический класс, обращаться к ней можно только
    после ``apps.populate()``. Поэтому подключение оформлено отдельной
    функцией, которую вызывает ``core/signals/__init__.py`` после
    загрузки всех submodules.
    """
    try:
        from core.models import AutoTransport

        m2m_changed.connect(autotransport_cars_changed_handler, sender=AutoTransport.cars.through)
    except Exception as e:
        logger.warning("Failed to connect AutoTransport signals: %s", e)
