# ✅ Чек-лист развертывания Caromoto Lithuania на VPS

## Перед началом убедитесь:
- [ ] У вас есть доступ к VPS серверу (SSH)
- [ ] Домен caromoto-lt.com направлен на IP сервера (A-запись в DNS)
- [ ] У вас есть все пароли и API ключи

---

## 🖥️ ЧАСТЬ 1: Подготовка сервера (один раз)

### 1. Подключитесь к серверу
```bash
ssh root@ваш-server-ip
```

### 2. Установите всё необходимое ПО
```bash
# Обновление системы
apt update && apt upgrade -y

# Установка Python, PostgreSQL, Nginx, Git
apt install -y python3 python3-pip python3-venv python3-dev \
    postgresql postgresql-contrib nginx git \
    build-essential libpq-dev gettext \
    certbot python3-certbot-nginx
```

### 3. Настройте PostgreSQL
```bash
sudo -u postgres psql
```

В PostgreSQL выполните:
```sql
CREATE DATABASE logist2_db;
CREATE USER logist2_user WITH PASSWORD 'ВАШ-СИЛЬНЫЙ-ПАРОЛЬ';
GRANT ALL PRIVILEGES ON DATABASE logist2_db TO logist2_user;
\q
```

### 4. Настройте Firewall
```bash
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw enable
```

---

## 📦 ЧАСТЬ 2: Развертывание проекта

### 5. Создайте директорию проекта
```bash
mkdir -p /var/www/caromoto-lt
cd /var/www/caromoto-lt
```

### 6. Загрузите файлы проекта

**Вариант A: Через Git**
```bash
git clone https://github.com/ваш-репозиторий/logist2.git .
```

**Вариант B: Через SCP с вашего компьютера**
```powershell
# На Windows (из папки проекта):
scp -r * root@ваш-server-ip:/var/www/caromoto-lt/
```

### 7. Создайте виртуальное окружение
```bash
python3 -m venv venv
source venv/bin/activate
```

### 8. Установите зависимости
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 9. Создайте .env файл
```bash
cp env.example .env
nano .env
```

**Заполните важные параметры:**
```env
SECRET_KEY=сгенерируйте-новый-ключ
DEBUG=False
ALLOWED_HOSTS=caromoto-lt.com,www.caromoto-lt.com,ваш-ip
DB_PASSWORD=ваш-пароль-от-postgresql
OPENAI_API_KEY=ваш-openai-ключ
```

**Генерация SECRET_KEY:**
```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

### 10. Примените миграции
```bash
python manage.py migrate
```

### 11. Создайте суперпользователя
```bash
python manage.py createsuperuser
```

### 12. Соберите статические файлы
```bash
python manage.py collectstatic --noinput
```

### 13. Скомпилируйте переводы
```bash
python manage.py compilemessages
```

---

## 🔧 ЧАСТЬ 3: Настройка веб-сервера

### 14. Настройте Nginx
```bash
# Копируем конфигурацию
cp nginx_caromoto.conf /etc/nginx/sites-available/caromoto-lt

# Создаем символическую ссылку
ln -s /etc/nginx/sites-available/caromoto-lt /etc/nginx/sites-enabled/

# Удаляем default конфигурацию
rm -f /etc/nginx/sites-enabled/default

# Проверяем конфигурацию
nginx -t
```

### 15. Получите SSL сертификат
```bash
# Остановите Nginx
systemctl stop nginx

# Получите сертификат
certbot certonly --standalone -d caromoto-lt.com -d www.caromoto-lt.com

# Запустите Nginx
systemctl start nginx
```

### 16. Настройте Gunicorn как systemd сервис
```bash
# Создаем директорию для логов
mkdir -p /var/log/gunicorn
chown -R www-data:www-data /var/log/gunicorn

# Копируем service файл
cp caromoto-lt.service /etc/systemd/system/

# Перезагружаем systemd
systemctl daemon-reload

# Включаем автозапуск
systemctl enable caromoto-lt

# Запускаем сервис
systemctl start caromoto-lt
```

### 17. Установите права доступа
```bash
chown -R www-data:www-data /var/www/caromoto-lt
chmod -R 755 /var/www/caromoto-lt
chmod -R 775 /var/www/caromoto-lt/media
```

### 18. Запустите Nginx
```bash
systemctl restart nginx
systemctl enable nginx
```

---

## ✅ ЧАСТЬ 4: Проверка

### 19. Проверьте статус сервисов
```bash
systemctl status caromoto-lt
systemctl status nginx
systemctl status postgresql
```

### 20. Откройте сайт в браузере
```
https://caromoto-lt.com
```

### 21. Проверьте админку
```
https://caromoto-lt.com/admin/
```

### 22. Проверьте логи
```bash
# Логи Django
journalctl -u caromoto-lt -n 50

# Логи Nginx
tail -50 /var/log/nginx/caromoto-lt-error.log
```

---

## 🔄 Обновление сайта

После внесения изменений в код:

```bash
cd /var/www/caromoto-lt
chmod +x deploy.sh
./deploy.sh
```

Или вручную:
```bash
cd /var/www/caromoto-lt
git pull
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py compilemessages
sudo systemctl restart caromoto-lt
```

---

## 🚨 В случае проблем

**Проверьте логи:**
```bash
journalctl -u caromoto-lt -f  # Django
tail -f /var/log/nginx/caromoto-lt-error.log  # Nginx
```

**Перезапустите сервисы:**
```bash
sudo systemctl restart caromoto-lt
sudo systemctl restart nginx
```

**Проверьте Django на ошибки:**
```bash
cd /var/www/caromoto-lt
source venv/bin/activate
python manage.py check --deploy
```

---

## 📞 Контакты для поддержки

Если что-то не работает:
1. Проверьте все шаги по порядку
2. Убедитесь что DNS записи настроены
3. Проверьте что firewall не блокирует порты 80/443
4. Посмотрите логи для диагностики

**Успехов с развертыванием! 🎉**

