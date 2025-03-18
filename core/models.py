from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import json

# Базовый менеджер для управления обновлениями
class BaseManager(models.Manager):
    def update_related(self, instance):
        pass

# Справочники
class Line(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название линии")
    def __str__(self):
        return self.name

class Client(models.Model):
    name = models.CharField(max_length=100, verbose_name="Имя клиента")
    def __str__(self):
        return self.name

class Warehouse(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название склада")
    def __str__(self):
        return self.name

# Контейнеры
class ContainerManager(BaseManager):
    def update_related(self, instance):
        cars = instance.cars.all()
        if not cars:
            return
        ths_per_car = (instance.ths or 0) / cars.count()
        for car in cars:
            car.sync_with_container(instance, ths_per_car)
            car.save()

class Container(models.Model):
    STATUS_CHOICES = (
        ('FLOATING', 'Плывет'),
        ('IN_PORT', 'В порту'),
        ('UNLOADED', 'Разгружен'),
    )
    CUSTOMS_PROCEDURE_CHOICES = (
        ('TRANSIT', 'Транзит'),
        ('IMPORT', 'Импорт'),
        ('REEXPORT', 'Реэкспорт'),
        ('EXPORT', 'Экспорт'),
    )

    number = models.CharField(max_length=20, verbose_name="Номер контейнера", unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='FLOATING', verbose_name="Статус")
    line = models.ForeignKey('Line', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Морская линия")
    eta = models.DateField(null=True, blank=True, verbose_name="ETA")
    client = models.ForeignKey('Client', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Клиент")
    customs_procedure = models.CharField(max_length=20, choices=CUSTOMS_PROCEDURE_CHOICES, null=True, blank=True, verbose_name="Таможенная процедура")
    ths = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="THS", validators=[MinValueValidator(0)])
    sklad = models.DecimalField(max_digits=10, decimal_places=2, default=160, verbose_name="SKLAD", validators=[MinValueValidator(0)])
    dekl = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="DEKL", validators=[MinValueValidator(0)])
    prof = models.DecimalField(max_digits=10, decimal_places=2, default=20, verbose_name="PROF", validators=[MinValueValidator(0)])
    warehouse = models.ForeignKey('Warehouse', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Склад")
    unload_date = models.DateField(null=True, blank=True, verbose_name="Дата разгрузки")
    free_days = models.PositiveIntegerField(default=0, verbose_name="Бесплатные дни")
    days = models.PositiveIntegerField(default=0, verbose_name="DAYS")
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=5, verbose_name="Ставка", validators=[MinValueValidator(0)])
    storage_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Складирование")

    objects = ContainerManager()

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
        self.sync_cars()
        # Отправляем уведомление через WebSocket
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "updates",
            {
                "type": "data_update",
                "data": {"model": "Container", "id": self.id, "status": self.status}
            }
        )

    def __str__(self):
        return self.number

# Автомобили
class CarManager(BaseManager):
    def update_related(self, instance):
        if instance.container:
            instance.container.sync_cars()
        for invoice in instance.invoice_set.all():
            invoice.update_total_amount()
            invoice.save()

class Car(models.Model):
    year = models.PositiveIntegerField(verbose_name="Год выпуска")
    brand = models.CharField(max_length=50, verbose_name="Марка")
    vin = models.CharField(max_length=17, unique=True, verbose_name="VIN")
    client = models.ForeignKey('Client', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Клиент")
    status = models.CharField(max_length=20, choices=Container.STATUS_CHOICES, verbose_name="Статус")
    warehouse = models.ForeignKey('Warehouse', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Склад")
    unload_date = models.DateField(null=True, blank=True, verbose_name="Дата разгрузки")
    container = models.ForeignKey('Container', on_delete=models.CASCADE, related_name="cars", verbose_name="Контейнер")
    total_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Итоговая цена")
    ths = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="THS", validators=[MinValueValidator(0)])
    sklad = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="SKLAD", validators=[MinValueValidator(0)])
    dekl = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="DEKL", validators=[MinValueValidator(0)])
    prof = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="PROF", validators=[MinValueValidator(0)])
    free_days = models.PositiveIntegerField(default=0, verbose_name="Бесплатные дни")
    days = models.PositiveIntegerField(default=0, verbose_name="DAYS")
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=5, verbose_name="Ставка", validators=[MinValueValidator(0)])
    storage_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Складирование")

    objects = CarManager()

    def calculate_total_price(self):
        ths = self.ths or 0
        sklad = self.sklad or 0
        dekl = self.dekl or 0
        prof = self.prof or 0
        return ths + sklad + dekl + prof + self.storage_cost

    def update_days_and_storage(self):
        if self.status == 'UNLOADED' and self.unload_date:
            total_days = (timezone.now().date() - self.unload_date).days + 1
            self.days = max(0, total_days - self.free_days)
            self.storage_cost = self.days * (self.rate or 0)
        else:
            self.days = 0
            self.storage_cost = 0

    def sync_with_container(self, container, ths_per_car):
        self.status = container.status
        self.warehouse = container.warehouse
        self.unload_date = container.unload_date
        self.ths = ths_per_car
        self.sklad = container.sklad
        self.dekl = container.dekl
        self.prof = container.prof
        self.free_days = container.free_days
        self.rate = container.rate
        self.update_days_and_storage()
        self.total_price = self.calculate_total_price()

    def save(self, *args, **kwargs):
        if self.container and not self.client:
            self.client = self.container.client
        super().save(*args, **kwargs)
        Car.objects.update_related(self)
        # Отправляем уведомление через WebSocket
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "updates",
            {
                "type": "data_update",
                "data": {"model": "Car", "id": self.id, "status": self.status}
            }
        )

    def __str__(self):
        return f"{self.brand} ({self.vin})"

# Инвойсы
class Invoice(models.Model):
    number = models.CharField(max_length=20, unique=True, verbose_name="Номер инвойса")
    client = models.ForeignKey('Client', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Клиент")
    warehouse = models.ForeignKey('Warehouse', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Склад")
    cars = models.ManyToManyField(Car, verbose_name="Автомобили")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Сумма")
    issue_date = models.DateField(auto_now_add=True, verbose_name="Дата выпуска")
    paid = models.BooleanField(default=False, verbose_name="Оплачен")
    is_outgoing = models.BooleanField(default=False, verbose_name="Нужно оплатить")

    _updating = False  # Флаг для предотвращения рекурсии

    def update_total_amount(self):
        if self._updating:
            return
        self._updating = True
        try:
            if not self.is_outgoing:
                self.total_amount = sum(float(car.total_price or 0) for car in self.cars.all())
            else:
                self.total_amount = sum(float(car.storage_cost or 0) for car in self.cars.all())
            if self.pk:  # Сохраняем только для существующих объектов
                self.save(update_fields=['total_amount'])
        finally:
            self._updating = False

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self._updating:  # Вызываем только если не в процессе обновления
            self.update_total_amount()
        # Отправляем уведомление через WebSocket
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "updates",
            {
                "type": "data_update",
                "data": {"model": "Invoice", "id": self.id, "total_amount": str(self.total_amount)}
            }
        )

    def __str__(self):
        direction = "Нужно оплатить" if self.is_outgoing else "Ждём оплату"
        return f"{self.number} ({direction})"

# Платежи
class Payment(models.Model):
    TYPE_CHOICES = (
        ('CASH', 'Наличные'),
        ('CARD', 'Безналичные'),
    )
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Инвойс")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Сумма")
    payment_type = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name="Тип платежа")
    date = models.DateField(auto_now_add=True, verbose_name="Дата")
    payer = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Плательщик")
    recipient = models.CharField(max_length=100, verbose_name="Получатель")

    def __str__(self):
        return f"{self.amount} ({self.payment_type})"

# Декларации
class Declaration(models.Model):
    number = models.CharField(max_length=20, unique=True, verbose_name="Номер декларации")
    container = models.ForeignKey(Container, on_delete=models.CASCADE, verbose_name="Контейнер")
    customs_procedure = models.CharField(max_length=20, choices=Container.CUSTOMS_PROCEDURE_CHOICES, verbose_name="Таможенная процедура")
    date = models.DateField(verbose_name="Дата оформления")

    def __str__(self):
        return self.number

# Бухгалтерия
class Accounting(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, verbose_name="Инвойс")
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, null=True, blank=True, verbose_name="Платеж")
    sync_status = models.CharField(max_length=20, default="PENDING", verbose_name="Статус синхронизации")
    sync_date = models.DateTimeField(null=True, blank=True, verbose_name="Дата синхронизации")

    def __str__(self):
        return f"{self.invoice} - {self.sync_status}"