"""
Тестовая команда для проверки добавления наценки в инвойсы
"""
from django.core.management.base import BaseCommand
from core.models import Car, Company, Client
from core.models_billing import NewInvoice
from django.utils import timezone
from decimal import Decimal


class Command(BaseCommand):
    help = 'Проверяет добавление наценки Caromoto Lithuania в инвойсы'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write(self.style.SUCCESS('ТЕСТ: Проверка наценки в инвойсах'))
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write('')

        # Ищем переданные автомобили с наценкой
        transferred_cars_qs = Car.objects.filter(
            status='TRANSFERRED',
            proft__gt=0
        ).select_related('client', 'warehouse')
        
        if not transferred_cars_qs.exists():
            self.stdout.write(self.style.ERROR('[ERROR] Не найдено переданных автомобилей с наценкой'))
            return
        
        transferred_cars = list(transferred_cars_qs[:3])
        
        self.stdout.write(self.style.WARNING(f'[INFO] Найдено {len(transferred_cars)} переданных автомобилей с наценкой'))
        self.stdout.write('')
        
        # Показываем информацию об автомобилях
        for car in transferred_cars:
            self.stdout.write(f'  - {car.vin} ({car.brand})')
            self.stdout.write(f'    Клиент: {car.client.name if car.client else "Не указан"}')
            self.stdout.write(f'    Наценка: {car.proft}')
            self.stdout.write(f'    Дата передачи: {car.transfer_date}')
        self.stdout.write('')
        
        # Получаем Caromoto Lithuania
        try:
            caromoto = Company.objects.get(name="Caromoto Lithuania")
            self.stdout.write(self.style.SUCCESS(f'[OK] Найдена компания: {caromoto.name}'))
        except Company.DoesNotExist:
            self.stdout.write(self.style.ERROR('[ERROR] Компания Caromoto Lithuania не найдена!'))
            return
        
        # Получаем клиента
        client = transferred_cars[0].client
        if not client:
            self.stdout.write(self.style.ERROR('[ERROR] У автомобилей нет клиента'))
            return
        
        self.stdout.write(self.style.SUCCESS(f'[OK] Клиент: {client.name}'))
        self.stdout.write('')
        
        # Создаем тестовый инвойс
        self.stdout.write(self.style.WARNING('[ACTION] Создаем тестовый инвойс...'))
        
        invoice = NewInvoice.objects.create(
            issuer_company=caromoto,
            recipient_client=client,
            date=timezone.now().date(),
            due_date=timezone.now().date() + timezone.timedelta(days=14)
        )
        
        # Добавляем автомобили
        invoice.cars.add(*transferred_cars)
        
        self.stdout.write(self.style.SUCCESS(f'[OK] Инвойс создан: {invoice.number}'))
        self.stdout.write(f'    Выставитель: {invoice.issuer_name}')
        self.stdout.write(f'    Получатель: {invoice.recipient_name}')
        self.stdout.write(f'    Автомобилей: {invoice.cars.count()}')
        self.stdout.write('')
        
        # Генерируем позиции
        self.stdout.write(self.style.WARNING('[ACTION] Генерируем позиции инвойса...'))
        invoice.regenerate_items_from_cars()
        
        self.stdout.write(self.style.SUCCESS(f'[OK] Создано позиций: {invoice.items.count()}'))
        self.stdout.write('')
        
        # Проверяем наличие наценки в позициях
        self.stdout.write(self.style.WARNING('[CHECK] Проверяем позиции инвойса:'))
        
        markup_items = []
        storage_items = []
        service_items = []
        
        for item in invoice.items.all().order_by('order'):
            self.stdout.write(f'  {item.order + 1}. {item.description}')
            self.stdout.write(f'     Кол-во: {item.quantity}, Цена: {item.unit_price}, Итого: {item.total_price}')
            
            if 'наценка' in item.description.lower():
                markup_items.append(item)
            elif 'хранение' in item.description.lower():
                storage_items.append(item)
            else:
                service_items.append(item)
        
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write(self.style.SUCCESS('РЕЗУЛЬТАТЫ:'))
        self.stdout.write(self.style.SUCCESS('=' * 70))
        
        self.stdout.write(f'Позиций хранения: {len(storage_items)}')
        self.stdout.write(f'Позиций услуг: {len(service_items)}')
        self.stdout.write(f'Позиций наценки: {len(markup_items)}')
        self.stdout.write('')
        
        if markup_items:
            self.stdout.write(self.style.SUCCESS(f'[SUCCESS] Наценка ДОБАВЛЕНА в инвойс!'))
            total_markup = sum(item.total_price for item in markup_items)
            self.stdout.write(self.style.SUCCESS(f'[SUCCESS] Общая наценка: {total_markup}'))
        else:
            self.stdout.write(self.style.ERROR(f'[ERROR] Наценка НЕ ДОБАВЛЕНА в инвойс!'))
            self.stdout.write(self.style.ERROR(f'[ERROR] Проверьте логи для деталей'))
        
        self.stdout.write('')
        self.stdout.write(f'Итого по инвойсу: {invoice.total}')
        self.stdout.write('')
        
        # Удаляем тестовый инвойс
        self.stdout.write(self.style.WARNING('[CLEANUP] Удаляем тестовый инвойс...'))
        invoice.delete()
        self.stdout.write(self.style.SUCCESS('[OK] Тестовый инвойс удален'))
        self.stdout.write('')

