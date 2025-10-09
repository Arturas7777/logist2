# 🌐 Установка и настройка клиентского сайта Caromoto Lithuania

## Обзор

Клиентский сайт Caromoto Lithuania - это современный веб-портал, интегрированный с вашей Django админкой, который предоставляет:

- 🏠 Информационный сайт компании (главная, о компании, услуги, контакты)
- 📦 Отслеживание грузов по VIN или номеру контейнера
- 👤 Личный кабинет клиента
- 📸 Просмотр и скачивание фотографий автомобилей
- 🤖 ИИ-помощник для ответов на вопросы клиентов
- 📰 Система новостей
- 📧 Форма обратной связи

## 📋 Шаг 1: Миграции базы данных

Создайте и примените миграции для новых моделей:

```bash
python manage.py makemigrations core
python manage.py migrate
```

## 📁 Шаг 2: Создание папок для медиа файлов

Создайте директорию для загружаемых файлов:

```bash
mkdir media
mkdir media/car_photos
mkdir media/container_photos
mkdir media/news
```

## 👨‍💼 Шаг 3: Создание клиентских пользователей

### Через Django админку:

1. Войдите в админку: `/admin/`
2. Перейдите в раздел **"Клиентские пользователи"**
3. Нажмите **"Добавить клиентский пользователь"**
4. Выберите существующего пользователя Django или создайте нового
5. Свяжите с клиентом из CRM
6. Сохраните

### Через Django shell:

```python
python manage.py shell

from django.contrib.auth.models import User
from core.models import Client
from core.models_website import ClientUser

# Создаем нового пользователя
user = User.objects.create_user(
    username='client_test',
    email='client@example.com',
    password='password123'
)

# Получаем клиента из CRM
client = Client.objects.get(name='Имя клиента')

# Создаем связь
ClientUser.objects.create(
    user=user,
    client=client,
    phone='+370XXXXXXX',
    language='ru',
    is_verified=True
)
```

## 📸 Шаг 4: Загрузка фотографий

### Через админку:

1. Перейдите в **"Фотографии автомобилей"** или **"Фотографии контейнеров"**
2. Нажмите **"Добавить фотографию"**
3. Выберите автомобиль/контейнер
4. Загрузите фото
5. Выберите тип фото (Погрузка, Разгрузка, Повреждения, Общее, Документы)
6. Отметьте "Доступно клиенту" если фото должно быть видно клиенту
7. Сохраните

### Программно (для массовой загрузки):

```python
from core.models import Car
from core.models_website import CarPhoto

car = Car.objects.get(vin='XXXXXXXXXXXXX')

photo = CarPhoto.objects.create(
    car=car,
    photo='path/to/photo.jpg',  # Относительный путь от MEDIA_ROOT
    photo_type='UNLOADING',
    description='Фото после разгрузки',
    is_public=True
)
```

## 📰 Шаг 5: Создание новостей

1. Перейдите в админку → **"Новости"**
2. Нажмите **"Добавить новость"**
3. Заполните:
   - Заголовок
   - URL (slug) - автоматически генерируется
   - Краткое описание (excerpt)
   - Содержание (content)
   - Изображение (опционально)
4. Отметьте **"Опубликовано"**
5. Сохраните

## 🤖 Шаг 6: Настройка ИИ-помощника

### Базовый вариант (используется по умолчанию):
ИИ-помощник работает на основе простой логики вопрос-ответ, встроенной в `core/views_website.py`.

### Интеграция с OpenAI GPT (опционально):

1. Установите библиотеку OpenAI:
```bash
pip install openai
```

2. Добавьте в `.env`:
```
OPENAI_API_KEY=sk-ваш-ключ-openai
```

3. Раскомментируйте и настройте функцию `get_ai_response_openai()` в `core/views_website.py`:
```python
import openai
from django.conf import settings

def get_ai_response_openai(message, user=None, client=None):
    openai.api_key = settings.OPENAI_API_KEY
    
    # Контекст о компании
    company_context = """
    Вы - ИИ-помощник логистической компании Caromoto Lithuania.
    Компания специализируется на доставке автомобилей из США в Казахстан.
    """
    
    messages = [
        {"role": "system", "content": company_context},
        {"role": "user", "content": message}
    ]
    
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=messages,
        temperature=0.7,
        max_tokens=500
    )
    
    return response.choices[0].message.content
```

4. Замените вызов `get_ai_response()` на `get_ai_response_openai()` в функции `ai_chat`.

## 🔒 Шаг 7: Настройка прав доступа

### Для клиентов:

Клиенты видят только свои автомобили и контейнеры. Фильтрация осуществляется автоматически через `ClientUser.client`.

### Для админов:

Администраторы видят все данные через Django админку.

## 🎨 Шаг 8: Кастомизация дизайна

### Логотип компании:
1. Замените текст "Caromoto Lithuania" в `templates/website/base.html` на изображение:
```html
<a class="navbar-brand" href="{% url 'website:home' %}">
    <img src="/static/website/img/logo.png" alt="Caromoto Lithuania" height="40">
</a>
```

### Цветовая схема:
Измените переменные в `core/static/website/css/style.css`:
```css
:root {
    --primary-color: #0d6efd;  /* Ваш основной цвет */
    --secondary-color: #6c757d;
    /* ... */
}
```

### Контактная информация:
Обновите контакты в `templates/website/base.html` (футер) и `templates/website/contact.html`.

## 🚀 Шаг 9: Запуск сайта

### В режиме разработки:

```bash
python manage.py runserver
```

Сайт будет доступен по адресу: `http://localhost:8000/`

### В продакшене:

1. Соберите статические файлы:
```bash
python manage.py collectstatic
```

2. Настройте веб-сервер (Nginx) для раздачи медиа файлов:
```nginx
location /media/ {
    alias /path/to/your/project/media/;
}
```

## 📊 Использование сайта

### Для клиентов:

1. **Главная страница** (`/`): информация о компании, отслеживание груза
2. **О компании** (`/about/`): подробная информация о Caromoto Lithuania
3. **Услуги** (`/services/`): список услуг и тарифы
4. **Новости** (`/news/`): актуальные новости компании
5. **Контакты** (`/contact/`): форма обратной связи
6. **Личный кабинет** (`/dashboard/`): просмотр автомобилей и контейнеров (требуется авторизация)

### ИИ-помощник:

Кликните на кнопку с чат-ботом в правом нижнем углу для получения мгновенных ответов на вопросы.

### Отслеживание:

Введите VIN автомобиля или номер контейнера на главной странице для получения актуальной информации о статусе груза.

## 🔧 Дополнительные настройки

### Email уведомления:

Для отправки email-уведомлений при новых сообщениях через форму обратной связи, настройте SMTP в `settings.py`:

```python
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'your-email@gmail.com'
EMAIL_HOST_PASSWORD = 'your-password'
DEFAULT_FROM_EMAIL = 'Caromoto Lithuania <noreply@caromoto-lt.com>'
```

### Мультиязычность:

Сайт поддерживает три языка: Русский (RU), English (EN), Lietuvių (LT).
Для добавления переводов используйте Django i18n.

## 🐛 Отладка

### Проверка логов ИИ-помощника:
```python
from core.models_website import AIChat

# Последние 10 чатов
AIChat.objects.all().order_by('-created_at')[:10]

# Статистика полезности
AIChat.objects.filter(was_helpful=True).count()
AIChat.objects.filter(was_helpful=False).count()
```

### Проверка запросов отслеживания:
```python
from core.models_website import TrackingRequest

# Последние запросы
TrackingRequest.objects.all().order_by('-created_at')[:20]

# Найденные грузы
TrackingRequest.objects.exclude(car__isnull=True, container__isnull=True)
```

## 📱 Адаптивность

Сайт полностью адаптивен и корректно отображается на:
- 🖥️ Десктопах
- 💻 Ноутбуках
- 📱 Планшетах
- 📱 Мобильных телефонах

## 🎯 Следующие шаги

1. ✅ Создайте несколько тестовых клиентов
2. ✅ Загрузите фотографии для автомобилей
3. ✅ Опубликуйте первые новости
4. ✅ Протестируйте ИИ-помощника
5. ✅ Настройте email уведомления
6. ✅ Добавьте реальные контактные данные
7. ✅ Интегрируйте с OpenAI GPT (опционально)

## 💡 Полезные команды

```bash
# Создать суперпользователя
python manage.py createsuperuser

# Собрать статику
python manage.py collectstatic

# Очистить кэш сессий
python manage.py clearsessions

# Экспорт новостей
python manage.py dumpdata core.NewsPost --indent 2 > news.json

# Импорт новостей
python manage.py loaddata news.json
```

## 📞 Поддержка

Если возникли вопросы или проблемы, обратитесь к документации Django или свяжитесь с разработчиком.

---

**Caromoto Lithuania** © 2024 - Современная логистика автомобилей


