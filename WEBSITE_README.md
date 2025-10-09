# 🌐 Клиентский сайт Caromoto Lithuania

## 📋 Описание

Современный веб-сайт для логистической компании Caromoto Lithuania, интегрированный с Django админ-панелью.

## ✨ Возможности

### Для клиентов:
- 🏠 **Информационный сайт**: главная, о компании, услуги, контакты
- 📦 **Отслеживание грузов**: поиск по VIN или номеру контейнера
- 👤 **Личный кабинет**: 
  - Просмотр своих автомобилей и контейнеров
  - Статус доставки в реальном времени
  - Просмотр и скачивание фотографий
  - История заказов
- 🤖 **ИИ-помощник**: мгновенные ответы на вопросы 24/7
- 📰 **Новости**: актуальная информация о компании
- 📧 **Обратная связь**: форма для связи с менеджерами

### Для администраторов:
- 📸 **Управление фотографиями**: загрузка и модерация фото авто
- 📰 **Управление новостями**: публикация новостей
- 💬 **Просмотр сообщений**: обратная связь от клиентов
- 📊 **Аналитика**: статистика обращений и запросов
- 🤖 **История ИИ-чатов**: анализ вопросов клиентов

## 🛠 Технологии

- **Backend**: Django 4.x, Django REST Framework
- **Frontend**: Bootstrap 5, Vanilla JavaScript
- **База данных**: PostgreSQL
- **ИИ**: Встроенная логика или OpenAI GPT-4 (опционально)
- **Медиа**: Pillow для обработки изображений

## 📦 Структура проекта

```
core/
├── models_website.py          # Модели для сайта (ClientUser, CarPhoto, NewsPost и др.)
├── views_website.py           # Views для клиентского сайта
├── serializers_website.py     # API сериализаторы
├── urls_website.py            # URL маршруты сайта
├── admin_website.py           # Админ-панель для сайта
└── static/website/
    ├── css/style.css          # Стили сайта
    └── js/
        ├── ai-chat.js         # ИИ-помощник
        └── main.js            # Основная логика

templates/website/
├── base.html                  # Базовый шаблон
├── home.html                  # Главная страница
├── about.html                 # О компании
├── services.html              # Услуги
├── contact.html               # Контакты
├── news_list.html             # Список новостей
├── news_detail.html           # Детальная страница новости
├── client_dashboard.html      # Личный кабинет
├── car_detail.html            # Детали автомобиля
└── container_detail.html      # Детали контейнера
```

## 🚀 Быстрый старт

### 1. Установка

```bash
# Установите зависимости
pip install Pillow django-cleanup

# Создайте миграции
python manage.py makemigrations
python manage.py migrate

# Создайте папки для медиа
mkdir -p media/car_photos media/container_photos media/news

# Соберите статику
python manage.py collectstatic
```

### 2. Создание тестового клиента

```bash
python manage.py shell
```

```python
from django.contrib.auth.models import User
from core.models import Client
from core.models_website import ClientUser

user = User.objects.create_user('test_client', 'test@example.com', 'password123')
client = Client.objects.first() or Client.objects.create(name='Test Client')
ClientUser.objects.create(user=user, client=client, is_verified=True)
```

### 3. Запуск

```bash
python manage.py runserver
```

Откройте: http://localhost:8000/

## 📖 Документация

- **Быстрый старт**: [`QUICK_START_WEBSITE.md`](QUICK_START_WEBSITE.md)
- **Полная документация**: [`WEBSITE_SETUP.md`](WEBSITE_SETUP.md)

## 🎨 Кастомизация

### Цвета

Измените CSS переменные в `core/static/website/css/style.css`:

```css
:root {
    --primary-color: #0d6efd;
    --secondary-color: #6c757d;
    /* ... */
}
```

### Контакты

Обновите информацию в:
- `templates/website/base.html` (футер)
- `templates/website/contact.html`

### Логотип

Добавьте файл `core/static/website/img/logo.png` и обновите навбар в `base.html`.

## 🤖 ИИ-помощник

### Базовый режим (по умолчанию)
Использует встроенную логику вопрос-ответ.

### Режим OpenAI GPT (опционально)

```bash
pip install openai
```

Добавьте в `.env`:
```
OPENAI_API_KEY=sk-ваш-ключ
```

Следуйте инструкциям в `WEBSITE_SETUP.md`.

## 📸 Работа с фотографиями

### Загрузка через админку:

1. Админка → "Фотографии автомобилей"
2. Добавить фотографию
3. Выбрать автомобиль и тип фото
4. Отметить "Доступно клиенту"

### Программная загрузка:

```python
from core.models import Car
from core.models_website import CarPhoto

car = Car.objects.get(vin='XXXXXXXXXXXXX')
photo = CarPhoto.objects.create(
    car=car,
    photo='path/to/photo.jpg',
    photo_type='UNLOADING',
    is_public=True
)
```

## 📰 Управление новостями

1. Админка → "Новости" → "Добавить"
2. Заполните заголовок, содержание
3. Добавьте изображение (опционально)
4. Отметьте "Опубликовано"
5. Сохраните

## 🔒 Безопасность

### Для продакшена:

1. Установите `DEBUG=False`
2. Настройте `ALLOWED_HOSTS`
3. Используйте HTTPS
4. Настройте CSRF_TRUSTED_ORIGINS
5. Регулярно обновляйте зависимости

## 📊 API Endpoints

### Публичные:
- `POST /api/track/` - Отслеживание груза
- `POST /api/ai-chat/` - ИИ-помощник
- `POST /api/contact/` - Форма обратной связи
- `GET /api/news/` - Список новостей

### Для клиентов (требуется авторизация):
- `GET /api/cars/` - Список автомобилей
- `GET /api/containers/` - Список контейнеров
- `GET /car/<id>/download-photos/` - Скачать все фото

## 🧪 Тестирование

```bash
# Запустите тесты
python manage.py test core

# Проверка покрытия кода
coverage run --source='.' manage.py test
coverage report
```

## 🚀 Деплой

### На VPS:

```bash
# Сборка статики
python manage.py collectstatic --noinput

# Запуск с Gunicorn
gunicorn logist2.wsgi:application --bind 0.0.0.0:8000

# Настройка Nginx для медиа
location /media/ {
    alias /path/to/media/;
}
```

## 📈 Мониторинг

### Статистика в админке:

- **История ИИ-чатов**: анализ вопросов
- **Запросы отслеживания**: популярные поиски
- **Сообщения**: обратная связь
- **Просмотры новостей**: популярность контента

### Полезные команды:

```bash
# Количество чатов с ИИ
python manage.py shell -c "from core.models_website import AIChat; print(AIChat.objects.count())"

# Статистика запросов
python manage.py shell -c "from core.models_website import TrackingRequest; print(TrackingRequest.objects.filter(car__isnull=False).count(), 'найдено')"
```

## 🐛 Решение проблем

### Статика не загружается
```bash
python manage.py collectstatic --clear
```

### Ошибка Pillow
```bash
pip uninstall Pillow
pip install Pillow
```

### Медиа файлы 404
Убедитесь что в `urls.py` добавлено:
```python
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
```

## 🤝 Вклад

Для улучшения сайта:
1. Fork проекта
2. Создайте feature branch
3. Commit изменений
4. Push в branch
5. Создайте Pull Request

## 📄 Лицензия

Proprietary - Caromoto Lithuania © 2024

## 📞 Контакты

- **Email**: info@caromoto-lt.com
- **Сайт**: https://caromoto-lt.com
- **Адрес**: Вильнюс, Литва

---

**Сделано с ❤️ для Caromoto Lithuania**


