#!/bin/bash

# Скрипт обновления проекта Logist2 на VPS
# Запускать на сервере от root
# Сервер: 176.118.198.78

set -e

PROJECT_DIR="/var/www/www-root/data/www/logist2"
VENV_DIR="$PROJECT_DIR/.venv"

echo "========================================="
echo "  Обновление Logist2"
echo "========================================="
echo "Директория: $PROJECT_DIR"
echo ""

# Переход в директорию проекта
cd $PROJECT_DIR

# Активация виртуального окружения
echo "[1/7] Активация виртуального окружения..."
source $VENV_DIR/bin/activate

# Проверка версии Python
echo "[2/7] Проверка Python..."
python --version

# Установка/обновление зависимостей
echo "[3/7] Установка зависимостей..."
pip install -r requirements.txt --upgrade --no-cache-dir

# Сбор статических файлов
echo "[4/7] Сбор статических файлов..."
python manage.py collectstatic --noinput --clear

# Применение миграций
echo "[5/7] Применение миграций базы данных..."
python manage.py migrate --noinput

# Перезапуск сервисов
echo "[6/7] Перезапуск сервисов..."
systemctl restart gunicorn
sleep 2
systemctl restart daphne
sleep 2
systemctl restart nginx

# Проверка статуса
echo "[7/7] Проверка статуса сервисов..."
echo ""
echo "Gunicorn:"
systemctl status gunicorn --no-pager -l | head -n 5
echo ""
echo "Daphne:"
systemctl status daphne --no-pager -l | head -n 5
echo ""
echo "Nginx:"
systemctl status nginx --no-pager -l | head -n 3

echo ""
echo "========================================="
echo "  ✓ Обновление завершено успешно!"
echo "========================================="
echo ""
echo "Сайт: http://176.118.198.78/admin"
echo ""
echo "Для просмотра логов:"
echo "  journalctl -u gunicorn -f"
echo "  journalctl -u daphne -f"
echo ""

