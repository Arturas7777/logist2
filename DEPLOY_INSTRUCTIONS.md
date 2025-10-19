# 🚀 Инструкции по деплою исправления фотографий

## ✅ Что уже сделано

1. ✅ Исправлен код в `core/models_website.py`
2. ✅ Созданы команды `check_photo_environment` и `regenerate_thumbnails`
3. ✅ Весь код закоммичен и отправлен на GitHub

## 🔧 Что нужно сделать на VPS

Подключитесь к серверу и выполните эти команды:

```bash
# Подключение к VPS
ssh root@176.118.198.78

# Переход в директорию проекта
cd /var/www/www-root/data/www/logist2

# Сброс локальных изменений (если есть конфликты)
git reset --hard HEAD
git clean -fd

# Обновление кода с GitHub
git pull origin master

# Активация виртуального окружения
source .venv/bin/activate

# Проверка окружения
python manage.py check_photo_environment

# Если Pillow требует переустановки (видно по выводу check_photo_environment):
# apt-get update
# apt-get install -y libjpeg-dev zlib1g-dev libpng-dev libtiff-dev libfreetype6-dev
# pip install --upgrade --force-reinstall Pillow

# Создание папок с правильными правами
mkdir -p media/container_photos/thumbnails
chown -R www-root:www-root media/
chmod -R 775 media/container_photos/

# Пересоздание миниатюр для существующих фото
python manage.py regenerate_thumbnails

# Сборка статики
python manage.py collectstatic --noinput

# Перезапуск сервисов
systemctl restart gunicorn
systemctl restart daphne

# Проверка статуса
systemctl status gunicorn
systemctl status daphne
```

## 🧪 Тестирование

После выполнения команд:

1. Откройте админку: https://caromoto-lt.com/admin/
2. Перейдите в "Архивы фотографий контейнеров"
3. Загрузите тестовый архив с фотографиями
4. Проверьте логи: 
   ```bash
   journalctl -u gunicorn -f
   ```
5. Убедитесь что миниатюры создались:
   ```bash
   ls -la media/container_photos/thumbnails/2025/*/
   ```
6. Проверьте на сайте что фотографии отображаются

## 📊 Ожидаемый результат

В логах должны появиться сообщения:
```
ContainerPhotoArchive 1: начало извлечения из ...
ContainerPhoto 123: миниатюра успешно создана: thumb_photo.jpg
ContainerPhotoArchive 1: обработка завершена. Успешно: 10, ошибок: 0
```

## 🐛 Если возникли проблемы

### Проблема: миниатюры не создаются

```bash
# Проверка прав
ls -la media/container_photos/
sudo -u www-root touch media/container_photos/thumbnails/test.txt

# Проверка Pillow
sudo -u www-root python3
>>> from PIL import Image, features
>>> print(features.check('jpg'))  # Должно быть True
```

### Проблема: не отображаются на сайте

```bash
# Проверка nginx конфигурации
nginx -t
cat /etc/nginx/sites-enabled/logist2 | grep -A 10 "location /media"

# Проверка доступности через curl
curl -I https://caromoto-lt.com/media/container_photos/2025/10/test.jpg
```

## 📝 Дополнительно

- Скрипт для автоматической настройки: `setup_vps_photos.sh`
- Подробная документация: `CONTAINER_PHOTOS_FIX.md`
- PowerShell скрипт для Windows: `deploy_photo_fix.ps1`

---

**Важно:** Все изменения обратно совместимы и не требуют изменения базы данных.

