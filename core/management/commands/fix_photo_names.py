from django.core.management.base import BaseCommand
from core.models_website import ContainerPhoto
import os
from django.conf import settings


class Command(BaseCommand):
    help = 'Исправляет имена файлов фотографий, убирая суффиксы Django'

    def add_arguments(self, parser):
        parser.add_argument(
            '--recent',
            action='store_true',
            help='Исправить только недавно загруженные фотографии (ID > 1200)',
        )

    def handle(self, *args, **options):
        if options['recent']:
            photos = ContainerPhoto.objects.filter(id__gt=1200)
            self.stdout.write("Исправляем только недавно загруженные фотографии...")
        else:
            photos = ContainerPhoto.objects.all()
            self.stdout.write("Исправляем все фотографии...")
        
        updated_count = 0

        for photo in photos:
            if photo.photo and photo.photo.name:
                # Получаем имя файла из базы данных
                filename_in_db = os.path.basename(photo.photo.name)
                
                # Проверяем, есть ли суффикс в имени файла
                if '_' in filename_in_db and '.' in filename_in_db:
                    parts = filename_in_db.split('.')
                    name_without_suffix = parts[0]
                    
                    # Ищем суффикс (последний _ и 7 символов после него)
                    if '_' in name_without_suffix:
                        base_name_parts = name_without_suffix.rsplit('_', 1)
                        if len(base_name_parts) > 1 and len(base_name_parts[1]) == 7:
                            # Создаем правильное имя без суффикса
                            correct_name = f"{base_name_parts[0]}.{parts[-1]}"
                            
                            # Проверяем, существует ли файл с правильным именем
                            photo_dir = os.path.dirname(photo.photo.name)
                            correct_path = os.path.join(photo_dir, correct_name)
                            full_correct_path = os.path.join(settings.MEDIA_ROOT, correct_path)
                            
                            if os.path.exists(full_correct_path):
                                # Обновляем запись в базе данных
                                old_name = photo.photo.name
                                photo.photo.name = correct_path
                                photo.save()
                                self.stdout.write(f"✓ Updated photo ID {photo.id}: {os.path.basename(old_name)} -> {correct_name}")
                                updated_count += 1
                            else:
                                self.stdout.write(f"✗ File not found for ID {photo.id}: {full_correct_path}")

        self.stdout.write(
            self.style.SUCCESS(f'\nРезультат: исправлено {updated_count} фотографий')
        )




