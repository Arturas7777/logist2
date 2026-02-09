"""
Помощник для настройки подключения к site.pro (b1.lt) Accounting API.

Этапы:
1. Ввод учётных данных (username/password от site.pro)
2. Тестовая аутентификация через API
3. Сохранение credentials в SiteProConnection (зашифрованные)
4. Опциональная настройка параметров инвойсов

Использование:
    python manage.py setup_sitepro
"""

from django.core.management.base import BaseCommand
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Настройка подключения к site.pro (b1.lt) Accounting API'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n  Настройка site.pro (b1.lt) Accounting API\n'
        ))

        # ── Шаг 1: Запрашиваем учётные данные ──
        self.stdout.write(self.style.MIGRATE_HEADING('Шаг 1: Учётные данные site.pro'))
        self.stdout.write(
            '  Введите логин и пароль от вашего аккаунта site.pro.\n'
            '  Данные будут зашифрованы и сохранены в базе данных.\n'
        )

        username = input('  Email/Username: ').strip()
        if not username:
            self.stdout.write(self.style.ERROR('Username обязателен!'))
            return

        password = input('  Password: ').strip()
        if not password:
            self.stdout.write(self.style.ERROR('Password обязателен!'))
            return

        # ── Шаг 2: Тестовая аутентификация ──
        self.stdout.write(self.style.MIGRATE_HEADING('\nШаг 2: Тестовая аутентификация'))
        self.stdout.write('  Проверяем подключение к https://api.sitepro.com...\n')

        # Создаём временный объект для теста
        from core.models_accounting import SiteProConnection
        from core.models import Company

        company = Company.objects.filter(name__icontains='Caromoto').first()
        if not company:
            self.stdout.write(self.style.ERROR(
                'Компания Caromoto не найдена! Создайте компанию сначала.'
            ))
            return

        # Проверяем существующее подключение
        existing = SiteProConnection.objects.filter(company=company).first()
        if existing:
            self.stdout.write(self.style.WARNING(
                f'  Найдено существующее подключение: {existing.name}\n'
                f'  Оно будет обновлено.\n'
            ))

        # Создаём/обновляем подключение
        conn_name = input(
            f'  Название подключения (по умолчанию "Site.pro Caromoto"): '
        ).strip()
        if not conn_name:
            conn_name = 'Site.pro Caromoto'

        conn, created = SiteProConnection.objects.update_or_create(
            company=company,
            defaults={
                'name': conn_name,
                'is_active': True,
            }
        )

        # Устанавливаем зашифрованные credentials
        conn.username = username
        conn.password = password
        conn.save()

        action = 'создано' if created else 'обновлено'
        self.stdout.write(self.style.SUCCESS(f'  Подключение {action}: {conn}\n'))

        # Тестируем аутентификацию
        from core.services.sitepro_service import SiteProService
        service = SiteProService(conn)
        result = service.test_connection()

        if result['success']:
            self.stdout.write(self.style.SUCCESS(
                f'  Аутентификация успешна!\n'
                f'  SitePro User ID: {result["user_id"]}\n'
                f'  SitePro Company ID: {result["company_id"]}\n'
            ))
        else:
            self.stdout.write(self.style.ERROR(
                f'  Ошибка аутентификации: {result["error"]}\n'
                f'  Проверьте логин/пароль и наличие API-доступа в тарифе.\n'
            ))
            return

        # ── Шаг 3: Настройки инвойсов ──
        self.stdout.write(self.style.MIGRATE_HEADING('Шаг 3: Настройки инвойсов'))

        vat_rate = input('  Ставка НДС по умолчанию в % (по умолчанию 0): ').strip()
        if vat_rate:
            try:
                conn.default_vat_rate = float(vat_rate)
            except ValueError:
                self.stdout.write(self.style.WARNING('  Некорректное значение, используется 0'))

        currency = input('  Валюта по умолчанию (по умолчанию EUR): ').strip()
        if currency:
            conn.default_currency = currency.upper()

        series = input('  Серия инвойсов в site.pro (по умолчанию пусто): ').strip()
        if series:
            conn.invoice_series = series

        auto_push = input(
            '  Автоматически отправлять инвойсы при выставлении? (y/n, по умолчанию n): '
        ).strip().lower()
        conn.auto_push_on_issue = (auto_push == 'y')

        conn.save()

        self.stdout.write(self.style.SUCCESS(
            f'\n  Настройка завершена!\n'
            f'  Подключение: {conn.name}\n'
            f'  НДС: {conn.default_vat_rate}%\n'
            f'  Валюта: {conn.default_currency}\n'
            f'  Серия: {conn.invoice_series or "(не задана)"}\n'
            f'  Авто-отправка: {"Да" if conn.auto_push_on_issue else "Нет"}\n'
        ))

        self.stdout.write(
            '  Для отправки инвойсов используйте:\n'
            '  - Админка → Инвойсы → Выбрать → "Отправить в site.pro"\n'
            '  - Или включите авто-отправку для автоматической синхронизации\n'
        )
