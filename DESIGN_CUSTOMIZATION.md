# 🎨 Кастомизация дизайна под Caromoto.com

## ✅ Что уже сделано:

Я обновил дизайн сайта в профессиональном стиле автомобильной логистики:

### Цветовая схема:
- **Основной цвет**: Темно-синий/черный (#1a1a2e)
- **Вторичный**: Глубокий синий (#16213e)
- **Акцентный**: Красный (#e94560)
- **Светлый акцент**: Синий (#0f3460)

### Типографика:
- **Шрифт**: Montserrat (профессиональный, современный)
- **Вес**: 400-900 (от обычного до очень жирного)

### Стиль:
- Темная навигация с красным акцентом
- Градиентные фоны
- Современные кнопки с эффектами hover
- Профессиональная цветовая палитра

## 🔧 Как настроить под ваш точный бренд:

### 1. Обновите цвета (если нужны другие)

Откройте файл: `core/static/website/css/style.css`

Найдите раздел `:root` (строки 7-25) и измените:

```css
:root {
    /* Ваши фирменные цвета */
    --primary-color: #1a1a2e;        /* Замените на ваш основной цвет */
    --secondary-color: #16213e;      /* Замените на ваш вторичный */
    --accent-color: #e94560;         /* Замените на ваш акцентный */
    --light-accent: #0f3460;         /* Замените на светлый вариант */
}
```

### 2. Добавьте логотип компании

**Вариант 1: Текстовый логотип (уже сделан)**
В `templates/website/base.html` строка 30-31 - уже настроен.

**Вариант 2: Изображение логотипа**

1. Поместите файл логотипа в: `core/static/website/img/logo.png`

2. В `templates/website/base.html` замените строки 29-32:
```html
<a class="navbar-brand d-flex align-items-center" href="{% url 'website:home' %}">
    {% load static %}
    <img src="{% static 'website/img/logo.png' %}" alt="Caromoto Lithuania" height="45" class="me-2">
</a>
```

### 3. Обновите контактную информацию

**В футере** (`templates/website/base.html`, строки 115-150):
```html
<p class="text-white-50">
    <i class="bi bi-geo-alt"></i> Ваш адрес в Вильнюсе
</p>
<p class="text-white-50">
    <i class="bi bi-telephone"></i> +370 XXX XXXXX (замените на реальный)
</p>
<p class="text-white-50">
    <i class="bi bi-envelope"></i> info@caromoto-lt.com
</p>
```

**На странице контактов** (`templates/website/contact.html`, строки 50-70):
Замените заглушки на реальные данные.

### 4. Настройте изображения

#### Favicon:
1. Создайте файл: `core/static/website/img/favicon.png` (32x32 или 64x64 пикселей)
2. Уже подключен в base.html строка 11

#### Hero изображение (опционально):
Добавьте фоновое изображение в Hero секцию:

В `templates/website/home.html` (строка 7):
```html
<section class="hero-section text-white py-5" style="background: url('{% static 'website/img/hero-bg.jpg' %}') center/cover, var(--gradient-primary);">
```

### 5. Получите точные цвета с caromoto.com

Если у вас есть доступ к сайту caromoto.com, используйте инструменты:

**Способ 1: Chrome DevTools**
1. Откройте caromoto.com
2. Нажмите F12
3. Кликните на элемент с нужным цветом
4. Скопируйте HEX код цвета (например, #e94560)

**Способ 2: Расширения браузера**
- ColorZilla (Chrome/Firefox)
- Eye Dropper (Chrome)

### 6. Примените изменения

После любых изменений в CSS или шаблонах:

```bash
# Пересоберите статику
.\.venv\Scripts\python.exe manage.py collectstatic --noinput

# Перезапустите сервер (Ctrl+C, затем снова runserver)
.\.venv\Scripts\python.exe manage.py runserver
```

## 🎯 Быстрая настройка (если знаете точные цвета)

Пришлите мне HEX коды цветов с caromoto.com:
- Основной цвет (фон навигации, футера)
- Акцентный цвет (кнопки, ссылки)
- Цвет текста

И я мгновенно обновлю все файлы!

## 📸 Примеры кастомизации

### Пример 1: Синяя тема
```css
--primary-color: #003366;
--accent-color: #FF6600;
```

### Пример 2: Зеленая тема  
```css
--primary-color: #1a4d2e;
--accent-color: #4f9a5d;
```

### Пример 3: Красная тема
```css
--primary-color: #1a1a2e;
--accent-color: #e74c3c;
```

## 🔄 Откат изменений

Если хотите вернуть старый дизайн:

```bash
git checkout core/static/website/css/style.css
git checkout templates/website/base.html
git checkout templates/website/home.html
```

## 📞 Помощь

Если нужна помощь с подбором цветов или настройкой:
1. Откройте caromoto.com
2. Сделайте скриншот
3. Пришлите скриншот или опишите, какие элементы нужно изменить

---

**Текущий дизайн уже профессиональный и современный!** 🎨
Осталось только заменить заглушки (телефоны, email) на реальные данные.


