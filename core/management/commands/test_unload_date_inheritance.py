"""
Тестовая команда для проверки наследования даты разгрузки контейнера
"""
from django.core.management.base import BaseCommand
from core.models import Container, Car
from django.utils import timezone
from datetime import timedelta


class Command(BaseCommand):
    help = 'Проверяет работу наследования даты разгрузки контейнера'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write(self.style.SUCCESS('ТЕСТ: Наследование даты разгрузки контейнера'))
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write('')

        # Найдем контейнер с автомобилями
        container = Container.objects.filter(container_cars__isnull=False).first()
        
        if not container:
            self.stdout.write(self.style.ERROR('[ERROR] Не найдено контейнеров с автомобилями для тестирования'))
            return

        self.stdout.write(self.style.WARNING(f'[CONTAINER] Тестируем контейнер: {container.number}'))
        self.stdout.write(f'   Текущая дата разгрузки: {container.unload_date}')
        self.stdout.write(f'   Количество автомобилей: {container.container_cars.count()}')
        self.stdout.write('')

        # Показываем текущее состояние автомобилей
        self.stdout.write(self.style.WARNING('[CARS] Текущие даты разгрузки автомобилей:'))
        for car in container.container_cars.all()[:10]:  # Показываем первые 10
            self.stdout.write(f'   - {car.vin}: {car.unload_date}')
        
        if container.container_cars.count() > 10:
            self.stdout.write(f'   ... и еще {container.container_cars.count() - 10} автомобилей')
        self.stdout.write('')

        # Сохраняем старую дату
        old_date = container.unload_date
        
        # Устанавливаем новую тестовую дату (сегодня)
        new_date = timezone.now().date()
        
        self.stdout.write(self.style.WARNING(f'[ACTION] Меняем дату разгрузки контейнера...'))
        self.stdout.write(f'   Старая дата: {old_date}')
        self.stdout.write(f'   Новая дата: {new_date}')
        self.stdout.write('')
        
        # Изменяем дату контейнера
        container.unload_date = new_date
        container.save()
        
        # Обновляем из БД
        container.refresh_from_db()
        
        self.stdout.write(self.style.SUCCESS('[OK] Контейнер сохранен'))
        self.stdout.write(f'   Дата разгрузки контейнера: {container.unload_date}')
        self.stdout.write('')

        # Проверяем, обновились ли автомобили
        self.stdout.write(self.style.WARNING('[CHECK] Проверяем автомобили после изменения...'))
        
        updated_count = 0
        not_updated_count = 0
        
        for car in container.container_cars.all():
            car.refresh_from_db()
            if car.unload_date == new_date:
                updated_count += 1
            else:
                not_updated_count += 1
                self.stdout.write(self.style.ERROR(f'   [FAIL] {car.vin}: дата НЕ обновлена (осталась {car.unload_date})'))
        
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write(self.style.SUCCESS('РЕЗУЛЬТАТЫ:'))
        self.stdout.write(self.style.SUCCESS('=' * 70))
        
        if not_updated_count == 0:
            self.stdout.write(self.style.SUCCESS(f'[SUCCESS] ВСЕ {updated_count} автомобилей успешно обновлены!'))
            self.stdout.write(self.style.SUCCESS(f'[SUCCESS] Наследование даты разгрузки работает КОРРЕКТНО!'))
        else:
            self.stdout.write(self.style.ERROR(f'[ERROR] Обновлено: {updated_count} из {updated_count + not_updated_count}'))
            self.stdout.write(self.style.ERROR(f'[ERROR] НЕ обновлено: {not_updated_count}'))
            self.stdout.write(self.style.ERROR(f'[ERROR] ПРОБЛЕМА: Наследование даты разгрузки НЕ работает!'))
        
        self.stdout.write('')
        
        # Возвращаем старую дату
        if old_date:
            self.stdout.write(self.style.WARNING('[RESTORE] Возвращаем исходную дату...'))
            container.unload_date = old_date
            container.save()
            self.stdout.write(self.style.SUCCESS('[OK] Исходная дата восстановлена'))
        
        self.stdout.write('')

