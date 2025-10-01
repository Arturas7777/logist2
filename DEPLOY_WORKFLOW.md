# 🚀 Рабочий процесс деплоя Logist2 на VPS

## 📋 Общая схема

```
Локальная разработка → Git Push → Сервер Git Pull → Автодеплой
```

---

## 🔄 Процесс обновления проекта

### **Вариант 1: Обновление КОДА (рекомендуется)**

Используйте этот метод для:
- Изменений в коде Python (views, models, admin, etc.)
- Новых миграций
- Изменений в шаблонах HTML
- Обновлений CSS/JS
- Изменений в зависимостях (requirements.txt)

#### **На локальной машине:**

```bash
# 1. Внесите изменения в код
# 2. Сохраните все файлы

# 3. Закоммитьте изменения
git add -A
git commit -m "Описание изменений"
git push origin master
```

#### **На сервере VPS:**

```bash
# Подключитесь по SSH
ssh root@176.118.198.78

# Перейдите в папку проекта
cd /var/www/www-root/data/www/logist2

# Запустите автодеплой
./auto_deploy.sh
```

**Готово! Изменения применены!** ✅

---

### **Вариант 2: Обновление ДАННЫХ**

Используйте этот метод для переноса данных между локальной БД и сервером.

#### **Экспорт данных (локально):**

```bash
# Все данные
python manage.py dumpdata --exclude auth.permission --exclude contenttypes --exclude sessions --indent 2 > full_data.json

# Конкретные модели
python manage.py dumpdata core.client core.car --indent 2 > specific_data.json
```

#### **Импорт данных (на сервере):**

```bash
# Загрузите файл через WinSCP в /var/www/www-root/data/tmp/

# Активируйте окружение
cd /var/www/www-root/data/www/logist2
source .venv/bin/activate

# Загрузите данные
python manage.py loaddata /var/www/www-root/data/tmp/full_data.json

# Перезапустите
systemctl restart gunicorn
```

---

### **Вариант 3: Ручная загрузка через WinSCP (для быстрых правок)**

Для мелких изменений в CSS/JS/шаблонах:

1. Откройте WinSCP
2. Подключитесь к серверу (176.118.198.78, root)
3. Замените файл в `/var/www/www-root/data/www/logist2/`
4. Выполните на сервере:
   ```bash
   cd /var/www/www-root/data/www/logist2
   source .venv/bin/activate
   python manage.py collectstatic --no-input
   systemctl restart gunicorn
   ```

---

## 🛠️ Полезные команды

### **Проверка статуса сервисов:**

```bash
systemctl status gunicorn
systemctl status daphne
systemctl status nginx
```

### **Просмотр логов:**

```bash
journalctl -u gunicorn -n 50 --no-pager
journalctl -u daphne -n 50 --no-pager
tail -f /var/log/nginx/error.log
```

### **Бэкап БД:**

```bash
pg_dump -U arturas logist2_db > /var/www/www-root/data/tmp/backup_$(date +%Y%m%d_%H%M%S).sql
```

### **Восстановление БД из бэкапа:**

```bash
psql -U arturas -d logist2_db -f /var/www/www-root/data/tmp/backup_20250930_123456.sql
```

---

## 📊 Сравнение данных локально vs сервер

```bash
# Локально
python manage.py shell -c "from django.apps import apps; models = [m for m in apps.get_app_config('core').get_models()]; print('\n'.join([f'{m.__name__}: {m.objects.count()}' for m in models]))"

# На сервере (после подключения SSH)
cd /var/www/www-root/data/www/logist2
source .venv/bin/activate
python manage.py shell -c "from django.apps import apps; models = [m for m in apps.get_app_config('core').get_models()]; print('\n'.join([f'{m.__name__}: {m.objects.count()}' for m in models]))"
```

---

## 🔒 Важные учетные данные

### **SSH:**
- Хост: `176.118.198.78`
- Пользователь: `root`
- Пароль: `lOaKcFF100O26nm3oC`

### **PostgreSQL:**
- Хост: `localhost`
- База: `logist2_db`
- Пользователь: `arturas`
- Пароль: `7154032tut`

### **Пути на сервере:**
- Проект: `/var/www/www-root/data/www/logist2`
- Виртуальное окружение: `/var/www/www-root/data/www/logist2/.venv`
- Временные файлы: `/var/www/www-root/data/tmp`

---

## 🎯 Типичные сценарии

### **Сценарий 1: Изменили CSS/JS**
```bash
# Локально
git add core/static/
git commit -m "Updated invoice admin CSS"
git push

# На сервере
./auto_deploy.sh
```

### **Сценарий 2: Добавили новое поле в модель**
```bash
# Локально
# 1. Изменили models.py
# 2. Создали миграцию
python manage.py makemigrations
git add -A
git commit -m "Added new field to Car model"
git push

# На сервере
./auto_deploy.sh  # автоматически применит миграции
```

### **Сценарий 3: Нужно перенести данные**
```bash
# Локально
python manage.py dumpdata core.client --indent 2 > new_clients.json
# Загрузите через WinSCP

# На сервере
python manage.py loaddata /var/www/www-root/data/tmp/new_clients.json
systemctl restart gunicorn
```

---

## ⚠️ Важные замечания

1. **Всегда делайте бэкап перед деплоем** - скрипт `auto_deploy.sh` делает это автоматически
2. **Проверяйте логи** после деплоя: `journalctl -u gunicorn -n 20`
3. **Тестируйте локально** перед пушем в Git
4. **Не редактируйте файлы напрямую на сервере** - используйте Git workflow

---

## 📦 Структура проекта на сервере

```
/var/www/www-root/data/www/logist2/
├── core/                    # Приложение Django
├── logist2/                 # Настройки проекта
├── templates/               # HTML шаблоны
├── staticfiles/             # Собранная статика
├── .venv/                   # Виртуальное окружение
├── .env                     # Переменные окружения (НЕ в Git!)
├── manage.py               # Django управление
├── requirements.txt        # Зависимости
├── auto_deploy.sh          # Скрипт автодеплоя
└── gunicorn.sock           # Unix сокет для Gunicorn
```

---

## 🎓 Git команды для новичков

```bash
# Посмотреть статус
git status

# Добавить все изменения
git add -A

# Создать коммит
git commit -m "Описание изменений"

# Отправить на сервер
git push origin master

# Посмотреть историю
git log --oneline -10

# Отменить последний коммит (локально)
git reset --soft HEAD~1
```

---

## 🆘 Решение проблем

### **Проблема: "502 Bad Gateway"**
```bash
systemctl status gunicorn
journalctl -u gunicorn -n 50
# Смотрите ошибки и исправляйте
systemctl restart gunicorn
```

### **Проблема: "Static files not found"**
```bash
python manage.py collectstatic --no-input --clear
systemctl restart nginx
```

### **Проблема: "Migration conflict"**
```bash
# Пометить миграцию как выполненную
python manage.py migrate core 0XXX --fake
python manage.py migrate
```

### **Проблема: "Permission denied"**
```bash
# Исправить права доступа
chown -R www-data:www-data /var/www/www-root/data/www/logist2
chmod -R 755 /var/www/www-root/data/www/logist2
```

---

## 📞 Поддержка

При возникновении проблем:
1. Проверьте логи: `journalctl -u gunicorn -n 50`
2. Проверьте статус: `systemctl status gunicorn`
3. Откатите к бэкапу при необходимости
4. Обратитесь к этой документации

---

**Дата создания:** 30 сентября 2025  
**Версия:** 1.0


