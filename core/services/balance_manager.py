"""
Централизованный менеджер для управления балансами всех сущностей
Заменяет разрозненную логику из models.py, signals.py и старого billing.py
"""

from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import Sum, Q
from typing import Optional, Union, Literal
import logging

logger = logging.getLogger('django')


class BalanceManager:
    """
    Централизованное управление балансами для всех сущностей системы
    """
    
    @staticmethod
    def quantize_amount(amount: Union[Decimal, int, float, str, None]) -> Decimal:
        """Нормализует сумму до 2 знаков после запятой"""
        if amount is None:
            return Decimal('0.00')
        return Decimal(str(amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
    @classmethod
    def update_entity_balance(
        cls,
        entity,
        amount: Decimal,
        balance_type: Literal['INVOICE', 'CASH', 'CARD'] = 'INVOICE',
        operation: Literal['ADD', 'SUBTRACT'] = 'ADD'
    ):
        """
        Обновляет баланс любой сущности (Client, Warehouse, Line, Company, Carrier)
        
        Args:
            entity: Объект сущности с BalanceMixin
            amount: Сумма операции
            balance_type: Тип баланса
            operation: Операция (ADD - увеличить, SUBTRACT - уменьшить)
        """
        if not hasattr(entity, 'get_balance'):
            logger.error(f"Entity {entity} doesn't have balance methods")
            return
        
        amount = cls.quantize_amount(amount)
        
        if operation == 'SUBTRACT':
            amount = -amount
        
        try:
            entity.update_balance(balance_type, amount)
            logger.info(f"Balance updated for {entity}: {balance_type} {operation} {abs(amount)}")
        except Exception as e:
            logger.error(f"Failed to update balance for {entity}: {e}")
            raise
    
    @classmethod
    def recalculate_invoice_balance(cls, entity):
        """
        Пересчитывает инвойс-баланс сущности на основе реальных данных из БД
        
        Args:
            entity: Объект сущности (Client, Warehouse, Line, Company, Carrier)
        """
        if not hasattr(entity, 'update_balance_from_invoices'):
            logger.warning(f"Entity {entity} doesn't have update_balance_from_invoices method")
            return
        
        try:
            entity.update_balance_from_invoices()
            logger.info(f"Invoice balance recalculated for {entity}")
        except Exception as e:
            logger.error(f"Failed to recalculate invoice balance for {entity}: {e}")
            raise
    
    @classmethod
    def process_payment(
        cls,
        sender,
        recipient,
        amount: Decimal,
        payment_type: str,
        invoice=None,
        description: str = ""
    ):
        """
        Обрабатывает платеж между двумя сущностями
        
        Args:
            sender: Отправитель платежа
            recipient: Получатель платежа
            amount: Сумма платежа
            payment_type: Тип платежа (CASH, CARD, INVOICE)
            invoice: Связанный инвойс (опционально)
            description: Описание платежа
            
        Returns:
            dict: Результат операции
        """
        amount = cls.quantize_amount(amount)
        
        if amount <= 0:
            raise ValueError("Payment amount must be positive")
        
        # Проверяем, является ли это пополнением собственного баланса
        is_self_payment = (sender == recipient and sender is not None)
        
        try:
            with transaction.atomic():
                if is_self_payment:
                    # Пополнение собственного баланса
                    balance_type = payment_type.upper()
                    cls.update_entity_balance(sender, amount, balance_type, 'ADD')
                    
                    return {
                        'success': True,
                        'message': f'Balance topped up: {amount}',
                        'operation': 'SELF_PAYMENT'
                    }
                else:
                    # Перевод между разными сущностями
                    balance_type = payment_type.upper()
                    
                    # Списываем с отправителя
                    if sender:
                        # Проверяем достаточность средств
                        sender_balance = sender.get_balance(balance_type)
                        if sender_balance < amount and 'корректировка' not in description.lower():
                            raise ValueError(
                                f"Insufficient funds: available {sender_balance}, required {amount}"
                            )
                        
                        cls.update_entity_balance(sender, amount, balance_type, 'SUBTRACT')
                    
                    # Зачисляем получателю
                    if recipient:
                        cls.update_entity_balance(recipient, amount, balance_type, 'ADD')
                    
                    # Обновляем инвойс-балансы
                    if sender:
                        cls.recalculate_invoice_balance(sender)
                    if recipient:
                        cls.recalculate_invoice_balance(recipient)
                    
                    return {
                        'success': True,
                        'message': f'Payment processed: {amount}',
                        'operation': 'TRANSFER',
                        'sender_balance': sender.get_balance_summary() if sender else None,
                        'recipient_balance': recipient.get_balance_summary() if recipient else None
                    }
                    
        except Exception as e:
            logger.error(f"Payment processing failed: {e}")
            raise
    
    @classmethod
    def handle_invoice_payment(cls, invoice, payment_amount: Decimal):
        """
        Обрабатывает платеж по инвойсу с учетом переплаты
        
        Args:
            invoice: Объект инвойса
            payment_amount: Сумма платежа
            
        Returns:
            dict: Результат операции с информацией о переплате
        """
        payment_amount = cls.quantize_amount(payment_amount)
        remaining_amount = cls.quantize_amount(invoice.total_amount - invoice.paid_amount)
        
        result = {
            'paid_amount': min(payment_amount, remaining_amount),
            'overpayment': max(Decimal('0.00'), payment_amount - remaining_amount),
            'invoice_fully_paid': payment_amount >= remaining_amount
        }
        
        # Обновляем статус инвойса
        invoice.paid = result['invoice_fully_paid']
        invoice.save(update_fields=['paid'])
        
        # Если есть переплата, возвращаем ее на баланс
        if result['overpayment'] > 0 and invoice.to_entity:
            logger.info(f"Overpayment detected: {result['overpayment']} for invoice {invoice.number}")
        
        return result
    
    @classmethod
    def reset_all_balances(cls):
        """
        Полностью обнуляет все балансы всех сущностей
        ВНИМАНИЕ: Используйте только для отладки или миграции!
        """
        from core.models import Client, Warehouse, Line, Company, Carrier
        
        with transaction.atomic():
            for model in [Client, Warehouse, Line, Company, Carrier]:
                model.objects.all().update(
                    invoice_balance=0,
                    cash_balance=0,
                    card_balance=0
                )
            
            logger.warning("All balances have been reset to zero!")
            
        return {'success': True, 'message': 'All balances reset'}
    
    @classmethod
    def recalculate_all_balances(cls):
        """
        Пересчитывает все балансы на основе существующих инвойсов и платежей
        Полезно для восстановления консистентности после сбоев
        """
        from core.models import Client, Warehouse, Line, Company, Carrier
        
        entities_updated = 0
        
        with transaction.atomic():
            for model in [Client, Warehouse, Line, Company, Carrier]:
                for entity in model.objects.all():
                    try:
                        cls.recalculate_invoice_balance(entity)
                        entities_updated += 1
                    except Exception as e:
                        logger.error(f"Failed to recalculate balance for {entity}: {e}")
        
        logger.info(f"Recalculated balances for {entities_updated} entities")
        
        return {
            'success': True,
            'entities_updated': entities_updated,
            'message': f'Recalculated {entities_updated} balances'
        }
    
    @classmethod
    def get_entity_balance_report(cls, entity) -> dict:
        """
        Формирует детальный отчет по балансам сущности
        
        Args:
            entity: Объект сущности
            
        Returns:
            dict: Детальный отчет
        """
        if not hasattr(entity, 'get_balance_summary'):
            return {'error': 'Entity does not support balances'}
        
        summary = entity.get_balance_summary()
        
        # Дополнительная информация в зависимости от типа сущности
        if hasattr(entity, 'balance_details'):
            summary.update(entity.balance_details())
        
        return summary
    
    @classmethod
    def validate_balance_consistency(cls, entity) -> dict:
        """
        Проверяет консистентность балансов сущности
        
        Args:
            entity: Объект сущности
            
        Returns:
            dict: Результат проверки
        """
        issues = []
        
        # Проверяем отрицательные балансы (кроме invoice_balance)
        if hasattr(entity, 'cash_balance') and entity.cash_balance < 0:
            issues.append(f"Negative cash balance: {entity.cash_balance}")
        
        if hasattr(entity, 'card_balance') and entity.card_balance < 0:
            issues.append(f"Negative card balance: {entity.card_balance}")
        
        return {
            'is_valid': len(issues) == 0,
            'issues': issues,
            'entity': str(entity)
        }
