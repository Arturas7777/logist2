"""
Ревизор балансов (фаза 4, PR 4.3).

Сверяет сохранённое поле ``balance`` каждой сущности
(Client/Company/Warehouse/Line/Carrier) с ожидаемым значением по
COMPLETED-транзакциям, а также ``paid_amount`` открытых инвойсов с суммой
платежей. Использует ЕДИНУЮ каноническую логику
``Transaction.expected_entity_balance`` (для контрагентов — только Tx без
инвойса), поэтому не даёт ложных расхождений.

Страховка от рассинхрона денормализованного ``balance``: запускать
периодически (Celery beat уже гоняет ``check_balance_consistency``) или
вручную перед аудитом.

Использование:
    python manage.py verify_balances                 # только отчёт
    python manage.py verify_balances --fix           # исправить расхождения
    python manage.py verify_balances --entity client # только клиенты
    python manage.py verify_balances --no-invoices   # без проверки инвойсов
"""

from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction

ENTITY_CHOICES = ("client", "company", "warehouse", "line", "carrier")


class Command(BaseCommand):
    help = "Сверка балансов сущностей и paid_amount инвойсов с расчётом из транзакций"

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Исправить расхождения (по умолчанию — только отчёт)",
        )
        parser.add_argument(
            "--entity",
            choices=ENTITY_CHOICES,
            default=None,
            help="Проверить только одну группу сущностей",
        )
        parser.add_argument(
            "--no-invoices",
            action="store_true",
            help="Не проверять paid_amount инвойсов",
        )

    def handle(self, *args, **options):
        from core.models_billing import NewInvoice
        from core.tasks import _collect_balance_mismatches

        fix = options["fix"]
        entity_filter = options["entity"]
        check_invoices = not options["no_invoices"]

        balance_mismatches, invoice_mismatches = _collect_balance_mismatches()

        if entity_filter:
            wanted = entity_filter.capitalize()
            balance_mismatches = [m for m in balance_mismatches if m["model"].__name__ == wanted]

        self.stdout.write("=== Сверка балансов ===")
        if not balance_mismatches:
            self.stdout.write(self.style.SUCCESS("  Балансы сущностей консистентны."))
        else:
            for m in balance_mismatches:
                diff = m["stored"] - m["expected"]
                self.stdout.write(
                    self.style.WARNING(
                        f"  {m['model'].__name__} id={m['pk']}: "
                        f"stored={m['stored']}, expected={m['expected']} (diff={diff})"
                    )
                )
            if fix:
                fixed = self._fix_balances(balance_mismatches)
                self.stdout.write(self.style.SUCCESS(f"  Исправлено балансов: {fixed}"))

        if check_invoices:
            self.stdout.write("\n=== Сверка paid_amount инвойсов ===")
            if not invoice_mismatches:
                self.stdout.write(self.style.SUCCESS("  paid_amount инвойсов консистентны."))
            else:
                for m in invoice_mismatches:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Инвойс {m['number']} (id={m['pk']}): stored={m['stored']}, expected={m['expected']}"
                        )
                    )
                if fix:
                    fixed = self._fix_invoices(invoice_mismatches, NewInvoice)
                    self.stdout.write(self.style.SUCCESS(f"  Исправлено инвойсов: {fixed}"))

        total = len(balance_mismatches) + (len(invoice_mismatches) if check_invoices else 0)
        self.stdout.write("")
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Всё консистентно."))
        elif fix:
            self.stdout.write(self.style.SUCCESS(f"Готово. Обработано расхождений: {total}."))
        else:
            self.stdout.write(self.style.WARNING(f"Найдено расхождений: {total}. Запустите с --fix для исправления."))

    def _fix_balances(self, mismatches):
        fixed = 0
        for m in mismatches:
            with db_transaction.atomic():
                entity = m["model"].objects.select_for_update().get(pk=m["pk"])
                entity.balance = m["expected"]
                entity.save(update_fields=["balance", "balance_updated_at"])
                fixed += 1
        return fixed

    def _fix_invoices(self, mismatches, NewInvoice):
        fixed = 0
        for m in mismatches:
            with db_transaction.atomic():
                inv = NewInvoice.objects.select_for_update().get(pk=m["pk"])
                inv.paid_amount = m["expected"]
                inv.update_status()
                inv.save(update_fields=["paid_amount", "status", "updated_at"])
                fixed += 1
        return fixed
