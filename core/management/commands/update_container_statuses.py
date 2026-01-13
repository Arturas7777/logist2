from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Container, Car
import logging

logger = logging.getLogger('django')


class Command(BaseCommand):
    help = 'Обновляет статусы контейнеров на основе статуса автомобилей'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет изменено без фактического обновления',
        )
        parser.add_argument(
            '--container-id',
            type=int,
            help='Обновить только конкретный контейнер по ID',
        )
        parser.add_argument(
            '--status',
            choices=['FLOATING', 'IN_PORT', 'UNLOADED', 'TRANSFERRED'],
            help='Обновить только контейнеры с определенным статусом',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        container_id = options.get('container_id')
        status_filter = options.get('status')

        # Получаем контейнеры для обновления
        containers_query = Container.objects.all()
        
        if container_id:
            containers_query = containers_query.filter(id=container_id)
        elif status_filter:
            containers_query = containers_query.filter(status=status_filter)
        
        containers = containers_query.prefetch_related('container_cars')
        
        self.stdout.write(f"Найдено {containers.count()} контейнеров для проверки")
        
        updated_count = 0
        skipped_count = 0
        error_count = 0
        
        with transaction.atomic():
            for container in containers:
                try:
                    # Проверяем статус автомобилей
                    cars = container.container_cars.all()
                    if not cars.exists():
                        self.stdout.write(
                            self.style.WARNING(f"Контейнер {container.number} не имеет автомобилей - пропускаем")
                        )
                        skipped_count += 1
                        continue
                    
                    # Проверяем, все ли автомобили переданы
                    all_transferred = all(car.status == 'TRANSFERRED' for car in cars)
                    transferred_cars_count = sum(1 for car in cars if car.status == 'TRANSFERRED')
                    total_cars_count = cars.count()
                    
                    if all_transferred and container.status != 'TRANSFERRED':
                        if dry_run:
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f"[DRY RUN] Контейнер {container.number} ({container.status}) -> TRANSFERRED "
                                    f"(все {total_cars_count} автомобилей переданы)"
                                )
                            )
                        else:
                            old_status = container.status
                            container.status = 'TRANSFERRED'
                            container.save(update_fields=['status'])
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f"✓ Контейнер {container.number} ({old_status}) -> TRANSFERRED "
                                    f"(все {total_cars_count} автомобилей переданы)"
                                )
                            )
                        updated_count += 1
                    else:
                        if container.status == 'TRANSFERRED':
                            self.stdout.write(
                                self.style.WARNING(
                                    f"Контейнер {container.number} уже имеет статус TRANSFERRED - пропускаем"
                                )
                            )
                        else:
                            self.stdout.write(
                                f"Контейнер {container.number} ({container.status}): "
                                f"{transferred_cars_count}/{total_cars_count} автомобилей переданы - не обновляем"
                            )
                        skipped_count += 1
                        
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"Ошибка при обработке контейнера {container.number}: {e}")
                    )
                    error_count += 1
                    logger.error(f"Error updating container {container.number}: {e}")
        
        # Итоговая статистика
        self.stdout.write("\n" + "="*50)
        self.stdout.write("ИТОГОВАЯ СТАТИСТИКА:")
        self.stdout.write(f"Обновлено контейнеров: {updated_count}")
        self.stdout.write(f"Пропущено контейнеров: {skipped_count}")
        self.stdout.write(f"Ошибок: {error_count}")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\nЭто был тестовый запуск (--dry-run). Никаких изменений не внесено."))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nОбновление завершено успешно!"))

