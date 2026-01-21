#!/bin/bash
#
# Скрипт автоматической синхронизации фотографий с Google Drive
# 
# Для автоматического запуска добавьте в crontab:
#   crontab -e
#   # Каждый час
#   0 * * * * /path/to/logist2/sync_photos_cron.sh >> /var/log/logist2_photo_sync.log 2>&1
#
#   # Или каждые 30 минут
#   */30 * * * * /path/to/logist2/sync_photos_cron.sh >> /var/log/logist2_photo_sync.log 2>&1

# Путь к проекту (измените на ваш путь)
PROJECT_DIR="/home/caromoto-lt/logist2"

# Активация виртуального окружения
source "$PROJECT_DIR/venv/bin/activate"

# Переход в директорию проекта
cd "$PROJECT_DIR"

# Установка переменных окружения Django
export DJANGO_SETTINGS_MODULE=logist2.settings

echo "========================================"
echo "$(date '+%Y-%m-%d %H:%M:%S') - Запуск синхронизации фотографий"
echo "========================================"

# Синхронизация только недавних контейнеров (за последние 30 дней)
# Это более эффективный вариант для регулярного запуска
python manage.py sync_photos_gdrive --recent --days 30

echo "========================================"
echo "$(date '+%Y-%m-%d %H:%M:%S') - Синхронизация завершена"
echo "========================================"
