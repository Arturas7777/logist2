"""
Единый сервис для управления всеми финансовыми операциями
============================================================

BillingService - централизованная точка входа для:
- Создания инвойсов
- Обработки платежей
- Управления балансами
- Возвратов и корректировок

Все операции транзакционны и логируются.

Авторы: AI Assistant
Дата: 30 сентября 2025
"""

from django.db import models, transaction
from django.db.models import Sum, Q
from django.utils import timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Dict, Union
import logging

logger = logging.getLogger(__name__)


class BillingService:
    """
    Единый сервис для всех финансовых операций
    
    Преимущества:
    - Вся бизнес-логика в одном месте
    - Транзакционная безопасность
    - Централизованное логирование
    - Валидация данных
    - Понятные методы
    """
    
    # ========================================================================
    # УТИЛИТЫ
    # ========================================================================
    
    @staticmethod
    def quantize(amount: Union[Decimal, int, float, str]) -> Decimal:
        """Нормализовать сумму до 2 знаков после запятой"""
        return Decimal(str(amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
    @staticmethod
    def validate_entity(entity) -> bool:
        """Проверить, что сущность валидна"""
        return entity is not None and hasattr(entity, 'pk') and entity.pk is not None
    
    # ========================================================================
    # РАБОТА С ИНВОЙСАМИ
    # ========================================================================
    
    @classmethod
    @transaction.atomic
    def create_invoice(
        cls,
        issuer,
        recipient,
        cars: List = None,
        items: List[Dict] = None,
        due_days: int = 14,
        notes: str = "",
        created_by=None
    ):
        """
        Создать новый инвойс
        
        Args:
            issuer: Компания-выставитель (обычно Caromoto Lithuania)
            recipient: Получатель (Client/Warehouse/Line/Carrier)
            cars: Список автомобилей (услуги берутся из CarService автоматически)
            items: Или список позиций вручную (если не указаны cars)
            due_days: Количество дней до срока оплаты
            notes: Примечания к инвойсу
            created_by: Пользователь, создавший инвойс
        
        Returns:
            NewInvoice: Созданный инвойс
        
        Raises:
            ValueError: Если данные невалидны
        """
        from core.models_billing import NewInvoice, InvoiceItem
        
        # Валидация
        if not cls.validate_entity(issuer):
            raise ValueError("Выставитель инвойса не указан или невалиден")
        
        if not cls.validate_entity(recipient):
            raise ValueError("Получатель инвойса не указан или невалиден")
        
        if not cars and not items:
            raise ValueError("Укажите либо автомобили (cars), либо позиции (items)")
        
        # Определяем тип выставителя
        issuer_type = issuer.__class__.__name__
        issuer_field = f'issuer_{issuer_type.lower()}'
        
        # Определяем тип получателя
        recipient_type = recipient.__class__.__name__
        recipient_field = f'recipient_{recipient_type.lower()}'
        
        # Создаем инвойс
        due_date = timezone.now().date() + timezone.timedelta(days=due_days)
        
        invoice = NewInvoice(
            due_date=due_date,
            notes=notes,
            created_by=created_by,
            status='DRAFT'
        )
        
        # Устанавливаем выставителя
        setattr(invoice, issuer_field, issuer)
        
        # Устанавливаем получателя
        setattr(invoice, recipient_field, recipient)
        
        # Сохраняем инвойс (генерирует номер)
        invoice.save()
        
        logger.info(f"Created invoice {invoice.number} from {issuer} to {recipient}")
        
        # Добавляем позиции
        order = 0
        
        if cars:
            # Автоматически создаем позиции из услуг автомобилей
            from core.models import CarService
            
            for car in cars:
                # Получаем услуги автомобиля по типу получателя
                recipient_type = recipient.__class__.__name__
                
                if recipient_type == 'Warehouse':
                    services = car.get_warehouse_services()
                    service_prefix = 'Склад'
                elif recipient_type == 'Line':
                    services = car.get_line_services()
                    service_prefix = 'Линия'
                elif recipient_type == 'Carrier':
                    services = car.get_carrier_services()
                    service_prefix = 'Перевозчик'
                else:
                    # Для клиента - все услуги
                    services = car.car_services.all()
                    service_prefix = 'Услуги'
                
                # Создаем позиции из услуг
                for service in services:
                    service_name = service.get_service_name()
                    description = f"{service_prefix}: {service_name} для {car.vin}"
                    
                    unit_price = (
                        service.custom_price
                        if service.custom_price is not None
                        else service.get_default_price()
                    )
                    invoice_item = InvoiceItem(
                        invoice=invoice,
                        description=description,
                        quantity=service.quantity,
                        unit_price=unit_price,
                        car=car,
                        order=order
                    )
                    invoice_item.save()
                    order += 1
                    
                    logger.debug(f"Added service item to invoice {invoice.number}: {description}")
        
        elif items:
            # Создаем позиции вручную
            for idx, item_data in enumerate(items):
                description = item_data.get('description', '')
                quantity = cls.quantize(item_data.get('quantity', 1))
                unit_price = cls.quantize(item_data.get('unit_price', 0))
                car = item_data.get('car')
                
                if not description:
                    raise ValueError(f"Позиция {idx + 1}: описание обязательно")
                
                if unit_price < 0:
                    raise ValueError(f"Позиция {idx + 1}: цена не может быть отрицательной")
                
                invoice_item = InvoiceItem(
                    invoice=invoice,
                    description=description,
                    quantity=quantity,
                    unit_price=unit_price,
                    car=car,
                    order=idx
                )
                invoice_item.save()
                
                logger.debug(f"Added item to invoice {invoice.number}: {description}")
        
        # Пересчитываем итоги
        invoice.calculate_totals()
        invoice.status = 'ISSUED'
        invoice.save()
        
        logger.info(f"Invoice {invoice.number} created: {invoice.items.count()} items, total={invoice.total}")
        
        return invoice
    
    @classmethod
    @transaction.atomic
    def add_invoice_item(
        cls,
        invoice,
        description: str,
        quantity: Decimal,
        unit_price: Decimal,
        car=None
    ):
        """
        Добавить позицию в существующий инвойс
        
        Args:
            invoice: Инвойс
            description: Описание позиции
            quantity: Количество
            unit_price: Цена за единицу
            car: Автомобиль (опционально)
        
        Returns:
            InvoiceItem: Созданная позиция
        """
        from core.models_billing import InvoiceItem
        
        if invoice.status not in ['DRAFT', 'ISSUED']:
            raise ValueError(f"Нельзя добавлять позиции в инвойс со статусом {invoice.get_status_display()}")
        
        # Находим максимальный порядок
        max_order = invoice.items.aggregate(max_order=models.Max('order'))['max_order'] or 0
        
        item = InvoiceItem(
            invoice=invoice,
            description=description,
            quantity=cls.quantize(quantity),
            unit_price=cls.quantize(unit_price),
            car=car,
            order=max_order + 1
        )
        item.save()
        
        # Пересчитываем итоги инвойса
        invoice.calculate_totals()
        invoice.save()
        
        logger.info(f"Added item to invoice {invoice.number}: {description}")
        
        return item
    
    @classmethod
    @transaction.atomic
    def cancel_invoice(cls, invoice, reason: str = ""):
        """
        Отменить инвойс
        
        Args:
            invoice: Инвойс для отмены
            reason: Причина отмены
        
        Returns:
            NewInvoice: Обновленный инвойс
        """
        if invoice.paid_amount > 0:
            raise ValueError("Нельзя отменить инвойс, по которому уже были платежи")
        
        invoice.status = 'CANCELLED'
        if reason:
            invoice.notes = f"{invoice.notes}\n\nОТМЕНЕН: {reason}" if invoice.notes else f"ОТМЕНЕН: {reason}"
        invoice.save()
        
        logger.info(f"Invoice {invoice.number} cancelled: {reason}")
        
        return invoice
    
    # ========================================================================
    # РАБОТА С ПЛАТЕЖАМИ
    # ========================================================================
    
    @classmethod
    @transaction.atomic
    def pay_invoice(
        cls,
        invoice,
        amount: Decimal,
        method: str,
        payer,
        description: str = "",
        created_by=None,
        bank_transaction_id=None
    ):
        """
        Оплатить инвойс
        
        Args:
            invoice: Инвойс для оплаты
            amount: Сумма платежа
            method: Способ оплаты ('CASH', 'CARD', 'TRANSFER', 'BALANCE')
            payer: Кто платит (обычно получатель инвойса)
            description: Описание платежа
            created_by: Пользователь, создавший платеж
            bank_transaction_id: ID банковской транзакции для сопоставления (опционально)
        
        Returns:
            dict: Информация о результате платежа
                  {
                      'transaction': Transaction,
                      'invoice': NewInvoice,
                      'remaining': Decimal,
                      'overpayment': Decimal
                  }
        """
        from core.models_billing import Transaction, NewInvoice
        
        amount = cls.quantize(amount)
        
        if amount <= 0:
            raise ValueError("Сумма платежа должна быть положительной")
        
        locked_invoice = NewInvoice.objects.select_for_update().get(pk=invoice.pk)
        
        if locked_invoice.status == 'CANCELLED':
            raise ValueError("Нельзя оплатить отмененный инвойс")
        
        if locked_invoice.status == 'PAID':
            raise ValueError("Инвойс уже полностью оплачен")
        
        remaining = locked_invoice.remaining_amount
        if amount > remaining and remaining > 0:
            amount = remaining
        
        payer_type = payer.__class__.__name__
        from_field = f'from_{payer_type.lower()}'
        
        invoice_issuer = locked_invoice.issuer
        if not invoice_issuer:
            raise ValueError("У инвойса не указан выставитель")
        issuer_type = invoice_issuer.__class__.__name__
        to_field = f'to_{issuer_type.lower()}'
        
        trx = Transaction(
            type='PAYMENT',
            method=method,
            invoice=locked_invoice,
            amount=amount,
            currency=locked_invoice.currency or 'EUR',
            description=description or f"Оплата инвойса {locked_invoice.number}",
            created_by=created_by,
            status='COMPLETED'
        )
        
        setattr(trx, from_field, payer)
        setattr(trx, to_field, invoice_issuer)
        
        trx.save()
        
        logger.info(f"Created payment transaction {trx.number}: {amount} for invoice {locked_invoice.number}")
        
        locked_invoice.refresh_from_db()
        invoice.refresh_from_db()
        
        remaining = locked_invoice.remaining_amount
        overpayment = Decimal('0.00')
        
        if locked_invoice.paid_amount > locked_invoice.total:
            overpayment = locked_invoice.paid_amount - locked_invoice.total
            logger.warning(f"Overpayment detected for invoice {locked_invoice.number}: {overpayment}")
        
        logger.info(f"Invoice {locked_invoice.number} payment processed: paid={locked_invoice.paid_amount}, total={locked_invoice.total}, remaining={remaining}")
        
        if bank_transaction_id:
            try:
                from core.models_banking import BankTransaction as BankTrx
                bank_trx = BankTrx.objects.get(pk=bank_transaction_id)
                bank_trx.matched_transaction = trx
                bank_trx.matched_invoice = locked_invoice
                bank_trx.save(update_fields=['matched_transaction', 'matched_invoice'])
                logger.info(f"Linked bank transaction {bank_trx.external_id} to payment {trx.number}")
            except BankTrx.DoesNotExist:
                logger.warning(f"Bank transaction {bank_transaction_id} not found for linking")
            except Exception as e:
                logger.error(f"Error linking bank transaction: {e}")
        
        return {
            'transaction': trx,
            'invoice': locked_invoice,
            'remaining': remaining,
            'overpayment': overpayment
        }
    
    @classmethod
    @transaction.atomic
    def refund(
        cls,
        original_transaction,
        amount: Optional[Decimal] = None,
        reason: str = "",
        created_by=None
    ):
        """
        Вернуть деньги
        
        Args:
            original_transaction: Оригинальная транзакция для возврата
            amount: Сумма возврата (None = полная сумма)
            reason: Причина возврата
            created_by: Пользователь, создавший возврат
        
        Returns:
            Transaction: Транзакция возврата
        """
        from core.models_billing import Transaction
        
        if original_transaction.type == 'REFUND':
            raise ValueError("Нельзя сделать возврат возврата")
        
        refund_amount = cls.quantize(amount) if amount else original_transaction.amount
        
        if refund_amount <= 0 or refund_amount > original_transaction.amount:
            raise ValueError("Некорректная сумма возврата")
        
        already_refunded = Transaction.objects.filter(
            type='REFUND',
            status='COMPLETED',
            description__contains=original_transaction.number,
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')
        
        max_refundable = original_transaction.amount - already_refunded
        if refund_amount > max_refundable:
            raise ValueError(
                f"Сумма возврата ({refund_amount}) превышает доступный остаток "
                f"({max_refundable}). Уже возвращено: {already_refunded}"
            )
        
        # Создаем транзакцию возврата (меняем отправителя и получателя местами)
        refund_trx = Transaction(
            type='REFUND',
            method=original_transaction.method,
            invoice=original_transaction.invoice,
            amount=refund_amount,
            description=f"Возврат по транзакции {original_transaction.number}. {reason}",
            created_by=created_by,
            status='COMPLETED'
        )
        
        # Меняем направление
        sender = original_transaction.sender
        recipient = original_transaction.recipient
        
        sender_type = sender.__class__.__name__ if sender else None
        recipient_type = recipient.__class__.__name__ if recipient else None
        
        if sender_type:
            setattr(refund_trx, f'to_{sender_type.lower()}', sender)
        
        if recipient_type:
            setattr(refund_trx, f'from_{recipient_type.lower()}', recipient)
        
        refund_trx.save()
        
        logger.info(f"Created refund transaction {refund_trx.number}: {refund_amount} for transaction {original_transaction.number}")
        
        # Балансы и paid_amount пересчитываются автоматически сигналом post_save Transaction
        
        return refund_trx
    
    @classmethod
    @transaction.atomic
    def transfer(
        cls,
        from_entity,
        to_entity,
        amount: Decimal,
        method: str = 'TRANSFER',
        description: str = "",
        created_by=None
    ):
        """
        Перевести деньги между сущностями
        
        Args:
            from_entity: Отправитель
            to_entity: Получатель
            amount: Сумма перевода
            method: Способ ('CASH', 'CARD', 'TRANSFER')
            description: Описание перевода
            created_by: Пользователь, создавший перевод
        
        Returns:
            Transaction: Транзакция перевода
        """
        from core.models_billing import Transaction
        
        amount = cls.quantize(amount)
        
        if amount <= 0:
            raise ValueError("Сумма перевода должна быть положительной")
        
        if not cls.validate_entity(from_entity) or not cls.validate_entity(to_entity):
            raise ValueError("Отправитель и получатель должны быть указаны")
        
        # Re-read with row-level lock to prevent concurrent balance drain
        if hasattr(from_entity, 'balance'):
            locked_entity = type(from_entity).objects.select_for_update().get(pk=from_entity.pk)
            if locked_entity.balance < amount:
                raise ValueError(f"Недостаточно средств: доступно {locked_entity.balance}, требуется {amount}")
        
        from_type = from_entity.__class__.__name__
        to_type = to_entity.__class__.__name__
        
        trx = Transaction(
            type='TRANSFER',
            method=method,
            amount=amount,
            description=description or f"Перевод от {from_entity} к {to_entity}",
            created_by=created_by,
            status='COMPLETED'
        )
        
        setattr(trx, f'from_{from_type.lower()}', from_entity)
        setattr(trx, f'to_{to_type.lower()}', to_entity)
        
        trx.save()
        
        logger.info(f"Created transfer transaction {trx.number}: {amount} from {from_entity} to {to_entity}")
        
        # Балансы пересчитываются автоматически сигналом post_save Transaction
        
        return trx
    
    @classmethod
    @transaction.atomic
    def topup_balance(
        cls,
        entity,
        amount: Decimal,
        method: str = 'CASH',
        description: str = "",
        created_by=None
    ):
        """
        Пополнить баланс сущности
        
        Args:
            entity: Сущность для пополнения
            amount: Сумма пополнения
            method: Способ пополнения ('CASH', 'CARD', 'TRANSFER')
            description: Описание
            created_by: Пользователь
        
        Returns:
            Transaction: Транзакция пополнения
        """
        from core.models_billing import Transaction
        
        amount = cls.quantize(amount)
        
        if amount <= 0:
            raise ValueError("Сумма пополнения должна быть положительной")
        
        if not cls.validate_entity(entity):
            raise ValueError("Сущность не указана или невалидна")
        
        entity_type = entity.__class__.__name__
        
        trx = Transaction(
            type='BALANCE_TOPUP',
            method=method,
            amount=amount,
            description=description or f"Пополнение баланса {entity}",
            created_by=created_by,
            status='COMPLETED'
        )
        
        setattr(trx, f'to_{entity_type.lower()}', entity)
        
        trx.save()
        
        logger.info(f"Created balance topup transaction {trx.number}: {amount} for {entity}")
        
        # Баланс пересчитывается автоматически сигналом post_save Transaction
        
        return trx
    
    @classmethod
    @transaction.atomic
    def adjust_balance(
        cls,
        entity,
        amount: Decimal,
        reason: str,
        created_by=None
    ):
        """
        Корректировка баланса (может быть положительной или отрицательной)
        
        Args:
            entity: Сущность для корректировки
            amount: Сумма корректировки (может быть отрицательной!)
            reason: Причина корректировки
            created_by: Пользователь
        
        Returns:
            Transaction: Транзакция корректировки
        """
        from core.models_billing import Transaction
        
        amount = cls.quantize(amount)
        
        if amount == 0:
            raise ValueError("Сумма корректировки не может быть нулевой")
        
        if not cls.validate_entity(entity):
            raise ValueError("Сущность не указана или невалидна")
        
        # Создаем транзакцию корректировки
        entity_type = entity.__class__.__name__
        
        trx = Transaction(
            type='ADJUSTMENT',
            method='OTHER',
            amount=abs(amount),
            description=f"Корректировка баланса: {reason}",
            created_by=created_by,
            status='COMPLETED'
        )
        
        # Если корректировка положительная - пополнение, если отрицательная - списание
        if amount > 0:
            setattr(trx, f'to_{entity_type.lower()}', entity)
        else:
            setattr(trx, f'from_{entity_type.lower()}', entity)
        
        trx.save()
        
        logger.info(f"Created balance adjustment transaction {trx.number}: {amount} for {entity}")
        
        # Баланс пересчитывается автоматически сигналом post_save Transaction
        
        return trx
    
    # ========================================================================
    # АВТОМАТИЧЕСКОЕ СОПОСТАВЛЕНИЕ С БАНКОМ (AUTO-RECONCILIATION)
    # ========================================================================
    
    @classmethod
    def auto_reconcile_bank_transactions(cls) -> Dict:
        """
        Автоматическое сопоставление банковских транзакций с инвойсами.
        
        Логика:
        1. Берём все НЕсопоставленные, ЗАВЕРШЁННЫЕ банковские транзакции (исходящие, amount < 0)
        2. Для каждой проверяем, содержит ли description номер external_number
           какого-либо неоплаченного ВХОДЯЩЕГО инвойса
        3. Если найдено совпадение и сумма банковской транзакции >= remaining_amount
           → автоматически проводим оплату через pay_invoice()
        4. Если сумма не совпадает точно — только привязываем банк. транзакцию к инвойсу
           (без авто-оплаты), чтобы оператор разобрался вручную
        
        Returns:
            dict: {'auto_paid': [...], 'linked_only': [...], 'errors': [...]}
        """
        from core.models_banking import BankTransaction
        from core.models_billing import NewInvoice
        from core.models import Company
        
        result = {
            'auto_paid': [],      # Инвойсы, автоматически оплаченные
            'linked_only': [],    # Только привязано (сумма не совпала)
            'errors': [],         # Ошибки
        }
        
        # 1. Все несопоставленные исходящие (amount < 0) завершённые банковские транзакции
        #    Исключаем помеченные как "не требует привязки"
        unmatched_bank_txns = BankTransaction.objects.filter(
            state='completed',
            matched_invoice__isnull=True,
            matched_transaction__isnull=True,
            reconciliation_skipped=False,
            amount__lt=0,  # Исходящие платежи (мы заплатили)
        ).exclude(
            description=''
        ).select_related('connection__company')
        
        if not unmatched_bank_txns.exists():
            logger.debug('[AutoReconcile] Нет несопоставленных банковских транзакций')
            return result
        
        from ..models import Company
        default_company_id = Company.get_default_id()
        unpaid_invoices = NewInvoice.objects.filter(
            status__in=['ISSUED', 'PARTIALLY_PAID', 'OVERDUE'],
            recipient_company_id=default_company_id,
        ).exclude(
            external_number=''
        )
        
        if not unpaid_invoices.exists():
            logger.debug('[AutoReconcile] Нет неоплаченных входящих инвойсов с external_number')
            return result
        
        # Строим индекс: external_number → invoice (для быстрого поиска)
        import re
        ext_num_to_invoices = {}
        for inv in unpaid_invoices:
            key = inv.external_number.strip()
            if key:
                ext_num_to_invoices.setdefault(key, []).append(inv)
        
        # 3. Сопоставляем
        caromoto = Company.get_default()
        if not caromoto:
            logger.error('[AutoReconcile] Компания по умолчанию не найдена (проверьте settings.COMPANY_NAME)')
            return result
        
        for bank_trx in unmatched_bank_txns:
            desc = bank_trx.description.strip()
            if not desc:
                continue
            
            # Ищем совпадение: external_number как целое слово в description
            matched_invoice = None
            for ext_num, invoices in ext_num_to_invoices.items():
                if re.search(r'(?<!\w)' + re.escape(ext_num) + r'(?!\w)', desc):
                    # Берём первый неоплаченный инвойс с таким номером
                    for inv in invoices:
                        if inv.status not in ['PAID', 'CANCELLED']:
                            matched_invoice = inv
                            break
                    if matched_invoice:
                        break
            
            if not matched_invoice:
                continue
            
            bank_amount = abs(bank_trx.amount)
            remaining = matched_invoice.remaining_amount
            
            invoice_currency = matched_invoice.currency or 'EUR'
            bank_currency = getattr(bank_trx, 'currency', 'EUR') or 'EUR'
            if bank_currency.upper() != invoice_currency.upper():
                logger.warning(
                    f'[AutoReconcile] Пропуск: валюта банка ({bank_currency}) != '
                    f'валюта инвойса ({invoice_currency}) для {matched_invoice.number}'
                )
                result['errors'].append({
                    'bank_trx': str(bank_trx),
                    'invoice': matched_invoice.number,
                    'error': f'Несовпадение валют: банк {bank_currency}, инвойс {invoice_currency}',
                })
                continue
            
            logger.info(
                f'[AutoReconcile] Совпадение: банк "{desc}" ({bank_amount} {bank_currency}) '
                f'↔ инвойс {matched_invoice.number} (external: {matched_invoice.external_number}, '
                f'remaining: {remaining})'
            )
            
            # Определяем плательщика: тот, кому выставлен инвойс (Caromoto Lithuania)
            payer = matched_invoice.recipient  # Company id=1
            if not payer:
                logger.warning(f'[AutoReconcile] У инвойса {matched_invoice.number} нет получателя, пропускаем')
                result['errors'].append({
                    'bank_trx': str(bank_trx),
                    'invoice': matched_invoice.number,
                    'error': 'Нет получателя у инвойса',
                })
                continue
            
            # Проверяем совпадение сумм (допускаем разницу до 0.02 € на округление)
            tolerance = Decimal('0.02')
            amounts_match = abs(bank_amount - remaining) <= tolerance
            
            if amounts_match or bank_amount >= remaining:
                # Суммы совпадают (или переплата) → автоматическая оплата
                pay_amount = min(bank_amount, remaining)  # Не платим больше, чем остаток
                try:
                    pay_result = cls.pay_invoice(
                        invoice=matched_invoice,
                        amount=pay_amount,
                        method='TRANSFER',
                        payer=payer,
                        description=f'Авто-оплата по банковской операции: {desc}',
                        bank_transaction_id=bank_trx.pk,
                    )
                    # Добавляем reconciliation_note
                    bank_trx.refresh_from_db()
                    bank_trx.reconciliation_note = (
                        f'Авто-сопоставлено: external_number "{matched_invoice.external_number}" '
                        f'найден в описании банковской операции'
                    )
                    bank_trx.save(update_fields=['reconciliation_note'])
                    
                    result['auto_paid'].append({
                        'invoice': matched_invoice.number,
                        'external_number': matched_invoice.external_number,
                        'amount': str(pay_amount),
                        'bank_trx': str(bank_trx),
                        'new_status': matched_invoice.status,
                    })
                    
                    # Убираем инвойс из индекса (уже оплачен)
                    ext_key = matched_invoice.external_number.strip()
                    if ext_key in ext_num_to_invoices:
                        ext_num_to_invoices[ext_key] = [
                            i for i in ext_num_to_invoices[ext_key]
                            if i.pk != matched_invoice.pk
                        ]
                    
                    logger.info(
                        f'[AutoReconcile] ✅ Авто-оплата: инвойс {matched_invoice.number} '
                        f'оплачен на {pay_amount} € → статус {matched_invoice.status}'
                    )
                    
                except Exception as e:
                    logger.error(f'[AutoReconcile] Ошибка авто-оплаты инвойса {matched_invoice.number}: {e}')
                    result['errors'].append({
                        'bank_trx': str(bank_trx),
                        'invoice': matched_invoice.number,
                        'error': str(e),
                    })
            else:
                # Суммы НЕ совпадают (банковская < остаток) → только привязка, без оплаты
                bank_trx.matched_invoice = matched_invoice
                bank_trx.reconciliation_note = (
                    f'Авто-привязано (суммы не совпали): банк {bank_amount} € ≠ остаток {remaining} €. '
                    f'Требуется ручная обработка.'
                )
                bank_trx.save(update_fields=['matched_invoice', 'reconciliation_note'])
                
                result['linked_only'].append({
                    'invoice': matched_invoice.number,
                    'external_number': matched_invoice.external_number,
                    'bank_amount': str(bank_amount),
                    'invoice_remaining': str(remaining),
                    'bank_trx': str(bank_trx),
                })
                
                logger.info(
                    f'[AutoReconcile] ⚠️ Привязано без оплаты: инвойс {matched_invoice.number} '
                    f'(банк {bank_amount} ≠ остаток {remaining})'
                )
        
        total = len(result['auto_paid']) + len(result['linked_only'])
        if total:
            logger.info(
                f'[AutoReconcile] Итого: {len(result["auto_paid"])} авто-оплачено, '
                f'{len(result["linked_only"])} привязано без оплаты, '
                f'{len(result["errors"])} ошибок'
            )
        
        return result
    
    # ========================================================================
    # ОТЧЕТЫ И АНАЛИТИКА
    # ========================================================================
    
    @classmethod
    def get_entity_balance_report(cls, entity) -> Dict:
        """
        Получить детальный отчет по балансам сущности
        
        Args:
            entity: Сущность (Client/Warehouse/Line/Carrier/Company)
        
        Returns:
            dict: Детальный отчет
        """
        from core.models_billing import Transaction, NewInvoice
        
        if not hasattr(entity, 'balance'):
            return {'error': 'Эта сущность не поддерживает балансы'}
        
        entity_type = entity.__class__.__name__
        
        # Базовая информация о балансе
        report = {
            'entity': str(entity),
            'entity_type': entity_type,
            'current_balance': entity.balance,
            'balance_updated_at': entity.balance_updated_at if hasattr(entity, 'balance_updated_at') else None,
        }
        
        # Разбивка баланса по способам оплаты
        if hasattr(entity, 'get_balance_breakdown'):
            report['breakdown'] = entity.get_balance_breakdown()
        
        # Статистика по инвойсам
        incoming_invoices = NewInvoice.objects.filter(
            **{f'recipient_{entity_type.lower()}': entity}
        )
        
        report['invoices'] = {
            'total_count': incoming_invoices.count(),
            'unpaid_count': incoming_invoices.exclude(status='PAID').count(),
            'total_amount': incoming_invoices.aggregate(Sum('total'))['total__sum'] or Decimal('0.00'),
            'paid_amount': incoming_invoices.aggregate(Sum('paid_amount'))['paid_amount__sum'] or Decimal('0.00'),
            'remaining_amount': (incoming_invoices.aggregate(Sum('total'))['total__sum'] or Decimal('0.00')) - 
                              (incoming_invoices.aggregate(Sum('paid_amount'))['paid_amount__sum'] or Decimal('0.00')),
        }
        
        # Статистика по транзакциям
        incoming_transactions = Transaction.objects.filter(
            **{f'to_{entity_type.lower()}': entity}
        )
        
        outgoing_transactions = Transaction.objects.filter(
            **{f'from_{entity_type.lower()}': entity}
        )
        
        report['transactions'] = {
            'incoming_count': incoming_transactions.count(),
            'outgoing_count': outgoing_transactions.count(),
            'incoming_amount': incoming_transactions.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00'),
            'outgoing_amount': outgoing_transactions.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00'),
        }
        
        return report
    
    @classmethod
    def get_invoice_report(cls, invoice) -> Dict:
        """
        Получить детальный отчет по инвойсу
        
        Args:
            invoice: Инвойс
        
        Returns:
            dict: Детальный отчет
        """
        from core.models_billing import Transaction
        
        # Базовая информация
        report = {
            'number': invoice.number,
            'date': invoice.date,
            'due_date': invoice.due_date,
            'status': invoice.get_status_display(),
            'issuer': str(invoice.issuer),
            'recipient': invoice.recipient_name,
            'subtotal': invoice.subtotal,
            'discount': invoice.discount,
            'tax': invoice.tax,
            'total': invoice.total,
            'paid_amount': invoice.paid_amount,
            'remaining_amount': invoice.remaining_amount,
            'is_overdue': invoice.is_overdue,
            'days_until_due': invoice.days_until_due,
        }
        
        # Позиции инвойса
        report['items'] = [
            {
                'description': item.description,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'total_price': item.total_price,
                'car': str(item.car) if item.car else None,
            }
            for item in invoice.items.all()
        ]
        
        # История платежей
        transactions = Transaction.objects.filter(invoice=invoice).order_by('date')
        
        report['payments'] = [
            {
                'number': trx.number,
                'date': trx.date,
                'type': trx.get_type_display(),
                'method': trx.get_method_display(),
                'amount': trx.amount,
                'sender': trx.sender_name,
            }
            for trx in transactions
        ]
        
        return report
