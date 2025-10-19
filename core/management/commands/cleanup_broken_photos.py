"""
Management command для очистки битых записей фотографий
"""
from django.core.management.base import BaseCommand
from core.models_website import ContainerPhoto
import os


class Command(BaseCommand):
    help = 'Удаляет записи фотографий контейнеров для которых нет файлов на диске'

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete',
            action='store_true',
            help='Удалить битые записи (без этого флага только показывает список)',
        )

    def handle(self, *args, **options):
        delete_mode = options.get('delete', False)
        
        self.stdout.write("="*70)
        self.stdout.write(self.style.HTTP_INFO("Проверка битых записей фотографий"))
        self.stdout.write("="*70)
        self.stdout.write("")
        
        all_photos = ContainerPhoto.objects.all()
        broken_photos = []
        
        self.stdout.write(f"Проверяю {all_photos.count()} фотографий...")
        self.stdout.write("")
        
        for photo in all_photos:
            if photo.photo:
                try:
                    if not os.path.exists(photo.photo.path):
                        broken_photos.append(photo)
                        self.stdout.write(
                            self.style.WARNING(
                                f"[BROKEN] ID {photo.id}: {photo.container.number} - файл не найден: {photo.photo.name}"
                            )
                        )
                except Exception as e:
                    broken_photos.append(photo)
                    self.stdout.write(
                        self.style.ERROR(
                            f"[ERROR] ID {photo.id}: {e}"
                        )
                    )
        
        self.stdout.write("")
        self.stdout.write("="*70)
        self.stdout.write(f"Найдено битых записей: {len(broken_photos)}")
        self.stdout.write("="*70)
        
        if broken_photos and delete_mode:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(f"Удаление {len(broken_photos)} битых записей..."))
            
            for photo in broken_photos:
                container_number = photo.container.number
                photo_id = photo.id
                photo.delete()
                self.stdout.write(f"  Удалено: ID {photo_id} (контейнер {container_number})")
            
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"Удалено {len(broken_photos)} битых записей"))
            
        elif broken_photos:
            self.stdout.write("")
            self.stdout.write(self.style.HTTP_INFO("Для удаления запустите с флагом --delete:"))
            self.stdout.write("  python manage.py cleanup_broken_photos --delete")
        else:
            self.stdout.write(self.style.SUCCESS("Все записи в порядке!"))
        
        self.stdout.write("")

