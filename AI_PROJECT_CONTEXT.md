# КОНТЕКСТ ПРОЕКТА LOGIST2 ДЛЯ AI

## О ПРОЕКТЕ

**Название:** Logist2 (Caromoto Lithuania)
**Тип:** Django-приложение для логистической компании
**Основная функция:** Управление контейнерами, автомобилями, клиентами, складами, инвойсами

### Технологии
- **Backend:** Django 5.1.7 + Python 3.10-3.12
- **Database:** PostgreSQL
- **Frontend:** Django Admin + HTMX
- **WebSockets:** Channels + Daphne
- **Web Server:** Nginx + Gunicorn
- **Клиентский сайт:** Django templates + REST API

### Основные компоненты
- **CRM система** - управление клиентами, балансами
- **Логистика** - контейнеры, автомобили, склады, линии, перевозчики
- **Биллинг** - инвойсы, платежи, балансы
- **Клиентский портал** - отслеживание грузов, фотографии
- **Google Drive интеграция** - автозагрузка фотографий контейнеров

## VPS СЕРВЕР

**IP:** 176.118.198.78
**SSH:** `root@176.118.198.78`
**Домен:** https://caromoto-lt.com

### Расположение
```
Проект: /var/www/www-root/data/www/logist2
Virtualenv: /var/www/www-root/data/www/logist2/.venv
Media: /var/www/www-root/data/www/logist2/media
Static: /var/www/www-root/data/www/logist2/staticfiles
```

### Сервисы
```bash
# Gunicorn (Django application)
systemctl restart gunicorn
systemctl status gunicorn

# Daphne (WebSockets)
systemctl restart daphne
systemctl status daphne

# Nginx
systemctl reload nginx
nginx -t
```

### Nginx конфигурация
```
Файл: /etc/nginx/vhosts/www-root/176.118.198.78.conf

location /media/ {
    alias /var/www/www-root/data/www/logist2/media/;
}

location /static/ {
    alias /var/www/www-root/data/www/logist2/staticfiles/;
}
```

## ДЕПЛОЙ

### Способ 1: PowerShell скрипт (с Windows)
```powershell
.\deploy.ps1
```

### Способ 2: Git на сервере
```bash
ssh root@176.118.198.78
cd /var/www/www-root/data/www/logist2
git pull origin master
source .venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
systemctl restart gunicorn
systemctl restart daphne
```

### Что делает deploy.ps1:
1. Копирует файлы через SCP: `core/*`, `logist2/*`, `templates/*`
2. Запускает collectstatic
3. Перезапускает gunicorn и daphne

## СТРУКТУРА ПРОЕКТА

```
logist2/
├── core/                           # Основное приложение
│   ├── models.py                   # Основные модели (Container, Car, Client, etc)
│   ├── models_website.py           # Модели для клиентского сайта
│   ├── models_billing.py           # Биллинг система
│   ├── admin.py                    # Django Admin конфигурация
│   ├── admin_website.py            # Admin для клиентского сайта
│   ├── views.py                    # Views для админки
│   ├── views_website.py            # Views для клиентского сайта
│   ├── google_drive_sync.py        # Интеграция с Google Drive
│   ├── management/commands/        # Management команды
│   │   ├── regenerate_thumbnails.py
│   │   ├── check_photo_environment.py
│   │   ├── cleanup_broken_photos.py
│   │   └── sync_google_drive_photos.py
│   ├── services/                   # Бизнес-логика
│   ├── static/                     # Статика для админки
│   └── templates/                  # Шаблоны админки
├── templates/                      # Общие шаблоны
│   ├── admin/                      # Кастомные админ шаблоны
│   └── website/                    # Клиентский сайт
├── media/                          # Загружаемые файлы
│   ├── container_photos/           # Фото контейнеров
│   │   └── thumbnails/             # Миниатюры
│   ├── container_archives/         # ZIP архивы
│   └── car_photos/                 # Фото автомобилей
├── logist2/                        # Настройки проекта
│   ├── settings.py                 # Основные настройки
│   ├── urls.py                     # URL routing
│   └── wsgi.py / asgi.py           # WSGI/ASGI
└── requirements.txt                # Python зависимости
```

## КЛЮЧЕВЫЕ МОДЕЛИ

### Container (Контейнер)
- `number` - номер контейнера (уникальный)
- `status` - статус (FLOATING, IN_PORT, UNLOADED, TRANSFERRED)
- `google_drive_folder_url` - ссылка на папку с фотографиями в Google Drive
- `container_cars` - связанные автомобили

### Car (Автомобиль)
- `vin` - VIN номер (уникальный)
- `container` - связь с контейнером
- `client` - клиент-владелец

### ContainerPhoto (Фотография контейнера)
- `container` - связь с контейнером
- `photo` - оригинальное фото
- `thumbnail` - миниатюра (создается автоматически)
- `is_public` - доступно клиенту

## GOOGLE DRIVE ИНТЕГРАЦИЯ

### Структура папок Google Drive:
```
Главная папка: https://drive.google.com/drive/u/1/folders/1PkrfxocilDZjDaT3R1SQ9DflyWBBFNpW

├── AUTO IŠ KONTO (ВЫГРУЖЕННЫЕ)
│   └── [месяц]/[номер_контейнера]/[фотографии]
│
└── KONTO VIDUS (В КОНТЕЙНЕРЕ)
    └── [месяц]/[номер_контейнера]/[фотографии]
```

### Как работает загрузка:
1. В админке контейнера указываем ссылку на папку Google Drive
2. Нажимаем кнопку "📥 Загрузить фото с Google Drive"
3. Фотографии скачиваются асинхронно (в фоне)
4. Миниатюры создаются автоматически
5. Через 1-2 минуты обновляем страницу - фотографии появляются

## ПОЛЕЗНЫЕ КОМАНДЫ

### На сервере:
```bash
# Подключение
ssh root@176.118.198.78

# Переход в проект
cd /var/www/www-root/data/www/logist2
source .venv/bin/activate

# Проверка окружения для фотографий
python manage.py check_photo_environment

# Пересоздание миниатюр
python manage.py regenerate_thumbnails
python manage.py regenerate_thumbnails --force

# Удаление битых записей фотографий
python manage.py cleanup_broken_photos
python manage.py cleanup_broken_photos --delete

# Синхронизация с Google Drive
python manage.py sync_google_drive_photos

# Исправление прав доступа после загрузки фотографий (ВАЖНО!)
./fix_media_permissions.sh

# Проверка логов
journalctl -u gunicorn -f
journalctl -u daphne -f

# Очистка кэша Python
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
```

### Локально:
```powershell
# Запуск dev сервера
.\START_ME.bat

# Деплой на VPS
.\deploy.ps1

# Миграции
python manage.py makemigrations
python manage.py migrate
```

## ТЕКУЩЕЕ СОСТОЯНИЕ

### Что работает:
✅ Основная CRM система
✅ Управление контейнерами и автомобилями
✅ Биллинг и балансы
✅ Клиентский портал
✅ Google Drive интеграция (загрузка фотографий)
✅ Автоматическое создание миниатюр

### Известные проблемы:
⚠️ SSH connection timeout при длительных операциях
⚠️ Миниатюры могут не отображаться если nginx конфигурация неправильная
⚠️ Асинхронная загрузка из Google Drive - нужно ждать 1-2 минуты
⚠️ **ВАЖНО:** После загрузки фотографий вручную (через команды от root) нужно исправить права доступа: `./fix_media_permissions.sh`

### Недавние изменения:
- Исправлена логика создания миниатюр контейнеров
- Добавлена интеграция с Google Drive
- Улучшена админка фотографий контейнеров
- Добавлены команды для диагностики и восстановления
- **ИСПРАВЛЕНО:** Проблема с отображением миниатюр на VPS (права доступа)
- Добавлен скрипт `fix_media_permissions.sh` для автоматического исправления прав

## ВАЖНЫЕ НАСТРОЙКИ

### База данных (на сервере)
```
DB_NAME=logist2
DB_USER=www-root
DB_HOST=localhost
DB_PORT=5432
```

### Пользователь веб-сервера
```
Nginx: www-data или www-root
Gunicorn: www-root
```

### Права на файлы
```bash
# Media файлы
chown -R www-root:www-root media/
chmod -R 755 media/

# Для загрузки
chmod -R 775 media/container_photos/
chmod -R 775 media/container_archives/
```

## ПАМЯТКА ДЛЯ AI

1. **Не создавай документацию** если пользователь не просит
2. **Сразу деплой** - измени код и задеплой через `.\deploy.ps1`
3. **SSH нестабилен** - если timeout, попробуй еще раз через минуту
4. **Главная компания** - Caromoto Lithuania (все процессы привязаны к ней)
5. **После изменений** - всегда перезапускай gunicorn и daphne
6. **Очищай __pycache__** после изменений в коде

## БЫСТРЫЕ КОМАНДЫ

```bash
# Полный деплой с сервера
cd /var/www/www-root/data/www/logist2 && git pull && source .venv/bin/activate && python manage.py migrate && python manage.py collectstatic --noinput && find . -type d -name __pycache__ -delete && systemctl restart gunicorn && systemctl restart daphne

# Проверка статуса
systemctl is-active gunicorn daphne nginx

# Логи последние
journalctl -u gunicorn -n 50 --no-pager

# Проверка фотографий
python manage.py check_photo_environment
python manage.py regenerate_thumbnails

# Исправление прав доступа к media файлам (после загрузки фотографий)
./fix_media_permissions.sh
```

## РЕПОЗИТОРИЙ

**GitHub:** https://github.com/Arturas7777/logist2.git
**Branch:** master

---

**Используй этот контекст в начале каждого диалога для быстрого старта работы над проектом.**

