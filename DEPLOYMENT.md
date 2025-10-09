# 🚀 Инструкция по развертыванию Caromoto Lithuania на VPS

## 📋 Требования к серверу

- **ОС**: Ubuntu 20.04 LTS или новее
- **RAM**: Минимум 2GB (рекомендуется 4GB+)
- **CPU**: Минимум 2 ядра
- **Диск**: Минимум 20GB свободного места
- **Домен**: caromoto-lt.com (с настроенными DNS записями на IP сервера)

## 🔧 Шаг 1: Подготовка сервера

### 1.1 Подключение к серверу
```bash
ssh root@your-server-ip
```

### 1.2 Обновление системы
```bash
apt update && apt upgrade -y
```

### 1.3 Установка необходимого ПО
```bash
# Python и инструменты разработки
apt install -y python3 python3-pip python3-venv python3-dev

# PostgreSQL
apt install -y postgresql postgresql-contrib

# Nginx
apt install -y nginx

# Git
apt install -y git

# Дополнительные пакеты
apt install -y build-essential libpq-dev gettext

# Certbot для SSL сертификатов
apt install -y certbot python3-certbot-nginx
```

## 🗄️ Шаг 2: Настройка PostgreSQL

### 2.1 Создание базы данных и пользователя
```bash
sudo -u postgres psql

# В psql выполните:
CREATE DATABASE logist2_db;
CREATE USER logist2_user WITH PASSWORD 'ваш-сильный-пароль';
ALTER ROLE logist2_user SET client_encoding TO 'utf8';
ALTER ROLE logist2_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE logist2_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE logist2_db TO logist2_user;
\q
```

### 2.2 Настройка PostgreSQL для внешних подключений (опционально)
```bash
# Редактируем pg_hba.conf
nano /etc/postgresql/14/main/pg_hba.conf

# Добавьте строку (для локальных подключений):
# local   all             logist2_user                            md5
```

## 📂 Шаг 3: Развертывание проекта

### 3.1 Создание директории проекта
```bash
mkdir -p /var/www/caromoto-lt
cd /var/www/caromoto-lt
```

### 3.2 Клонирование репозитория
```bash
# Если у вас есть Git репозиторий:
git clone your-git-repo-url .

# Или загрузите файлы через SCP/SFTP
```

### 3.3 Создание виртуального окружения
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3.4 Установка зависимостей
```bash
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn  # Если не включен в requirements.txt
```

### 3.5 Создание .env файла
```bash
cp env.example .env
nano .env
```

**Заполните .env следующими данными:**
```env
SECRET_KEY=ваш-секретный-ключ-сгенерируйте-новый
DEBUG=False
ALLOWED_HOSTS=caromoto-lt.com,www.caromoto-lt.com,ваш-server-ip

DB_NAME=logist2_db
DB_USER=logist2_user
DB_PASSWORD=ваш-пароль-от-postgresql
DB_HOST=localhost
DB_PORT=5432

CSRF_TRUSTED_ORIGINS=https://caromoto-lt.com,https://www.caromoto-lt.com
OPENAI_API_KEY=ваш-openai-api-key
```

**Для генерации SECRET_KEY:**
```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

### 3.6 Применение миграций
```bash
python manage.py migrate
```

### 3.7 Создание суперпользователя
```bash
python manage.py createsuperuser
```

### 3.8 Сбор статических файлов
```bash
python manage.py collectstatic --noinput
```

### 3.9 Компиляция переводов
```bash
python manage.py compilemessages
```

### 3.10 Создание директорий для логов
```bash
mkdir -p /var/log/gunicorn
chown -R www-data:www-data /var/log/gunicorn
```

### 3.11 Установка прав доступа
```bash
chown -R www-data:www-data /var/www/caromoto-lt
chmod -R 755 /var/www/caromoto-lt
chmod -R 775 /var/www/caromoto-lt/media
chmod -R 775 /var/www/caromoto-lt/staticfiles
```

## 🔧 Шаг 4: Настройка Nginx

### 4.1 Копирование конфигурации
```bash
cp nginx_caromoto.conf /etc/nginx/sites-available/caromoto-lt
ln -s /etc/nginx/sites-available/caromoto-lt /etc/nginx/sites-enabled/
```

### 4.2 Проверка конфигурации
```bash
nginx -t
```

### 4.3 Удаление default конфигурации (если нужно)
```bash
rm /etc/nginx/sites-enabled/default
```

## 🔐 Шаг 5: Настройка SSL (HTTPS)

### 5.1 Получение SSL сертификата от Let's Encrypt
```bash
# Временно остановите Nginx
systemctl stop nginx

# Получите сертификат
certbot certonly --standalone -d caromoto-lt.com -d www.caromoto-lt.com

# Запустите Nginx
systemctl start nginx
```

### 5.2 Автоматическое обновление сертификата
```bash
# Настройте cron для автообновления
certbot renew --dry-run

# Если всё ОК, добавьте в crontab:
crontab -e

# Добавьте строку:
0 3 * * * certbot renew --quiet --post-hook "systemctl reload nginx"
```

## ⚙️ Шаг 6: Настройка systemd

### 6.1 Копирование service файла
```bash
cp caromoto-lt.service /etc/systemd/system/
```

### 6.2 Перезагрузка systemd и запуск сервиса
```bash
systemctl daemon-reload
systemctl enable caromoto-lt
systemctl start caromoto-lt
```

### 6.3 Проверка статуса
```bash
systemctl status caromoto-lt
```

## 🌐 Шаг 7: Запуск Nginx
```bash
systemctl restart nginx
systemctl enable nginx
systemctl status nginx
```

## ✅ Шаг 8: Проверка работоспособности

### 8.1 Проверка сайта
Откройте в браузере: `https://caromoto-lt.com`

### 8.2 Проверка логов
```bash
# Логи приложения
journalctl -u caromoto-lt -f

# Логи Nginx
tail -f /var/log/nginx/caromoto-lt-error.log
tail -f /var/log/nginx/caromoto-lt-access.log

# Логи Gunicorn
tail -f /var/log/gunicorn/caromoto-lt-error.log
tail -f /var/log/gunicorn/caromoto-lt-access.log
```

### 8.3 Проверка админки
Откройте: `https://caromoto-lt.com/admin/`

## 🔄 Обновление сайта (после внесения изменений)

### Вариант 1: Использование скрипта deploy.sh
```bash
cd /var/www/caromoto-lt
chmod +x deploy.sh
./deploy.sh
```

### Вариант 2: Вручную
```bash
cd /var/www/caromoto-lt
git pull origin master
source venv/bin/activate
pip install -r requirements.txt --upgrade
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py compilemessages
sudo systemctl restart caromoto-lt
sudo systemctl reload nginx
```

## 🛠️ Полезные команды

### Перезапуск сервисов
```bash
# Перезапуск Django приложения
sudo systemctl restart caromoto-lt

# Перезагрузка Nginx (без простоя)
sudo systemctl reload nginx

# Полный перезапуск Nginx
sudo systemctl restart nginx
```

### Просмотр логов
```bash
# Последние 100 строк логов приложения
journalctl -u caromoto-lt -n 100 --no-pager

# Следить за логами в реальном времени
journalctl -u caromoto-lt -f

# Логи Nginx
tail -f /var/log/nginx/caromoto-lt-error.log
```

### Управление базой данных
```bash
# Подключение к PostgreSQL
sudo -u postgres psql logist2_db

# Создание бэкапа базы данных
pg_dump -U logist2_user -d logist2_db > backup_$(date +%Y%m%d_%H%M%S).sql

# Восстановление из бэкапа
psql -U logist2_user -d logist2_db < backup.sql
```

### Django management команды
```bash
cd /var/www/caromoto-lt
source venv/bin/activate

# Создание суперпользователя
python manage.py createsuperuser

# Очистка старых сессий
python manage.py clearsessions

# Проверка проекта на ошибки
python manage.py check --deploy
```

## 🔒 Безопасность

### Firewall (UFW)
```bash
# Разрешить SSH, HTTP и HTTPS
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw enable
ufw status
```

### Автоматические обновления безопасности
```bash
apt install unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades
```

## 📊 Мониторинг

### Проверка использования ресурсов
```bash
# Использование диска
df -h

# Использование памяти
free -h

# Процессы
htop  # или top

# Размер медиа файлов
du -sh /var/www/caromoto-lt/media/
```

### Ротация логов
Создайте `/etc/logrotate.d/caromoto-lt`:
```
/var/log/gunicorn/caromoto-lt-*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 www-data www-data
    sharedscripts
    postrotate
        systemctl reload caromoto-lt > /dev/null 2>&1 || true
    endscript
}
```

## 🚨 Решение проблем

### Сайт не загружается
1. Проверьте статус сервисов:
   ```bash
   systemctl status caromoto-lt
   systemctl status nginx
   ```

2. Проверьте логи ошибок:
   ```bash
   journalctl -u caromoto-lt -n 50
   tail -50 /var/log/nginx/caromoto-lt-error.log
   ```

3. Проверьте настройки Django:
   ```bash
   cd /var/www/caromoto-lt
   source venv/bin/activate
   python manage.py check --deploy
   ```

### Ошибка 502 Bad Gateway
- Проверьте, запущен ли Gunicorn: `systemctl status caromoto-lt`
- Проверьте логи Gunicorn: `journalctl -u caromoto-lt -f`

### Статические файлы не загружаются
```bash
cd /var/www/caromoto-lt
source venv/bin/activate
python manage.py collectstatic --noinput --clear
chown -R www-data:www-data /var/www/caromoto-lt/staticfiles
```

### Проблемы с базой данных
```bash
# Проверка подключения к БД
sudo -u postgres psql -c "SELECT version();"

# Проверка существования базы
sudo -u postgres psql -l | grep logist2
```

## 📝 Дополнительные настройки

### Настройка времени сервера
```bash
timedatectl set-timezone Europe/Vilnius
```

### Увеличение лимитов для загрузки файлов
Если нужно загружать большие архивы с фото, отредактируйте:

**PostgreSQL** (`/etc/postgresql/14/main/postgresql.conf`):
```
max_connections = 100
shared_buffers = 256MB
```

**Nginx** (уже настроено в nginx_caromoto.conf):
```
client_max_body_size 500M;
```

## 🔄 Автоматизация

### Git Hooks для автоматического деплоя
Создайте `/var/www/caromoto-lt/.git/hooks/post-receive`:
```bash
#!/bin/bash
cd /var/www/caromoto-lt
./deploy.sh
```

```bash
chmod +x /var/www/caromoto-lt/.git/hooks/post-receive
```

## 📞 Поддержка

Если возникнут проблемы:
1. Проверьте все логи
2. Убедитесь, что все сервисы запущены
3. Проверьте настройки firewall
4. Проверьте DNS записи домена

---

## 🎯 Быстрый чеклист развертывания

- [ ] Обновить сервер и установить ПО
- [ ] Настроить PostgreSQL
- [ ] Склонировать проект в `/var/www/caromoto-lt`
- [ ] Создать виртуальное окружение и установить зависимости
- [ ] Настроить `.env` файл
- [ ] Применить миграции и создать суперпользователя
- [ ] Собрать статические файлы
- [ ] Настроить Nginx
- [ ] Получить SSL сертификат
- [ ] Настроить systemd service
- [ ] Запустить все сервисы
- [ ] Проверить работу сайта
- [ ] Настроить firewall
- [ ] Настроить автоматические бэкапы

Удачи! 🚀

