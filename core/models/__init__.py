"""Пакет ``core.models`` — модели разнесены по доменам.

Раньше всё лежало в одном файле ``core/models.py`` (~2100 строк).
В рамках H6a (см. ``docs/ROADMAP_2026-05_high_medium.md``) он распилен
на отдельные модули по доменам. Имена классов и ``app_label = 'core'``
сохранены, поэтому миграций не добавляется, а исторические импорты
вида ``from core.models import Car`` продолжают работать через
реэкспорт ниже.

Подмодули:

* :mod:`._vehicle_types` — общие choices типов ТС.
* :mod:`.lines` — :class:`Line`, :class:`LineTHSCoefficient`.
* :mod:`.carriers` — :class:`Carrier`, :class:`CarrierTruck`,
  :class:`CarrierDriver`.
* :mod:`.clients` — :class:`Client`, :class:`ClientTariffRate`,
  :class:`ClientEmail`.
* :mod:`.warehouses` — :class:`Warehouse`, :class:`WarehouseSite`.
* :mod:`.company` — :class:`Company`.
* :mod:`.containers` — :class:`Container`.
* :mod:`.cars` — :class:`Car` (самая крупная модель).
* :mod:`.services` — :class:`BaseService` и подклассы
  (``Company/Line/Carrier/Warehouse Service``), :class:`CarService`,
  :class:`DeletedCarService`.
* :mod:`.auto_transport` — :class:`AutoTransport`.
* :mod:`.tasks` — :class:`Task` («Дела»).

Порядок импортов важен: модели с прямыми FK на другие классы (а не
строковыми ссылками) ставятся ПОСЛЕ своих зависимостей. На уровне
``__init__.py`` это:

  1. справочники без FK (``lines``, ``carriers``, ``clients``,
     ``warehouses``, ``company``);
  2. ``containers`` (использует ``Warehouse.SITE_CHOICES`` как
     class-attribute);
  3. ``cars`` (FK через строки, но читает ``Container.STATUS_CHOICES`` и
     ``Warehouse.SITE_CHOICES``);
  4. ``services`` (FK напрямую на ``Car`` / ``Company`` / ``Line`` / ...);
  5. ``auto_transport`` (FK напрямую на ``Carrier`` и др.);
  6. ``tasks`` (только строковые FK).

В конце реэкспортируем модели из соседних файлов ``core/models_*.py``,
чтобы Django зарегистрировал их в приложении ``core`` — раньше эту
функцию выполнял хвост старого ``core/models.py``.
"""

from ._vehicle_types import VEHICLE_TYPE_CHOICES
from .agent import AgentAction, AgentMemory, AgentPolicy, AgentQuestion, AgentRun
from .auto_transport import AutoTransport
from .cars import Car, CarModelImage
from .carriers import Carrier, CarrierDriver, CarrierTruck
from .clients import Client, ClientEmail, ClientTariffRate
from .company import Company
from .containers import Container
from .lines import Line, LineTHSCoefficient
from .series import SeriesCounter, next_document_number
from .services import (
    BaseService,
    CarrierService,
    CarService,
    CompanyService,
    DeletedCarService,
    LineService,
    WarehouseService,
)
from .tasks import Task
from .warehouses import Warehouse, WarehouseSite

# Доменные модули, перенесённые из топ-левел монолитов ``core/models_*.py``
# (A1, AUDIT_ROUND3). Старые пути сохранены как реэкспорт-шимы.
# Импорт здесь гарантирует регистрацию моделей во всех сценариях
# (management-команды, тесты без полного autodiscover).
from .contact import (  # noqa: E402, F401
    Contact,
    ContactEmail,
    ContactPhone,
)
from .email import (  # noqa: E402, F401
    ContainerEmail,
    ContainerEmailLink,
    EmailGroup,
    EmailGroupMember,
    EmailIngestFilter,
    GmailSyncState,
)
from .invoice_audit import (  # noqa: E402, F401
    InvoiceAudit,
    SupplierCost,
)
from .monitoring import (  # noqa: E402, F401
    SystemMetric,
    UptimeCheck,
)
from .scans import ScanProcessingJob  # noqa: E402, F401

__all__ = [
    # constants
    'VEHICLE_TYPE_CHOICES',
    # справочники
    'Line', 'LineTHSCoefficient',
    'Carrier', 'CarrierTruck', 'CarrierDriver',
    'Client', 'ClientTariffRate', 'ClientEmail',
    'Warehouse', 'WarehouseSite',
    'Company',
    # основные сущности
    'Container',
    'Car',
    'CarModelImage',
    # услуги
    'BaseService',
    'CompanyService', 'LineService', 'CarrierService', 'WarehouseService',
    'DeletedCarService', 'CarService',
    # автовоз
    'AutoTransport',
    # счётчики серий документов
    'SeriesCounter', 'next_document_number',
    # дела
    'Task',
    # AI-агент
    'AgentRun', 'AgentAction', 'AgentQuestion', 'AgentMemory', 'AgentPolicy',
    # реэкспорт из соседних файлов
    'Contact', 'ContactEmail', 'ContactPhone',
    'ContainerEmail', 'ContainerEmailLink',
    'EmailGroup', 'EmailGroupMember', 'EmailIngestFilter', 'GmailSyncState',
    'InvoiceAudit', 'SupplierCost',
    'SystemMetric', 'UptimeCheck',
    'ScanProcessingJob',
]
