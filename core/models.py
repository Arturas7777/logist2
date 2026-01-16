from django.db import models
from django.core.validators import MinValueValidator
from .constants import STATUS_COLORS
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.db.models import Sum, Q
from django.db import transaction
from decimal import Decimal
import logging

from datetime import timedelta

# Импортируем оптимизированные менеджеры
from .managers import (
    OptimizedCarManager, OptimizedContainerManager, OptimizedClientManager, 
    OptimizedWarehouseManager, OptimizedCompanyManager
)



logger = logging.getLogger('django')

def get_current_user():
    """Получить текущего пользователя"""
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.auth import get_user
    
    try:
        user = get_user()
        if user.is_authenticated:
            return user.username
        return 'system'
    except:
        return 'system'

# Базовый менеджер для управления обновлениями
class BaseManager(models.Manager):
    def update_related(self, instance):
        pass

# Новые модели для системы балансов



# Справочники
class Line(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название линии")
    
    # Единый баланс (новая система)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Баланс",
                                  help_text="Положительный = нам должны, отрицательный = мы должны")
    balance_updated_at = models.DateTimeField(auto_now=True, verbose_name="Баланс обновлен")
    
    # Услуги и цены
    ocean_freight_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость перевозки (за авто)")
    documentation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость документов")
    handling_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость обработки")
    ths_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="THS сбор (оплата линиям)")
    additional_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Дополнительные сборы")

    class Meta:
        verbose_name = "Линия"
        verbose_name_plural = "Линии"

    def __str__(self):
        return self.name


class LineTHSPercent(models.Model):
    """Процент от THS для типа ТС у конкретной линии.
    
    Используется для распределения общей суммы THS контейнера между ТС
    пропорционально их типам. Проценты нормируются до 100%.
    """
    # Типы ТС дублируем здесь чтобы избежать циклического импорта
    VEHICLE_TYPE_CHOICES = [
        ('SEDAN', 'Легковой'),
        ('CROSSOVER', 'Кроссовер'),
        ('SUV', 'Джип'),
        ('PICKUP', 'Пикап'),
        ('NEW_CAR', 'Новая машина'),
        ('MOTO', 'Мотоцикл'),
        ('BIG_MOTO', 'Большой мотоцикл'),
        ('ATV', 'Квадроцикл/Багги'),
        ('BOAT', 'Лодка'),
        ('RV', 'Автодом (RV)'),
        ('CONSTRUCTION', 'Стр. техника'),
    ]
    
    line = models.ForeignKey(Line, on_delete=models.CASCADE, related_name='ths_percents', verbose_name="Линия")
    vehicle_type = models.CharField(max_length=20, choices=VEHICLE_TYPE_CHOICES, verbose_name="Тип ТС")
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=25.00, 
                                  verbose_name="Процент от THS",
                                  help_text="Процент от общей суммы THS для данного типа ТС")
    
    class Meta:
        verbose_name = "Процент THS для типа ТС"
        verbose_name_plural = "Проценты THS для типов ТС"
        unique_together = ['line', 'vehicle_type']
    
    def __str__(self):
        return f"{self.line.name} - {self.get_vehicle_type_display()}: {self.percent}%"


class Carrier(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название перевозчика")
    short_name = models.CharField(max_length=20, blank=True, null=True, verbose_name="Короткое название")
    contact_person = models.CharField(max_length=100, blank=True, null=True, verbose_name="Контактное лицо")
    phone = models.CharField(max_length=20, blank=True, null=True, verbose_name="Телефон")
    email = models.EmailField(blank=True, null=True, verbose_name="Email")
    
    # Единый баланс (новая система)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Баланс",
                                  help_text="Положительный = нам должны, отрицательный = мы должны")
    balance_updated_at = models.DateTimeField(auto_now=True, verbose_name="Баланс обновлен")
    
    # Услуги и цены
    transport_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость перевозки (за км)")
    loading_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость погрузки")
    unloading_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость разгрузки")
    fuel_surcharge = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Топливная надбавка")
    additional_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Дополнительные сборы")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    
    objects = OptimizedCompanyManager()
    
    class Meta:
        verbose_name = "Перевозчик"
        verbose_name_plural = "Перевозчики"
    
    def __str__(self):
        return self.name


class Client(models.Model):
    name = models.CharField(max_length=100, verbose_name="Имя клиента")
    email = models.EmailField(blank=True, null=True, verbose_name="Email 1",
                              help_text="Основной email для уведомлений о разгрузке контейнеров")
    email2 = models.EmailField(blank=True, null=True, verbose_name="Email 2",
                               help_text="Дополнительный email для уведомлений")
    email3 = models.EmailField(blank=True, null=True, verbose_name="Email 3",
                               help_text="Дополнительный email для уведомлений")
    email4 = models.EmailField(blank=True, null=True, verbose_name="Email 4",
                               help_text="Дополнительный email для уведомлений")
    notification_enabled = models.BooleanField(default=True, verbose_name="Получать уведомления",
                                               help_text="Отправлять email-уведомления о контейнерах")

    # Единый баланс (новая система)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Баланс", 
                                   help_text="Положительный = переплата, отрицательный = долг")
    balance_updated_at = models.DateTimeField(auto_now=True, verbose_name="Баланс обновлен")
    
    objects = OptimizedClientManager()
    
    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"
    
    def __str__(self):
        return self.name
    
    def get_notification_emails(self):
        """
        Возвращает список всех заполненных email-адресов для уведомлений.
        Пустые и None значения исключаются.
        """
        emails = []
        for field in [self.email, self.email2, self.email3, self.email4]:
            if field and field.strip():
                emails.append(field.strip())
        return emails
    
    def has_notification_emails(self):
        """Проверяет, есть ли хотя бы один email для уведомлений"""
        return len(self.get_notification_emails()) > 0
    
    @property
    def balance_status(self):
        """Статус баланса для отображения"""
        if self.balance > 0:
            return "ПЕРЕПЛАТА"
        elif self.balance < 0:
            return "ДОЛГ"
        return "БАЛАНС"
    
    @property
    def balance_color(self):
        """Цвет для отображения баланса"""
        if self.balance > 0:
            return "#28a745"  # зеленый для переплаты
        elif self.balance < 0:
            return "#dc3545"  # красный для долга
        return "#6c757d"  # серый для нуля

class Warehouse(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название склада")
    address = models.CharField(max_length=300, blank=True, verbose_name="Адрес склада")
    
    # Единый баланс (новая система)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Баланс",
                                  help_text="Положительный = нам должны, отрицательный = мы должны")
    balance_updated_at = models.DateTimeField(auto_now=True, verbose_name="Баланс обновлен")

    # Цены на услуги
    default_unloading_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Цена за разгрузку")
    free_days = models.PositiveIntegerField(default=0, verbose_name="Бесплатные дни")
    # УДАЛЕНО: rate - ставка за хранение теперь берётся из услуги "Хранение" (WarehouseService)
    complex_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Комплекс",validators=[MinValueValidator(0)])
    delivery_to_warehouse = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Доставка до склада")
    loading_on_trawl = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Погрузка на трал")
    documents_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Документы")
    transfer_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Плата за передачу")
    transit_declaration = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Транзитная декл.")
    export_declaration = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Экспортная декл.")
    additional_expenses = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Доп.расходы")

    objects = OptimizedWarehouseManager()
    
    class Meta:
        verbose_name = "Склад"
        verbose_name_plural = "Склады"

    def __str__(self):
        return self.name

# Контейнеры
class ContainerManager(BaseManager):
    def update_related(self, instance):
        cars = instance.container_cars.all()
        if not cars:
            return
        ths_per_car = (instance.ths or 0) / cars.count()
        for car in cars:
            car.sync_with_container(instance, ths_per_car)
            car.save()

class Container(models.Model):
    STATUS_CHOICES = [
        ('FLOATING', 'В пути'),
        ('IN_PORT', 'В порту'),
        ('UNLOADED', 'Разгружен'),
        ('TRANSFERRED', 'Передан'),
    ]

    def get_status_color(self):
        return STATUS_COLORS.get(self.status, '#3a8c3d')  # Темнее зелёного по умолчанию

    CUSTOMS_PROCEDURE_CHOICES = (
        ('TRANSIT', 'Транзит'),
        ('IMPORT', 'Импорт'),
        ('REEXPORT', 'Реэкспорт'),
        ('EXPORT', 'Экспорт'),
    )

    number = models.CharField(max_length=100, unique=True, verbose_name="Номер контейнера")
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='FLOATING', verbose_name="Статус")
    line = models.ForeignKey('Line', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Морская линия")
    eta = models.DateField(null=True, blank=True, verbose_name="ETA")
    client = models.ForeignKey('Client', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Клиент")
    customs_procedure = models.CharField(max_length=20, choices=CUSTOMS_PROCEDURE_CHOICES, null=True, blank=True,
                                         verbose_name="Таможенная процедура")
    THS_PAYER_CHOICES = [
        ('LINE', 'Напрямую линии'),
        ('WAREHOUSE', 'Через склад'),
    ]
    
    ths = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Оплата линиям",
                              validators=[MinValueValidator(0)])
    ths_payer = models.CharField(max_length=20, choices=THS_PAYER_CHOICES, default='LINE',
                                 verbose_name="Оплата THS через",
                                 help_text="От чьего имени записать расход THS в карточках ТС: напрямую линии или через склад")
    sklad = models.DecimalField(max_digits=10, decimal_places=2, default=160, verbose_name="Оплата складу",
                                validators=[MinValueValidator(0)])
    dekl = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Декларация",
                               validators=[MinValueValidator(0)])
    proft = models.DecimalField(max_digits=10, decimal_places=2, default=20, verbose_name="Наценка",
                                validators=[MinValueValidator(0)])
    warehouse = models.ForeignKey('Warehouse', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Склад")
    planned_unload_date = models.DateField(null=True, blank=True, verbose_name="Будем разгружать",
                                           help_text="Укажите когда планируете разгружать контейнер (клиенты получат уведомление)")
    unload_date = models.DateField(null=True, blank=True, verbose_name="Дата разгрузки")
    free_days = models.PositiveIntegerField(default=0, verbose_name="Бесплатные дни")
    days = models.PositiveIntegerField(default=0, verbose_name="Платные дни")
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=5, verbose_name="Ставка",
                               validators=[MinValueValidator(0)])
    storage_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Складирование")
    notes = models.CharField(max_length=200, blank=True, verbose_name="Примечания")
    
    # Google Drive integration
    google_drive_folder_url = models.URLField(max_length=500, blank=True, verbose_name="Google Drive папка", 
                                               help_text="Прямая ссылка на папку с фотографиями контейнера в Google Drive")

    objects = OptimizedContainerManager()
    # Сохраняем старый менеджер для совместимости
    legacy_objects = ContainerManager()

    def update_days_and_storage(self):
        if self.status == 'UNLOADED' and self.unload_date:
            total_days = (timezone.now().date() - self.unload_date).days + 1
            self.days = max(0, total_days - self.free_days)
            self.storage_cost = self.days * (self.rate or 0)
        else:
            self.days = 0
            self.storage_cost = 0

    def sync_cars(self):
        self.update_days_and_storage()
        Container.objects.update_related(self)

    def save(self, *args, **kwargs):
        if self.status == 'UNLOADED' and (not self.warehouse or not self.unload_date):
            raise ValueError("Для статуса 'Разгружен' обязательны поля 'Склад' и 'Дата разгрузки'")
        super().save(*args, **kwargs)

    def sync_cars_after_warehouse_change(self):
        """
        Применяет новый склад ко всем авто контейнера:
        - ставит warehouse
        - жёстко перезаписывает все складские поля дефолтами нового склада
        - дата разгрузки ВСЕГДА наследуется из контейнера (принудительно)
        - пересчитывает хранение и суммы
        """
        # Проверяем, что у экземпляра есть первичный ключ
        if not self.pk:
            return
            
        for car in self.container_cars.all():
            car.warehouse = self.warehouse
            car.apply_warehouse_defaults(force=True)  # перезаписать rate/free_days и прочее
            # Дата разгрузки ВСЕГДА наследуется из контейнера (принудительно)
            if self.unload_date:
                car.unload_date = self.unload_date
                logger.debug(f"Car {car.vin}: forced unload_date={self.unload_date} from container {self.number}")
            car.update_days_and_storage()
            car.calculate_total_price()
            car.save()

    def sync_cars_after_edit(self):
        """
        Обновляет поля машин после изменения контейнера:
        — проставляет склад/клиента, если у авто они пустые,
        — дата разгрузки ВСЕГДА берется из контейнера (принудительное наследование),
        — подтягивает дефолты склада (rate/free_days/и т.д.) при пустых/дефолтных значениях,
        — пересчитывает хранение и цены.
        """
        # Проверяем, что у экземпляра есть первичный ключ
        if not self.pk:
            return
            
        from .models import Car  # если файл общий, импорт не обязателен
        for car in self.container_cars.all():
            changed = False

            # базовые связки
            if not car.warehouse and self.warehouse:
                car.warehouse = self.warehouse
                changed = True
            if not car.client and self.client:
                car.client = self.client
                changed = True
            
            # Дата разгрузки ВСЕГДА наследуется из контейнера (принудительно)
            if self.unload_date:
                if car.unload_date != self.unload_date:
                    car.unload_date = self.unload_date
                    changed = True
                    logger.info(f"Car {car.vin}: forced unload_date update to {self.unload_date} from container {self.number}")

            # подтянуть дефолты со склада (перезаписать только пустые/дефолтные)
            if car.warehouse:
                before_rate = car.rate
                before_free = car.free_days
                car.apply_warehouse_defaults(override_on_defaults=True)
                changed = changed or (car.rate != before_rate or car.free_days != before_free)

            # пересчёт
            car.update_days_and_storage()
            car.calculate_total_price()

            if changed:
                car.save()  # сохранит и отправит WS-обновление, если у тебя это в save()
            else:
                # всё равно сохраним, если изменилась стоимость/дни из-за новой даты
                car.save(update_fields=['storage_cost', 'days', 'total_price'])

    def check_and_update_status_from_cars(self):
        """Проверяет статус всех автомобилей в контейнере и обновляет статус контейнера"""
        if not self.pk:
            return
            
        cars = self.container_cars.all()
        if not cars.exists():
            return
            
        # Проверяем, все ли автомобили имеют статус "Передан"
        all_transferred = all(car.status == 'TRANSFERRED' for car in cars)
        
        if all_transferred and self.status != 'TRANSFERRED':
            self.status = 'TRANSFERRED'
            self.save(update_fields=['status'])
            logger.info(f"Container {self.number} status automatically changed to TRANSFERRED")

    def __str__(self):
        return self.number

    class Meta:
        verbose_name = "Контейнер"
        verbose_name_plural = "Контейнеры"
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['client', 'status']),
            models.Index(fields=['warehouse', 'status']),
            models.Index(fields=['line']),
            models.Index(fields=['eta']),
            models.Index(fields=['unload_date']),
        ]

# Автомобили
class CarManager(BaseManager):
    pass


class Car(models.Model):
    # Типы транспортных средств (расширенный список)
    VEHICLE_TYPE_CHOICES = [
        ('SEDAN', 'Легковой'),
        ('CROSSOVER', 'Кроссовер'),
        ('SUV', 'Джип'),
        ('PICKUP', 'Пикап'),
        ('NEW_CAR', 'Новая машина'),
        ('MOTO', 'Мотоцикл'),
        ('BIG_MOTO', 'Большой мотоцикл'),
        ('ATV', 'Квадроцикл/Багги'),
        ('BOAT', 'Лодка'),
        ('RV', 'Автодом (RV)'),
        ('CONSTRUCTION', 'Стр. техника'),
    ]
    
    year = models.PositiveIntegerField(verbose_name="Год выпуска")
    brand = models.CharField(max_length=50, verbose_name="Марка")
    vehicle_type = models.CharField(max_length=20, choices=VEHICLE_TYPE_CHOICES, default='SEDAN', verbose_name="Тип ТС")
    vin = models.CharField(max_length=17, unique=True, verbose_name="VIN")
    client = models.ForeignKey('Client', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Клиент")
    status = models.CharField(max_length=20, choices=Container.STATUS_CHOICES, verbose_name="Статус")
    warehouse = models.ForeignKey('Warehouse', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Склад")
    line = models.ForeignKey('Line', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Линия")
    carrier = models.ForeignKey('Carrier', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Перевозчик")
    unload_date = models.DateField(null=True, blank=True, verbose_name="Дата разгрузки")
    transfer_date = models.DateField(null=True, blank=True, verbose_name="Дата передачи")
    has_title = models.BooleanField(default=False, verbose_name="Тайтл у нас")
    title_notes = models.CharField(max_length=200, blank=True, verbose_name="Примечания к тайтлу")
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Цена")
    # УДАЛЕНО: current_price - теперь используется только total_price
    storage_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Складирование")
    days = models.PositiveIntegerField(default=0, verbose_name="Платные дни")
    container = models.ForeignKey('Container', on_delete=models.CASCADE, related_name="container_cars", null=True, blank=True, verbose_name="Контейнер")

    # Расходы
    ths = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Оплата линиям", validators=[MinValueValidator(0)])
    unload_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Цена за разгрузку", validators=[MinValueValidator(0)])
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Доставка до склада", validators=[MinValueValidator(0)])
    loading_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Погрузка на трал", validators=[MinValueValidator(0)])
    docs_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Документы", validators=[MinValueValidator(0)])
    transfer_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Плата за передачу", validators=[MinValueValidator(0)])
    transit_declaration = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Транзитная декл.", validators=[MinValueValidator(0)])
    export_declaration = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Экспортная декл.", validators=[MinValueValidator(0)])
    extra_costs = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Доп.расходы", validators=[MinValueValidator(0)])
    dekl = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Декларация", validators=[MinValueValidator(0)])
    proft = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('20.00'), null=True, blank=True, verbose_name="Наценка", validators=[MinValueValidator(0)])
    free_days = models.PositiveIntegerField(default=0, verbose_name="Бесплатные дни")
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=5, verbose_name="Ставка за сутки", validators=[MinValueValidator(0)])
    complex_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Комплекс", validators=[MinValueValidator(0)])
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Цена", validators=[MinValueValidator(0)])
    auction_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Аукционный сбор", validators=[MinValueValidator(0)])
    transport_usa = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Транспорт США", validators=[MinValueValidator(0)])
    ocean_freight = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Океанский фрахт", validators=[MinValueValidator(0)])
    transport_kz = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Транспорт КЗ", validators=[MinValueValidator(0)])
    broker_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Брокерский сбор", validators=[MinValueValidator(0)])
    additional_expenses = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Дополнительные расходы", validators=[MinValueValidator(0)])

    objects = OptimizedCarManager()
    # Сохраняем старый менеджер для совместимости
    legacy_objects = CarManager()

    def get_status_color(self):
        return STATUS_COLORS.get(self.status, '#3a8c3d')

    def apply_warehouse_defaults(self, force: bool = False):
        """
        Копирует дефолты со склада в авто из кастомных услуг.
        force=True — перезаписывает ВСЕ соответствующие поля значениями склада.
        force=False — перезаписывает только если поле пустое или равно дефолту модели.
        """
        if not self.warehouse:
            return

        from decimal import Decimal

        # Получаем кастомные услуги склада
        warehouse_services = WarehouseService.objects.filter(warehouse=self.warehouse, is_active=True)
        
        # Маппинг названий услуг на поля автомобиля
        service_mapping = {
            'Цена за разгрузку': 'unload_fee',
            'Доставка до склада': 'delivery_fee', 
            'Погрузка на трал': 'loading_fee',
            'Документы': 'docs_fee',
            'Плата за передачу': 'transfer_fee',
            'Транзитная декл.': 'transit_declaration',
            'Экспортная декл.': 'export_declaration',
            'Доп.расходы': 'extra_costs',
            'Комплекс': 'complex_fee',
            'Ставка за сутки': 'rate',
            'Бесплатные дни': 'free_days',
        }

        for service in warehouse_services:
            car_field = service_mapping.get(service.name)
            if car_field:
                wh_val = service.default_price or 0
                cur_val = getattr(self, car_field, None)

                if force:
                    setattr(self, car_field, wh_val)
                else:
                    if cur_val is None:
                        setattr(self, car_field, wh_val)
                    else:
                        try:
                            cur = Decimal(str(cur_val))
                            if cur == 0:
                                setattr(self, car_field, wh_val)
                            else:
                                model_default = self._meta.get_field(car_field).default
                                mdl = Decimal(str(model_default or 0))
                                if cur == mdl:
                                    setattr(self, car_field, wh_val)
                        except Exception:
                            setattr(self, car_field, wh_val)



    def warehouse_details(self):
        """Возвращает дефолтные цены на услуги из склада из кастомных услуг."""
        if not self.warehouse:
            return {"message": "Склад не назначен"}
        
        # Получаем кастомные услуги склада
        warehouse_services = WarehouseService.objects.filter(warehouse=self.warehouse, is_active=True)
        
        details = {"Название": self.warehouse.name}
        for service in warehouse_services:
            details[service.name] = str(service.default_price)
        
        return details

    def set_initial_warehouse_values(self):
        """Подтягивает дефолты со склада при создании авто.
        Если текущее значение = модельному дефолту (например, rate=5) или 0 — берём значение со склада.
        """
        if not self.warehouse:
            return

        initializing = self._state.adding

        def _override(cur_val, field_name, wh_val):
            if cur_val is None:
                return wh_val
            if initializing:
                # перетираем, если 0 или равно модельному дефолту
                try:
                    cur = Decimal(str(cur_val))
                    if cur == 0:
                        return wh_val
                    model_default = self._meta.get_field(field_name).default
                    mdl = Decimal(str(model_default or 0))
                    if cur == mdl:
                        return wh_val
                except Exception:
                    return wh_val
            return cur_val

        # ключевые поля
        self.rate = _override(self.rate, 'rate', self.warehouse.rate or 0)
        self.free_days = _override(self.free_days, 'free_days', self.warehouse.free_days or 0)

        # заодно остальные складские услуги, если нужно
        self.unload_fee = _override(self.unload_fee, 'unload_fee', self.warehouse.default_unloading_fee or 0)
        self.delivery_fee = _override(self.delivery_fee, 'delivery_fee', self.warehouse.delivery_to_warehouse or 0)
        self.loading_fee = _override(self.loading_fee, 'loading_fee', self.warehouse.loading_on_trawl or 0)
        self.docs_fee = _override(self.docs_fee, 'docs_fee', self.warehouse.documents_fee or 0)
        self.transfer_fee = _override(self.transfer_fee, 'transfer_fee', self.warehouse.transfer_fee or 0)
        self.transit_declaration = _override(self.transit_declaration, 'transit_declaration',
                                             self.warehouse.transit_declaration or 0)
        self.export_declaration = _override(self.export_declaration, 'export_declaration',
                                            self.warehouse.export_declaration or 0)
        self.extra_costs = _override(self.extra_costs, 'extra_costs', self.warehouse.additional_expenses or 0)
        self.complex_fee = _override(self.complex_fee, 'complex_fee', self.warehouse.complex_fee or 0)

    def calculate_total_price(self):
        """Пересчитывает цену используя систему услуг CarService.
        
        Цена = сумма всех услуг + наценка.
        После статуса TRANSFERRED цена фиксируется и не пересчитывается,
        пока статус не изменится обратно.
        """
        # Сначала обновляем дни и цену услуги "Хранение"
        self.update_days_and_storage()
        
        # Получаем суммы по поставщикам из CarService
        line_total = self.get_services_total_by_provider('LINE')
        carrier_total = self.get_services_total_by_provider('CARRIER')
        warehouse_total = self.get_warehouse_services_total()  # Включает услугу "Хранение"
        
        # Наценка Caromoto Lithuania из поля proft автомобиля
        markup_amount = self.proft or Decimal('0.00')
        
        # Общая сумма всех услуг + наценка
        self.total_price = line_total + warehouse_total + carrier_total + markup_amount

        return self.total_price

    def update_days_and_storage(self):
        """Обновляет платные дни и стоимость хранения для автомобиля.
        
        Цена за день берётся из услуги "Хранение" в списке услуг склада.
        Стоимость = платные_дни × цена_за_день
        """
        if not self.unload_date or not self.warehouse:
            self.days = 0
            self.storage_cost = Decimal('0.00')
            self._update_storage_service_price()
            return

        # Бесплатные дни из настроек склада
        free_days = int(self.warehouse.free_days or 0)

        end_date = self.transfer_date if self.status == 'TRANSFERRED' and self.transfer_date else timezone.now().date()
        total_days = (end_date - self.unload_date).days + 1
        self.days = max(0, total_days - free_days)
        
        # Получаем ставку из услуги "Хранение"
        daily_rate = self._get_storage_daily_rate()
        self.storage_cost = Decimal(str(self.days)) * daily_rate
        
        # Обновляем цену услуги "Хранение" в CarService
        self._update_storage_service_price()
    
    def _get_storage_daily_rate(self):
        """Получает ставку хранения за день из услуги 'Хранение' склада."""
        if not self.warehouse:
            return Decimal('0.00')
        
        try:
            storage_service = WarehouseService.objects.filter(
                warehouse=self.warehouse,
                name='Хранение',
                is_active=True
            ).first()
            
            if storage_service:
                return Decimal(str(storage_service.default_price or 0))
        except Exception:
            pass
        
        return Decimal('0.00')
    
    def _update_storage_service_price(self):
        """Обновляет цену услуги 'Хранение' в CarService.
        
        Цена = платные_дни × ставка_за_день (из WarehouseService)
        """
        if not self.pk or not self.warehouse:
            return
        
        try:
            # Находим услугу "Хранение" для этого склада
            storage_service = WarehouseService.objects.filter(
                warehouse=self.warehouse,
                name='Хранение',
                is_active=True
            ).first()
            
            if storage_service:
                # Стоимость = платные_дни × цена_за_день
                storage_price = Decimal(str(self.days)) * Decimal(str(storage_service.default_price or 0))
                
                # Обновляем цену в CarService напрямую в базе
                from core.models import CarService
                CarService.objects.filter(
                    car=self,
                    service_type='WAREHOUSE',
                    service_id=storage_service.id
                ).update(custom_price=storage_price)
                
                # Сбрасываем prefetch кэш чтобы получить актуальные данные
                if hasattr(self, '_prefetched_objects_cache'):
                    self._prefetched_objects_cache.pop('car_services', None)
        except Exception:
            pass  # Игнорируем ошибки - модель может быть ещё не сохранена

    def sync_with_container(self, container, ths_per_car):
        """Синхронизирует данные автомобиля с контейнером."""
        self.status = container.status
        self.warehouse = container.warehouse
        self.unload_date = container.unload_date
        self.transfer_date = timezone.now().date() if container.status == 'TRANSFERRED' else None
        self.ths = ths_per_car
        self.dekl = container.dekl
        self.proft = container.proft
        self.set_initial_warehouse_values()
        self.update_days_and_storage()
        self.calculate_total_price()

    WAREHOUSE_FEE_FIELDS = (
        'unload_fee',  # цена за разгрузку
        'delivery_fee',  # доставка до склада
        'loading_fee',  # погрузка на трал
        'docs_fee',  # документы
        'transfer_fee',  # плата за передачу
        'transit_declaration',  # транзитная декл.
        'export_declaration',  # экспортная декл.
        'extra_costs',  # доп.расходы
        'complex_fee',  # комплекс
    )

    def warehouse_payment_amount(self) -> Decimal:
        """Сколько должны складу за услуги (без учёта хранения) - использует новую систему услуг."""
        return self.get_warehouse_services_total()

    @property
    def warehouse_payment(self) -> Decimal:
        # удобное свойство, если где-то понадобится
        return self.warehouse_payment_amount()
    
    def get_line_services(self):
        """Получает услуги линии для этого автомобиля"""
        if not self.line or not self.pk:
            return self.car_services.none()
        # Получаем ID услуг линии
        line_service_ids = LineService.objects.only('id').filter(line=self.line).values_list('id', flat=True)
        return self.car_services.filter(service_type='LINE', service_id__in=line_service_ids)
    
    def get_carrier_services(self):
        """Получает услуги перевозчика для этого автомобиля"""
        if not self.carrier or not self.pk:
            return self.car_services.none()
        # Получаем ID услуг перевозчика
        carrier_service_ids = CarrierService.objects.only('id').filter(carrier=self.carrier).values_list('id', flat=True)
        return self.car_services.filter(service_type='CARRIER', service_id__in=carrier_service_ids)
    
    def get_warehouse_services(self):
        """Получает все услуги складов для этого автомобиля (включая услуги от других складов)"""
        if not self.pk:
            return self.car_services.none()
        # Получаем ВСЕ услуги складов, привязанные к этому автомобилю
        return self.car_services.filter(service_type='WAREHOUSE')
    
    def get_services_total_by_provider(self, provider_type):
        """Получает общую стоимость услуг по типу поставщика.
        
        Для склада: стоимость хранения уже включена в услугу "Хранение" (CarService).
        """
        total = Decimal('0.00')
        if provider_type == 'LINE' and self.line:
            services = self.get_line_services()
        elif provider_type == 'CARRIER' and self.carrier:
            services = self.get_carrier_services()
        elif provider_type == 'WAREHOUSE' and self.warehouse:
            services = self.get_warehouse_services()
        else:
            return total
            
        for service in services:
            total += Decimal(str(service.final_price))
        return total
    
    def get_warehouse_services_total(self):
        """Получает стоимость только услуг склада (без хранения)"""
        if not self.warehouse:
            return Decimal('0.00')
        
        services = self.get_warehouse_services()
        total = Decimal('0.00')
        
        for service in services:
            total += Decimal(str(service.final_price))
        
        return total
    
    def calculate_storage_cost(self):
        """Рассчитывает стоимость хранения на складе.
        
        Ставка берётся из услуги "Хранение" в списке услуг склада.
        """
        if not self.warehouse or not self.unload_date:
            return Decimal('0.00')
        
        # Получаем ставку из услуги "Хранение" и бесплатные дни со склада
        daily_rate = self._get_storage_daily_rate()
        free_days = self.warehouse.free_days or 0
        
        # Рассчитываем общее количество дней хранения
        # Включаем день разгрузки и день забора авто
        end_date = self.transfer_date if self.status == 'TRANSFERRED' and self.transfer_date else timezone.now().date()
        total_days = (end_date - self.unload_date).days + 1
        
        # Рассчитываем платные дни (общие дни минус бесплатные)
        chargeable_days = max(0, total_days - free_days)
        
        # Рассчитываем стоимость
        storage_cost = daily_rate * chargeable_days
        
        return storage_cost

    def get_rates_by_provider(self, provider_type):
        """Получает ставки по типу поставщика"""
        rates = []
        if provider_type == 'LINE' and self.line:
            services = self.get_line_services()
        elif provider_type == 'CARRIER' and self.carrier:
            services = self.get_carrier_services()
        elif provider_type == 'WAREHOUSE' and self.warehouse:
            services = self.get_warehouse_services()
        else:
            return rates
            
        # Пока возвращаем пустой список (когда добавим service_type, изменим логику)
        return rates

    def get_parameters_by_provider(self, provider_type):
        """Получает параметры расчета по типу поставщика"""
        parameters = []
        if provider_type == 'LINE' and self.line:
            services = self.get_line_services()
        elif provider_type == 'CARRIER' and self.carrier:
            services = self.get_carrier_services()
        elif provider_type == 'WAREHOUSE' and self.warehouse:
            services = self.get_warehouse_services()
        else:
            return parameters
            
        # Пока возвращаем пустой список (когда добавим service_type, изменим логику)
        return parameters

    def save(self, *args, **kwargs):
        # подхватить данные с контейнера ДО копирования дефолтов
        # Проверяем, что у контейнера есть первичный ключ
        if not self.warehouse and self.container and self.container.pk and self.container.warehouse:
            self.warehouse = self.container.warehouse
        
        # Дата разгрузки ВСЕГДА берется из контейнера (принудительное наследование)
        if self.container and self.container.pk and self.container.unload_date:
            self.unload_date = self.container.unload_date

        if self.transfer_date and self.status != 'TRANSFERRED':
            self.status = 'TRANSFERRED'

        # на создании — тянем дефолты склада (в т.ч. rate/free_days) ДО первого save()
        if self.pk is None and self.warehouse:
            try:
                self.set_initial_warehouse_values()
            except Exception as e:
                logger.error(f"Failed to set initial warehouse values for car {self.vin}: {e}")

        if self.status == 'TRANSFERRED' and not self.transfer_date:
            from django.utils import timezone
            self.transfer_date = timezone.now().date()

        # Сохраняем объект сначала, чтобы получить pk
        super().save(*args, **kwargs)
        
        # пересчёт ПОСЛЕ сохранения, когда у объекта уже есть pk
        try:
            old_total_price = self.total_price
            
            self.calculate_total_price()
            
            # Сохраняем еще раз с пересчитанной ценой, только если цена изменилась
            if self.total_price != old_total_price:
                super().save(update_fields=['total_price'])
        except Exception as e:
            logger.error(f"Failed to calculate total price for car {self.vin}: {e}")

        # Обновляем связанные объекты только если у автомобиля есть первичный ключ
        if self.pk:
            try:
                Car.objects.update_related(self)
            except Exception as e:
                logger.error(f"Failed to update related objects for car {self.id}: {e}")
            
            # Проверяем и обновляем статус контейнера, если все автомобили переданы
            if self.container and self.container.pk:
                try:
                    self.container.check_and_update_status_from_cars()
                except Exception as e:
                    logger.error(f"Failed to check container status for car {self.id}: {e}")

        def _notify():
            try:
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    "updates",
                    {
                        "type": "data_update",
                        "data": {
                            "model": "Car",
                            "id": self.id,
                            "status": self.status,
                            "storage_cost": str(self.storage_cost),
                            "days": self.days,
                            "price": str(self.total_price),
                        },
                    },
                )
            except Exception as e:
                logger.error(f"Failed to send WebSocket notification for car {self.id}: {e}")
        transaction.on_commit(_notify)

    def __str__(self):
        return f"{self.brand} ({self.vin})"

    class Meta:
        verbose_name = "Автомобиль"
        verbose_name_plural = "Автомобили"
        indexes = [
            models.Index(fields=['vin']),
            models.Index(fields=['status']),
            models.Index(fields=['unload_date', 'transfer_date']),
            # Дополнительные индексы для оптимизации запросов
            models.Index(fields=['client', 'status']),
            models.Index(fields=['warehouse', 'status']),
            models.Index(fields=['line']),
            models.Index(fields=['carrier']),
            models.Index(fields=['container']),
            models.Index(fields=['unload_date']),
            models.Index(fields=['transfer_date']),
        ]


class Company(models.Model):
    """Модель для логистической компании Caromoto Lithuania"""
    
    name = models.CharField(max_length=100, default="Caromoto Lithuania", verbose_name="Название компании")
    
    # Единый баланс (новая система)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Баланс",
                                  help_text="Положительный = нам должны, отрицательный = мы должны")
    balance_updated_at = models.DateTimeField(auto_now=True, verbose_name="Баланс обновлен")
    
    # Метаданные
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    
    objects = OptimizedCompanyManager()
    
    class Meta:
        verbose_name = "Компания"
        verbose_name_plural = "Компании"
    
    def __str__(self):
        return self.name

# Модели для системы услуг



class LineService(models.Model):
    """Услуги морских линий"""
    line = models.ForeignKey(Line, on_delete=models.CASCADE, related_name='services', verbose_name="Линия")
    name = models.CharField(max_length=200, verbose_name="Название услуги")
    description = models.TextField(blank=True, verbose_name="Описание")
    default_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Цена по умолчанию")
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    
    def __str__(self):
        return f"{self.line.name} - {self.name}"
    
    class Meta:
        verbose_name = "Услуга линии"
        verbose_name_plural = "Услуги линий"


class CarrierService(models.Model):
    """Услуги перевозчиков"""
    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name='services', verbose_name="Перевозчик")
    name = models.CharField(max_length=200, verbose_name="Название услуги")
    description = models.TextField(blank=True, verbose_name="Описание")
    default_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Цена по умолчанию")
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    
    def __str__(self):
        return f"{self.carrier.name} - {self.name}"
    
    class Meta:
        verbose_name = "Услуга перевозчика"
        verbose_name_plural = "Услуги перевозчиков"


class WarehouseService(models.Model):
    """Услуги складов"""
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='services', verbose_name="Склад")
    name = models.CharField(max_length=200, verbose_name="Название услуги")
    description = models.TextField(blank=True, verbose_name="Описание")
    default_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Цена по умолчанию")
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    add_by_default = models.BooleanField(default=False, verbose_name="Добавлять по умолчанию",
        help_text="Автоматически добавлять эту услугу при создании автомобиля на этом складе")
    
    def __str__(self):
        return f"{self.warehouse.name} - {self.name}"
    
    class Meta:
        verbose_name = "Услуга склада"
        verbose_name_plural = "Услуги складов"


class DeletedCarService(models.Model):
    """Отслеживание удаленных пользователем услуг для конкретного автомобиля"""
    car = models.ForeignKey(Car, on_delete=models.CASCADE, related_name='deleted_services', verbose_name="Автомобиль")
    service_type = models.CharField(max_length=20, choices=[
        ('LINE', 'Линия'),
        ('CARRIER', 'Перевозчик'),
        ('WAREHOUSE', 'Склад'),
    ], verbose_name="Тип поставщика")
    service_id = models.PositiveIntegerField(verbose_name="ID услуги")
    deleted_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата удаления")
    
    class Meta:
        unique_together = ['car', 'service_type', 'service_id']
        verbose_name = "Удаленная услуга автомобиля"
        verbose_name_plural = "Удаленные услуги автомобилей"


class CarService(models.Model):
    """Связь автомобиля с услугами и их ценами"""
    SERVICE_TYPES = [
        ('LINE', 'Линия'),
        ('CARRIER', 'Перевозчик'),
        ('WAREHOUSE', 'Склад'),
    ]
    
    car = models.ForeignKey(Car, on_delete=models.CASCADE, related_name='car_services', verbose_name="Автомобиль")
    service_type = models.CharField(max_length=20, choices=SERVICE_TYPES, verbose_name="Тип поставщика")
    service_id = models.PositiveIntegerField(verbose_name="ID услуги")
    custom_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Индивидуальная цена")
    quantity = models.PositiveIntegerField(default=1, verbose_name="Количество")
    notes = models.TextField(blank=True, verbose_name="Примечания")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    
    def __str__(self):
        return f"{self.car.vin} - {self.get_service_name()}: {self.final_price}"
    
    def get_service_name(self):
        """Получает название услуги"""
        if self.service_type == 'LINE':
            try:
                service = LineService.objects.get(id=self.service_id)
                return service.name
            except LineService.DoesNotExist:
                return "Услуга не найдена"
        elif self.service_type == 'CARRIER':
            try:
                service = CarrierService.objects.get(id=self.service_id)
                return service.name
            except CarrierService.DoesNotExist:
                return "Услуга не найдена"
        elif self.service_type == 'WAREHOUSE':
            try:
                service = WarehouseService.objects.get(id=self.service_id)
                return service.name
            except WarehouseService.DoesNotExist:
                return "Услуга не найдена"
        return "Неизвестная услуга"
    
    def get_default_price(self):
        """Получает цену по умолчанию"""
        if self.service_type == 'LINE':
            try:
                service = LineService.objects.get(id=self.service_id)
                return service.default_price
            except LineService.DoesNotExist:
                return 0
        elif self.service_type == 'CARRIER':
            try:
                service = CarrierService.objects.get(id=self.service_id)
                return service.default_price
            except CarrierService.DoesNotExist:
                return 0
        elif self.service_type == 'WAREHOUSE':
            try:
                service = WarehouseService.objects.get(id=self.service_id)
                return service.default_price
            except WarehouseService.DoesNotExist:
                return 0
        return 0
    
    @property
    def final_price(self):
        """Итоговая цена с учетом количества"""
        price = self.custom_price if self.custom_price else self.get_default_price()
        return price * self.quantity
    
    class Meta:
        verbose_name = "Услуга автомобиля"
        verbose_name_plural = "Услуги автомобилей"
        unique_together = ('car', 'service_type', 'service_id')
        indexes = [
            models.Index(fields=['car', 'service_type']),
            models.Index(fields=['service_type', 'service_id']),
            models.Index(fields=['car']),
        ]


# Сигналы для автоматического пересчета текущей цены при изменении услуг
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver


@receiver(post_save, sender=CarService)
def recalculate_car_price_on_service_save(sender, instance, **kwargs):
    """Пересчитывает текущую цену автомобиля при сохранении услуги"""
    # Защита от рекурсии - пропускаем если идёт создание услуг
    if getattr(instance.car, '_creating_services', False):
        return
    try:
        instance.car.calculate_total_price()
        # Используем update() вместо save() чтобы не триггерить сигналы
        Car.objects.filter(id=instance.car.id).update(
            total_price=instance.car.total_price
        )
    except Exception as e:
        print(f"Ошибка пересчета цены при сохранении услуги: {e}")


@receiver(post_delete, sender=CarService)
def recalculate_car_price_on_service_delete(sender, instance, **kwargs):
    """Пересчитывает текущую цену автомобиля при удалении услуги"""
    # Защита от рекурсии - пропускаем если идёт создание услуг
    if getattr(instance.car, '_creating_services', False):
        return
    try:
        instance.car.calculate_total_price()
        # Используем update() вместо save() чтобы не триггерить сигналы
        Car.objects.filter(id=instance.car.id).update(
            total_price=instance.car.total_price
        )
    except Exception as e:
        print(f"Ошибка пересчета цены при удалении услуги: {e}")


@receiver(post_save, sender=Car)
def recalculate_car_price_on_car_save(sender, instance, **kwargs):
    """Пересчитывает текущую цену автомобиля при изменении полей, влияющих на расчет"""
    # Защита от рекурсии
    if getattr(instance, '_recalculating_price', False):
        return
    if getattr(instance, '_creating_services', False):
        return
    
    try:
        # Пропускаем для новых объектов
        if hasattr(instance, '_state') and instance._state.adding:
            return
        
        # Пропускаем если это update_fields=['total_price'] - означает что цена уже пересчитана
        update_fields = kwargs.get('update_fields')
        if update_fields and 'total_price' in update_fields:
            return
        
        # Устанавливаем флаг защиты от рекурсии
        instance._recalculating_price = True
        
        try:
            instance.calculate_total_price()
            # Используем update() вместо save() чтобы не триггерить сигналы
            Car.objects.filter(id=instance.id).update(
                total_price=instance.total_price
            )
        finally:
            instance._recalculating_price = False
            
    except Exception as e:
        print(f"Ошибка пересчета цены при изменении автомобиля: {e}")


# ==============================================================================
# 🎉 НОВАЯ СИСТЕМА ИНВОЙСОВ И ПЛАТЕЖЕЙ
# ==============================================================================
# Импортируем новые модели, чтобы Django их видел

from .models_billing import (
    NewInvoice,
    InvoiceItem,
    Transaction,
    SimpleBalanceMixin
)

# ==============================================================================
# 🌐 МОДЕЛИ ДЛЯ КЛИЕНТСКОГО САЙТА
# ==============================================================================
# Импортируем модели для клиентского портала

from .models_website import (
    ClientUser,
    CarPhoto,
    ContainerPhoto,
    AIChat,
    NewsPost,
    ContactMessage,
    TrackingRequest
)