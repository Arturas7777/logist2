# 🚀 Быстрый запуск клиентского сайта Caromoto Lithuania

## ⚡ За 5 минут

### 1. Установка зависимостей

```bash
pip install Pillow django-cleanup
```

### 2. Обновление requirements.txt

Добавьте в ваш `requirements.txt`:
```
Pillow>=10.0.0
django-cleanup>=8.0.0
```

### 3. Добавление приложения в INSTALLED_APPS

Django автоматически определит новые модели из `core/models_website.py` так как они импортированы в `core/models.py`.

Опционально добавьте в `INSTALLED_APPS` в `settings.py`:
```python
INSTALLED_APPS = [
    # ... существующие приложения
    'django_cleanup.apps.CleanupConfig',  # Для автоочистки файлов
]
```

### 4. Создание миграций

```bash
python manage.py makemigrations
python manage.py migrate
```

### 5. Создание папок для медиа

```bash
mkdir media
mkdir media/car_photos
mkdir media/container_photos
mkdir media/news
```

### 6. Сбор статики

```bash
python manage.py collectstatic --noinput
```

### 7. Создание тестового клиента

```bash
python manage.py shell
```

В shell выполните:
```python
from django.contrib.auth.models import User
from core.models import Client
from core.models_website import ClientUser

# Создаем пользователя
user = User.objects.create_user(
    username='test_client',
    email='test@example.com',
    password='test123456'
)

# Берем первого клиента или создаем нового
try:
    client = Client.objects.first()
    if not client:
        client = Client.objects.create(name='Тестовый клиент')
except:
    client = Client.objects.create(name='Тестовый клиент')

# Связываем
ClientUser.objects.create(
    user=user,
    client=client,
    phone='+370123456789',
    is_verified=True
)

print(f"✅ Создан клиент: {user.username}")
print(f"   Email: {user.email}")
print(f"   Пароль: test123456")
exit()
```

### 8. Запуск сервера

```bash
python manage.py runserver
```

## 🎉 Готово!

Откройте браузер и перейдите по адресам:

- **Главная страница**: http://localhost:8000/
- **Личный кабинет**: http://localhost:8000/dashboard/
- **Админка**: http://localhost:8000/admin/

Войдите в личный кабинет с данными:
- Username: `test_client`
- Password: `test123456`

## 📸 Добавление фотографий

### Через админку:

1. Войдите в админку: http://localhost:8000/admin/
2. Перейдите в **"Фотографии автомобилей"**
3. Нажмите **"Добавить"**
4. Выберите автомобиль
5. Загрузите фото
6. Отметьте "Доступно клиенту"
7. Сохраните

## 🤖 Тестирование ИИ-помощника

1. Откройте главную страницу
2. Кликните на иконку чата в правом нижнем углу
3. Задайте вопросы:
   - "Привет"
   - "Сколько стоит доставка?"
   - "Как отследить груз?"
   - "Контакты компании"

## 📰 Создание новостей

1. Админка → **"Новости"** → **"Добавить"**
2. Заполните:
   - Заголовок: "Новая услуга доставки"
   - Slug: `novaya-usluga-dostavki` (автозаполнение)
   - Краткое описание: "Мы запустили..."
   - Содержание: полный текст
3. Отметьте **"Опубликовано"**
4. Сохраните

Новость появится на главной странице и в разделе "Новости".

## 🔍 Тестирование отслеживания

На главной странице введите:
- VIN автомобиля из вашей БД
- Или номер контейнера

Система покажет актуальную информацию о грузе.

## 🎨 Кастомизация

### Изменить цвета:

Отредактируйте `core/static/website/css/style.css`:
```css
:root {
    --primary-color: #ваш-цвет;
}
```

### Изменить контакты:

Отредактируйте `templates/website/base.html` и `templates/website/contact.html`

### Добавить логотип:

1. Поместите файл в `core/static/website/img/logo.png`
2. Обновите `templates/website/base.html`:
```html
<a class="navbar-brand" href="/">
    <img src="{% static 'website/img/logo.png' %}" alt="Caromoto Lithuania" height="40">
</a>
```

## 📱 Мобильная версия

Сайт полностью адаптивен. Протестируйте на разных устройствах:
- Откройте DevTools (F12)
- Переключитесь в режим устройства (Ctrl+Shift+M)
- Выберите iPhone/iPad/Android

## 🐛 Решение проблем

### Ошибка импорта Pillow:
```bash
pip install --upgrade Pillow
```

### Статика не загружается:
```bash
python manage.py collectstatic --clear
```

### База данных не обновляется:
```bash
python manage.py migrate --run-syncdb
```

### Порт занят:
```bash
python manage.py runserver 8001
```

## 🔒 Безопасность для продакшена

Перед запуском на сервере:

1. **Установите DEBUG=False** в `.env`
2. **Настройте ALLOWED_HOSTS**
3. **Используйте безопасные пароли**
4. **Настройте HTTPS**
5. **Настройте backup медиа файлов**

## 📊 Статистика

Просмотр статистики в админке:

- **Чаты с ИИ** → посмотрите популярные вопросы
- **Запросы отслеживания** → анализ интереса клиентов
- **Сообщения** → обратная связь от клиентов
- **Новости** → просмотры публикаций

## 🚀 Продвинутые функции

### Интеграция с OpenAI GPT:

```bash
pip install openai
```

Добавьте в `.env`:
```
OPENAI_API_KEY=sk-ваш-ключ
```

Следуйте инструкциям в `WEBSITE_SETUP.md`.

### Email уведомления:

Настройте SMTP в `settings.py` для отправки уведомлений о новых сообщениях.

## 💡 Советы

1. **Регулярно делайте backup БД и медиа файлов**
2. **Оптимизируйте изображения перед загрузкой**
3. **Используйте CDN для статики на продакшене**
4. **Мониторьте логи ИИ-помощника для улучшения ответов**
5. **Собирайте обратную связь от клиентов**

## 📞 Поддержка

Полная документация в `WEBSITE_SETUP.md`

---

**Успешного запуска! 🎉**


