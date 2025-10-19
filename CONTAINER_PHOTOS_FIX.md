# 🔧 Исправление проблемы с фотографиями контейнеров на VPS

## 📋 Проблема
После загрузки архива фотографий контейнера на VPS:
- Не отображаются картинки предпросмотра (thumbnails)
- Не открываются фотографии на сайте

## 🔍 Причины
1. **Неправильная логика создания миниатюр** - миниатюры не создавались при извлечении из архива
2. **Отсутствие логирования** - ошибки не записывались в логи
3. **Возможно отсутствуют права на папку thumbnails** на VPS
4. **Могут отсутствовать системные библиотеки** для обработки изображений

## ✅ Что было исправлено

### 1. Исправлен метод `ContainerPhoto.save()`
- Убрана логическая ошибка в условии создания миниатюр
- Добавлено полное логирование всех операций
- Улучшена обработка ошибок

### 2. Исправлен метод `ContainerPhotoArchive.extract_photos()`
- Исправлена последовательность сохранения объектов
- Добавлена фильтрация служебных файлов (__MACOSX, .DS_Store)
- Добавлено детальное логирование процесса
- Улучшена обработка ошибок

### 3. Создана команда для пересоздания миниатюр
```bash
python manage.py regenerate_thumbnails
```

## 🚀 Установка на VPS

### Шаг 1: Проверка системных зависимостей

```bash
# Подключаемся к VPS
ssh user@your-vps-ip

# Переходим в директорию проекта
cd /var/www/caromoto-lt

# Проверяем наличие необходимых библиотек
python3 -c "from PIL import Image; print('Pillow OK')"

# Если ошибка, устанавливаем зависимости
sudo apt-get update
sudo apt-get install -y \
    python3-dev \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    libtiff-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev

# Переустанавливаем Pillow с поддержкой всех форматов
pip install --upgrade --force-reinstall Pillow
```

### Шаг 2: Проверка прав доступа

```bash
# Проверяем владельца папки media
ls -la media/

# Проверяем папки для фотографий
ls -la media/container_photos/
ls -la media/container_photos/thumbnails/

# Если папка thumbnails не существует, создаем её
mkdir -p media/container_photos/thumbnails

# Устанавливаем правильные права
# Замените www-data на пользователя, под которым работает gunicorn/uwsgi
sudo chown -R www-data:www-data media/
sudo chmod -R 755 media/

# Для папок загрузки нужны права на запись
sudo chmod -R 775 media/container_photos/
sudo chmod -R 775 media/container_photos/thumbnails/
```

### Шаг 3: Обновление кода

```bash
# Переходим в директорию проекта
cd /var/www/caromoto-lt

# Забираем изменения из репозитория
git pull origin master

# Или, если код загружается вручную, копируем файлы:
# - core/models_website.py
# - core/management/commands/regenerate_thumbnails.py
```

### Шаг 4: Пересоздание существующих миниатюр

```bash
# Активируем виртуальное окружение (если используется)
source venv/bin/activate

# Пересоздаем все миниатюры
python manage.py regenerate_thumbnails --force

# Или только для фотографий без миниатюр
python manage.py regenerate_thumbnails

# Для конкретного контейнера
python manage.py regenerate_thumbnails --container CONTAINER_NUMBER
```

### Шаг 5: Перезапуск сервисов

```bash
# Перезапускаем Django приложение
sudo systemctl restart caromoto-lt

# Или если используется gunicorn напрямую
sudo systemctl restart gunicorn

# Проверяем статус
sudo systemctl status caromoto-lt
```

### Шаг 6: Проверка логов

```bash
# Смотрим логи приложения
sudo tail -f /var/log/caromoto-lt/app.log

# Или логи systemd
sudo journalctl -u caromoto-lt -f

# Смотрим логи nginx
sudo tail -f /var/log/nginx/caromoto-lt-error.log
```

## 🧪 Тестирование

### 1. Загрузка нового архива

1. Войдите в админ-панель: https://your-domain.com/admin/
2. Перейдите в раздел "Архивы фотографий контейнеров"
3. Загрузите тестовый архив с фотографиями
4. Проверьте логи: должны появиться сообщения о создании миниатюр
5. Проверьте, что файлы создались:
   ```bash
   ls -la media/container_photos/2025/*/
   ls -la media/container_photos/thumbnails/2025/*/
   ```

### 2. Проверка на сайте

1. Откройте страницу контейнера на сайте
2. Проверьте, что миниатюры загружаются
3. Проверьте, что полные фотографии открываются

### 3. Проверка API

```bash
# Получаем фотографии контейнера через API
curl -X GET "https://your-domain.com/api/container-photos/CONTAINER_NUMBER/"
```

## 🐛 Отладка проблем

### Проблема: Миниатюры не создаются

**Проверка 1: Права доступа**
```bash
# Проверяем права
ls -la media/container_photos/thumbnails/

# Проверяем, может ли пользователь писать
sudo -u www-data touch media/container_photos/thumbnails/test.txt
sudo -u www-data rm media/container_photos/thumbnails/test.txt
```

**Проверка 2: Библиотеки Pillow**
```bash
# Запускаем Python от пользователя веб-сервера
sudo -u www-data python3
>>> from PIL import Image
>>> import PIL
>>> print(PIL.__version__)
>>> print(PIL.features.check('jpg'))  # Должно быть True
>>> print(PIL.features.check('png'))  # Должно быть True
>>> exit()
```

**Проверка 3: Логи**
```bash
# Смотрим логи Django с фильтрацией по ContainerPhoto
sudo grep -i "containerphoto" /var/log/caromoto-lt/app.log | tail -50
```

### Проблема: Фотографии не отображаются на сайте

**Проверка 1: Nginx раздает media файлы**
```bash
# Проверяем конфигурацию nginx
sudo nginx -t

# Проверяем раздел location /media/ в конфиге
sudo cat /etc/nginx/sites-enabled/caromoto-lt.conf | grep -A 10 "location /media"
```

**Проверка 2: Доступность файлов**
```bash
# Проверяем, что файлы существуют
ls -la media/container_photos/2025/10/

# Проверяем доступ через curl
curl -I "https://your-domain.com/media/container_photos/2025/10/photo.jpg"
# Должен вернуть 200 OK
```

## 📊 Полезные команды

```bash
# Подсчет фотографий без миниатюр
python manage.py shell
>>> from core.models_website import ContainerPhoto
>>> total = ContainerPhoto.objects.count()
>>> without_thumbs = ContainerPhoto.objects.filter(thumbnail='').count()
>>> print(f"Всего фото: {total}, без миниатюр: {without_thumbs}")
>>> exit()

# Очистка старых миниатюр (осторожно!)
find media/container_photos/thumbnails/ -type f -name "thumb_*" -delete

# Проверка размера папок
du -sh media/container_photos/
du -sh media/container_photos/thumbnails/
```

## 📝 Рекомендации

1. **Регулярный мониторинг**: Настройте алерты на ошибки в логах
2. **Бэкапы**: Регулярно делайте бэкапы папки media
3. **Тестирование**: После каждого обновления проверяйте загрузку архивов
4. **Права доступа**: Периодически проверяйте права на папки media

## 🆘 Дополнительная помощь

Если проблема не решается:
1. Проверьте логи Django: `/var/log/caromoto-lt/app.log`
2. Проверьте логи nginx: `/var/log/nginx/caromoto-lt-error.log`
3. Проверьте логи systemd: `sudo journalctl -u caromoto-lt -n 100`
4. Запустите команду с дополнительным выводом:
   ```bash
   python manage.py regenerate_thumbnails --force --verbosity 3
   ```

