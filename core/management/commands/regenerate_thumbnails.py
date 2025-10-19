"""
Management command для пересоздания миниатюр фотографий контейнеров
"""
from django.core.management.base import BaseCommand
from django.db.models import Q
from core.models_website import ContainerPhoto
import os


class Command(BaseCommand):
    help = 'Пересоздает миниатюры для фотографий контейнеров'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Пересоздать все миниатюры, даже если они уже существуют',
        )
        parser.add_argument(
            '--container',
            type=str,
            help='Номер контейнера для обработки (опционально)',
        )

    def handle(self, *args, **options):
        force = options.get('force', False)
        container_number = options.get('container')
        
        # Получаем фотографии для обработки
        photos_query = ContainerPhoto.objects.select_related('container')
        
        if container_number:
            photos_query = photos_query.filter(container__number=container_number)
            self.stdout.write(f"Обработка фотографий контейнера {container_number}...")
        else:
            self.stdout.write("Обработка всех фотографий контейнеров...")
        
        if force:
            # Обрабатываем все фотографии
            photos = photos_query.all()
            self.stdout.write(f"Режим --force: будут пересозданы все {photos.count()} миниатюр")
        else:
            # Обрабатываем только фотографии без миниатюр или с несуществующими миниатюрами
            photos = photos_query.filter(
                Q(thumbnail='') | Q(thumbnail__isnull=True)
            )
            self.stdout.write(f"Найдено {photos.count()} фотографий без миниатюр")
        
        success_count = 0
        error_count = 0
        
        for photo in photos:
            try:
                # Проверяем существование оригинального файла
                if not photo.photo or not os.path.exists(photo.photo.path):
                    self.stdout.write(
                        self.style.WARNING(
                            f"Пропуск фото ID {photo.id}: оригинальный файл не найден"
                        )
                    )
                    error_count += 1
                    continue
                
                # Удаляем старую миниатюру если force=True
                if force and photo.thumbnail:
                    try:
                        if os.path.exists(photo.thumbnail.path):
                            os.remove(photo.thumbnail.path)
                    except Exception as e:
                        self.stdout.write(
                            self.style.WARNING(
                                f"Не удалось удалить старую миниатюру для фото ID {photo.id}: {e}"
                            )
                        )
                    photo.thumbnail = None
                
                # Создаем миниатюру
                if photo.create_thumbnail():
                    photo.save(update_fields=['thumbnail'])
                    success_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[OK] Создана миниатюра для {photo.container.number} - {photo.filename}"
                        )
                    )
                else:
                    error_count += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f"[ERROR] Ошибка создания миниатюры для фото ID {photo.id}"
                        )
                    )
                    
            except Exception as e:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"[ERROR] Ошибка обработки фото ID {photo.id}: {e}"
                    )
                )
        
        self.stdout.write("\n" + "="*50)
        self.stdout.write(
            self.style.SUCCESS(
                f"Обработка завершена!\n"
                f"Успешно создано миниатюр: {success_count}\n"
                f"Ошибок: {error_count}"
            )
        )

