# 🔒 ПОЛНОЕ РУКОВОДСТВО ПО БЕЗОПАСНОСТИ СЕРВЕРА

## ✅ ЧТО НАСТРОИМ:

1. ✅ SSH - защищенный доступ
2. ✅ Firewall (UFW) - блокировка ненужных портов
3. ✅ HTTPS/SSL - шифрование трафика
4. ✅ Django Security - защита приложения
5. ✅ PostgreSQL - безопасность БД
6. ✅ Fail2Ban - защита от брутфорса
7. ✅ Автоматические обновления
8. ✅ Регулярные бэкапы
9. ✅ Мониторинг

---

## 🛡️ 1. НАСТРОЙКА SSH (КРИТИЧЕСКИ ВАЖНО!)

### Текущая проблема:
❌ Вход по паролю от root - **ОЧЕНЬ ОПАСНО!**

### Решение:

```bash
# На сервере
ssh root@176.118.198.78

# 1.1 Создаем нового пользователя (не root!)
adduser arturas
usermod -aG sudo arturas

# 1.2 Настраиваем SSH ключи (на локальной машине - Windows)
# В PowerShell:
ssh-keygen -t ed25519 -C "arturas@logist2"
# Нажмите Enter 3 раза (сохранить в стандартное место, без пароля или с паролем на выбор)

# 1.3 Копируем ключ на сервер (из PowerShell)
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh root@176.118.198.78 "mkdir -p /home/arturas/.ssh && cat >> /home/arturas/.ssh/authorized_keys && chown -R arturas:arturas /home/arturas/.ssh && chmod 700 /home/arturas/.ssh && chmod 600 /home/arturas/.ssh/authorized_keys"

# 1.4 ПРОВЕРЬТЕ что можете войти БЕЗ ПАРОЛЯ:
ssh arturas@176.118.198.78
# Если работает - продолжаем!

# 1.5 Отключаем вход по паролю и root доступ
sudo nano /etc/ssh/sshd_config

# Измените:
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
Port 22  # Можно изменить на нестандартный (например 2222) для дополнительной защиты

# Сохраните (Ctrl+O, Enter, Ctrl+X)

# 1.6 Перезапускаем SSH
sudo systemctl restart sshd

# ВАЖНО: НЕ ЗАКРЫВАЙТЕ текущую SSH сессию, откройте новую для проверки!
# В новом окне PowerShell:
ssh arturas@176.118.198.78
```

---

## 🔥 2. НАСТРОЙКА FIREWALL (UFW)

```bash
ssh arturas@176.118.198.78

# 2.1 Установка UFW (если нет)
sudo apt install ufw -y

# 2.2 Настройка правил
sudo ufw default deny incoming   # Блокируем все входящие
sudo ufw default allow outgoing  # Разрешаем все исходящие

# 2.3 Открываем только нужные порты
sudo ufw allow 22/tcp           # SSH (или ваш custom port)
sudo ufw allow 80/tcp           # HTTP
sudo ufw allow 443/tcp          # HTTPS
sudo ufw allow 1500/tcp         # ISPmanager (если нужен)

# 2.4 Включаем firewall
sudo ufw enable

# 2.5 Проверяем статус
sudo ufw status verbose
```

**Результат:**
```
Status: active

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW       Anywhere
80/tcp                     ALLOW       Anywhere
443/tcp                    ALLOW       Anywhere
1500/tcp                   ALLOW       Anywhere
```

---

## 🔐 3. НАСТРОЙКА HTTPS/SSL (Let's Encrypt)

### БЕЗ этого ваши данные передаются ОТКРЫТЫМ ТЕКСТОМ!

```bash
ssh arturas@176.118.198.78

# 3.1 Установка Certbot
sudo apt update
sudo apt install certbot python3-certbot-nginx -y

# 3.2 Регистрация домена (если есть)
# Если у вас есть домен (например logist.example.com):
sudo certbot --nginx -d logist.example.com

# Если НЕТ домена (используете IP):
# К сожалению, Let's Encrypt не работает с IP адресами
# Нужно либо купить домен, либо использовать самоподписанный сертификат
```

### Если НЕТ домена - самоподписанный сертификат:

```bash
# Создаем самоподписанный сертификат
sudo mkdir -p /etc/nginx/ssl
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/logist2.key \
  -out /etc/nginx/ssl/logist2.crt \
  -subj "/C=LT/ST=Vilnius/L=Vilnius/O=Caromoto/CN=176.118.198.78"

# Обновляем Nginx конфиг
sudo nano /etc/nginx/sites-available/logist2
```

Добавьте в конфиг:
```nginx
server {
    listen 443 ssl http2;
    server_name 176.118.198.78;

    ssl_certificate /etc/nginx/ssl/logist2.crt;
    ssl_certificate_key /etc/nginx/ssl/logist2.key;
    
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # ... остальная конфигурация ...
}

# Редирект с HTTP на HTTPS
server {
    listen 80;
    server_name 176.118.198.78;
    return 301 https://$server_name$request_uri;
}
```

```bash
# Тестируем и перезапускаем Nginx
sudo nginx -t
sudo systemctl reload nginx
```

---

## 🛡️ 4. DJANGO SECURITY SETTINGS

Обновите ваш `settings_prod.py`:

```python
# SECURITY
DEBUG = False  # ✅ УЖЕ ЕСТЬ

# HTTPS
SECURE_SSL_REDIRECT = True  # Редирект на HTTPS
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# HSTS (HTTP Strict Transport Security)
SECURE_HSTS_SECONDS = 31536000  # 1 год
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Дополнительная защита
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'

# Админка - меняем URL (необязательно, но рекомендуется)
# В urls.py:
# path('admin/', admin.site.urls),  # Старый
# path('secret-admin-panel-xyz/', admin.site.urls),  # Новый

# Ограничение размера загружаемых файлов
DATA_UPLOAD_MAX_MEMORY_SIZE = 5242880  # 5 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 5242880  # 5 MB

# Защита от Host header attacks
ALLOWED_HOSTS = ['176.118.198.78']  # ✅ УЖЕ ЕСТЬ
# Если появится домен, добавьте: ['176.118.198.78', 'logist.yourdomain.com']
```

---

## 🗄️ 5. POSTGRESQL SECURITY

```bash
ssh arturas@176.118.198.78

# 5.1 Настраиваем pg_hba.conf
sudo nano /etc/postgresql/*/main/pg_hba.conf

# Убедитесь что есть ТОЛЬКО:
# local   all             postgres                                peer
# local   all             arturas                                 md5
# host    all             arturas         127.0.0.1/32            md5

# НЕ ДОЛЖНО БЫТЬ:
# host    all             all             0.0.0.0/0               md5  # ОПАСНО!

# 5.2 Убедитесь что PostgreSQL слушает только localhost
sudo nano /etc/postgresql/*/main/postgresql.conf

# Должно быть:
listen_addresses = 'localhost'

# 5.3 Перезапускаем PostgreSQL
sudo systemctl restart postgresql

# 5.4 Регулярные бэкапы (настроим далее)
```

---

## 🚫 6. FAIL2BAN - ЗАЩИТА ОТ БРУТФОРСА

```bash
ssh arturas@176.118.198.78

# 6.1 Установка
sudo apt install fail2ban -y

# 6.2 Создаем конфигурацию
sudo nano /etc/fail2ban/jail.local
```

Вставьте:
```ini
[DEFAULT]
bantime = 3600        # Бан на 1 час
findtime = 600        # За 10 минут
maxretry = 5          # 5 неудачных попыток

[sshd]
enabled = true
port = 22
logpath = /var/log/auth.log

[nginx-http-auth]
enabled = true
port = http,https
logpath = /var/log/nginx/error.log

[nginx-noscript]
enabled = true
port = http,https
logpath = /var/log/nginx/access.log

[nginx-badbots]
enabled = true
port = http,https
logpath = /var/log/nginx/access.log

[nginx-noproxy]
enabled = true
port = http,https
logpath = /var/log/nginx/access.log
```

```bash
# 6.3 Запускаем Fail2Ban
sudo systemctl enable fail2ban
sudo systemctl start fail2ban

# 6.4 Проверяем статус
sudo fail2ban-client status
sudo fail2ban-client status sshd
```

---

## 🔄 7. АВТОМАТИЧЕСКИЕ ОБНОВЛЕНИЯ БЕЗОПАСНОСТИ

```bash
ssh arturas@176.118.198.78

# 7.1 Установка unattended-upgrades
sudo apt install unattended-upgrades -y

# 7.2 Настройка
sudo dpkg-reconfigure --priority=low unattended-upgrades
# Выберите "Yes"

# 7.3 Проверка конфигурации
sudo nano /etc/apt/apt.conf.d/50unattended-upgrades

# Убедитесь что включено:
Unattended-Upgrade::Automatic-Reboot "false";  # Не перезагружать автоматически
Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";

# 7.4 Включаем автообновления
sudo systemctl enable unattended-upgrades
sudo systemctl start unattended-upgrades
```

---

## 💾 8. АВТОМАТИЧЕСКИЕ БЭКАПЫ

Создайте скрипт бэкапа:

```bash
ssh arturas@176.118.198.78

sudo nano /usr/local/bin/backup_logist2.sh
```

Вставьте:
```bash
#!/bin/bash

# Конфигурация
BACKUP_DIR="/var/backups/logist2"
DB_NAME="logist2_db"
DB_USER="arturas"
RETENTION_DAYS=30  # Хранить 30 дней

# Создаем директорию если нет
mkdir -p $BACKUP_DIR

# Имя файла с датой
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/backup_$TIMESTAMP.sql"

# Создаем бэкап
pg_dump -U $DB_USER $DB_NAME | gzip > "$BACKUP_FILE.gz"

# Проверяем успешность
if [ $? -eq 0 ]; then
    echo "$(date): Бэкап успешно создан: $BACKUP_FILE.gz" >> /var/log/logist2_backup.log
    
    # Удаляем старые бэкапы
    find $BACKUP_DIR -name "backup_*.sql.gz" -mtime +$RETENTION_DAYS -delete
else
    echo "$(date): ОШИБКА создания бэкапа!" >> /var/log/logist2_backup.log
fi
```

```bash
# Делаем исполняемым
sudo chmod +x /usr/local/bin/backup_logist2.sh

# Настраиваем cron (каждый день в 3:00)
sudo crontab -e

# Добавьте строку:
0 3 * * * /usr/local/bin/backup_logist2.sh

# Проверка работы (запустите вручную)
sudo /usr/local/bin/backup_logist2.sh
ls -lh /var/backups/logist2/
```

---

## 📊 9. МОНИТОРИНГ И ЛОГИРОВАНИЕ

```bash
ssh arturas@176.118.198.78

# 9.1 Проверка логов Django
sudo tail -f /var/log/gunicorn/error.log
sudo tail -f /var/log/daphne/error.log

# 9.2 Проверка логов Nginx
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/nginx/access.log

# 9.3 Проверка логов системы
sudo tail -f /var/log/auth.log  # SSH попытки входа
sudo tail -f /var/log/syslog    # Системные события

# 9.4 Установка monitoring tools
sudo apt install htop iotop nethogs -y

# htop - мониторинг процессов
# iotop - мониторинг дисковых операций
# nethogs - мониторинг сетевой активности
```

### Простой скрипт мониторинга:

```bash
sudo nano /usr/local/bin/check_logist2.sh
```

```bash
#!/bin/bash

echo "=== Статус Logist2 ==="
echo ""

# Проверяем сервисы
echo "Gunicorn: $(systemctl is-active gunicorn)"
echo "Daphne: $(systemctl is-active daphne)"
echo "Nginx: $(systemctl is-active nginx)"
echo "PostgreSQL: $(systemctl is-active postgresql)"
echo ""

# Проверяем диск
echo "Диск:"
df -h / | tail -1
echo ""

# Проверяем память
echo "Память:"
free -h | grep Mem
echo ""

# Последние ошибки
echo "Последние ошибки Nginx:"
sudo tail -5 /var/log/nginx/error.log
```

```bash
sudo chmod +x /usr/local/bin/check_logist2.sh

# Запуск
sudo /usr/local/bin/check_logist2.sh
```

---

## ✅ 10. ФИНАЛЬНАЯ ПРОВЕРКА БЕЗОПАСНОСТИ

```bash
ssh arturas@176.118.198.78

# 10.1 Проверяем открытые порты
sudo netstat -tulpn | grep LISTEN

# Должны видеть ТОЛЬКО:
# :22 (SSH)
# :80 (HTTP)
# :443 (HTTPS)
# 127.0.0.1:8000 (Gunicorn - локально)
# 127.0.0.1:8001 (Daphne - локально)
# 127.0.0.1:5432 (PostgreSQL - локально)

# 10.2 Проверяем UFW
sudo ufw status

# 10.3 Проверяем Fail2Ban
sudo fail2ban-client status

# 10.4 Проверяем обновления
sudo apt update
sudo apt list --upgradable

# 10.5 Запускаем тест безопасности Django
cd /var/www/www-root/data/www/logist2
source .venv/bin/activate
python manage.py check --deploy

# Должны быть ВСЕ ОК! Если есть предупреждения - исправьте их.
```

---

## 📋 ЧЕКЛИСТ БЕЗОПАСНОСТИ

```
✅ SSH ключи настроены, доступ по паролю отключен
✅ Root доступ по SSH отключен
✅ Firewall (UFW) настроен и активен
✅ HTTPS/SSL настроен
✅ Django security settings применены
✅ PostgreSQL слушает только localhost
✅ Fail2Ban установлен и работает
✅ Автоматические обновления безопасности включены
✅ Автоматические бэкапы настроены
✅ Мониторинг логов настроен
✅ python manage.py check --deploy пройден без ошибок
```

---

## 🚨 ЧТО ДЕЛАТЬ В СЛУЧАЕ АТАКИ

```bash
# 1. Проверьте логи
sudo tail -100 /var/log/auth.log
sudo tail -100 /var/log/nginx/access.log

# 2. Проверьте забаненные IP
sudo fail2ban-client status sshd

# 3. Заблокируйте IP вручную (если нужно)
sudo ufw deny from 123.45.67.89

# 4. Проверьте активные соединения
sudo netstat -antp | grep ESTABLISHED

# 5. В крайнем случае - смените SSH порт и пароли БД
```

---

## 🎯 ДОПОЛНИТЕЛЬНЫЕ РЕКОМЕНДАЦИИ

1. **Купите домен** (от $5/год) для полноценного HTTPS с Let's Encrypt
2. **Настройте email уведомления** для критических событий
3. **Используйте 2FA** для ISPmanager
4. **Регулярно проверяйте логи** (хотя бы раз в неделю)
5. **Храните бэкапы в разных местах** (сервер + локально + облако)
6. **Обновляйте Django** при выходе security патчей
7. **Используйте strong пароли** (минимум 16 символов, случайные)

---

## 📞 ПОДДЕРЖКА

Если обнаружили подозрительную активность:
1. Заблокируйте IP через UFW
2. Проверьте логи
3. Смените пароли
4. Создайте бэкап БД
5. Проверьте код на изменения: `git status`

---

**Безопасность - это процесс, а не разовое действие! Регулярно проверяйте и обновляйте настройки.**


