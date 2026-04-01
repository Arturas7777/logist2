#!/bin/bash
#
# Скрипт автоматической синхронизации фотографий с Google Drive
# 
# ВАЖНО: cron-задачи настроены в crontab пользователя www-root:
#   crontab -u www-root -e
#
# Текущие задачи (crontab -u www-root -l):
#   # Фото: каждые 3 часа — контейнеры без фото
#   0 */3 * * * cd /var/www/www-root/data/www/logist2 && .venv/bin/python manage.py sync_photos_gdrive --no-photos >> /var/log/logist2/photo_sync.log 2>&1
#   # Фото: ежедневно 3:00 — недавние контейнеры (14 дней)
#   0 3 * * * cd /var/www/www-root/data/www/logist2 && .venv/bin/python manage.py sync_photos_gdrive --recent >> /var/log/logist2/photo_sync.log 2>&1
#   # Ремонт фото: ежедневно 4:30
#   30 4 * * * cd /var/www/www-root/data/www/logist2 && .venv/bin/python manage.py repair_container_photos >> /var/log/logist2/photo_repair.log 2>&1
#   # Банк: каждые 15 мин
#   */15 * * * * cd /var/www/www-root/data/www/logist2 && .venv/bin/python manage.py sync_bank_accounts >> /var/log/logist2/bank_sync.log 2>&1
#   # Пересчёт хранения: ежедневно 6:00
#   0 6 * * * cd /var/www/www-root/data/www/logist2 && .venv/bin/python manage.py recalculate_storage >> /var/log/logist2/recalculate_storage.log 2>&1
#
# Этот скрипт можно использовать для ручного запуска:
#   sudo -u www-root /var/www/www-root/data/www/logist2/sync_photos_cron.sh

# Путь к проекту
PROJECT_DIR="/var/www/www-root/data/www/logist2"
PYTHON="$PROJECT_DIR/.venv/bin/python"

# Переход в директорию проекта
cd "$PROJECT_DIR"

# Установка переменных окружения Django
export DJANGO_SETTINGS_MODULE=logist2.settings

echo "========================================"
echo "$(date '+%Y-%m-%d %H:%M:%S') - Запуск синхронизации фотографий"
echo "========================================"

# Проверка разгруженных контейнеров без фото после задержки 12 часов
$PYTHON manage.py sync_photos_gdrive --unloaded-delay --delay-hours 12

echo "========================================"
echo "$(date '+%Y-%m-%d %H:%M:%S') - Синхронизация завершена"
echo "========================================"