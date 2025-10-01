#!/bin/bash
#
# Скрипт автоматического деплоя для Logist2 на VPS
# Использование: ./auto_deploy.sh
#

set -e  # Остановить при ошибке

PROJECT_DIR="/var/www/www-root/data/www/logist2"
BACKUP_DIR="/var/www/www-root/data/tmp"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "========================================="
echo "🚀 Автодеплой Logist2"
echo "========================================="
echo ""

# Переход в директорию проекта
cd "$PROJECT_DIR"

# Активация виртуального окружения
echo "[1/8] 🔌 Активация виртуального окружения..."
source .venv/bin/activate

# Создание бэкапа БД
echo "[2/8] 💾 Создание бэкапа базы данных..."
pg_dump -U arturas logist2_db > "$BACKUP_DIR/backup_$TIMESTAMP.sql"
echo "    ✓ Бэкап сохранен: backup_$TIMESTAMP.sql"

# Получение изменений из Git
echo "[3/8] 📥 Получение изменений из Git..."
git fetch origin
git reset --hard origin/master
echo "    ✓ Код обновлен"

# Установка зависимостей
echo "[4/8] 📦 Установка зависимостей..."
pip install -r requirements.txt --quiet
echo "    ✓ Зависимости установлены"

# Применение миграций
echo "[5/8] 🗄️ Применение миграций..."
python manage.py migrate --no-input
echo "    ✓ Миграции применены"

# Сбор статических файлов
echo "[6/8] 📁 Сбор статических файлов..."
python manage.py collectstatic --no-input --clear
echo "    ✓ Статика собрана"

# Создание компании по умолчанию (если нужно)
echo "[7/8] 🏢 Проверка компании по умолчанию..."
python manage.py create_default_company

# Перезапуск сервисов
echo "[8/8] 🔄 Перезапуск сервисов..."
systemctl restart gunicorn
systemctl restart daphne
systemctl restart nginx
echo "    ✓ Сервисы перезапущены"

echo ""
echo "========================================="
echo "✅ ДЕПЛОЙ ЗАВЕРШЕН УСПЕШНО!"
echo "========================================="
echo ""
echo "🌐 Сайт доступен по адресу: http://176.118.198.78/admin/"
echo "💾 Бэкап сохранен в: $BACKUP_DIR/backup_$TIMESTAMP.sql"
echo ""


