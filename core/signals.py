from django.db.models.signals import post_save, post_delete, pre_delete, pre_save, m2m_changed
from django.dispatch import receiver
from django.db import models as db_models
from .models import Car, Container, WarehouseService, LineService, CarrierService, Company, CompanyService, CarService, DeletedCarService, LineTHSCoefficient
from .models_billing import NewInvoice, Transaction
from django.db.models import Sum
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.db import transaction, OperationalError
from django.utils import timezone
from decimal import Decimal
import logging

logger = logging.getLogger('django')


# ============================================================================
# ИНВАЛИДАЦИЯ КЭША УСЛУГ ПРИ ИЗМЕНЕНИИ СПРАВОЧНИКОВ
# ============================================================================

def invalidate_service_cache(sender, instance, **kwargs):
    from django.core.cache import cache
    type_map = {LineService: 'LINE', WarehouseService: 'WAREHOUSE',
                CarrierService: 'CARRIER', CompanyService: 'COMPANY'}
    svc_type = type_map.get(sender)
    if svc_type:
        cache.delete(f"svc:{svc_type}:{instance.id}")


for _model in (LineService, WarehouseService, CarrierService, CompanyService):
    post_save.connect(invalidate_service_cache, sender=_model)
    post_delete.connect(invalidate_service_cache, sender=_model)


# ============================================================================
# СОХРАНЕНИЕ СТАРЫХ ЗНАЧЕНИЙ НА ЭКЗЕМПЛЯРЕ (thread-safe)
# ============================================================================
# Вместо глобальных словарей _old_container_values / _old_contractors / etc.
# храним старые значения на self._pre_save_* атрибутах экземпляра.
# Это безопасно при параллельных запросах (каждый request работает со своим экземпляром).

@receiver(pre_save, sender=Container)
def save_old_container_values(sender, instance, **kwargs):
    """Единый pre_save для Container: сохраняет старые значения и авто-устанавливает статус.
    Оптимизация: пропускает DB-запрос при update_fields (частичное обновление).
    """
    if instance.unload_date and instance.status in ('FLOATING', 'IN_PORT'):
        instance.status = 'UNLOADED'
        logger.info("[PRE_SAVE] Auto-set status to UNLOADED for container %s", instance.number)

    update_fields = kwargs.get('update_fields')
    if update_fields is not None:
        instance._pre_save_values = None
        instance._pre_save_notification = None
        return

    if instance.pk:
        try:
            old = Container.objects.filter(pk=instance.pk).values(
                'status', 'unload_date', 'planned_unload_date'
            ).first()
            if old:
                instance._pre_save_values = old
                instance._pre_save_notification = {
                    'planned_unload_date': old.get('planned_unload_date'),
                    'unload_date': old.get('unload_date')
                }

                old_status = old.get('status')
                if (
                    instance.status == 'UNLOADED'
                    and old_status != 'UNLOADED'
                    and not instance.unloaded_status_at
                ):
                    instance.unloaded_status_at = timezone.now()
            else:
                instance._pre_save_values = None
                instance._pre_save_notification = None
        except Exception as e:
            logger.error("[PRE_SAVE] Error: %s", e)
            instance._pre_save_values = None
            instance._pre_save_notification = None
    else:
        instance._pre_save_values = None
        instance._pre_save_notification = None
        if instance.status == 'UNLOADED' and not instance.unloaded_status_at:
            instance.unloaded_status_at = timezone.now()

@receiver(post_save, sender=Container)
def update_related_on_container_save(sender, instance, created, **kwargs):
    """
    Обновляет автомобили при изменении контейнера.
    
    Основная логика в ContainerAdmin.save_model(), но этот сигнал работает как 
    резервный механизм для случаев когда:
    - form.changed_data не распознал изменение
    - Сохранение произошло не через админку (API, shell, management command)
    """
    old_values = getattr(instance, '_pre_save_values', None)
    instance._pre_save_values = None  # очищаем после использования
    
    if not instance.pk:
        return
    
    # Проверяем изменилась ли дата разгрузки
    if old_values:
        old_unload_date = old_values.get('unload_date')
        new_unload_date = instance.unload_date
        
        # Если дата разгрузки изменилась - обновляем все авто
        if old_unload_date != new_unload_date and new_unload_date is not None:
            logger.info("[SIGNAL] unload_date changed for container %s: %s -> %s", instance.number, old_unload_date, new_unload_date)
            
            try:
                # Проверяем, не обновлены ли уже авто (через admin.save_model)
                # Берём первый авто и проверяем его дату
                first_car = instance.container_cars.first()
                if first_car and first_car.unload_date == new_unload_date:
                    logger.debug(f"[SIGNAL] Cars already updated by admin.save_model, skipping")
                    return
                
                # Обновляем дату у всех авто одним запросом (быстро и надёжно)
                updated_count = instance.container_cars.update(unload_date=new_unload_date)
                logger.info("[SIGNAL] Updated unload_date to %s for %d cars in container %s", new_unload_date, updated_count, instance.number)
                
                if updated_count > 0:
                    cars_to_update = []
                    for car in instance.container_cars.select_related('warehouse').prefetch_related('car_services').all():
                        car.update_days_and_storage()
                        car.calculate_total_price()
                        cars_to_update.append(car)
                    
                    if cars_to_update:
                        Car.objects.bulk_update(
                            cars_to_update,
                            ['days', 'storage_cost', 'total_price'],
                            batch_size=50
                        )
                        logger.info("[SIGNAL] Recalculated prices for %d cars", len(cars_to_update))
                        
            except Exception as e:
                logger.error("[SIGNAL] Failed to update cars for container %s: %s", instance.number, e, exc_info=True)

@receiver(post_save, sender=Car)
def update_related_on_car_save(sender, instance, **kwargs):
    """Defer invoice regeneration to after transaction commit to avoid blocking saves."""
    if not instance.pk:
        return
    
    if getattr(instance, '_updating_invoices', False):
        return
    
    new_invoices = list(NewInvoice.objects.filter(cars=instance).values_list('id', flat=True))
    if not new_invoices:
        return

    car_id = instance.pk
    def _regenerate_invoices():
        for invoice_id in new_invoices:
            try:
                with transaction.atomic():
                    invoice = NewInvoice.objects.select_for_update(nowait=True).get(id=invoice_id)
                    invoice.regenerate_items_from_cars()
                    logger.info(f"Auto-regenerated invoice {invoice.number} for car {car_id}")
            except OperationalError:
                logger.warning(f"Skipping invoice {invoice_id} - locked by another transaction")
            except NewInvoice.DoesNotExist:
                pass
            except Exception as e:
                logger.error(f"Failed to regenerate invoice {invoice_id}: {e}")

    transaction.on_commit(_regenerate_invoices)


# Сигналы для автоматического создания CarService при изменении контрагентов

@receiver(pre_save, sender=Car)
def save_old_car_values(sender, instance, **kwargs):
    """Единый pre_save для Car: сохраняет старые значения контрагентов и уведомлений.
    Оптимизация: пропускает DB-запрос при update_fields (bulk_update, частичные сохранения).
    """
    update_fields = kwargs.get('update_fields')
    if update_fields is not None:
        tracked = {'warehouse_id', 'line_id', 'carrier_id', 'unload_date', 'container_id'}
        if not tracked.intersection(update_fields):
            instance._pre_save_contractors = None
            instance._pre_save_car_notification = None
            return

    if instance.pk:
        try:
            old = Car.objects.filter(pk=instance.pk).values(
                'warehouse_id', 'line_id', 'carrier_id', 'unload_date', 'container_id'
            ).first()
            if old:
                instance._pre_save_contractors = {
                    'warehouse_id': old['warehouse_id'],
                    'line_id': old['line_id'],
                    'carrier_id': old['carrier_id'],
                }
                instance._pre_save_car_notification = {
                    'unload_date': old['unload_date'],
                    'container_id': old['container_id'],
                }
            else:
                instance._pre_save_contractors = None
                instance._pre_save_car_notification = None
        except Exception:
            instance._pre_save_contractors = None
            instance._pre_save_car_notification = None
    else:
        instance._pre_save_contractors = None
        instance._pre_save_car_notification = None

def find_line_service_by_container_count(line, container, vehicle_type):
    """
    Находит подходящую услугу линии на основе количества авто в контейнере и типа ТС.
    
    УСТАРЕВШИЙ МЕТОД - используется для обратной совместимости.
    Для новой логики с процентами используйте calculate_ths_for_container().
    
    Логика выбора:
    - Для мотоциклов: ищем "THS {ЛИНИЯ} MOTO" или "MOTO" в названии
    - Для авто: ищем "THS {ЛИНИЯ} {КОЛ-ВО} АВТО" или "{КОЛ-ВО} АВТО" в названии
    
    ВАЖНО: Мотоциклы НЕ учитываются при подсчёте количества авто!
    """
    if not line or not container:
        return None
    
    line_name_upper = line.name.upper()
    
    # Считаем количество ТОЛЬКО автомобилей в контейнере (мотоциклы не учитываются!)
    # Исключаем все мото-типы
    moto_types = ['MOTO', 'BIG_MOTO', 'ATV']
    car_count = container.container_cars.exclude(vehicle_type__in=moto_types).count()
    
    # Получаем все активные услуги линии
    services = LineService.objects.filter(line=line, is_active=True)
    
    if vehicle_type in moto_types:
        # Для мотоциклов ищем услугу с MOTO в названии
        for service in services:
            service_name_upper = service.name.upper()
            if 'MOTO' in service_name_upper:
                # Проверяем что это услуга для этой линии
                if line_name_upper in service_name_upper or 'THS' in service_name_upper:
                    return service
        # Если не нашли специфичную, ищем любую с MOTO
        for service in services:
            if 'MOTO' in service.name.upper():
                return service
    else:
        # Для авто ищем услугу по количеству
        # Формат: "THS MAERSK 3 АВТО" или "3 АВТО"
        search_patterns = [
            f'{car_count} АВТО',
            f'{car_count} AUTO',
            f'{car_count}АВТО',
            f'{car_count}AUTO',
        ]
        
        for service in services:
            service_name_upper = service.name.upper()
            for pattern in search_patterns:
                if pattern in service_name_upper:
                    return service
    
    return None


def calculate_ths_for_container(container):
    """
    Рассчитывает THS для каждого ТС в контейнере пропорционально их коэффициентам.
    
    Алгоритм:
    1. Получить общую сумму THS контейнера
    2. Для каждого ТС получить коэффициент его типа из LineTHSCoefficient
    3. Рассчитать долю каждого ТС = коэффициент / сумма_всех_коэффициентов
    4. THS для ТС = общий_THS × доля
    
    Возвращает словарь: {car_id: ths_amount}
    
    Пример:
    - Контейнер THS = 500 EUR
    - 3 машины: легковой(1.0) + джип(2.0) + мото(0.5) = сумма коэффициентов 3.5
    - Легковой: 500 × (1.0/3.5) = 143 EUR → округляем до 145 EUR
    - Джип: 500 × (2.0/3.5) = 286 EUR → округляем до 290 EUR
    - Мото: 500 × (0.5/3.5) = 71 EUR → округляем до 75 EUR
    """
    from core.models import LineTHSCoefficient
    
    if not container or not container.line or not container.ths:
        return {}
    
    total_ths = Decimal(str(container.ths))
    if total_ths <= 0:
        return {}
    
    # Получаем все ТС в контейнере
    cars = list(container.container_cars.all())
    if not cars:
        return {}
    
    # Получаем коэффициенты для типов ТС этой линии
    ths_coefficients = {
        tc.vehicle_type: Decimal(str(tc.coefficient))
        for tc in LineTHSCoefficient.objects.filter(line=container.line)
    }
    
    # Рассчитываем сумму коэффициентов для всех машин
    total_coefficient = Decimal('0.00')
    car_coefficients = {}
    
    for car in cars:
        # Получаем коэффициент для типа ТС, по умолчанию 1.0 (стандартный)
        coeff = ths_coefficients.get(car.vehicle_type, Decimal('1.00'))
        car_coefficients[car.id] = coeff
        total_coefficient += coeff
    
    from core.utils import round_up_to_5
    
    # Если сумма коэффициентов = 0, делим поровну
    if total_coefficient == 0:
        equal_share = total_ths / len(cars)
        return {car.id: round_up_to_5(equal_share) for car in cars}
    
    # Рассчитываем THS для каждого ТС пропорционально коэффициенту
    result = {}
    for car in cars:
        car_share = car_coefficients[car.id] / total_coefficient
        car_ths = total_ths * car_share
        # Округляем в большую сторону с шагом 5 EUR
        result[car.id] = round_up_to_5(car_ths)
    
    logger.info(f"THS distribution for container {container.number}: total={total_ths}, coefficients={car_coefficients}, result={result}")
    
    return result


def create_ths_services_for_container(container):
    """
    Создает услуги THS для всех ТС в контейнере на основе процентного распределения.
    
    Тип поставщика услуги (LINE или WAREHOUSE) определяется полем container.ths_payer.
    
    Возвращает количество созданных услуг.
    """
    if not container or not container.line:
        return 0
    
    # Рассчитываем THS для каждого ТС
    ths_distribution = calculate_ths_for_container(container)
    if not ths_distribution:
        return 0
    
    # Определяем тип услуги (LINE или WAREHOUSE)
    service_type = container.ths_payer if hasattr(container, 'ths_payer') else 'LINE'
    
    # Получаем или создаем услугу THS для линии
    # Ищем услугу с названием "THS" или создаем абстрактную услугу
    line_service = None
    if service_type == 'LINE':
        line_service = LineService.objects.filter(
            line=container.line,
            is_active=True,
            name__icontains='THS'
        ).first()
        
        if not line_service:
            # Создаем услугу THS если её нет
            line_service, created = LineService.objects.get_or_create(
                line=container.line,
                name=f"THS {container.line.name}",
                defaults={
                    'description': 'Услуга THS (рассчитывается пропорционально)',
                    'default_price': 0,
                    'is_active': True
                }
            )
    
    warehouse_service = None
    if service_type == 'WAREHOUSE' and container.warehouse:
        warehouse_service = WarehouseService.objects.filter(
            warehouse=container.warehouse,
            is_active=True,
            name__icontains='THS'
        ).first()
        
        if not warehouse_service:
            # Создаем услугу THS если её нет
            warehouse_service, created = WarehouseService.objects.get_or_create(
                warehouse=container.warehouse,
                name=f"THS {container.warehouse.name}",
                defaults={
                    'description': 'Услуга THS (рассчитывается пропорционально)',
                    'default_price': 0,
                    'is_active': True,
                    'add_by_default': False
                }
            )
    
    created_count = 0

    # Batch-fetch all cars at once to avoid N+1
    car_ids = list(ths_distribution.keys())
    cars_by_id = {c.id: c for c in Car.objects.filter(id__in=car_ids)}

    for car_id, ths_amount in ths_distribution.items():
        try:
            car = cars_by_id.get(car_id)
            if not car:
                logger.warning(f"Car {car_id} not found when creating THS service")
                continue

            # Удаляем старые услуги THS для этого авто
            # Удаляем от линии
            CarService.objects.filter(
                car=car,
                service_type='LINE'
            ).filter(
                service_id__in=LineService.objects.filter(
                    name__icontains='THS'
                ).values_list('id', flat=True)
            ).delete()

            # Удаляем от склада
            CarService.objects.filter(
                car=car,
                service_type='WAREHOUSE'
            ).filter(
                service_id__in=WarehouseService.objects.filter(
                    name__icontains='THS'
                ).values_list('id', flat=True)
            ).delete()

            # Создаем новую услугу THS
            if service_type == 'LINE' and line_service:
                CarService.objects.create(
                    car=car,
                    service_type='LINE',
                    service_id=line_service.id,
                    custom_price=ths_amount,
                    quantity=1,
                    notes=f"THS рассчитан пропорционально. Тип ТС: {car.get_vehicle_type_display()}"
                )
                logger.info(f"🚢 THS {ths_amount} EUR для {car.vin} (тип: {car.get_vehicle_type_display()}) от линии")
                created_count += 1

            elif service_type == 'WAREHOUSE' and warehouse_service:
                CarService.objects.create(
                    car=car,
                    service_type='WAREHOUSE',
                    service_id=warehouse_service.id,
                    custom_price=ths_amount,
                    quantity=1,
                    notes=f"THS рассчитан пропорционально. Тип ТС: {car.get_vehicle_type_display()}"
                )
                logger.info(f"🏭 THS {ths_amount} EUR для {car.vin} (тип: {car.get_vehicle_type_display()}) от склада")
                created_count += 1

        except Exception as e:
            logger.error(f"Error creating THS service for car {car_id}: {e}")
    
    return created_count


def apply_client_tariffs_for_container(container):
    """
    Применяет тарифы клиентов к наценкам услуг после расчёта THS.
    
    Вызывается ПОСЛЕ create_ths_services_for_container().
    
    agreed_total_price — это ОБЩАЯ цена за авто (все услуги КРОМЕ хранения).
    
    Алгоритм:
      1. Определяется agreed_total_price из ClientTariffRate
         - FIXED: ставка по типу ТС (не зависит от кол-ва авто)
         - FLEXIBLE: ставка по типу ТС + диапазону кол-ва авто в контейнере
      2. actual_total = сумма custom_price ВСЕХ не-хранение услуг
      3. diff = agreed_total_price - actual_total (это прибыль / наценка)
      4. Распределяет diff ПОРОВНУ между всеми не-хранение услугами как markup_amount
    """
    if not container:
        return
    
    from core.models import CarService, ClientTariffRate
    
    cars = list(container.container_cars.select_related('client').all())
    if not cars:
        return
    
    # Общее кол-во ТС в контейнере (нужно для FLEXIBLE)
    total_cars_in_container = len(cars)
    
    for car in cars:
        if not car.client or car.client.tariff_type == 'NONE':
            continue
        
        client = car.client
        
        # Получаем согласованную общую цену
        agreed_total = None
        
        if client.tariff_type == 'FIXED':
            # FIXED: ищем ставку только по типу ТС (без учёта кол-ва)
            rate = ClientTariffRate.objects.filter(
                client=client, vehicle_type=car.vehicle_type
            ).first()
            if rate:
                agreed_total = rate.agreed_total_price
        
        elif client.tariff_type == 'FLEXIBLE':
            # FLEXIBLE: ищем ставку по типу ТС + диапазону кол-ва авто
            rate = ClientTariffRate.objects.filter(
                client=client,
                vehicle_type=car.vehicle_type,
                min_cars__lte=total_cars_in_container
            ).filter(
                db_models.Q(max_cars__gte=total_cars_in_container) | db_models.Q(max_cars__isnull=True)
            ).first()
            if rate:
                agreed_total = rate.agreed_total_price
        
        if agreed_total is None:
            logger.debug(
                f"Нет тарифа для {client.name} ({client.tariff_type}), "
                f"тип ТС: {car.vehicle_type}, кол-во авто: {total_cars_in_container}"
            )
            continue
        
        # Получаем ВСЕ услуги этого авто и фильтруем не-хранение
        all_services = list(CarService.objects.filter(car=car))
        non_storage = []
        for svc in all_services:
            svc_name = svc.get_service_name()
            if svc_name and 'Хранение' not in svc_name:
                non_storage.append(svc)
        
        if not non_storage:
            continue
        
        # Сумма себестоимости всех не-хранение услуг
        actual_total = sum((svc.custom_price or Decimal('0')) for svc in non_storage)
        
        # Разница = наценка (прибыль), которую нужно распределить
        diff = agreed_total - actual_total
        
        # Распределяем diff поровну между не-хранение услугами
        share = (diff / len(non_storage)).quantize(Decimal('0.01'))
        remainder = diff - share * len(non_storage)
        
        for i, svc in enumerate(non_storage):
            svc.markup_amount = share
            if i == len(non_storage) - 1:
                svc.markup_amount = share + remainder
            svc.save(update_fields=['markup_amount'])
        
        logger.info(
            f"📊 {client.tariff_type} тариф для {car.vin} ({client.name}): "
            f"agreed={agreed_total}€, actual_cost={actual_total}€, наценка={diff}€, "
            f"кол-во авто={total_cars_in_container}, распределено по {len(non_storage)} услугам"
        )


def apply_client_tariff_for_car(car):
    """
    Применяет тариф клиента к наценкам услуг одного автомобиля.

    Вызывается при смене клиента в карточке авто.
    Логика аналогична apply_client_tariffs_for_container(), но для одного авто.

    Если у нового клиента tariff_type == NONE — сбрасывает markup на default_markup
    из каталога услуг (WarehouseService/LineService/CarrierService/CompanyService).
    """
    from core.models import CarService, ClientTariffRate

    if not car or not car.pk:
        return

    client = car.client

    if not client or client.tariff_type == 'NONE':
        _reset_markup_to_defaults(car)
        return

    total_cars_in_container = 1
    if car.container_id:
        total_cars_in_container = Car.objects.filter(container_id=car.container_id).count()

    agreed_total = None

    if client.tariff_type == 'FIXED':
        rate = ClientTariffRate.objects.filter(
            client=client, vehicle_type=car.vehicle_type
        ).first()
        if rate:
            agreed_total = rate.agreed_total_price

    elif client.tariff_type == 'FLEXIBLE':
        rate = ClientTariffRate.objects.filter(
            client=client,
            vehicle_type=car.vehicle_type,
            min_cars__lte=total_cars_in_container
        ).filter(
            db_models.Q(max_cars__gte=total_cars_in_container) | db_models.Q(max_cars__isnull=True)
        ).first()
        if rate:
            agreed_total = rate.agreed_total_price

    if agreed_total is None:
        logger.debug(
            f"Нет тарифа для {client.name} ({client.tariff_type}), "
            f"тип ТС: {car.vehicle_type}, кол-во авто: {total_cars_in_container}"
        )
        return

    all_services = list(CarService.objects.filter(car=car))
    non_storage = [svc for svc in all_services
                   if svc.get_service_name() and 'Хранение' not in svc.get_service_name()]

    if not non_storage:
        return

    actual_total = sum((svc.custom_price or Decimal('0')) for svc in non_storage)
    diff = agreed_total - actual_total

    share = (diff / len(non_storage)).quantize(Decimal('0.01'))
    remainder = diff - share * len(non_storage)

    for i, svc in enumerate(non_storage):
        svc.markup_amount = share
        if i == len(non_storage) - 1:
            svc.markup_amount = share + remainder
        svc.save(update_fields=['markup_amount'])

    logger.info(
        f"📊 Пересчёт тарифа при смене клиента {car.vin} ({client.name}): "
        f"agreed={agreed_total}€, actual_cost={actual_total}€, наценка={diff}€"
    )


def _reset_markup_to_defaults(car):
    """Сбрасывает markup_amount на default_markup из каталога услуг."""
    from core.models import CarService

    for svc in CarService.objects.filter(car=car):
        default_markup = Decimal('0')
        try:
            service_obj = svc._get_service_obj()
            if service_obj:
                default_markup = Decimal(str(getattr(service_obj, 'default_markup', 0) or 0))
        except Exception:
            pass

        if svc.markup_amount != default_markup:
            svc.markup_amount = default_markup
            svc.save(update_fields=['markup_amount'])


def find_warehouse_services_for_car(warehouse):
    """
    Находит услуги склада для автомобиля, которые должны добавляться по умолчанию.
    Возвращает только услуги с флагом add_by_default=True.
    """
    if not warehouse:
        return []
    
    # Возвращаем только услуги с флагом add_by_default=True
    return list(WarehouseService.objects.filter(
        warehouse=warehouse, 
        is_active=True,
        add_by_default=True
    ))


def find_line_services_for_car(line):
    """
    Находит услуги линии для автомобиля, которые должны добавляться по умолчанию.
    THS-услуги исключаются (THS управляется отдельно).
    """
    if not line:
        return []
    return list(LineService.objects.filter(
        line=line,
        is_active=True,
        add_by_default=True
    ).exclude(name__icontains='THS'))


def find_carrier_services_for_car(carrier):
    """
    Находит услуги перевозчика для автомобиля, которые должны добавляться по умолчанию.
    """
    if not carrier:
        return []
    return list(CarrierService.objects.filter(
        carrier=carrier,
        is_active=True,
        add_by_default=True
    ))


def get_main_company():
    """Возвращает главную компанию (из settings.COMPANY_NAME)."""
    return Company.get_default()


def find_company_services_for_car(company):
    """
    Находит услуги компании для автомобиля, которые должны добавляться по умолчанию.
    """
    if not company:
        return []
    return list(CompanyService.objects.filter(
        company=company,
        is_active=True,
        add_by_default=True
    ))


@receiver(post_save, sender=Car)
def create_car_services_on_car_save(sender, instance, **kwargs):
    """
    Создает записи CarService при сохранении автомобиля с контрагентами.
    
    Умный выбор услуг:
    - Услуги линий: выбираются по количеству авто в контейнере (THS MAERSK 3 АВТО)
    - Для мотоциклов: выбирается услуга с MOTO (THS CMA MOTO)
    - Услуги складов: добавляются "Разгрузка/Погрузка/Декларация" и "Хранение"
    """
    if not instance.pk:
        return
    
    # Защита от рекурсии - пропускаем если уже создаем услуги для этого авто
    if getattr(instance, '_creating_services', False):
        return
    
    # Проверяем, изменились ли контрагенты (только при создании или смене контрагентов)
    created = kwargs.get('created', False)
    if not created:
        # Если это не создание, проверяем, изменились ли контрагенты
        old_contractors = getattr(instance, '_pre_save_contractors', None)
        instance._pre_save_contractors = None  # очищаем после использования
        if old_contractors:
            warehouse_changed = old_contractors.get('warehouse_id') != instance.warehouse_id
            line_changed = old_contractors.get('line_id') != instance.line_id
            carrier_changed = old_contractors.get('carrier_id') != instance.carrier_id
            
            # Если контрагенты не изменились, не обновляем услуги
            if not (warehouse_changed or line_changed or carrier_changed):
                return
        else:
            # Нет сохранённых значений - значит контрагенты не менялись
            return
    
    # Устанавливаем флаг для защиты от рекурсии
    instance._creating_services = True
    
    try:
        # Получаем черные списки удаленных услуг
        deleted_warehouse_services = set(
            DeletedCarService.objects.filter(car=instance, service_type='WAREHOUSE').values_list('service_id', flat=True)
        )
        deleted_line_services = set(
            DeletedCarService.objects.filter(car=instance, service_type='LINE').values_list('service_id', flat=True)
        )
        deleted_carrier_services = set(
            DeletedCarService.objects.filter(car=instance, service_type='CARRIER').values_list('service_id', flat=True)
        )
        deleted_company_services = set(
            DeletedCarService.objects.filter(car=instance, service_type='COMPANY').values_list('service_id', flat=True)
        )
        
        # ========== УСЛУГИ СКЛАДА ==========
        # Удаляем старые услуги склада если склад изменился
        instance.car_services.filter(service_type='WAREHOUSE').delete()
        
        if instance.warehouse:
            # Находим стандартные услуги склада (Разгрузка/Декларация и Хранение)
            warehouse_services = find_warehouse_services_for_car(instance.warehouse)
            
            for service in warehouse_services:
                if service.id not in deleted_warehouse_services:
                    # Для услуги "Хранение" цена и наценка = платные_дни × ставка_за_день
                    # Если платных дней нет - цена = 0
                    if service.name == 'Хранение':
                        days = Decimal(str(instance.days or 0))
                        custom_price = days * Decimal(str(service.default_price or 0))
                        # Наценка тоже умножается на дни
                        default_markup = days * Decimal(str(getattr(service, 'default_markup', 0) or 0))
                    else:
                        custom_price = service.default_price
                        # Получаем default_markup из услуги
                        default_markup = getattr(service, 'default_markup', None) or Decimal('0')
                    
                    CarService.objects.get_or_create(
                        car=instance,
                        service_type='WAREHOUSE',
                        service_id=service.id,
                        defaults={'custom_price': custom_price, 'markup_amount': default_markup}
                    )
                    logger.info(f"🏭 Добавлена услуга склада '{service.name}' для {instance.vin} (цена: {custom_price}, наценка: {default_markup})")
        
        # ========== УСЛУГИ ЛИНИИ ==========
        # THS создается отдельно через create_ths_services_for_container()
        # Здесь добавляем только услуги с add_by_default=True (кроме THS)
        instance.car_services.filter(
            service_type='LINE'
        ).exclude(
            service_id__in=LineService.objects.filter(name__icontains='THS').values_list('id', flat=True)
        ).delete()
        
        if instance.line:
            line_services = find_line_services_for_car(instance.line)
            for service in line_services:
                if service.id not in deleted_line_services:
                    default_markup = getattr(service, 'default_markup', None) or Decimal('0')
                    CarService.objects.get_or_create(
                        car=instance,
                        service_type='LINE',
                        service_id=service.id,
                        defaults={'custom_price': service.default_price, 'markup_amount': default_markup}
                    )
                    logger.info(f"🚢 Добавлена услуга линии '{service.name}' для {instance.vin} (цена: {service.default_price}, наценка: {default_markup})")
        
        # ========== УСЛУГИ ПЕРЕВОЗЧИКА ==========
        # Удаляем старые услуги перевозчика если перевозчик изменился
        instance.car_services.filter(service_type='CARRIER').delete()
        
        if instance.carrier:
            carrier_services = find_carrier_services_for_car(instance.carrier)
            
            for service in carrier_services:
                if service.id not in deleted_carrier_services:
                    # Получаем default_markup из услуги
                    default_markup = getattr(service, 'default_markup', None) or Decimal('0')
                    
                    CarService.objects.get_or_create(
                        car=instance,
                        service_type='CARRIER',
                        service_id=service.id,
                        defaults={'custom_price': service.default_price, 'markup_amount': default_markup}
                    )
        
        # ========== УСЛУГИ КОМПАНИИ ==========
        # Добавляем только для новых авто и только для главной компании
        if created:
            main_company = get_main_company()
            if main_company:
                company_services = find_company_services_for_car(main_company)
                for service in company_services:
                    if service.id in deleted_company_services:
                        continue
                    default_markup = getattr(service, 'default_markup', None) or Decimal('0')
                    CarService.objects.get_or_create(
                        car=instance,
                        service_type='COMPANY',
                        service_id=service.id,
                        defaults={'custom_price': service.default_price, 'markup_amount': default_markup}
                    )
                
    except Exception as e:
        logger.error(f"Error creating car services: {e}")
    finally:
        # Сбрасываем флаг защиты от рекурсии
        instance._creating_services = False

@receiver(post_save, sender=WarehouseService)
def update_cars_on_warehouse_service_change(sender, instance, **kwargs):
    """Обновляет записи CarService при изменении услуг склада (bulk)."""
    try:
        if instance.is_active and instance.default_price > 0:
            car_services = list(CarService.objects.filter(
                service_type='WAREHOUSE',
                service_id=instance.id,
                car__warehouse=instance.warehouse
            ).select_related('car'))

            if not car_services:
                return

            default_markup_val = getattr(instance, 'default_markup', None) or Decimal('0')
            for cs in car_services:
                if instance.name == 'Хранение':
                    days = Decimal(str(cs.car.days or 0))
                    cs.custom_price = days * Decimal(str(instance.default_price or 0))
                    cs.markup_amount = days * Decimal(str(default_markup_val))
                else:
                    cs.custom_price = instance.default_price
                    cs.markup_amount = default_markup_val

            CarService.objects.bulk_update(car_services, ['custom_price', 'markup_amount'], batch_size=100)
        else:
            affected_car_ids = list(CarService.objects.filter(
                service_type='WAREHOUSE',
                service_id=instance.id
            ).values_list('car_id', flat=True))

            CarService.objects.filter(
                service_type='WAREHOUSE',
                service_id=instance.id
            ).delete()

            if affected_car_ids:
                cars_to_update = []
                for car in Car.objects.filter(pk__in=affected_car_ids):
                    car.calculate_total_price()
                    cars_to_update.append(car)
                if cars_to_update:
                    Car.objects.bulk_update(cars_to_update, ['total_price'], batch_size=100)
    except Exception as e:
        logger.error(f"Error updating cars on warehouse service change: {e}")

@receiver(post_save, sender=LineService)
def update_cars_on_line_service_change(sender, instance, **kwargs):
    """
    Услуги линии (THS) управляются централизованно через
    create_ths_services_for_container() при сохранении контейнера.
    Этот сигнал только удаляет услуги если LineService стала неактивной,
    и пересчитывает total_price затронутых авто (bulk).
    """
    if not instance.is_active:
        try:
            affected_car_ids = list(CarService.objects.filter(
                service_type='LINE',
                service_id=instance.id
            ).values_list('car_id', flat=True))
            
            deleted = CarService.objects.filter(
                service_type='LINE',
                service_id=instance.id
            ).delete()
            if deleted[0] > 0:
                logger.info(f"Deleted {deleted[0]} LINE services for inactive LineService {instance.id}")
                cars_to_update = []
                for car in Car.objects.filter(id__in=affected_car_ids):
                    car.calculate_total_price()
                    cars_to_update.append(car)
                if cars_to_update:
                    Car.objects.bulk_update(cars_to_update, ['total_price'], batch_size=100)
        except Exception as e:
            logger.error(f"Error deleting inactive line service: {e}")

@receiver(post_save, sender=CarrierService)
def update_cars_on_carrier_service_change(sender, instance, **kwargs):
    """Обновляет записи CarService при изменении услуг перевозчика (bulk)."""
    try:
        if instance.is_active and instance.default_price > 0:
            default_markup = getattr(instance, 'default_markup', None) or Decimal('0')
            CarService.objects.filter(
                service_type='CARRIER',
                service_id=instance.id,
                car__carrier=instance.carrier
            ).update(custom_price=instance.default_price, markup_amount=default_markup)
        else:
            affected_car_ids = list(CarService.objects.filter(
                service_type='CARRIER',
                service_id=instance.id
            ).values_list('car_id', flat=True))

            CarService.objects.filter(
                service_type='CARRIER',
                service_id=instance.id
            ).delete()

            if affected_car_ids:
                cars_to_update = []
                for car in Car.objects.filter(pk__in=affected_car_ids):
                    car.calculate_total_price()
                    cars_to_update.append(car)
                if cars_to_update:
                    Car.objects.bulk_update(cars_to_update, ['total_price'], batch_size=100)
    except Exception as e:
        logger.error(f"Error updating cars on carrier service change: {e}")


@receiver(post_save, sender=CompanyService)
def update_cars_on_company_service_change(sender, instance, **kwargs):
    """Обновляет записи CarService при изменении услуг компании (bulk)."""
    try:
        car_services = CarService.objects.filter(
            service_type='COMPANY',
            service_id=instance.id
        )
        
        affected_car_ids = list(car_services.values_list('car_id', flat=True).distinct())
        
        if instance.is_active and instance.default_price > 0:
            default_markup = getattr(instance, 'default_markup', None) or Decimal('0')
            car_services.update(custom_price=instance.default_price, markup_amount=default_markup)
        else:
            car_services.delete()
        
        if affected_car_ids:
            cars_to_update = []
            for car in Car.objects.filter(id__in=affected_car_ids):
                car.calculate_total_price()
                cars_to_update.append(car)
            if cars_to_update:
                Car.objects.bulk_update(cars_to_update, ['total_price'], batch_size=100)
    except Exception as e:
        logger.error(f"Error updating cars on company service change: {e}")


# ============================================================================
# СИГНАЛ ДЛЯ АВТО-ОТПРАВКИ ИНВОЙСА В SITE.PRO
# ============================================================================

# Сохраняем старый статус инвойса для определения смены на ISSUED
# ============================================================================
# АВТОМАТИЧЕСКАЯ КАТЕГОРИЗАЦИЯ ИНВОЙСОВ
# ============================================================================

@receiver(pre_save, sender=NewInvoice)
def auto_categorize_invoice(sender, instance, **kwargs):
    """
    Автоматически назначает категорию "Логистика" если инвойс
    выставлен складом, линией или перевозчиком.
    Не перезаписывает уже установленную категорию.
    """
    if instance.category_id:
        return  # Категория уже задана — не трогаем
    
    # Если выставитель — склад, линия или перевозчик → "Логистика"
    if instance.issuer_warehouse_id or instance.issuer_line_id or instance.issuer_carrier_id:
        try:
            from .models_billing import ExpenseCategory
            logistics_cat = ExpenseCategory.objects.filter(name='Логистика').first()
            if logistics_cat:
                instance.category = logistics_cat
                logger.info(f"🏷️ Инвойс {instance.number or 'новый'}: автоматически назначена категория 'Логистика'")
        except Exception as e:
            logger.warning(f"Не удалось назначить категорию: {e}")


@receiver(pre_save, sender=NewInvoice)
def save_old_invoice_status(sender, instance, **kwargs):
    """Сохраняет старый статус инвойса на экземпляре (thread-safe).
    Пропускает DB-запрос при update_fields без 'status'.
    """
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'status' not in update_fields:
        instance._pre_save_status = None
        return

    if instance.pk:
        try:
            old = NewInvoice.objects.filter(pk=instance.pk).values('status').first()
            if old:
                instance._pre_save_status = old['status']
            else:
                instance._pre_save_status = None
        except Exception:
            instance._pre_save_status = None
    else:
        instance._pre_save_status = None


@receiver(post_save, sender=NewInvoice)
def auto_push_invoice_to_sitepro(sender, instance, created, **kwargs):
    """
    Автоматически отправляет инвойс в site.pro при смене статуса на ISSUED.
    Работает только если есть активное подключение с auto_push_on_issue=True.
    """
    if not instance.pk:
        return

    # Проверяем, что статус сменился на ISSUED
    old_status = getattr(instance, '_pre_save_status', None)
    instance._pre_save_status = None  # очищаем после использования
    if instance.status != 'ISSUED':
        return
    if old_status == 'ISSUED':
        return  # Статус не изменился

    # Защита от рекурсии
    if getattr(instance, '_pushing_to_sitepro', False):
        return

    def _do_push():
        try:
            from core.models_accounting import SiteProConnection
            connection = SiteProConnection.objects.filter(
                is_active=True,
                auto_push_on_issue=True,
            ).first()

            if not connection:
                return

            instance._pushing_to_sitepro = True
            try:
                from core.services.sitepro_service import SiteProService
                service = SiteProService(connection)
                service.push_invoice(instance)
                logger.info(f'[SitePro] Авто-отправка инвойса {instance.number} при статусе ISSUED')
            finally:
                instance._pushing_to_sitepro = False

        except Exception as e:
            logger.error(f'[SitePro] Ошибка авто-отправки инвойса {instance.number}: {e}')

    # Выполняем после коммита транзакции
    transaction.on_commit(_do_push)


# ============================================================================
# СИГНАЛЫ ДЛЯ ПЕРЕСЧЕТА ИНВОЙСОВ ПРИ ИЗМЕНЕНИИ УСЛУГ АВТОМОБИЛЯ
# ============================================================================

def _deferred_invoice_regeneration(car_id):
    """Deferred invoice regeneration via on_commit to avoid cascading during bulk saves."""
    def _do_regenerate():
        try:
            invoice_ids = list(
                NewInvoice.objects.filter(
                    cars__id=car_id,
                    status__in=['DRAFT', 'ISSUED', 'PARTIALLY_PAID', 'OVERDUE']
                ).values_list('id', flat=True)
            )
            for invoice_id in invoice_ids:
                try:
                    with transaction.atomic():
                        invoice = NewInvoice.objects.select_for_update(nowait=True).get(id=invoice_id)
                        invoice.regenerate_items_from_cars()
                except OperationalError:
                    logger.warning("Skipping invoice %s - locked", invoice_id)
                except NewInvoice.DoesNotExist:
                    pass
        except Exception as e:
            logger.error("Error in deferred invoice regeneration for car %s: %s", car_id, e)
    transaction.on_commit(_do_regenerate)


@receiver(post_save, sender=CarService)
def recalculate_invoices_on_car_service_save(sender, instance, **kwargs):
    """Пересчитывает инвойсы при изменении услуги автомобиля (deferred)."""
    if instance.car_id:
        _deferred_invoice_regeneration(instance.car_id)


@receiver(post_delete, sender=CarService)
def recalculate_invoices_on_car_service_delete(sender, instance, **kwargs):
    """Пересчитывает инвойсы при удалении услуги автомобиля (deferred)."""
    if instance.car_id:
        _deferred_invoice_regeneration(instance.car_id)


# ============================================================================
# КАСКАДНОЕ УДАЛЕНИЕ CarService ПРИ УДАЛЕНИИ УСЛУГ ИЗ СПРАВОЧНИКОВ
# ============================================================================

@receiver(pre_delete, sender=LineService)
def delete_car_services_on_line_service_delete(sender, instance, **kwargs):
    """
    Удаляет связанные CarService записи при удалении услуги линии.
    Это предотвращает появление 'битых' записей с несуществующими service_id.
    """
    try:
        deleted_count = CarService.objects.filter(
            service_type='LINE',
            service_id=instance.id
        ).delete()[0]
        
        if deleted_count > 0:
            logger.info(f"🗑️ Удалено {deleted_count} CarService записей при удалении LineService '{instance.name}' (id={instance.id})")
    except Exception as e:
        logger.error(f"Error deleting CarService on LineService delete: {e}")


@receiver(pre_delete, sender=WarehouseService)
def delete_car_services_on_warehouse_service_delete(sender, instance, **kwargs):
    """
    Удаляет связанные CarService записи при удалении услуги склада.
    Это предотвращает появление 'битых' записей с несуществующими service_id.
    """
    try:
        deleted_count = CarService.objects.filter(
            service_type='WAREHOUSE',
            service_id=instance.id
        ).delete()[0]
        
        if deleted_count > 0:
            logger.info(f"🗑️ Удалено {deleted_count} CarService записей при удалении WarehouseService '{instance.name}' (id={instance.id})")
    except Exception as e:
        logger.error(f"Error deleting CarService on WarehouseService delete: {e}")


@receiver(pre_delete, sender=CarrierService)
def delete_car_services_on_carrier_service_delete(sender, instance, **kwargs):
    """
    Удаляет связанные CarService записи при удалении услуги перевозчика.
    Это предотвращает появление 'битых' записей с несуществующими service_id.
    """
    try:
        deleted_count = CarService.objects.filter(
            service_type='CARRIER',
            service_id=instance.id
        ).delete()[0]
        
        if deleted_count > 0:
            logger.info(f"🗑️ Удалено {deleted_count} CarService записей при удалении CarrierService '{instance.name}' (id={instance.id})")
    except Exception as e:
        logger.error(f"Error deleting CarService on CarrierService delete: {e}")


@receiver(pre_delete, sender=CompanyService)
def delete_car_services_on_company_service_delete(sender, instance, **kwargs):
    """
    Удаляет связанные CarService записи при удалении услуги компании.
    """
    try:
        deleted_count = CarService.objects.filter(
            service_type='COMPANY',
            service_id=instance.id
        ).delete()[0]
        
        if deleted_count > 0:
            logger.info(f"🗑️ Удалено {deleted_count} CarService записей при удалении CompanyService '{instance.name}' (id={instance.id})")
    except Exception as e:
        logger.error(f"Error deleting CarService on CompanyService delete: {e}")


# ============================================================================
# СИГНАЛЫ ДЛЯ АВТО-ПЕРЕСЧЁТА БАЛАНСА И PAID_AMOUNT ПРИ ИЗМЕНЕНИИ ТРАНЗАКЦИИ
# ============================================================================

def _recalc_transaction_effects(instance):
    """Пересчитать баланс затронутых сущностей и paid_amount связанного инвойса."""
    if instance.status != 'COMPLETED':
        return

    for entity in (instance.sender, instance.recipient):
        try:
            Transaction.recalculate_entity_balance(entity)
        except Exception as e:
            logger.error(f"Error recalculating balance for {entity}: {e}")

    if instance.invoice_id:
        try:
            instance.invoice.recalculate_paid_amount()
        except Exception as e:
            logger.error(f"Error recalculating paid_amount for invoice {instance.invoice_id}: {e}")


@receiver(post_save, sender=Transaction)
def recalculate_on_transaction_save(sender, instance, **kwargs):
    """При создании/изменении транзакции пересчитать балансы и paid_amount."""
    if getattr(instance, '_skip_balance_recalc', False):
        return
    _recalc_transaction_effects(instance)


@receiver(post_delete, sender=Transaction)
def recalculate_on_transaction_delete(sender, instance, **kwargs):
    """При удалении транзакции пересчитать балансы и paid_amount."""
    _recalc_transaction_effects(instance)


# ============================================================================
# СИГНАЛЫ ДЛЯ EMAIL-УВЕДОМЛЕНИЙ КЛИЕНТОВ
# ============================================================================


# NOTE: save_old_notification_values for Container was merged into save_old_container_values above


@receiver(post_save, sender=Container)
def send_container_notifications_on_save(sender, instance, created, **kwargs):
    """
    Автоматически отправляет уведомления клиентам:
    - При установке planned_unload_date -> уведомление о планируемой разгрузке
    - При установке unload_date -> уведомление о фактической разгрузке
    """
    if not instance.pk:
        return
    
    # Получаем старые значения с экземпляра
    old_values = getattr(instance, '_pre_save_notification', None) or {}
    instance._pre_save_notification = None  # очищаем после использования
    old_planned_unload_date = old_values.get('planned_unload_date')
    old_unload_date = old_values.get('unload_date')
    
    # Проверяем нужно ли отправить уведомление о планируемой разгрузке
    should_notify_planned = False
    if instance.planned_unload_date:
        if created:
            should_notify_planned = True
        elif old_planned_unload_date is None:
            # Планируемая дата разгрузки была установлена впервые
            should_notify_planned = True
    
    # Проверяем нужно ли отправить уведомление о фактической разгрузке
    should_notify_unload = False
    if instance.unload_date:
        if created:
            should_notify_unload = True
        elif old_unload_date is None:
            # Дата разгрузки была установлена впервые
            should_notify_unload = True
    
    # Отправляем уведомления асинхронно через Celery после коммита транзакции
    if should_notify_planned:
        def _enqueue_planned():
            try:
                from core.tasks import send_planned_notifications_task
                send_planned_notifications_task.delay(instance.pk)
            except Exception:
                # Fallback: synchronous send if Celery unavailable
                from core.services.email_service import ContainerNotificationService
                if not ContainerNotificationService.was_planned_notification_sent(instance):
                    ContainerNotificationService.send_planned_to_all_clients(instance)

        transaction.on_commit(_enqueue_planned)

    if should_notify_unload:
        def _enqueue_unload():
            try:
                from core.tasks import send_unload_notifications_task
                send_unload_notifications_task.delay(instance.pk)
            except Exception:
                # Fallback: synchronous send if Celery unavailable
                from core.services.email_service import ContainerNotificationService
                if not ContainerNotificationService.was_unload_notification_sent(instance):
                    ContainerNotificationService.send_unload_to_all_clients(instance)

        transaction.on_commit(_enqueue_unload)


# ============================================================================
# СИГНАЛЫ ДЛЯ EMAIL-УВЕДОМЛЕНИЙ О РАЗГРУЗКЕ ОТДЕЛЬНЫХ ТС (БЕЗ КОНТЕЙНЕРА)
# ============================================================================


# NOTE: save_old_car_notification_values was merged into save_old_car_values above


@receiver(post_save, sender=Car)
def send_car_unload_notification_on_save(sender, instance, created, **kwargs):
    """
    Автоматически отправляет уведомление клиенту при разгрузке ТС без контейнера.
    Срабатывает когда unload_date устанавливается впервые для ТС без контейнера.
    """
    if not instance.pk or instance.container_id:
        return
    
    old_values = getattr(instance, '_pre_save_car_notification', None) or {}
    instance._pre_save_car_notification = None
    
    old_container_id = old_values.get('container_id')
    if old_container_id:
        return
    
    old_unload_date = old_values.get('unload_date')
    
    should_notify = False
    if instance.unload_date:
        if created:
            should_notify = True
        elif old_unload_date is None:
            should_notify = True
    
    if should_notify:
        def _enqueue_car_unload():
            try:
                from core.tasks import send_car_unload_notification_task
                send_car_unload_notification_task.delay(instance.pk)
            except Exception:
                from core.services.email_service import CarNotificationService
                if not CarNotificationService.was_car_unload_notification_sent(instance):
                    CarNotificationService.send_car_unload_notification(instance)

        transaction.on_commit(_enqueue_car_unload)


# Сигнал для автоматической синхронизации фотографий с Google Drive
@receiver(post_save, sender=Container)
def auto_sync_photos_on_container_change(sender, instance, created, **kwargs):
    """
    Автоматическая синхронизация фотографий перенесена в регулярный cron.
    Логика: через 12 часов после статуса "Разгружен" и затем каждый час.
    """
    if not instance.pk:
        return
    
    if instance.status == 'UNLOADED':
        logger.info(
            f"📸 Контейнер {instance.number}: статус UNLOADED. "
            "Синхронизация будет выполнена по крону (через 12 часов и далее каждый час)."
        )


# ==============================================================================
# 🚛 СИГНАЛЫ ДЛЯ АВТОВОЗОВ
# ==============================================================================

@receiver(post_save, sender='core.AutoTransport')
def autotransport_post_save(sender, instance, created, **kwargs):
    """
    При сохранении автовоза:
    - FORMED: создаем/обновляем инвойсы
    - LOADED/IN_TRANSIT/DELIVERED: все авто → статус TRANSFERRED + дата передачи
    """
    if instance.status == 'FORMED':
        try:
            invoices = instance.generate_invoices()
            if invoices:
                logger.info(f"🚛 Автовоз {instance.number}: создано/обновлено инвойсов: {len(invoices)}")
        except Exception as e:
            logger.error(f"🚛 Ошибка при создании инвойсов для автовоза {instance.number}: {e}")

    # При переходе в LOADED/IN_TRANSIT/DELIVERED — передать все авто
    if instance.status in ('LOADED', 'IN_TRANSIT', 'DELIVERED'):
        transfer_date = getattr(instance, '_transfer_date_override', None)
        _mark_cars_as_transferred(instance, transfer_date)


def _mark_cars_as_transferred(autotransport, transfer_date=None):
    """Помечает все авто автовоза как переданные с указанной датой (bulk)."""
    from django.utils import timezone as tz
    if transfer_date is None:
        transfer_date = tz.now().date()

    count = autotransport.cars.exclude(status='TRANSFERRED').update(
        status='TRANSFERRED',
        transfer_date=transfer_date
    )
    if count:
        logger.info(
            f"Autotransport {autotransport.number}: {count} cars -> TRANSFERRED "
            f"(transfer date: {transfer_date})"
        )


# Сигнал для изменения автомобилей в автовозе будет подключен после инициализации моделей
def autotransport_cars_changed_handler(sender, instance, action, **kwargs):
    """
    При изменении списка автомобилей в автовозе обновляем инвойсы
    """
    if action in ['post_add', 'post_remove', 'post_clear']:
        # Обновляем только если автовоз уже сформирован
        if instance.status == 'FORMED':
            try:
                invoices = instance.generate_invoices()
                if invoices:
                    logger.info(f"🚛 Автовоз {instance.number}: инвойсы обновлены после изменения списка авто")
            except Exception as e:
                logger.error(f"🚛 Ошибка при обновлении инвойсов для автовоза {instance.number}: {e}")


# Подключаем сигнал после инициализации моделей
def connect_autotransport_signals():
    """Подключение сигналов для автовозов"""
    try:
        from .models import AutoTransport
        m2m_changed.connect(autotransport_cars_changed_handler, sender=AutoTransport.cars.through)
        logger.info("🚛 Сигналы для автовозов подключены")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось подключить сигналы для автовозов: {e}")


# Вызываем подключение после импорта всех моделей
from django.apps import apps
if apps.ready:
    connect_autotransport_signals()
else:
    # Если модели еще не готовы, подключим при готовности приложения
    from django.db.models.signals import post_migrate
    
    def setup_autotransport_signals(sender, **kwargs):
        if sender.name == 'core':
            connect_autotransport_signals()
    
    post_migrate.connect(setup_autotransport_signals)