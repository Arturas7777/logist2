# 🚀 Обновление проекта на ВАШЕМ VPS сервере

## 📋 Данные сервера

- **IP:** 176.118.198.78
- **SSH пользователь:** root
- **Директория проекта:** /var/www/www-root/data/www/logist2
- **База данных:** logist2_db (пользователь: arturas)

## 🔧 Метод 1: Автоматическое обновление

### Шаг 1: Создать архив локально

```powershell
# В директории проекта запустите:
python prepare_for_deploy.py
```

Будет создан архив `logist2_deploy_YYYYMMDD_HHMMSS.zip`

### Шаг 2: Загрузить на сервер

**Вариант А: Через WinSCP (рекомендуется для Windows)**

1. Откройте WinSCP
2. Подключитесь:
   - Host: `176.118.198.78`
   - User: `root`
   - Password: `lOaKcFF100O26nm3oC`
3. Загрузите `logist2_deploy_*.zip` в `/tmp/`

**Вариант Б: Через командную строку (если есть scp)**

```bash
scp logist2_deploy_*.zip root@176.118.198.78:/tmp/
# Пароль: lOaKcFF100O26nm3oC
```

### Шаг 3: Подключиться к серверу через SSH

**Через PuTTY:**
- Host: `176.118.198.78`
- User: `root`
- Password: `lOaKcFF100O26nm3oC`

### Шаг 4: Распаковать и обновить

```bash
# На сервере выполните:
cd /var/www/www-root/data/www/logist2

# Распаковать архив (заменит существующие файлы)
unzip -o /tmp/logist2_deploy_*.zip

# Сделать скрипт исполняемым
chmod +x update_server.sh

# Запустить обновление
./update_server.sh
```

Скрипт автоматически:
- ✅ Обновит зависимости
- ✅ Соберет статические файлы
- ✅ Применит миграции БД
- ✅ Перезапустит сервисы (gunicorn, daphne, nginx)

---

## 🔧 Метод 2: Ручное обновление (если нужен контроль)

### Шаг 1-3: Те же (загрузить и распаковать архив)

### Шаг 4: Вручную выполнить команды

```bash
cd /var/www/www-root/data/www/logist2

# Активировать виртуальное окружение
source .venv/bin/activate

# Обновить зависимости
pip install -r requirements.txt --upgrade

# Собрать статику
python manage.py collectstatic --noinput --clear

# Применить миграции
python manage.py migrate

# Перезапустить сервисы
systemctl restart gunicorn
systemctl restart daphne
systemctl restart nginx

# Проверить статус
systemctl status gunicorn
systemctl status daphne
systemctl status nginx
```

---

## 🔍 Проверка работоспособности

После обновления откройте в браузере:
```
http://176.118.198.78/admin
```

Войдите под своей учетной записью и проверьте:
- ✅ Новая система инвойсов работает
- ✅ Автоматическое создание позиций из автомобилей
- ✅ Хранение подтягивается правильно
- ✅ SVG стрелки отображаются
- ✅ Балансы клиентов обновляются

---

## 📊 Мониторинг логов

```bash
# Логи Gunicorn
tail -f /var/log/gunicorn/error.log

# Логи Daphne (WebSocket)
tail -f /var/log/daphne/error.log

# Логи Nginx
tail -f /var/log/nginx/error.log

# Логи Django через systemd
journalctl -u gunicorn -f
journalctl -u daphne -f
```

---

## 🆘 Если что-то пошло не так

### Сервисы не запускаются

```bash
# Проверить логи
journalctl -u gunicorn -n 50 --no-pager
journalctl -u daphne -n 50 --no-pager

# Попробовать запустить вручную для диагностики
cd /var/www/www-root/data/www/logist2
source .venv/bin/activate
gunicorn logist2.wsgi:application --bind 127.0.0.1:8000
```

### Ошибка подключения к БД

```bash
# Проверить подключение
sudo -u postgres psql -d logist2_db -U arturas

# Если нужно пересоздать пользователя:
sudo -u postgres psql
ALTER USER arturas WITH PASSWORD '7154032tut';
GRANT ALL PRIVILEGES ON DATABASE logist2_db TO arturas;
\q
```

### Статические файлы не загружаются

```bash
# Пересобрать статику
cd /var/www/www-root/data/www/logist2
source .venv/bin/activate
python manage.py collectstatic --clear --noinput

# Проверить права
ls -la staticfiles/
chown -R www-data:www-data staticfiles/
```

### Миграции не применяются

```bash
# Просмотреть статус миграций
python manage.py showmigrations

# Применить конкретную миграцию
python manage.py migrate core

# Создать фейковую миграцию (если уже была применена вручную)
python manage.py migrate --fake core 0072
```

---

## 🔄 Резервное копирование ПЕРЕД обновлением

**ВАЖНО! Рекомендуется сделать бэкап перед обновлением:**

```bash
# Бэкап базы данных
sudo -u postgres pg_dump logist2_db > /tmp/backup_$(date +%Y%m%d_%H%M%S).sql

# Бэкап файлов проекта (опционально)
cd /var/www/www-root/data/www/
tar -czf logist2_backup_$(date +%Y%m%d_%H%M%S).tar.gz logist2/
```

**Восстановление бэкапа (если нужно откатиться):**

```bash
# Восстановить БД
sudo -u postgres psql logist2_db < /tmp/backup_20250930_*.sql

# Восстановить файлы
cd /var/www/www-root/data/www/
tar -xzf logist2_backup_*.tar.gz
```

---

## ✅ Готово!

После успешного обновления ваш проект будет работать с:
- ✅ Новой системой инвойсов
- ✅ Автоматическим подсчетом хранения
- ✅ Красивыми SVG стрелками
- ✅ Всеми последними изменениями

**URL:** http://176.118.198.78/admin

Если возникнут проблемы - смотрите раздел "Если что-то пошло не так" выше! 🛠️

