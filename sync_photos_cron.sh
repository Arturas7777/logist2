#!/bin/bash
# Cron script для автоматической синхронизации фотографий с Google Drive
# Добавьте в crontab: */30 * * * * /var/www/www-root/data/www/logist2/sync_photos_cron.sh

cd /var/www/www-root/data/www/logist2
source .venv/bin/activate
python manage.py sync_google_drive_photos >> /var/log/gdrive_sync.log 2>&1

