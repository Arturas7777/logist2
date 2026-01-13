"""
Management command для проверки окружения для работы с фотографиями контейнеров
"""
from django.core.management.base import BaseCommand
from django.conf import settings
import os
import sys


class Command(BaseCommand):
    help = 'Проверяет окружение для работы с фотографиями контейнеров'

    def handle(self, *args, **options):
        self.stdout.write("="*70)
        self.stdout.write(self.style.HTTP_INFO("Проверка окружения для фотографий контейнеров"))
        self.stdout.write("="*70 + "\n")
        
        all_checks_passed = True
        
        # 1. Проверка Pillow
        self.stdout.write(self.style.HTTP_INFO("1. Проверка библиотеки Pillow..."))
        try:
            from PIL import Image, features
            pillow_version = Image.__version__ if hasattr(Image, '__version__') else "unknown"
            self.stdout.write(self.style.SUCCESS(f"   [OK] Pillow установлена: версия {pillow_version}"))
            
            # Проверка поддержки форматов
            formats = {
                'JPEG': features.check('jpg'),
                'PNG': features.check('png'),
                'WEBP': features.check('webp'),
            }
            
            for fmt, supported in formats.items():
                if supported:
                    self.stdout.write(self.style.SUCCESS(f"   [OK] Поддержка {fmt}"))
                else:
                    self.stdout.write(self.style.WARNING(f"   [WARNING] {fmt} не поддерживается"))
                    all_checks_passed = False
                    
        except ImportError as e:
            self.stdout.write(self.style.ERROR(f"   [ERROR] Pillow не установлена: {e}"))
            all_checks_passed = False
        
        self.stdout.write("")
        
        # 2. Проверка MEDIA_ROOT
        self.stdout.write(self.style.HTTP_INFO("2. Проверка MEDIA_ROOT..."))
        media_root = settings.MEDIA_ROOT
        self.stdout.write(f"   Путь: {media_root}")
        
        if os.path.exists(media_root):
            self.stdout.write(self.style.SUCCESS("   [OK] Директория существует"))
            
            # Проверка прав на чтение
            if os.access(media_root, os.R_OK):
                self.stdout.write(self.style.SUCCESS("   [OK] Права на чтение"))
            else:
                self.stdout.write(self.style.ERROR("   [ERROR] Нет прав на чтение"))
                all_checks_passed = False
            
            # Проверка прав на запись
            if os.access(media_root, os.W_OK):
                self.stdout.write(self.style.SUCCESS("   [OK] Права на запись"))
            else:
                self.stdout.write(self.style.ERROR("   [ERROR] Нет прав на запись"))
                all_checks_passed = False
        else:
            self.stdout.write(self.style.ERROR("   [ERROR] Директория не существует"))
            all_checks_passed = False
        
        self.stdout.write("")
        
        # 3. Проверка директорий для фотографий
        self.stdout.write(self.style.HTTP_INFO("3. Проверка директорий для фотографий контейнеров..."))
        
        dirs_to_check = [
            ('container_photos', os.path.join(media_root, 'container_photos')),
            ('thumbnails', os.path.join(media_root, 'container_photos', 'thumbnails')),
            ('archives', os.path.join(media_root, 'container_archives')),
        ]
        
        for name, path in dirs_to_check:
            self.stdout.write(f"   {name}: {path}")
            
            if os.path.exists(path):
                self.stdout.write(self.style.SUCCESS(f"      [OK] Существует"))
                
                # Проверка прав
                if os.access(path, os.W_OK):
                    self.stdout.write(self.style.SUCCESS("      [OK] Права на запись"))
                else:
                    self.stdout.write(self.style.ERROR("      [ERROR] Нет прав на запись"))
                    all_checks_passed = False
                    
                # Статистика
                try:
                    files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
                    self.stdout.write(f"      Файлов: {len(files)}")
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"      [WARNING] Ошибка подсчета файлов: {e}"))
            else:
                self.stdout.write(self.style.WARNING(f"      [WARNING] Не существует (будет создана при загрузке)"))
                
                # Пытаемся создать
                try:
                    os.makedirs(path, exist_ok=True)
                    self.stdout.write(self.style.SUCCESS(f"      [OK] Директория создана"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"      [ERROR] Не удалось создать: {e}"))
                    all_checks_passed = False
        
        self.stdout.write("")
        
        # 4. Проверка моделей
        self.stdout.write(self.style.HTTP_INFO("4. Проверка моделей..."))
        try:
            from core.models_website import ContainerPhoto, ContainerPhotoArchive
            
            total_photos = ContainerPhoto.objects.count()
            photos_without_thumbs = ContainerPhoto.objects.filter(thumbnail='').count()
            total_archives = ContainerPhotoArchive.objects.count()
            processed_archives = ContainerPhotoArchive.objects.filter(is_processed=True).count()
            
            self.stdout.write(f"   Всего фотографий: {total_photos}")
            self.stdout.write(f"   Фотографий без миниатюр: {photos_without_thumbs}")
            
            if photos_without_thumbs > 0:
                percentage = (photos_without_thumbs / total_photos * 100) if total_photos > 0 else 0
                self.stdout.write(self.style.WARNING(
                    f"   [WARNING] {percentage:.1f}% фотографий не имеют миниатюр"
                ))
                self.stdout.write(self.style.HTTP_INFO(
                    "   Рекомендация: запустите 'python manage.py regenerate_thumbnails'"
                ))
            else:
                self.stdout.write(self.style.SUCCESS("   [OK] Все фотографии имеют миниатюры"))
            
            self.stdout.write(f"   Всего архивов: {total_archives}")
            self.stdout.write(f"   Обработано архивов: {processed_archives}")
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   [ERROR] Ошибка проверки моделей: {e}"))
            all_checks_passed = False
        
        self.stdout.write("")
        
        # 5. Тест создания миниатюры
        self.stdout.write(self.style.HTTP_INFO("5. Тест создания тестовой миниатюры..."))
        try:
            from PIL import Image
            from io import BytesIO
            
            # Создаем тестовое изображение
            img = Image.new('RGB', (800, 600), color='red')
            
            # Создаем миниатюру
            img.thumbnail((400, 400), Image.Resampling.LANCZOS)
            
            # Сохраняем в буфер
            buffer = BytesIO()
            img.save(buffer, format='JPEG', quality=85)
            
            self.stdout.write(self.style.SUCCESS("   [OK] Тест создания миниатюры успешен"))
            self.stdout.write(f"   Размер миниатюры: {len(buffer.getvalue())} байт")
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   [ERROR] Ошибка создания тестовой миниатюры: {e}"))
            all_checks_passed = False
        
        self.stdout.write("")
        
        # 6. Проверка системной информации
        self.stdout.write(self.style.HTTP_INFO("6. Системная информация..."))
        self.stdout.write(f"   Python версия: {sys.version.split()[0]}")
        self.stdout.write(f"   Платформа: {sys.platform}")
        
        try:
            import django
            self.stdout.write(f"   Django версия: {django.get_version()}")
        except:
            pass
        
        self.stdout.write("")
        self.stdout.write("="*70)
        
        if all_checks_passed:
            self.stdout.write(
                self.style.SUCCESS(
                    "[OK] Все проверки пройдены! Окружение готово для работы с фотографиями."
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR(
                    "[ERROR] Некоторые проверки не пройдены. См. детали выше."
                )
            )
            self.stdout.write("")
            self.stdout.write(self.style.HTTP_INFO("Рекомендуемые действия:"))
            self.stdout.write("   1. Установите системные библиотеки:")
            self.stdout.write("      sudo apt-get install libjpeg-dev zlib1g-dev libpng-dev")
            self.stdout.write("   2. Переустановите Pillow:")
            self.stdout.write("      pip install --upgrade --force-reinstall Pillow")
            self.stdout.write("   3. Проверьте права на папки:")
            self.stdout.write("      sudo chown -R www-data:www-data media/")
            self.stdout.write("      sudo chmod -R 775 media/container_photos/")
        
        self.stdout.write("="*70)

