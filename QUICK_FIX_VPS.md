# 🚀 Быстрое исправление фотографий контейнеров на VPS

## ⚡ TL;DR (Краткая инструкция)

Проблема: не отображаются миниатюры и фотографии контейнеров на сайте после загрузки архива.

**Решение: выполните эти команды на VPS:**

```bash
# 1. Подключаемся к VPS
ssh user@your-vps-ip

# 2. Переходим в директорию проекта
cd /var/www/caromoto-lt

# 3. Обновляем код
git pull origin master

# 4. Активируем виртуальное окружение
source venv/bin/activate

# 5. Проверяем окружение
python manage.py check_photo_environment

# 6. Если есть ошибки с Pillow, устанавливаем зависимости:
sudo apt-get update
sudo apt-get install -y libjpeg-dev zlib1g-dev libpng-dev
pip install --upgrade --force-reinstall Pillow

# 7. Проверяем/создаем папки с правами
sudo mkdir -p media/container_photos/thumbnails
sudo chown -R www-data:www-data media/
sudo chmod -R 775 media/container_photos/

# 8. Пересоздаем миниатюры для существующих фото
python manage.py regenerate_thumbnails

# 9. Перезапускаем сервис
sudo systemctl restart caromoto-lt

# 10. Проверяем логи
sudo tail -f /var/log/nginx/caromoto-lt-error.log
```

## 📋 Что было исправлено

1. **Логика создания миниатюр** - исправлена ошибка в методе `ContainerPhoto.save()`
2. **Обработка архивов** - исправлена последовательность сохранения в `extract_photos()`
3. **Логирование** - все операции теперь логируются в журнал
4. **Фильтрация файлов** - игнорируются служебные файлы (__MACOSX, .DS_Store)

## 🔍 Диагностика

Если проблема остается, выполните диагностику:

```bash
# Проверка окружения
python manage.py check_photo_environment

# Проверка логов
sudo grep -i "containerphoto" /var/log/caromoto-lt/app.log | tail -50

# Проверка прав
ls -la media/container_photos/
ls -la media/container_photos/thumbnails/

# Тест записи
sudo -u www-data touch media/container_photos/thumbnails/test.txt
sudo -u www-data rm media/container_photos/thumbnails/test.txt
```

## 📁 Измененные файлы

- `core/models_website.py` - основные исправления
- `core/management/commands/regenerate_thumbnails.py` - новая команда
- `core/management/commands/check_photo_environment.py` - новая команда
- `CONTAINER_PHOTOS_FIX.md` - подробная документация
- `CHANGELOG.md` - полный список изменений

## 🎯 Ожидаемый результат

После выполнения команд:
- ✅ Все новые архивы будут обрабатываться правильно
- ✅ Миниатюры будут создаваться автоматически
- ✅ Старые фотографии получат миниатюры
- ✅ Фотографии будут отображаться на сайте
- ✅ Все ошибки будут в логах

## 💡 Полезные команды

```bash
# Пересоздать ВСЕ миниатюры (принудительно)
python manage.py regenerate_thumbnails --force

# Пересоздать миниатюры для конкретного контейнера
python manage.py regenerate_thumbnails --container CONTAINER_NUMBER

# Просмотр статистики
python manage.py shell
>>> from core.models_website import ContainerPhoto
>>> total = ContainerPhoto.objects.count()
>>> without_thumbs = ContainerPhoto.objects.filter(thumbnail='').count()
>>> print(f"Всего: {total}, без миниатюр: {without_thumbs}")
```

## 📖 Дополнительная информация

Подробная документация: `CONTAINER_PHOTOS_FIX.md`

---

**Важно:** После развертывания обязательно проверьте, что:
1. Новые архивы обрабатываются без ошибок
2. Миниатюры отображаются в админке и на сайте
3. В логах нет ошибок создания миниатюр

