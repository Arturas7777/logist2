#!/bin/bash
#
# Скрипт автоматической синхронизации фотографий с Google Drive
# 
# Для автоматического запуска добавьте в crontab:
#   crontab -e
#   # Каждый час (проверка разгруженных контейнеров через 12 часов)
#   0 * * * * /var/www/www-root/data/www/logist2/sync_photos_cron.sh >> /var/log/logist2_photo_sync.log 2>&1
#
#   # Или каждые 30 минут
#   */30 * * * * /path/to/logist2/sync_photos_cron.sh >> /var/log/logist2_photo_sync.log 2>&1

# Путь к проекту
PROJECT_DIR="/var/www/www-root/data/www/logist2"

# Активация виртуального окружения
source "$PROJECT_DIR/venv/bin/activate"

# Переход в директорию проекта
cd "$PROJECT_DIR"

# Установка переменных окружения Django
export DJANGO_SETTINGS_MODULE=logist2.settings

echo "========================================"
echo "$(date '+%Y-%m-%d %H:%M:%S') - Запуск синхронизации фотографий"
echo "========================================"

# Проверка разгруженных контейнеров без фото после задержки 12 часов
# Запускается каждый час
python manage.py sync_photos_gdrive --unloaded-delay --delay-hours 12

echo "========================================"
echo "$(date '+%Y-%m-%d %H:%M:%S') - Синхронизация завершена"
echo "========================================"