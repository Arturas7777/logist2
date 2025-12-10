#!/usr/bin/env python3
"""
Тестовый скрипт для проверки настроек Django
"""
import os
import sys
import django

# Добавляем путь к проекту
sys.path.append('/var/www/www-root/data/www/logist2')

# Настраиваем Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'logist2.settings')
django.setup()

from django.conf import settings

print("=== НАСТРОЙКИ DJANGO ===")
print(f"MEDIA_URL: {settings.MEDIA_URL}")
print(f"MEDIA_ROOT: {settings.MEDIA_ROOT}")
print(f"DEBUG: {settings.DEBUG}")

# Тестируем метод _safe_media_url
from core.admin_website import ContainerPhotoAdmin
from core.models_website import ContainerPhoto
from django.contrib.admin.sites import site

admin = site._registry[ContainerPhoto]
photo = ContainerPhoto.objects.first()

if photo and photo.thumbnail:
    print(f"\n=== ТЕСТ _safe_media_url ===")
    print(f"Thumbnail name: {photo.thumbnail.name}")
    print(f"Thumbnail URL: {photo.thumbnail.url}")
    
    # Тестируем метод напрямую
    safe_url = admin._safe_media_url(photo.thumbnail)
    print(f"Safe URL: {safe_url}")
    
    # Тестируем image_preview
    preview = admin.image_preview(photo)
    print(f"Image preview: {preview}")



