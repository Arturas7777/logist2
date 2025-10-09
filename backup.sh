#!/bin/bash
# Скрипт автоматического бэкапа Caromoto Lithuania

set -e

# Переменные
BACKUP_DIR="/var/backups/caromoto-lt"
PROJECT_DIR="/var/www/caromoto-lt"
DB_NAME="logist2_db"
DB_USER="logist2_user"
DATE=$(date +%Y%m%d_%H%M%S)

# Создаем директорию для бэкапов
mkdir -p $BACKUP_DIR

echo "🔄 Начинаем бэкап Caromoto Lithuania..."

# Бэкап базы данных
echo "📊 Создание бэкапа базы данных..."
sudo -u postgres pg_dump $DB_NAME > $BACKUP_DIR/db_backup_$DATE.sql
gzip $BACKUP_DIR/db_backup_$DATE.sql
echo "✅ База данных сохранена: $BACKUP_DIR/db_backup_$DATE.sql.gz"

# Бэкап медиа файлов (фотографии контейнеров и т.д.)
echo "📸 Создание бэкапа медиа файлов..."
tar -czf $BACKUP_DIR/media_backup_$DATE.tar.gz -C $PROJECT_DIR media/
echo "✅ Медиа файлы сохранены: $BACKUP_DIR/media_backup_$DATE.tar.gz"

# Бэкап конфигурационных файлов
echo "⚙️ Создание бэкапа конфигурации..."
tar -czf $BACKUP_DIR/config_backup_$DATE.tar.gz \
    $PROJECT_DIR/.env \
    $PROJECT_DIR/gunicorn_config.py \
    /etc/nginx/sites-available/caromoto-lt \
    /etc/systemd/system/caromoto-lt.service
echo "✅ Конфигурация сохранена: $BACKUP_DIR/config_backup_$DATE.tar.gz"

# Удаление старых бэкапов (старше 30 дней)
echo "🗑️ Удаление старых бэкапов (старше 30 дней)..."
find $BACKUP_DIR -name "*.gz" -type f -mtime +30 -delete
find $BACKUP_DIR -name "*.sql.gz" -type f -mtime +30 -delete

# Информация о размере бэкапов
echo ""
echo "📊 Информация о бэкапах:"
du -sh $BACKUP_DIR/*backup_$DATE*

echo ""
echo "✅ Бэкап завершен успешно!"
echo "📁 Все файлы сохранены в: $BACKUP_DIR"

