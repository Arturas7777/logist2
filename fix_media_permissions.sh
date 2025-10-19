#!/bin/bash
# Скрипт для исправления прав доступа к media файлам на VPS сервере

echo "🔧 Исправление прав доступа к media файлам..."

# Путь к проекту
PROJECT_DIR="/var/www/www-root/data/www/logist2"

# Переход в папку проекта
cd "$PROJECT_DIR" || exit 1

# Исправляем владельца всех media файлов
echo "📁 Изменение владельца media файлов на www-root..."
chown -R www-root:www-root media/

# Устанавливаем правильные права доступа
echo "🔐 Установка прав доступа 755 для директорий и 644 для файлов..."
find media/ -type d -exec chmod 755 {} \;
find media/ -type f -exec chmod 644 {} \;

# Специально для фотографий контейнеров
echo "📷 Проверка прав доступа к фотографиям контейнеров..."
if [ -d "media/container_photos" ]; then
    chown -R www-root:www-root media/container_photos/
    chmod -R 755 media/container_photos/
    echo "✅ Права доступа к фотографиям контейнеров обновлены"
fi

# Специально для миниатюр
if [ -d "media/container_photos/thumbnails" ]; then
    chown -R www-root:www-root media/container_photos/thumbnails/
    chmod -R 755 media/container_photos/thumbnails/
    echo "✅ Права доступа к миниатюрам обновлены"
fi

echo "✅ Исправление прав доступа завершено!"
echo ""
echo "Проверка результатов:"
ls -la media/container_photos/ | head -10
echo ""
echo "Миниатюры:"
ls -la media/container_photos/thumbnails/ | head -5

