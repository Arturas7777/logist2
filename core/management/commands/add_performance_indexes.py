"""
Команда для добавления индексов производительности напрямую через SQL
"""

from django.core.management.base import BaseCommand
from django.db import connection
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Добавляет индексы производительности в базу данных'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать SQL команды без их выполнения',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        # SQL команды для создания индексов
        indexes = [
            # Индексы для модели Car
            ("idx_car_vin", "CREATE INDEX IF NOT EXISTS idx_car_vin ON core_car (vin);"),
            ("idx_car_status", "CREATE INDEX IF NOT EXISTS idx_car_status ON core_car (status);"),
            ("idx_car_unload_date", "CREATE INDEX IF NOT EXISTS idx_car_unload_date ON core_car (unload_date);"),
            ("idx_car_client_id", "CREATE INDEX IF NOT EXISTS idx_car_client_id ON core_car (client_id);"),
            ("idx_car_warehouse_id", "CREATE INDEX IF NOT EXISTS idx_car_warehouse_id ON core_car (warehouse_id);"),
            ("idx_car_container_id", "CREATE INDEX IF NOT EXISTS idx_car_container_id ON core_car (container_id);"),
            ("idx_car_brand", "CREATE INDEX IF NOT EXISTS idx_car_brand ON core_car (brand);"),
            ("idx_car_year", "CREATE INDEX IF NOT EXISTS idx_car_year ON core_car (year);"),
            
            # Составные индексы для Car
            ("idx_car_client_status", "CREATE INDEX IF NOT EXISTS idx_car_client_status ON core_car (client_id, status);"),
            ("idx_car_warehouse_status", "CREATE INDEX IF NOT EXISTS idx_car_warehouse_status ON core_car (warehouse_id, status);"),
            ("idx_car_unload_date_status", "CREATE INDEX IF NOT EXISTS idx_car_unload_date_status ON core_car (unload_date, status);"),
            
            # Индексы для модели Invoice
            ("idx_invoice_number", "CREATE INDEX IF NOT EXISTS idx_invoice_number ON core_invoice (number);"),
            ("idx_invoice_issue_date", "CREATE INDEX IF NOT EXISTS idx_invoice_issue_date ON core_invoice (issue_date);"),
            ("idx_invoice_paid", "CREATE INDEX IF NOT EXISTS idx_invoice_paid ON core_invoice (paid);"),
            ("idx_invoice_from_entity_type", "CREATE INDEX IF NOT EXISTS idx_invoice_from_entity_type ON core_invoice (from_entity_type);"),
            ("idx_invoice_to_entity_type", "CREATE INDEX IF NOT EXISTS idx_invoice_to_entity_type ON core_invoice (to_entity_type);"),
            ("idx_invoice_from_entity_id", "CREATE INDEX IF NOT EXISTS idx_invoice_from_entity_id ON core_invoice (from_entity_id);"),
            ("idx_invoice_to_entity_id", "CREATE INDEX IF NOT EXISTS idx_invoice_to_entity_id ON core_invoice (to_entity_id);"),
            
            # Составные индексы для Invoice
            ("idx_invoice_entity_type_id", "CREATE INDEX IF NOT EXISTS idx_invoice_entity_type_id ON core_invoice (from_entity_type, from_entity_id);"),
            ("idx_invoice_to_entity_type_id", "CREATE INDEX IF NOT EXISTS idx_invoice_to_entity_type_id ON core_invoice (to_entity_type, to_entity_id);"),
            ("idx_invoice_issue_date_paid", "CREATE INDEX IF NOT EXISTS idx_invoice_issue_date_paid ON core_invoice (issue_date, paid);"),
            
            # Индексы для модели Payment
            ("idx_payment_date", "CREATE INDEX IF NOT EXISTS idx_payment_date ON core_payment (date);"),
            ("idx_payment_payment_type", "CREATE INDEX IF NOT EXISTS idx_payment_payment_type ON core_payment (payment_type);"),
            ("idx_payment_from_balance", "CREATE INDEX IF NOT EXISTS idx_payment_from_balance ON core_payment (from_balance);"),
            ("idx_payment_sender_content_type", "CREATE INDEX IF NOT EXISTS idx_payment_sender_content_type ON core_payment (sender_content_type_id);"),
            ("idx_payment_sender_object_id", "CREATE INDEX IF NOT EXISTS idx_payment_sender_object_id ON core_payment (sender_object_id);"),
            ("idx_payment_recipient_content_type", "CREATE INDEX IF NOT EXISTS idx_payment_recipient_content_type ON core_payment (recipient_content_type_id);"),
            ("idx_payment_recipient_object_id", "CREATE INDEX IF NOT EXISTS idx_payment_recipient_object_id ON core_payment (recipient_object_id);"),
            ("idx_payment_invoice_id", "CREATE INDEX IF NOT EXISTS idx_payment_invoice_id ON core_payment (invoice_id);"),
            
            # Составные индексы для Payment
            ("idx_payment_sender", "CREATE INDEX IF NOT EXISTS idx_payment_sender ON core_payment (sender_content_type_id, sender_object_id);"),
            ("idx_payment_recipient", "CREATE INDEX IF NOT EXISTS idx_payment_recipient ON core_payment (recipient_content_type_id, recipient_object_id);"),
            ("idx_payment_date_type", "CREATE INDEX IF NOT EXISTS idx_payment_date_type ON core_payment (date, payment_type);"),
            
            # Индексы для модели Container
            ("idx_container_number", "CREATE INDEX IF NOT EXISTS idx_container_number ON core_container (number);"),
            ("idx_container_status", "CREATE INDEX IF NOT EXISTS idx_container_status ON core_container (status);"),
            ("idx_container_eta", "CREATE INDEX IF NOT EXISTS idx_container_eta ON core_container (eta);"),
            ("idx_container_unload_date", "CREATE INDEX IF NOT EXISTS idx_container_unload_date ON core_container (unload_date);"),
            ("idx_container_client_id", "CREATE INDEX IF NOT EXISTS idx_container_client_id ON core_container (client_id);"),
            ("idx_container_warehouse_id", "CREATE INDEX IF NOT EXISTS idx_container_warehouse_id ON core_container (warehouse_id);"),
            ("idx_container_line_id", "CREATE INDEX IF NOT EXISTS idx_container_line_id ON core_container (line_id);"),
            
            # Составные индексы для Container
            ("idx_container_client_status", "CREATE INDEX IF NOT EXISTS idx_container_client_status ON core_container (client_id, status);"),
            ("idx_container_warehouse_status", "CREATE INDEX IF NOT EXISTS idx_container_warehouse_status ON core_container (warehouse_id, status);"),
            ("idx_container_eta_status", "CREATE INDEX IF NOT EXISTS idx_container_eta_status ON core_container (eta, status);"),
            
            # Индексы для модели Client
            ("idx_client_name", "CREATE INDEX IF NOT EXISTS idx_client_name ON core_client (name);"),
            
            # Индексы для модели Warehouse
            ("idx_warehouse_name", "CREATE INDEX IF NOT EXISTS idx_warehouse_name ON core_warehouse (name);"),
            
            # Индексы для модели Line
            ("idx_line_name", "CREATE INDEX IF NOT EXISTS idx_line_name ON core_line (name);"),
            
            # Индексы для модели Company
            ("idx_company_name", "CREATE INDEX IF NOT EXISTS idx_company_name ON core_company (name);"),
            
            # Индексы для модели Carrier
            ("idx_carrier_name", "CREATE INDEX IF NOT EXISTS idx_carrier_name ON core_carrier (name);"),
            ("idx_carrier_short_name", "CREATE INDEX IF NOT EXISTS idx_carrier_short_name ON core_carrier (short_name);"),
        ]
        
        if dry_run:
            self.stdout.write("SQL команды для создания индексов:")
            for name, sql in indexes:
                self.stdout.write(f"-- {name}")
                self.stdout.write(sql)
                self.stdout.write("")
        else:
            with connection.cursor() as cursor:
                created_count = 0
                for name, sql in indexes:
                    try:
                        cursor.execute(sql)
                        self.stdout.write(
                            self.style.SUCCESS(f"✓ Создан индекс: {name}")
                        )
                        created_count += 1
                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(f"✗ Ошибка создания индекса {name}: {e}")
                        )
                
                self.stdout.write(
                    self.style.SUCCESS(f"\nСоздано индексов: {created_count}/{len(indexes)}")
                )
