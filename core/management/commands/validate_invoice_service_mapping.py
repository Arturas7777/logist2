"""Валидация invoice_service_mapping.json.

Проверяет:
1. Все указанные service_id реально существуют в каталогах WarehouseService / LineService / CarrierService / CompanyService.
2. Нет дублей service_id внутри одного контрагента (разные AI_service_type не должны указывать на один ID).
3. entity_id реально существует в таблице соответствующего поставщика.

Использование:
    python manage.py validate_invoice_service_mapping
    python manage.py validate_invoice_service_mapping --strict  # exit 1 при любых ошибках
"""
from __future__ import annotations

import json
import os
import sys

from django.core.management.base import BaseCommand, CommandError


PROVIDER_MODEL_MAP = {
    'WAREHOUSE': ('core', 'Warehouse', 'WarehouseService'),
    'LINE':      ('core', 'Line',      'LineService'),
    'CARRIER':   ('core', 'Carrier',   'CarrierService'),
    'COMPANY':   ('core', 'Company',   'CompanyService'),
}


class Command(BaseCommand):
    help = "Проверяет целостность core/invoice_service_mapping.json"

    def add_arguments(self, parser):
        parser.add_argument('--strict', action='store_true',
                            help='Выйти с кодом 1 при любой ошибке (для CI).')

    def handle(self, *args, **options):
        from django.apps import apps

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'invoice_service_mapping.json',
        )
        if not os.path.exists(path):
            raise CommandError(f"Файл не найден: {path}")

        with open(path, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise CommandError(f"Невалидный JSON: {e}")

        data.pop('_comment', None)

        errors: list[str] = []
        warnings: list[str] = []

        for key, conf in data.items():
            provider_type = conf.get('provider_type')
            entity_id = conf.get('entity_id')
            services = conf.get('services', {})

            if provider_type not in PROVIDER_MODEL_MAP:
                errors.append(f"[{key}] Неизвестный provider_type: {provider_type!r}")
                continue

            app_label, owner_model_name, service_model_name = PROVIDER_MODEL_MAP[provider_type]
            try:
                OwnerModel = apps.get_model(app_label, owner_model_name)
                ServiceModel = apps.get_model(app_label, service_model_name)
            except LookupError as e:
                errors.append(f"[{key}] Модель не найдена: {e}")
                continue

            if not OwnerModel.objects.filter(pk=entity_id).exists():
                errors.append(
                    f"[{key}] entity_id={entity_id} отсутствует в {owner_model_name}"
                )

            seen_ids: dict[int, str] = {}
            for ai_type, sid in services.items():
                if not ServiceModel.objects.filter(pk=sid).exists():
                    errors.append(
                        f"[{key}] services.{ai_type}={sid} — такой {service_model_name} не найден"
                    )
                if sid in seen_ids:
                    warnings.append(
                        f"[{key}] services: AI-типы {seen_ids[sid]!r} и {ai_type!r} "
                        f"указывают на один service_id={sid} — вероятно, опечатка"
                    )
                else:
                    seen_ids[sid] = ai_type

        if warnings:
            self.stdout.write(self.style.WARNING("Предупреждения:"))
            for w in warnings:
                self.stdout.write(f"  - {w}")

        if errors:
            self.stdout.write(self.style.ERROR(f"Ошибки ({len(errors)}):"))
            for e in errors:
                self.stdout.write(f"  - {e}")
            if options.get('strict'):
                sys.exit(1)
            raise CommandError(f"Обнаружено ошибок: {len(errors)}")

        self.stdout.write(self.style.SUCCESS(
            f"OK: {len(data)} контрагентов, все service_id валидны."
        ))
