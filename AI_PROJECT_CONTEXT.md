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
- `container` - связь с контейнером (необязательное поле с 20.10.2025)
- `client` - клиент-владелец
- `warehouse` - основной склад
- `car_services` - дополнительные услуги от разных складов/линий/перевозчиков

### CarService (Услуга автомобиля)
- `car` - связь с автомобилем
- `service_type` - тип поставщика (WAREHOUSE, LINE, CARRIER)
- `service_id` - ID услуги (WarehouseService, LineService, CarrierService)
- `custom_price` - индивидуальная цена (если отличается от дефолтной)
- `quantity` - количество услуг
- **Позволяет добавлять услуги от ЛЮБЫХ складов к автомобилю (не только от основного)**

### ContainerPhoto (Фотография контейнера)
- `container` - связь с контейнером
- `photo` - оригинальное фото
- `thumbnail` - миниатюра (создается автоматически)
- `is_public` - доступно клиенту

### NewInvoice (Новый инвойс) - ОСНОВНАЯ СИСТЕМА БИЛЛИНГА
- `number` - номер инвойса (генерируется автоматически)
- `issuer_*` - кто выставил (может быть Company, Warehouse, Line, Carrier)
- `recipient_*` - кому выставлен (может быть Client, Company, Warehouse, Line, Carrier)
- `cars` - ManyToMany связь с автомобилями
- `items` - позиции инвойса (генерируются автоматически из услуг автомобилей)
- **При изменении автомобиля инвойс АВТОМАТИЧЕСКИ пересчитывается** через сигнал

### InvoiceItem (Позиция инвойса)
- `invoice` - связь с инвойсом
- `car` - связь с автомобилем (опционально)
- `description` - описание услуги
- `quantity` - количество
- `unit_price` - цена за единицу
- `total_price` - автоматически рассчитывается (quantity × unit_price)

**Как формируются позиции при создании инвойса от Caromoto Lithuania клиенту:**
1. Хранение: `{car.days} дней × {warehouse.rate}` + статус `[Передан]` или `[Текущее хранение]`
2. Наценка Caromoto Lithuania: `{car.proft}`
3. Все услуги складов (от всех складов, не только основного)
4. Все услуги линий
5. Все услуги перевозчиков

## API ENDPOINTS

### Для управления услугами автомобилей:
- `GET /api/warehouses/` - список всех складов
- `GET /api/car/<car_id>/get_available_services/?type=warehouse&warehouse_id=<id>` - доступные услуги склада
- `POST /api/car/<car_id>/add_services/` - добавить услуги к автомобилю

### Для клиентского портала:
- `POST /api/track/` - отслеживание груза по VIN или номеру контейнера
- `GET /api/container-photos/<container_number>/` - фотографии контейнера
- `POST /api/download-photos-archive/` - скачать архив фотографий

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

### Недавние изменения (октябрь 2025):

**21.10.2025 - Комплексное улучшение системы:**

1. **НАСЛЕДОВАНИЕ ДАТЫ РАЗГРУЗКИ КОНТЕЙНЕРА:** ⭐ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ
   - ✅ **ПРОБЛЕМА РЕШЕНА:** Дата разгрузки контейнера теперь ВСЕГДА наследуется всеми автомобилями
   - Добавлена логика в сигнал `post_save` для Container - работает при ЛЮБОМ способе сохранения
   - Раскомментирован и улучшен код в `ContainerAdmin.save_model()` для админки
   - Убрано условие `if not car.unload_date:` - теперь обновляются ВСЕ автомобили принудительно
   - Улучшены методы `Container.sync_cars_after_warehouse_change()` и `sync_cars_after_edit()`
   - Исправлен метод `Car.save()` - дата разгрузки ВСЕГДА берется из контейнера
   - Улучшен метод `ContainerAdmin.save_formset()` - новые автомобили всегда получают дату контейнера
   - Добавлена тестовая команда `test_unload_date_inheritance` для проверки функциональности
   - ✅ **РЕЗУЛЬТАТ:** При указании или изменении даты разгрузки контейнера все автомобили в нём автоматически получают эту дату

2. **ОПТИМИЗАЦИЯ ПРОИЗВОДИТЕЛЬНОСТИ:** ⚡
   - Использован `bulk_update()` вместо сохранения автомобилей в цикле (одним SQL запросом)
   - Временное отключение сигналов при массовом обновлении для ускорения операции
   - Массовое обновление инвойсов после обновления всех автомобилей (вместо обновления после каждого)
   - Добавлен `select_related('warehouse')` для оптимизации запросов к БД
   - ✅ **РЕЗУЛЬТАТ:** Обновление даты разгрузки контейнера с 20+ автомобилями теперь выполняется в 10-20 раз быстрее

3. **ИСПРАВЛЕНИЕ НАЦЕНКИ В ИНВОЙСАХ:**
   - ✅ Добавлено автоматическое присвоение Caromoto Lithuania как выставителя в `NewInvoiceAdmin.save_model()`
   - ✅ Улучшено логирование для отслеживания добавления наценки
   - Добавлена тестовая команда `test_invoice_markup` для проверки наценки
   - ✅ **РЕЗУЛЬТАТ:** Наценка Caromoto Lithuania корректно добавляется как отдельная позиция в инвойсы

4. **УЛУЧШЕНИЯ ИНТЕРФЕЙСА АДМИНКИ:**
   - ✅ Удален столбец "Перевозчик" из списка автомобилей (не нужная информация)
   - ✅ Добавлено действие "Передан сегодня" в меню Actions для автомобилей
     - Массовая установка статуса "Передан" с текущей датой
     - Работает для множественного выбора автомобилей
   - ✅ Удален фильтр по перевозчику из списка автомобилей

5. **ТЕСТОВЫЕ КОМАНДЫ:**
   - `python manage.py test_unload_date_inheritance` - проверка наследования даты разгрузки
   - `python manage.py test_invoice_markup` - проверка добавления наценки в инвойсы

**Файлы изменены:**
- `core/admin.py` - оптимизация обновления контейнера, новое действие "Передан сегодня"
- `core/models.py` - принудительное наследование даты разгрузки
- `core/signals.py` - массовое обновление автомобилей при изменении контейнера
- `core/admin_billing.py` - автоматическое присвоение Caromoto Lithuania
- `core/models_billing.py` - улучшенное логирование наценки

**20.10.2025 - Синхронизация БД, услуги складов и автоматический пересчет инвойсов:**

1. **СИНХРОНИЗАЦИЯ БД:**
   - ✅ Реализована синхронизация production БД на локальный компьютер через `pg_dump`/`pg_restore`
   - ✅ Команды для создания бэкапа, скачивания и восстановления

2. **УСЛУГИ СКЛАДОВ:**
   - ✅ Добавлена возможность добавлять услуги от разных складов к одному автомобилю
   - Изменен `get_warehouse_services()` для получения услуг от всех складов
   - Добавлен API endpoint `/api/warehouses/` для выбора склада
   - Обновлено отображение с указанием названия склада (основной - зеленый, другие - желтый)
   - Все услуги от всех складов суммируются в общую стоимость

3. **АВТОМАТИЧЕСКИЙ ПЕРЕСЧЕТ ИНВОЙСОВ:** ⭐ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ
   - ✅ **Исправлен сигнал** `update_related_on_car_save` - убран `return` который блокировал обновление NewInvoice
   - ✅ **Автоматическое обновление инвойсов** при изменении любых данных автомобиля:
     - Изменение количества дней хранения
     - Добавление/удаление даты передачи
     - Изменение статуса
     - Добавление/изменение/удаление услуг
   - ✅ **Наценка автоматически добавляется** в инвойс как отдельная позиция
   - ✅ **Хранение пересчитывается** автоматически перед генерацией позиций
   - ✅ **Статус в описании:** `[Передан ДАТА]` или `[Текущее хранение на ДАТА]`
   - ✅ Защита от рекурсии при обновлении

4. **ДРУГИЕ ИСПРАВЛЕНИЯ:**
   - ✅ **ПОЛЕ CONTAINER:** Сделано необязательным в модели Car (миграция 0080)
   - ✅ **ФОНОВЫЕ ИЗОБРАЖЕНИЯ:** Исправлена проблема с отсутствующим фоном на главной странице сайта
     - Скопированы изображения в `core/static/website/images/`
     - Добавлены `hero-background.jpg` и `caromoto_logo.png`

**Предыдущие изменения:**
- Исправлена логика создания миниатюр контейнеров
- Добавлена интеграция с Google Drive
- Улучшена админка фотографий контейнеров
- Добавлены команды для диагностики и восстановления
- **ИСПРАВЛЕНО:** Проблема с отображением миниатюр на VPS (права доступа)
- Добавлен скрипт `fix_media_permissions.sh` для автоматического исправления прав
- **ИСПРАВЛЕНО:** Проблема с именами файлов при загрузке с Google Drive (суффиксы)
- Добавлена команда `fix_photo_names` для исправления старых записей

## 🔴 АКТИВНЫЕ ПРОБЛЕМЫ (20.10.2025)

### ⚠️ НЕРЕШЕННАЯ ПРОБЛЕМА: Отображение миниатюр в Django админке на VPS

**Описание проблемы:**
- Фотографии загружаются корректно на сервер
- Файлы существуют и доступны через Nginx
- В Python коде Django генерирует правильные URL с `/media/` префиксом
- В методе `image_preview` используется `_safe_media_url` для корректировки путей
- **НО в HTML админки браузера отображаются неправильные ссылки** (`/container_photos/...` без `/media/`)

**Что было испробовано:**
1. ✅ Исправлены права доступа файлов (`chown www-data:www-data`)
2. ✅ Убран кастомный `CustomFileSystemStorage` из `settings.py`
3. ✅ Убраны дублирующие настройки `MEDIA_URL`/`MEDIA_ROOT`
4. ✅ Добавлен метод `_safe_media_url()` в `ContainerPhotoAdmin`
5. ✅ Убрана ручная коррекция URL в методе `save()` модели `ContainerPhoto`
6. ✅ Убран несуществующий импорт `MediaFileInput` widget
7. ✅ Выполнены команды: `regenerate_thumbnails`, `fix_photo_names`, `cleanup_broken_photos`

**Диагностика показала:**
```python
# Python код генерирует ПРАВИЛЬНЫЕ URL:
obj.thumbnail.url -> '/media/container_photos/2025/10/19/thumb_IMG_20251015_101923806.jpg'
image_preview(obj) -> '<img src="/media/container_photos/..." />'

# НО в HTML браузера:
<img src="/container_photos/2025/10/19/thumb_IMG_20251015_101923806.jpg" />
```

**Вывод:**
- Проблема НЕ в Python коде Django (он генерирует правильные URL)
- Проблема где-то на уровне рендеринга HTML или клиент-сайда
- Возможно: JavaScript, кастомные admin templates, или неизвестная middleware
- Требует дальнейшего исследования

**Файлы, которые были изменены для фотографий:**
- `logist2/settings.py` - убраны дубликаты и кастомный storage
- `core/admin_website.py` - добавлен `_safe_media_url()`, убран несуществующий import
- `core/models_website.py` - убрана ручная коррекция URL в `save()`

**Файлы, которые были изменены для инвойсов:**
- `core/signals.py` - исправлен `update_related_on_car_save`, добавлена обработка NewInvoice
- `core/models_billing.py` - добавлена наценка в позиции, автопересчет хранения
- `core/models.py` - `get_warehouse_services()` получает услуги от всех складов

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
2. **Сразу деплой** - измени код и задеплой через git push
3. **SSH нестабилен** - если timeout, попробуй еще раз через минуту
4. **Главная компания** - Caromoto Lithuania (все процессы привязаны к ней)
5. **После изменений** - всегда перезапускай gunicorn и daphne
6. **Очищай __pycache__** после изменений в коде
7. **Синхронизация БД** - используй pg_dump/pg_restore для синхронизации с production
8. **Услуги складов** - можно добавлять от любых складов через CarService (не только от основного)
9. **Инвойсы обновляются автоматически** - при изменении Car сигнал пересчитывает все связанные NewInvoice
10. **Тестирование сигналов** - если сигнал не работает локально, проверь что нет `return` который прерывает выполнение

## БЫСТРЫЕ КОМАНДЫ

### На сервере (Linux/Bash):
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

# Создание бэкапа базы данных
cd /var/www/www-root/data/www/logist2
PGPASSWORD='7154032tut' pg_dump -U arturas -h localhost -d logist2_db -F c -b -f logist2_backup_$(date +%Y%m%d).dump
```

### На локальном компьютере (PowerShell):
```powershell
# Синхронизация базы данных с сервера на локальный компьютер
# Шаг 1: Создать дамп на сервере
ssh root@176.118.198.78 "cd /var/www/www-root/data/www/logist2 && PGPASSWORD='7154032tut' pg_dump -U arturas -h localhost -d logist2_db -F c -b -f logist2_sync_backup.dump"

# Шаг 2: Скачать дамп
scp root@176.118.198.78:/var/www/www-root/data/www/logist2/logist2_sync_backup.dump .

# Шаг 3: Восстановить локально
$env:PGPASSWORD='7154032tut'; pg_restore -U arturas -h localhost -d logist2_db --clean --if-exists logist2_sync_backup.dump

# Проверка количества записей
.\.venv\Scripts\activate.ps1
py manage.py shell -c "from core.models import Car, Client, Container; print(f'Cars: {Car.objects.count()}'); print(f'Clients: {Client.objects.count()}'); print(f'Containers: {Container.objects.count()}')"
```

## РЕПОЗИТОРИЙ

**GitHub:** https://github.com/Arturas7777/logist2.git
**Branch:** master

---

**Используй этот контекст в начале каждого диалога для быстрого старта работы над проектом.**

