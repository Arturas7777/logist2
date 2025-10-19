#!/bin/bash
# Скрипт для настройки фотографий контейнеров на VPS
# Запускать на сервере: bash setup_vps_photos.sh

set -e  # Останавливаться при ошибках

echo "=========================================="
echo "Настройка фотографий контейнеров на VPS"
echo "=========================================="
echo ""

# Переходим в директорию проекта
cd /var/www/www-root/data/www/logist2

echo "[1/7] Обновление кода из GitHub..."
git pull origin master
echo "      ✓ Код обновлен"
echo ""

echo "[2/7] Активация виртуального окружения..."
source .venv/bin/activate
echo "      ✓ Виртуальное окружение активировано"
echo ""

echo "[3/7] Проверка системных зависимостей..."
# Проверяем наличие системных библиотек для Pillow
if ! python -c "from PIL import Image; Image.open" 2>/dev/null; then
    echo "      ⚠ Устанавливаем системные библиотеки..."
    apt-get update -qq
    apt-get install -y libjpeg-dev zlib1g-dev libpng-dev libtiff-dev libfreetype6-dev -qq
    pip install --upgrade --force-reinstall Pillow
    echo "      ✓ Pillow переустановлена"
else
    echo "      ✓ Pillow работает"
fi
echo ""

echo "[4/7] Создание и настройка папок..."
mkdir -p media/container_photos/thumbnails
chown -R www-root:www-root media/
chmod -R 775 media/container_photos/
echo "      ✓ Папки настроены"
echo ""

echo "[5/7] Проверка окружения..."
python manage.py check_photo_environment
echo ""

echo "[6/7] Пересоздание миниатюр для существующих фото..."
python manage.py regenerate_thumbnails
echo ""

echo "[7/7] Перезапуск сервисов..."
systemctl restart gunicorn
systemctl restart daphne
echo "      ✓ Сервисы перезапущены"
echo ""

echo "=========================================="
echo "✓ Настройка завершена!"
echo "=========================================="
echo ""
echo "Проверьте:"
echo "  1. Загрузите новый архив через админку"
echo "  2. Проверьте отображение миниатюр"
echo "  3. Проверьте логи: tail -f /var/log/gunicorn/error.log"
echo ""

