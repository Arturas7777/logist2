# 🚀 Инструкция по развертыванию Logist2 на VPS

## 📋 Требования

- Ubuntu 20.04/22.04 или Debian 11/12
- Python 3.10+
- PostgreSQL 14+
- Nginx
- Root или sudo доступ

## 🔧 Шаг 1: Подготовка сервера

```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y

# Установка необходимых пакетов
sudo apt install -y python3-pip python3-venv python3-dev \
    postgresql postgresql-contrib nginx git \
    libpq-dev build-essential supervisor
```

## 🗄️ Шаг 2: Настройка PostgreSQL

```bash
# Войти в PostgreSQL
sudo -u postgres psql

# В консоли PostgreSQL выполнить:
CREATE DATABASE logist2_db;
CREATE USER logist2_user WITH PASSWORD 'your_secure_password';
ALTER ROLE logist2_user SET client_encoding TO 'utf8';
ALTER ROLE logist2_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE logist2_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE logist2_db TO logist2_user;
\q
```

## 📁 Шаг 3: Клонирование проекта

```bash
# Создать директорию
sudo mkdir -p /var/www/logist2
sudo chown $USER:$USER /var/www/logist2

# Клонировать репозиторий (или загрузить архив)
cd /var/www
# Вариант 1: Из git
git clone your-repo-url logist2

# Вариант 2: Загрузить архив и распаковать
# scp logist2.zip user@server:/var/www/
# unzip logist2.zip
```

## 🐍 Шаг 4: Настройка Python окружения

```bash
cd /var/www/logist2

# Создать виртуальное окружение
python3 -m venv venv

# Активировать
source venv/bin/activate

# Установить зависимости
pip install --upgrade pip
pip install -r requirements.txt
```

## ⚙️ Шаг 5: Настройка переменных окружения

```bash
# Скопировать пример и отредактировать
cp env.production.example .env

# Отредактировать .env
nano .env
```

Важные параметры в `.env`:
```env
SECRET_KEY=  # Сгенерировать: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
DEBUG=False
ALLOWED_HOSTS=ваш-домен.com,www.ваш-домен.com,IP-сервера

DB_NAME=logist2_db
DB_USER=logist2_user
DB_PASSWORD=ваш_пароль_от_БД
DB_HOST=localhost
DB_PORT=5432
```

## 🗃️ Шаг 6: Миграции и статика

```bash
source venv/bin/activate

# Применить миграции
python manage.py migrate

# Создать суперпользователя
python manage.py createsuperuser

# Собрать статические файлы
python manage.py collectstatic --noinput

# Создать директории для логов
sudo mkdir -p /var/log/logist2
sudo mkdir -p /var/run/logist2
sudo chown -R www-data:www-data /var/log/logist2
sudo chown -R www-data:www-data /var/run/logist2
```

## 🔧 Шаг 7: Настройка Gunicorn (systemd service)

```bash
# Скопировать service файл
sudo cp logist2.service /etc/systemd/system/

# Установить права
sudo chown -R www-data:www-data /var/www/logist2

# Перезагрузить systemd
sudo systemctl daemon-reload

# Запустить сервис
sudo systemctl start logist2

# Добавить в автозагрузку
sudo systemctl enable logist2

# Проверить статус
sudo systemctl status logist2
```

## 🌐 Шаг 8: Настройка Nginx

```bash
# Скопировать конфиг
sudo cp nginx_logist2.conf /etc/nginx/sites-available/logist2

# Отредактировать домен
sudo nano /etc/nginx/sites-available/logist2
# Замените your-domain.com на ваш реальный домен

# Создать симлинк
sudo ln -s /etc/nginx/sites-available/logist2 /etc/nginx/sites-enabled/

# Удалить дефолтный сайт (опционально)
sudo rm /etc/nginx/sites-enabled/default

# Проверить конфигурацию
sudo nginx -t

# Перезапустить Nginx
sudo systemctl restart nginx
```

## 🔐 Шаг 9: SSL сертификат (Let's Encrypt)

```bash
# Установить Certbot
sudo apt install certbot python3-certbot-nginx -y

# Получить сертификат
sudo certbot --nginx -d ваш-домен.com -d www.ваш-домен.com

# Автообновление сертификатов уже настроено через cron
```

## 🔄 Шаг 10: Скрипт для обновлений

```bash
# Сделать скрипт исполняемым
chmod +x deploy_vps.sh

# Для обновления в будущем просто запускайте:
./deploy_vps.sh
```

## ✅ Проверка работоспособности

1. Откройте `https://ваш-домен.com/admin`
2. Войдите под суперпользователем
3. Проверьте работу всех функций

## 📊 Мониторинг логов

```bash
# Логи Django/Gunicorn
sudo tail -f /var/log/logist2/gunicorn-error.log
sudo tail -f /var/log/logist2/gunicorn-access.log

# Логи Nginx
sudo tail -f /var/log/nginx/logist2-error.log

# Логи systemd
sudo journalctl -u logist2 -f
```

## 🔥 Firewall (опционально, но рекомендуется)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

## 🆘 Troubleshooting

### Ошибка подключения к БД
```bash
# Проверить, что PostgreSQL запущен
sudo systemctl status postgresql

# Проверить подключение
sudo -u postgres psql -d logist2_db
```

### Ошибка с правами доступа
```bash
# Установить правильные права
sudo chown -R www-data:www-data /var/www/logist2
sudo chmod -R 755 /var/www/logist2
```

### Gunicorn не запускается
```bash
# Проверить логи
sudo journalctl -u logist2 -n 50 --no-pager

# Попробовать запустить вручную
cd /var/www/logist2
source venv/bin/activate
gunicorn --config gunicorn_config.py logist2.wsgi:application
```

### Статические файлы не загружаются
```bash
# Пересобрать статику
cd /var/www/logist2
source venv/bin/activate
python manage.py collectstatic --clear --noinput

# Проверить права
sudo chown -R www-data:www-data /var/www/logist2/staticfiles
```

## 📈 Производительность

Для улучшения производительности:

1. **Настройте PostgreSQL** (увеличьте `shared_buffers`, `effective_cache_size`)
2. **Включите gzip в Nginx** (уже в конфиге)
3. **Используйте Redis для кэширования** (опционально)
4. **Настройте мониторинг** (Prometheus + Grafana)

## 🔄 Резервное копирование

```bash
# Бэкап базы данных
sudo -u postgres pg_dump logist2_db > backup_$(date +%Y%m%d).sql

# Бэкап медиафайлов
tar -czf media_backup_$(date +%Y%m%d).tar.gz /var/www/logist2/media/

# Автоматизация через cron
# crontab -e
# 0 2 * * * /path/to/backup_script.sh
```

## 📞 Поддержка

При возникновении проблем проверьте:
1. Логи приложения
2. Логи Nginx
3. Статус сервисов: `sudo systemctl status logist2 nginx postgresql`

---

**Готово!** 🎉 Ваше приложение теперь работает на сервере!

