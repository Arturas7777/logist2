"""
InvoiceAudit + SupplierCost — модели для проверки счетов и сверки затрат.
"""
from django.db import models
from django.contrib.auth.models import User
from core.models import Car, CarService


class InvoiceAudit(models.Model):
    STATUS_PENDING    = 'PENDING'
    STATUS_PROCESSING = 'PROCESSING'
    STATUS_OK         = 'OK'
    STATUS_HAS_ISSUES = 'HAS_ISSUES'
    STATUS_ERROR      = 'ERROR'

    STATUS_CHOICES = [
        (STATUS_PENDING,    'Ожидает обработки'),
        (STATUS_PROCESSING, 'Обрабатывается'),
        (STATUS_OK,         'Всё совпадает'),
        (STATUS_HAS_ISSUES, 'Есть расхождения'),
        (STATUS_ERROR,      'Ошибка обработки'),
    ]

    # ── Файл ────────────────────────────────────────────────────────────────
    pdf_file          = models.FileField(upload_to='invoice_audits/', verbose_name='PDF файл')
    original_filename = models.CharField(max_length=255, blank=True, verbose_name='Имя файла')

    # ── Распознанные данные (заполняет LLM) ─────────────────────────────────
    counterparty_detected = models.CharField(max_length=200, blank=True, verbose_name='Контрагент')
    invoice_number        = models.CharField(max_length=100, blank=True, verbose_name='Номер счёта')
    invoice_date          = models.DateField(null=True, blank=True, verbose_name='Дата счёта')
    total_amount          = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Сумма счёта')
    currency              = models.CharField(max_length=3, default='EUR', verbose_name='Валюта')

    # ── Сырые данные и результаты ────────────────────────────────────────────
    raw_extracted  = models.JSONField(default=dict, blank=True, verbose_name='Извлечённые данные (LLM)')
    discrepancies  = models.JSONField(default=list, blank=True, verbose_name='Расхождения')

    # ── Статус и статистика ──────────────────────────────────────────────────
    status        = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, verbose_name='Статус')
    error_message = models.TextField(blank=True, verbose_name='Сообщение об ошибке')
    cars_found    = models.IntegerField(default=0, verbose_name='Машин найдено в системе')
    cars_missing  = models.IntegerField(default=0, verbose_name='Машин нет в системе')
    issues_count  = models.IntegerField(default=0, verbose_name='Расхождений')

    # ── Связь с NewInvoice ────────────────────────────────────────────────────
    invoice = models.OneToOneField(
        'core.NewInvoice', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='audit',
        verbose_name='Инвойс',
    )

    # ── Служебные ────────────────────────────────────────────────────────────
    created_by   = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Загрузил')
    created_at   = models.DateTimeField(auto_now_add=True, verbose_name='Загружен')
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name='Обработан')

    class Meta:
        verbose_name        = 'Проверка счёта'
        verbose_name_plural = 'Проверки счетов'
        ordering            = ['-created_at']

    def __str__(self):
        name = self.counterparty_detected or self.original_filename or f'Счёт #{self.pk}'
        date = self.invoice_date.strftime('%d.%m.%Y') if self.invoice_date else ''
        return f'{name} {date}'.strip()

    @property
    def status_color(self):
        return {
            self.STATUS_PENDING:    'secondary',
            self.STATUS_PROCESSING: 'warning',
            self.STATUS_OK:         'success',
            self.STATUS_HAS_ISSUES: 'danger',
            self.STATUS_ERROR:      'dark',
        }.get(self.status, 'secondary')


class SupplierCost(models.Model):
    """Фактическая стоимость услуги от поставщика, привязанная к конкретной машине и CarService."""

    SERVICE_TYPE_CHOICES = [
        ('UNLOADING',    'Разгрузка/Погрузка'),
        ('THS',          'THS (портовые сборы)'),
        ('STORAGE',      'Хранение'),
        ('TRANSPORT',    'Транспорт'),
        ('DECLARATION',  'Декларация'),
        ('BDK',          'BDK'),
        ('DOCS',         'Документы'),
        ('COMPENSATION', 'Компенсация'),
        ('OTHER',        'Прочее'),
    ]

    SOURCE_CHOICES = [
        ('INVOICE', 'Из инвойса (PDF)'),
        ('MANUAL',  'Ручной ввод'),
    ]

    car            = models.ForeignKey(Car, on_delete=models.CASCADE, null=True, blank=True,
                                       related_name='supplier_costs', verbose_name='Машина')
    car_service    = models.ForeignKey(CarService, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='supplier_costs', verbose_name='Услуга в карточке')
    audit          = models.ForeignKey(InvoiceAudit, on_delete=models.CASCADE, null=True, blank=True,
                                       related_name='supplier_costs', verbose_name='Счёт')
    source         = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='INVOICE',
                                       verbose_name='Источник')
    counterparty   = models.CharField(max_length=200, verbose_name='Контрагент')
    service_type   = models.CharField(max_length=20, choices=SERVICE_TYPE_CHOICES, verbose_name='Тип услуги')
    amount         = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Сумма')
    storage_days   = models.IntegerField(default=0, verbose_name='Платных дней (хранение)')
    vin            = models.CharField(max_length=20, blank=True, verbose_name='VIN')
    description    = models.CharField(max_length=300, blank=True, verbose_name='Описание из счёта')
    reviewed       = models.BooleanField(default=False, verbose_name='Проверено')
    reviewed_at    = models.DateTimeField(null=True, blank=True, verbose_name='Дата проверки')
    created_at     = models.DateTimeField(auto_now_add=True, verbose_name='Создано')

    class Meta:
        verbose_name        = 'Затрата от поставщика'
        verbose_name_plural = 'Затраты от поставщиков'
        ordering            = ['-created_at']
        indexes = [
            models.Index(fields=['car', 'service_type']),
            models.Index(fields=['vin']),
            models.Index(fields=['audit']),
            models.Index(fields=['car_service']),
        ]

    def __str__(self):
        car_str = self.vin or '—'
        src = '📎' if self.source == 'INVOICE' else '✍️'
        return f'{src} {self.counterparty} | {self.get_service_type_display()} | {car_str} | {self.amount}€'
