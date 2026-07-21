"""Пакет ``core.signals`` — сигналы Django разнесены по доменам.

Раньше всё лежало в одном файле ``core/signals.py`` (~1550 строк).
В рамках H6d (см. ``docs/ROADMAP_2026-05_high_medium.md``) он распилен
на подмодули по ответственности. Этот ``__init__.py`` импортирует все
submodules — при импорте каждого декораторы ``@receiver(...)``
регистрируют свои обработчики в ``django.dispatch``. Затем вручную
поднимаются те сигналы, которые нельзя подключить декоратором (m2m
через-таблица + per-model cache invalidation).

``core.apps.CoreConfig.ready`` делает ``from . import signals`` —
этот импорт автоматически подтягивает пакет (``__init__.py``), поэтому
менять ``apps.py`` не нужно.

Подмодули:

* :mod:`.service_cache`       — инвалидация per-instance кэша
  ``LineService`` / ``WarehouseService`` / ``CarrierService`` /
  ``CompanyService``.
* :mod:`.container`           — pre/post_save для ``Container``,
  email-нотификации, GDrive-note.
* :mod:`.car`                 — pre/post_save для ``Car`` + хелперы
  (создание ``CarService``, регенерация инвойсов, e-mail standalone,
  «Дело» по ``is_important``).
* :mod:`.car_service`         — пересчёт ``Car.total_price`` и
  регенерация ``NewInvoice.items`` по ``CarService``.
* :mod:`.service_catalog`     — массовое обновление ``CarService`` при
  изменении каталога услуг + каскадное удаление.
* :mod:`.invoice`             — авто-категоризация ``NewInvoice``,
  снимок старого статуса, авто-push в site.pro, синхронизация
  ``LINKED_PAID``.
* :mod:`.transaction`         — синхронный пересчёт балансов и
  ``paid_amount`` инвойса.
* :mod:`.autotransport`       — генерация инвойсов автовоза, массовый
  ``TRANSFERRED``, m2m-валидация «Важное».
* :mod:`.cache_invalidation`  — инвалидация stats/payment_objects-кэша.

Backward-compat реэкспорт: ``core.admin.container`` импортирует
``car_post_save`` и пару ``recalculate_*`` напрямую из ``core.signals``;
эти имена реэкспортированы ниже.
"""

# Импорт submodules регистрирует @receiver декораторы.
# NOTE: бывший .bank (авто-платёж при привязке matched_invoice) заменён
# явным вызовом BillingService.create_payment_for_bank_match().
from core.signals import (  # noqa: F401
    car,
    car_service,
    cache_invalidation,
    container,
    invoice,
    partners,
    photos,
    service_cache,
    service_catalog,
    transaction,
)

# m2m + cache invalidation подключаются вручную (через apps.get_model и
# AutoTransport.cars.through, которые требуют apps.populate()).
#
# Раньше блок был обёрнут в `if apps.ready` с фолбэком на post_migrate,
# и до миграций сигналы не подключались — в т.ч. в обычном рантайме
# веб-приложения, где post_migrate не срабатывает (Django вызывает
# signals из CoreConfig.ready(), apps.ready тогда ещё False, а
# post_migrate стрельнёт только при `manage.py migrate`). В итоге
# m2m_changed на `AutoTransport.cars.through` фактически отключался.
# Models уже подгружены к этому моменту (signals импортируется ИЗ ready()
# после populate apps), поэтому import .models внутри connect_*_signals
# отрабатывает корректно.
from core.signals.autotransport import connect_autotransport_signals
from core.signals.cache_invalidation import connect_cache_invalidation_signals

connect_autotransport_signals()
connect_cache_invalidation_signals()


# Также оставляем post_migrate-хук на случай, если пакет будет
# импортирован до полной загрузки моделей (например, при кастомных
# скриптах bootstrap).
from django.apps import apps as _apps  # noqa: E402

if not _apps.ready:
    from django.db.models.signals import post_migrate

    def _setup_autotransport_signals_on_migrate(sender, **kwargs):
        if sender.name == "core":
            connect_autotransport_signals()
            connect_cache_invalidation_signals()

    post_migrate.connect(_setup_autotransport_signals_on_migrate)


# ---------------------------------------------------------------------------
# Backward-compat реэкспорт для внешних импортов (``core.admin.container``)
# ---------------------------------------------------------------------------

from core.signals.car import car_post_save  # noqa: E402, F401
from core.signals.car_service import (  # noqa: E402, F401
    recalculate_car_price_on_service_delete,
    recalculate_car_price_on_service_save,
    recalculate_invoices_on_car_service_delete,
    recalculate_invoices_on_car_service_save,
)

__all__ = [
    'car_post_save',
    'recalculate_car_price_on_service_save',
    'recalculate_car_price_on_service_delete',
    'recalculate_invoices_on_car_service_save',
    'recalculate_invoices_on_car_service_delete',
    'connect_autotransport_signals',
    'connect_cache_invalidation_signals',
]
