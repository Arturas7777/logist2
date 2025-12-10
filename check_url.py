import os
import django
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'logist2.settings')
django.setup()

from core.models_website import ContainerPhoto

# Найдем фотографию с ID 1632 (из скриншота)
photo = ContainerPhoto.objects.get(id=1632)
print(f"Photo ID: {photo.id}")
print(f"Photo name: {photo.photo.name}")
print(f"Photo URL: {photo.photo.url}")
print(f"Photo path: {photo.photo.path}")
print(f"Photo exists: {os.path.exists(photo.photo.path)}")

# Проверим настройки MEDIA
print(f"\nMEDIA_URL: {settings.MEDIA_URL}")
print(f"MEDIA_ROOT: {settings.MEDIA_ROOT}")

# Сгенерируем полный URL
full_url = f"https://caromoto-lt.com{photo.photo.url}"
print(f"Full URL: {full_url}")




