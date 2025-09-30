from django.core.management.base import BaseCommand
from django.db.models import Sum
from decimal import Decimal
from core.models import Client, InvoiceOLD as Invoice, PaymentOLD as Payment
import logging

logger = logging.getLogger('django')

class Command(BaseCommand):
    help = 'Recalculates the balance for a specified client based on their invoices and payments.'

    def add_arguments(self, parser):
        parser.add_argument('client_id', type=int, help='ID of the client to recalculate balance for')

    def handle(self, *args, **kwargs):
        client_id = kwargs['client_id']
        try:
            client = Client.objects.get(id=client_id)
            self.stdout.write(f"Recalculating balance for client: {client.name} (ID: {client_id})")
            logger.info(f"Starting balance recalculation for client ID={client_id}, Name={client.name}")

            # Сбрасываем балансы
            client.balance = Decimal('0.00')
            client.cash_balance = Decimal('0.00')
            client.card_balance = Decimal('0.00')

            # Получаем все инвойсы клиента (не исходящие)
            invoices = Invoice.objects.filter(client=client, is_outgoing=False)
            total_invoices = sum(Decimal(str(invoice.total_amount or '0.00')) for invoice in invoices)
            logger.info(f"Total invoices amount for client {client.name}: {total_invoices}")

            # Получаем все платежи клиента
            payments = Payment.objects.filter(payer=client).order_by('date')
            total_cash_payments = Decimal('0.00')
            total_card_payments = Decimal('0.00')
            total_cash_balance_payments = Decimal('0.00')
            total_card_balance_payments = Decimal('0.00')

            # Храним paid_amount для каждого инвойса
            invoice_paid_amounts = {invoice.id: Decimal('0.00') for invoice in invoices}

            for payment in payments:
                amount = Decimal(str(payment.amount))
                invoice_number = payment.invoice.number if payment.invoice else 'None'
                logger.info(f"Processing payment ID={payment.id}, Amount={amount}, Type={payment.payment_type}, From Balance={payment.from_balance}, From Cash Balance={payment.from_cash_balance}, Invoice={invoice_number}")
                if payment.from_balance:
                    if payment.from_cash_balance:
                        total_cash_balance_payments += amount
                    else:
                        total_card_balance_payments += amount
                    client.balance -= amount
                else:
                    client.balance += amount
                    if payment.invoice:
                        invoice_paid_amounts[payment.invoice.id] += amount
                        invoice_balance = invoice_paid_amounts[payment.invoice.id] - payment.invoice.total_amount
                        if invoice_balance > 0:
                            excess = invoice_balance
                            if payment.payment_type == 'CASH' or (payment.payment_type == 'BALANCE' and payment.from_cash_balance):
                                total_cash_payments += excess
                            elif payment.payment_type == 'CARD' or (payment.payment_type == 'BALANCE' and not payment.from_cash_balance):
                                total_card_payments += excess
                    else:
                        if payment.payment_type == 'CASH' or (payment.payment_type == 'BALANCE' and payment.from_cash_balance):
                            total_cash_payments += amount
                        elif payment.payment_type == 'CARD' or (payment.payment_type == 'BALANCE' and not payment.from_cash_balance):
                            total_card_payments += amount

            # Вычитаем сумму инвойсов из общего баланса
            client.balance -= total_invoices

            # Устанавливаем наличный и безналичный баланс
            total_payments = total_cash_payments + total_card_payments
            free_funds = total_payments - total_invoices
            unpaid_invoices = any(invoice.paid_amount < invoice.total_amount for invoice in invoices)
            logger.info(f"Free funds: {free_funds}, Unpaid invoices: {unpaid_invoices}")
            logger.info(f"Cash payments: {total_cash_payments}, Card payments: {total_card_payments}")
            logger.info(f"Cash balance payments: {total_cash_balance_payments}, Card balance payments: {total_card_balance_payments}")

            client.cash_balance = total_cash_payments - total_cash_balance_payments
            client.card_balance = total_card_payments - total_card_balance_payments

            client.save()
            logger.info(f"Client {client.name} updated: balance={client.balance}, cash_balance={client.cash_balance}, card_balance={client.card_balance}")

            # Отправляем обновление через WebSocket
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                "updates",
                {
                    "type": "data_update",
                    "data": {
                        "model": "Client",
                        "id": client.id,
                        "balance": str(client.balance),
                        "cash_balance": str(client.cash_balance),
                        "card_balance": str(client.card_balance)
                    }
                }
            )

            self.stdout.write(self.style.SUCCESS(
                f"Successfully recalculated balance for {client.name}: "
                f"balance={client.balance}, cash_balance={client.cash_balance}, card_balance={client.card_balance}"
            ))

        except Client.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Client with ID {client_id} does not exist"))
            logger.error(f"Client not found: ID={client_id}")