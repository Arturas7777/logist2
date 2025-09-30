from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import Company, Balance, Client, Line, Warehouse
from django.contrib.contenttypes.models import ContentType


class Command(BaseCommand):
    help = 'Инициализирует новую систему балансов: создает компанию Caromoto Lithuania и балансы для всех сущностей'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Принудительно пересоздать компанию и балансы',
        )

    def handle(self, *args, **options):
        self.stdout.write('Начинаем инициализацию новой системы балансов...')
        
        # Создаем или получаем компанию Caromoto Lithuania
        company, created = Company.objects.get_or_create(
            name='Caromoto Lithuania',
            defaults={
                'invoice_balance': 0.00,
                'cash_balance': 0.00,
                'card_balance': 0.00,
            }
        )
        
        if created:
            self.stdout.write(
                self.style.SUCCESS(f'Создана компания: {company.name}')
            )
        else:
            if options['force']:
                company.invoice_balance = 0.00
                company.cash_balance = 0.00
                company.card_balance = 0.00
                company.save()
                self.stdout.write(
                    self.style.WARNING(f'Обновлена компания: {company.name}')
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f'Компания уже существует: {company.name}')
                )
        
        # Создаем балансы для компании
        company_content_type = ContentType.objects.get_for_model(Company)
        for balance_type in ['INVOICE', 'CASH', 'CARD']:
            balance, created = Balance.objects.get_or_create(
                content_type=company_content_type,
                object_id=company.id,
                balance_type=balance_type,
                defaults={'amount': 0.00}
            )
            if created:
                self.stdout.write(
                    self.style.SUCCESS(f'Создан {balance_type} баланс для компании')
                )
        
        # Создаем балансы для всех клиентов
        client_content_type = ContentType.objects.get_for_model(Client)
        clients = Client.objects.all()
        self.stdout.write(f'Обрабатываем {clients.count()} клиентов...')
        
        for client in clients:
            for balance_type in ['INVOICE', 'CASH', 'CARD']:
                balance, created = Balance.objects.get_or_create(
                    content_type=client_content_type,
                    object_id=client.id,
                    balance_type=balance_type,
                    defaults={'amount': 0.00}
                )
                if created:
                    self.stdout.write(
                        f'  Создан {balance_type} баланс для клиента {client.name}'
                    )
        
        # Создаем балансы для всех линий
        line_content_type = ContentType.objects.get_for_model(Line)
        lines = Line.objects.all()
        self.stdout.write(f'Обрабатываем {lines.count()} линий...')
        
        for line in lines:
            for balance_type in ['INVOICE', 'CASH', 'CARD']:
                balance, created = Balance.objects.get_or_create(
                    content_type=line_content_type,
                    object_id=line.id,
                    balance_type=balance_type,
                    defaults={'amount': 0.00}
                )
                if created:
                    self.stdout.write(
                        f'  Создан {balance_type} баланс для линии {line.name}'
                    )
        
        # Создаем балансы для всех складов
        warehouse_content_type = ContentType.objects.get_for_model(Warehouse)
        warehouses = Warehouse.objects.all()
        self.stdout.write(f'Обрабатываем {warehouses.count()} складов...')
        
        for warehouse in warehouses:
            for balance_type in ['INVOICE', 'CASH', 'CARD']:
                balance, created = Balance.objects.get_or_create(
                    content_type=warehouse_content_type,
                    object_id=warehouse.id,
                    balance_type=balance_type,
                    defaults={'amount': 0.00}
                )
                if created:
                    self.stdout.write(
                        f'  Создан {balance_type} баланс для склада {warehouse.name}'
                    )
        
        self.stdout.write(
            self.style.SUCCESS('Инициализация новой системы балансов завершена успешно!')
        )
        
        # Показываем статистику
        total_balances = Balance.objects.count()
        self.stdout.write(f'Всего создано балансов: {total_balances}')
        
        # Показываем балансы компании
        company_balances = Balance.objects.filter(
            content_type=company_content_type,
            object_id=company.id
        )
        self.stdout.write('\nБалансы компании Caromoto Lithuania:')
        for balance in company_balances:
            self.stdout.write(f'  {balance.balance_type}: {balance.amount:.2f}')
