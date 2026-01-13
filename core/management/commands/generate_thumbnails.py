"""
Команда для создания миниатюр для существующих фотографий контейнеров
"""
from django.core.management.base import BaseCommand
from core.models_website import ContainerPhoto


class Command(BaseCommand):
    help = 'Создает миниатюры для всех фотографий контейнеров'

    def handle(self, *args, **options):
        photos = ContainerPhoto.objects.filter(thumbnail__isnull=True)
        total = photos.count()
        
        if total == 0:
            self.stdout.write(self.style.SUCCESS('Все фотографии уже имеют миниатюры'))
            return
        
        self.stdout.write(f'Найдено фотографий без миниатюр: {total}')
        
        success_count = 0
        error_count = 0
        
        for i, photo in enumerate(photos, 1):
            try:
                photo.create_thumbnail()
                photo.save(update_fields=['thumbnail'])
                success_count += 1
                
                if i % 10 == 0:
                    self.stdout.write(f'Обработано: {i}/{total}')
                    
            except Exception as e:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(f'Ошибка для фото ID {photo.id}: {str(e)}')
                )
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\nГотово! Успешно: {success_count}, Ошибок: {error_count}'
            )
        )

