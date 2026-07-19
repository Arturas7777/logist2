"""
Команда для полной очистки данных биллинга и обнуления балансов
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Очистить все данные биллинга (транзакции, инвойсы) и обнулить все балансы"

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Подтвердить операцию без запроса",
        )

    def handle(self, *args, **options):
        # Guard от катастрофы на проде: команда сносит ВЕСЬ финансовый леджер.
        # На боевой базе (DEBUG=False) требуем явный env-флаг, чтобы её нельзя
        # было выполнить случайно (автодополнение, копипаст из истории, agent).
        if not settings.DEBUG and os.getenv("ALLOW_DESTRUCTIVE") != "1":
            raise CommandError(
                "reset_billing удаляет все Transaction/NewInvoice и обнуляет балансы. "
                "На проде (DEBUG=False) запуск запрещён. Если это действительно нужно — "
                "выполните с переменной окружения: ALLOW_DESTRUCTIVE=1 python manage.py reset_billing"
            )

        if not options["yes"]:
            confirm = input("⚠️  Это удалит ВСЕ транзакции, инвойсы и обнулит балансы. Продолжить? [y/N]: ")
            if confirm.lower() != "y":
                self.stdout.write(self.style.WARNING("Операция отменена"))
                return

        from decimal import Decimal

        from core.models import Carrier, Client, Company, Line, Warehouse
        from core.models_billing import InvoiceItem, NewInvoice, Transaction

        with transaction.atomic():
            # Удаляем транзакции
            trx_count = Transaction.objects.count()
            Transaction.objects.all().delete()
            self.stdout.write(f"✓ Удалено транзакций: {trx_count}")

            # Удаляем позиции инвойсов
            items_count = InvoiceItem.objects.count()
            InvoiceItem.objects.all().delete()
            self.stdout.write(f"✓ Удалено позиций инвойсов: {items_count}")

            # Удаляем инвойсы
            inv_count = NewInvoice.objects.count()
            for inv in NewInvoice.objects.all():
                inv.cars.clear()  # Очищаем M2M связи
            NewInvoice.objects.all().delete()
            self.stdout.write(f"✓ Удалено инвойсов: {inv_count}")

            # Обнуляем балансы клиентов
            client_count = Client.objects.update(balance=Decimal("0.00"))
            self.stdout.write(f"✓ Обнулено балансов клиентов: {client_count}")

            # Обнуляем балансы складов
            wh_count = Warehouse.objects.update(balance=Decimal("0.00"))
            self.stdout.write(f"✓ Обнулено балансов складов: {wh_count}")

            # Обнуляем балансы линий
            line_count = Line.objects.update(balance=Decimal("0.00"))
            self.stdout.write(f"✓ Обнулено балансов линий: {line_count}")

            # Обнуляем балансы перевозчиков
            carrier_count = Carrier.objects.update(balance=Decimal("0.00"))
            self.stdout.write(f"✓ Обнулено балансов перевозчиков: {carrier_count}")

            # Обнуляем балансы компаний
            company_count = Company.objects.update(balance=Decimal("0.00"))
            self.stdout.write(f"✓ Обнулено балансов компаний: {company_count}")

        self.stdout.write(
            self.style.SUCCESS(
                "\n✅ Все данные биллинга успешно очищены!\n   Теперь можно начинать тестирование новой системы."
            )
        )
