"""Безопасно меняет `default_markup` услуги склада в каталоге.

Зачем отдельная команда: обычное сохранение `WarehouseService` через админку
триггерит сигнал `update_cars_on_warehouse_service_change`, который МАССОВО
перезаписывает `markup_amount` у всех `CarService` этого склада (включая
переданные авто и не-FIXED клиентов) и сбивает распределённую тарифную наценку
у FIXED-авто. Эта команда меняет ТОЛЬКО значение в каталоге через `.update()`
(QuerySet.update сигнал post_save НЕ вызывает), поэтому существующие авто не
затрагиваются — изменение влияет только на вновь создаваемые услуги авто.

Примеры:
    python manage.py set_warehouse_service_markup --id 123 --markup 0 --dry-run
    python manage.py set_warehouse_service_markup --warehouse NETO \
        --name-contains "Разгрузка" --markup 0
"""

from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from core.models import WarehouseService


class Command(BaseCommand):
    help = "Меняет default_markup услуги склада без массового пересчёта существующих авто"

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, help="ID услуги склада (точное совпадение)")
        parser.add_argument("--warehouse", type=str, help="Имя склада (для поиска по имени услуги)")
        parser.add_argument("--name-contains", type=str, help="Подстрока в названии услуги")
        parser.add_argument("--markup", type=str, default="0", help="Новое значение default_markup (по умолчанию 0)")
        parser.add_argument("--dry-run", action="store_true", help="Только показать, без записи")

    def handle(self, *args, **options):
        try:
            markup = Decimal(str(options["markup"]))
        except (InvalidOperation, TypeError):
            raise CommandError(f"Некорректное значение --markup: {options['markup']!r}")

        if options.get("id"):
            qs = WarehouseService.objects.filter(id=options["id"])
        else:
            warehouse = options.get("warehouse")
            name_contains = options.get("name_contains")
            if not (warehouse and name_contains):
                raise CommandError("Укажите либо --id, либо пару --warehouse и --name-contains")
            qs = WarehouseService.objects.filter(warehouse__name__iexact=warehouse, name__icontains=name_contains)

        services = list(qs.select_related("warehouse"))
        if not services:
            raise CommandError("Услуги не найдены по заданным критериям")

        self.stdout.write(f"Найдено услуг: {len(services)}")
        for ws in services:
            self.stdout.write(
                f"  #{ws.id} [{ws.warehouse.name}] {ws.name!r}: "
                f"default_markup {ws.default_markup} -> {markup} "
                f"(add_by_default={ws.add_by_default}, active={ws.is_active})"
            )

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("[dry-run] изменения не сохранены"))
            return

        # ВАЖНО: .update() в обход сигнала — существующие CarService не трогаем.
        updated = qs.update(default_markup=markup)
        self.stdout.write(self.style.SUCCESS(f"Обновлено услуг: {updated} (существующие авто не затронуты)"))
