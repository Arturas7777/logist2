"""
Django management command для автоматической синхронизации фотографий с Google Drive.

ВАЖНО: Склад загружает фотографии с задержкой 1-2 суток после разгрузки!
Поэтому рекомендуется запускать --no-photos каждые 2-4 часа.

Использование:
    # РЕКОМЕНДУЕМЫЙ РЕЖИМ: только контейнеры без фото (быстро, для частого запуска)
    python manage.py sync_photos_gdrive --no-photos
    
    # Синхронизировать недавние контейнеры (за последние 30 дней)
    python manage.py sync_photos_gdrive --recent
    
    # Синхронизировать конкретный контейнер
    python manage.py sync_photos_gdrive --container MSCU1234567
    
    # Полная синхронизация всех контейнеров
    python manage.py sync_photos_gdrive --all

Для автоматического запуска по крону:
    # Каждые 3 часа - проверка контейнеров БЕЗ фото (быстро)
    0 */3 * * * cd /path/to/project && python manage.py sync_photos_gdrive --no-photos >> /var/log/photo_sync.log 2>&1
    
    # Раз в сутки ночью - полная проверка всех недавних
    0 3 * * * cd /path/to/project && python manage.py sync_photos_gdrive --recent >> /var/log/photo_sync.log 2>&1
"""
from django.core.management.base import BaseCommand, CommandError
from core.google_drive_sync import GoogleDriveSync
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Синхронизирует фотографии контейнеров с Google Drive'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--unloaded-delay',
            action='store_true',
            help='Проверять разгруженные контейнеры без фото после задержки (см. --delay-hours)'
        )
        parser.add_argument(
            '--delay-hours',
            type=int,
            default=12,
            help='Задержка в часах после статуса UNLOADED (по умолчанию 12)'
        )
        parser.add_argument(
            '--no-photos',
            action='store_true',
            help='[BEST] Проверить только контейнеры БЕЗ фотографий (быстрый режим)'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Синхронизировать все контейнеры (сканирует всю структуру Google Drive)'
        )
        parser.add_argument(
            '--recent',
            action='store_true',
            help='Синхронизировать недавние контейнеры (за последние N дней)'
        )
        parser.add_argument(
            '--days',
            type=int,
            default=14,
            help='Количество дней для --recent и --no-photos (по умолчанию 14)'
        )
        parser.add_argument(
            '--container',
            type=str,
            help='Синхронизировать конкретный контейнер по номеру'
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Максимальное количество контейнеров для обработки (для тестов)'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Выводить подробную информацию'
        )
    
    def handle(self, *args, **options):
        if options['verbose']:
            logging.getLogger('core.google_drive_sync').setLevel(logging.DEBUG)
        
        self.stdout.write(self.style.NOTICE('=' * 60))
        self.stdout.write(self.style.NOTICE('[SYNC] Google Drive Photo Sync'))
        self.stdout.write(self.style.NOTICE('=' * 60))
        
        if options['container']:
            # Синхронизация конкретного контейнера
            container_number = options['container']
            self.stdout.write(f'[CONTAINER] {container_number}')
            
            added = GoogleDriveSync.sync_container_by_number(container_number)
            
            if added > 0:
                self.stdout.write(self.style.SUCCESS(f'[OK] Added {added} photos'))
            else:
                self.stdout.write(self.style.WARNING(f'[WARN] No new photos found'))
        
        elif options['unloaded_delay']:
            delay_hours = options['delay_hours']
            self.stdout.write(f'[DELAY] UNLOADED containers without photos (delay {delay_hours} hours)')

            stats = GoogleDriveSync.sync_unloaded_containers_after_delay(hours=delay_hours)

            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('=' * 60))
            self.stdout.write(self.style.SUCCESS('[RESULTS]:'))
            self.stdout.write(f'   Containers checked: {stats["containers_checked"]}')
            self.stdout.write(f'   With new photos: {stats["containers_with_new_photos"]}')
            self.stdout.write(f'   Photos added: {stats["photos_added"]}')
            if stats['errors']:
                self.stdout.write(self.style.ERROR(f'   Errors: {len(stats["errors"])}'))
                for error in stats['errors'][:5]:
                    self.stdout.write(self.style.ERROR(f'      - {error}'))

        elif options['no_photos']:
            # БЫСТРЫЙ РЕЖИМ: только контейнеры без фото
            days = options['days']
            self.stdout.write(f'[SEARCH] Containers WITHOUT photos (last {days} days)')
            self.stdout.write(self.style.WARNING('   TIP: This mode is fast - run every 2-4 hours'))
            
            stats = GoogleDriveSync.sync_containers_without_photos(days=days)
            
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('=' * 60))
            self.stdout.write(self.style.SUCCESS('[RESULTS]:'))
            self.stdout.write(f'   Containers checked: {stats["containers_checked"]}')
            self.stdout.write(f'   With new photos: {stats["containers_with_new_photos"]}')
            self.stdout.write(f'   Photos added: {stats["photos_added"]}')
            if stats['errors']:
                self.stdout.write(self.style.ERROR(f'   Errors: {len(stats["errors"])}'))
                for error in stats['errors'][:5]:
                    self.stdout.write(self.style.ERROR(f'      - {error}'))
        
        elif options['recent']:
            # Синхронизация недавних контейнеров
            days = options['days']
            self.stdout.write(f'[RECENT] Containers for last {days} days')
            
            stats = GoogleDriveSync.sync_recent_containers(days=days)
            
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('=' * 60))
            self.stdout.write(self.style.SUCCESS('[RESULTS]:'))
            self.stdout.write(f'   Containers processed: {stats["containers_processed"]}')
            self.stdout.write(f'   With new photos: {stats["containers_with_new_photos"]}')
            self.stdout.write(f'   Photos added: {stats["photos_added"]}')
            if stats['errors']:
                self.stdout.write(self.style.ERROR(f'   Errors: {len(stats["errors"])}'))
                for error in stats['errors'][:5]:
                    self.stdout.write(self.style.ERROR(f'      - {error}'))
        
        elif options['all']:
            # Полная синхронизация
            self.stdout.write('[FULL] Full sync of all containers')
            
            if options['limit']:
                self.stdout.write(f'   (limit: {options["limit"]} containers)')
            
            stats = GoogleDriveSync.sync_all_containers(limit=options['limit'])
            
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('=' * 60))
            self.stdout.write(self.style.SUCCESS('[RESULTS]:'))
            self.stdout.write(f'   Containers processed: {stats["containers_processed"]}')
            self.stdout.write(f'   Photos added: {stats["photos_added"]}')
            self.stdout.write(f'   Not found in DB: {len(stats["containers_not_found"])}')
            
            if stats['containers_not_found']:
                self.stdout.write(self.style.WARNING('   Not in DB:'))
                for name in stats['containers_not_found'][:10]:
                    self.stdout.write(f'      - {name}')
                if len(stats['containers_not_found']) > 10:
                    self.stdout.write(f'      ... and {len(stats["containers_not_found"]) - 10} more')
            
            if stats['errors']:
                self.stdout.write(self.style.ERROR(f'   Errors: {len(stats["errors"])}'))
                for error in stats['errors'][:5]:
                    self.stdout.write(self.style.ERROR(f'      - {error}'))
        
        else:
            raise CommandError(
                'Specify sync mode:\n'
                '  --unloaded-delay [NEW] containers UNLOADED after delay\n'
                '  --no-photos  [BEST] only containers without photos (fast)\n'
                '  --recent     - all recent containers\n'
                '  --all        - full sync\n'
                '  --container NUMBER - specific container'
            )
        
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS('[DONE] Sync completed'))
