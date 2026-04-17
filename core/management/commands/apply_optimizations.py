"""
Django management команда для применения оптимизаций проекта
"""

import time

from django.core.management.base import BaseCommand
from django.db import connection

from core.models import Carrier, Client, Company, Line, Warehouse
from core.services.balance_manager import BalanceManager


class Command(BaseCommand):
    help = 'Применяет оптимизации проекта: проверяет индексы, пересчитывает балансы'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-balance-recalc',
            action='store_true',
            help='Пропустить пересчет балансов',
        )
        parser.add_argument(
            '--validate-only',
            action='store_true',
            help='Только проверить консистентность, не пересчитывать',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS('🚀 Применение оптимизаций проекта Logist2'))
        self.stdout.write(self.style.SUCCESS('=' * 60))

        # 1. Проверка индексов
        self.check_indexes()

        # 2. Проверка connection pooling
        self.check_connection_pooling()

        # 3. Валидация балансов
        if options['validate_only']:
            self.validate_balances()
        elif not options['skip_balance_recalc']:
            # 4. Пересчет балансов
            self.recalculate_balances()

        # 5. Финальный отчет
        self.print_summary()

        self.stdout.write(self.style.SUCCESS('\n✅ Оптимизации применены успешно!'))

    def check_indexes(self):
        """Проверяет наличие индексов в БД"""
        self.stdout.write('\n📊 Проверка индексов...')

        with connection.cursor() as cursor:
            # Проверяем количество индексов для core моделей
            cursor.execute("""
                SELECT
                    count(*) as index_count
                FROM pg_indexes
                WHERE schemaname = 'public'
                    AND tablename LIKE 'core_%'
            """)
            index_count = cursor.fetchone()[0]

            self.stdout.write(f'   Найдено индексов: {index_count}')

            if index_count < 30:
                self.stdout.write(self.style.WARNING(
                    '   ⚠️  Мало индексов! Возможно, миграции не применены.'
                ))
                self.stdout.write(self.style.WARNING(
                    '   Выполните: python manage.py migrate'
                ))
            else:
                self.stdout.write(self.style.SUCCESS('   ✅ Индексы созданы'))

    def check_connection_pooling(self):
        """Проверяет настройки connection pooling"""
        self.stdout.write('\n🔌 Проверка connection pooling...')

        from django.conf import settings

        conn_max_age = settings.DATABASES['default'].get('CONN_MAX_AGE', 0)

        if conn_max_age > 0:
            self.stdout.write(self.style.SUCCESS(
                f'   ✅ Connection pooling включен (CONN_MAX_AGE={conn_max_age}s)'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                '   ⚠️  Connection pooling отключен'
            ))
            self.stdout.write(self.style.WARNING(
                '   Добавьте CONN_MAX_AGE в settings.py'
            ))

    def validate_balances(self):
        """Проверяет консистентность балансов"""
        self.stdout.write('\n🔍 Валидация балансов...')

        issues_found = 0
        entities_checked = 0

        for model in [Client, Warehouse, Line, Company, Carrier]:
            model_name = model.__name__

            for entity in model.objects.all():
                entities_checked += 1
                validation = BalanceManager.validate_balance_consistency(entity)

                if not validation['is_valid']:
                    issues_found += len(validation['issues'])
                    self.stdout.write(self.style.ERROR(
                        f'   ❌ {model_name} #{entity.id} ({entity}): '
                        f'{", ".join(validation["issues"])}'
                    ))

        self.stdout.write(f'\n   Проверено сущностей: {entities_checked}')

        if issues_found > 0:
            self.stdout.write(self.style.ERROR(
                f'   ❌ Найдено проблем: {issues_found}'
            ))
            self.stdout.write(self.style.WARNING(
                '   Запустите команду без --validate-only для исправления'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                '   ✅ Все балансы консистентны'
            ))

    def recalculate_balances(self):
        """Пересчитывает все балансы"""
        self.stdout.write('\n💰 Пересчет балансов...')

        start_time = time.time()

        try:
            result = BalanceManager.recalculate_all_balances()

            elapsed = time.time() - start_time

            self.stdout.write(self.style.SUCCESS(
                f'   ✅ Пересчитано сущностей: {result["entities_updated"]}'
            ))
            self.stdout.write(f'   ⏱️  Время выполнения: {elapsed:.2f}s')

        except Exception as e:
            self.stdout.write(self.style.ERROR(
                f'   ❌ Ошибка пересчета: {e}'
            ))

    def print_summary(self):
        """Выводит итоговую статистику"""
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('📊 Итоговая статистика:'))
        self.stdout.write('=' * 60)

        # Статистика по моделям
        stats = {
            'Автомобили': ('Car', ['FLOATING', 'IN_PORT', 'UNLOADED', 'TRANSFERRED']),
            'Контейнеры': ('Container', ['FLOATING', 'IN_PORT', 'UNLOADED', 'TRANSFERRED']),
            'Инвойсы': ('Invoice', None),
            'Платежи': ('Payment', None),
            'Клиенты': ('Client', None),
            'Склады': ('Warehouse', None),
            'Линии': ('Line', None),
            'Компании': ('Company', None),
        }

        for label, (model_name, statuses) in stats.items():
            from django.apps import apps
            model = apps.get_model('core', model_name)

            if statuses:
                # Модели со статусами
                for status in statuses:
                    count = model.objects.filter(status=status).count()
                    self.stdout.write(f'   {label} ({status}): {count}')
            else:
                count = model.objects.count()
                self.stdout.write(f'   {label}: {count}')

        self.stdout.write('=' * 60)
