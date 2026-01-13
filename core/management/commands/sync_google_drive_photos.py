"""
Management command для синхронизации фотографий контейнеров с Google Drive
"""
from django.core.management.base import BaseCommand
from core.google_drive_sync import GoogleDriveSync


class Command(BaseCommand):
    help = 'Синхронизирует фотографии контейнеров с Google Drive'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--test',
            action='store_true',
            help='Тестовый режим (только вывод списка папок без загрузки)',
        )

    def handle(self, *args, **options):
        test_mode = options.get('test', False)
        
        self.stdout.write("="*70)
        self.stdout.write(self.style.HTTP_INFO("Синхронизация фотографий с Google Drive"))
        self.stdout.write("="*70)
        self.stdout.write("")
        
        if test_mode:
            self.stdout.write(self.style.WARNING("ТЕСТОВЫЙ РЕЖИМ - загрузка файлов отключена"))
            self.stdout.write("")
        
        try:
            # Запускаем синхронизацию
            stats = GoogleDriveSync.sync_all_containers()
            
            # Выводим статистику
            self.stdout.write("")
            self.stdout.write("="*70)
            self.stdout.write(self.style.SUCCESS("Синхронизация завершена!"))
            self.stdout.write("")
            self.stdout.write(f"Выгруженные контейнеры: {stats['unloaded_photos']} фото")
            self.stdout.write(f"В контейнере: {stats['in_container_photos']} фото")
            self.stdout.write(f"Всего добавлено: {stats['unloaded_photos'] + stats['in_container_photos']} фото")
            
            if stats['errors']:
                self.stdout.write("")
                self.stdout.write(self.style.WARNING(f"Ошибок: {len(stats['errors'])}"))
                for error in stats['errors']:
                    self.stdout.write(f"  - {error}")
            
            self.stdout.write("="*70)
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Критическая ошибка: {e}"))
            raise

