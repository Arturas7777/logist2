"""
Команда для тестирования системы сравнения сумм
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal

from core.models import Car, Client, Warehouse, InvoiceOLD as Invoice
from core.services.comparison_service import ComparisonService


class Command(BaseCommand):
    help = 'Тестирует систему сравнения сумм между расчетами и счетами склада'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Количество дней для анализа (по умолчанию 30)'
        )
        parser.add_argument(
            '--client-id',
            type=int,
            help='ID конкретного клиента для тестирования'
        )
        parser.add_argument(
            '--warehouse-id',
            type=int,
            help='ID конкретного склада для тестирования'
        )

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS('🔍 Запуск тестирования системы сравнения сумм...')
        )
        
        # Определяем период для анализа
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=options['days'])
        
        self.stdout.write(f"📅 Период анализа: {start_date} - {end_date}")
        
        # Создаем сервис сравнения
        comparison_service = ComparisonService()
        
        # Общий отчет
        self.stdout.write("\n" + "="*60)
        self.stdout.write("📊 ОБЩИЙ ОТЧЕТ")
        self.stdout.write("="*60)
        
        report = comparison_service.get_comparison_report(start_date, end_date)
        
        self.stdout.write(f"🚗 Автомобилей обработано: {report['summary']['cars_count']}")
        self.stdout.write(f"💰 Общая стоимость автомобилей: {report['summary']['cars_total']:.2f} €")
        self.stdout.write(f"📄 Инвойсов создано: {report['summary']['invoices_count']}")
        self.stdout.write(f"💳 Общая сумма инвойсов: {report['summary']['invoices_total']:.2f} €")
        self.stdout.write(f"💸 Платежей произведено: {report['summary']['payments_count']}")
        self.stdout.write(f"💵 Общая сумма платежей: {report['summary']['payments_total']:.2f} €")
        
        # Разницы
        cars_vs_invoices_diff = report['summary']['cars_vs_invoices_difference']
        invoices_vs_payments_diff = report['summary']['invoices_vs_payments_difference']
        
        if cars_vs_invoices_diff != 0:
            color = self.style.WARNING if abs(cars_vs_invoices_diff) < 100 else self.style.ERROR
            self.stdout.write(
                color(f"⚠️  Разница (автомобили - инвойсы): {cars_vs_invoices_diff:.2f} €")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("✅ Стоимость автомобилей совпадает с инвойсами")
            )
        
        if invoices_vs_payments_diff != 0:
            color = self.style.WARNING if abs(invoices_vs_payments_diff) < 100 else self.style.ERROR
            self.stdout.write(
                color(f"⚠️  Разница (инвойсы - платежи): {invoices_vs_payments_diff:.2f} €")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("✅ Сумма инвойсов совпадает с платежами")
            )
        
        # Поиск расхождений
        self.stdout.write("\n" + "="*60)
        self.stdout.write("🚨 ПОИСК РАСХОЖДЕНИЙ")
        self.stdout.write("="*60)
        
        discrepancies = comparison_service.find_discrepancies(start_date, end_date)
        
        if discrepancies:
            self.stdout.write(
                self.style.WARNING(f"Найдено расхождений: {len(discrepancies)}")
            )
            
            for i, discrepancy in enumerate(discrepancies[:10], 1):  # Показываем первые 10
                self.stdout.write(f"\n{i}. {discrepancy['type'].upper()}: {discrepancy['entity']}")
                self.stdout.write(f"   📝 {discrepancy['comparison']['message']}")
                
                if discrepancy['type'] == 'client_comparison':
                    comp = discrepancy['comparison']
                    self.stdout.write(
                        f"   🚗 Автомобилей: {comp['cars_count']} | "
                        f"Стоимость: {comp['cars_total_cost']:.2f}€ | "
                        f"Инвойсы: {comp['warehouse_invoices_total']:.2f}€"
                    )
                elif discrepancy['type'] == 'warehouse_comparison':
                    comp = discrepancy['comparison']
                    self.stdout.write(
                        f"   📄 Инвойсов: {comp['invoices_count']} | "
                        f"Сумма: {comp['invoices_total']:.2f}€ | "
                        f"Платежи: {comp['payments_total']:.2f}€"
                    )
            
            if len(discrepancies) > 10:
                self.stdout.write(f"\n... и еще {len(discrepancies) - 10} расхождений")
        else:
            self.stdout.write(self.style.SUCCESS("✅ Расхождений не найдено!"))
        
        # Тестирование конкретного клиента
        if options['client_id']:
            self.stdout.write("\n" + "="*60)
            self.stdout.write("👤 АНАЛИЗ КЛИЕНТА")
            self.stdout.write("="*60)
            
            try:
                client = Client.objects.get(id=options['client_id'])
                self.stdout.write(f"Анализ клиента: {client.name}")
                
                comparison = comparison_service.compare_client_costs_with_warehouse_invoices(
                    client, start_date, end_date
                )
                
                self.stdout.write(f"📝 Статус: {comparison['status']}")
                self.stdout.write(f"💬 Сообщение: {comparison['message']}")
                
                if comparison['status'] != 'no_data':
                    self.stdout.write(f"🚗 Автомобилей: {comparison['cars_count']}")
                    self.stdout.write(f"💰 Стоимость автомобилей: {comparison['cars_total_cost']:.2f} €")
                    self.stdout.write(f"📄 Сумма инвойсов склада: {comparison['warehouse_invoices_total']:.2f} €")
                    self.stdout.write(f"📊 Разница: {comparison['difference']:.2f} €")
                
            except Client.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"❌ Клиент с ID {options['client_id']} не найден")
                )
        
        # Тестирование конкретного склада
        if options['warehouse_id']:
            self.stdout.write("\n" + "="*60)
            self.stdout.write("🏢 АНАЛИЗ СКЛАДА")
            self.stdout.write("="*60)
            
            try:
                warehouse = Warehouse.objects.get(id=options['warehouse_id'])
                self.stdout.write(f"Анализ склада: {warehouse.name}")
                
                comparison = comparison_service.compare_warehouse_costs_with_payments(
                    warehouse, start_date, end_date
                )
                
                self.stdout.write(f"📝 Статус: {comparison['status']}")
                self.stdout.write(f"💬 Сообщение: {comparison['message']}")
                
                if comparison['status'] != 'no_data':
                    self.stdout.write(f"📄 Инвойсов: {comparison['invoices_count']}")
                    self.stdout.write(f"💰 Сумма инвойсов: {comparison['invoices_total']:.2f} €")
                    self.stdout.write(f"💳 Сумма платежей: {comparison['payments_total']:.2f} €")
                    self.stdout.write(f"📊 Разница: {comparison['difference']:.2f} €")
                
            except Warehouse.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"❌ Склад с ID {options['warehouse_id']} не найден")
                )
        
        # Тестирование отдельных автомобилей (примеры)
        self.stdout.write("\n" + "="*60)
        self.stdout.write("🚗 АНАЛИЗ АВТОМОБИЛЕЙ (примеры)")
        self.stdout.write("="*60)
        
        # Получаем несколько автомобилей для примера
        cars = Car.objects.filter(
            unload_date__gte=start_date,
            unload_date__lte=end_date
        ).select_related('client', 'warehouse')[:5]
        
        if cars.exists():
            for car in cars:
                comparison = comparison_service.compare_car_costs_with_warehouse_invoices(car)
                
                status_style = self.style.SUCCESS if comparison['status'] == 'match' else self.style.WARNING
                
                self.stdout.write(f"\n🚗 {car.vin} ({car.brand} {car.year})")
                self.stdout.write(f"   👤 Клиент: {car.client.name if car.client else 'N/A'}")
                self.stdout.write(f"   🏢 Склад: {car.warehouse.name if car.warehouse else 'N/A'}")
                self.stdout.write(status_style(f"   📝 Статус: {comparison['status']}"))
                self.stdout.write(f"   💰 Стоимость автомобиля: {comparison['car_total_cost']:.2f} €")
                self.stdout.write(f"   📄 Инвойсы склада: {comparison['warehouse_invoices_total']:.2f} €")
                self.stdout.write(f"   📊 Разница: {comparison['difference']:.2f} €")
        else:
            self.stdout.write("❌ Автомобили в указанном периоде не найдены")
        
        self.stdout.write("\n" + "="*60)
        self.stdout.write(self.style.SUCCESS("✅ Тестирование завершено!"))
        self.stdout.write("="*60)
        
        # Рекомендации
        self.stdout.write("\n💡 РЕКОМЕНДАЦИИ:")
        self.stdout.write("• Регулярно проверяйте дашборд сравнения: /comparison-dashboard/")
        self.stdout.write("• Используйте API для автоматизации: /api/compare-car-costs/")
        self.stdout.write("• Настройте уведомления о расхождениях")
        self.stdout.write("• Проверяйте расхождения перед выставлением инвойсов")

