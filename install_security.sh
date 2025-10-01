#!/bin/bash

# ========================================
# АВТОМАТИЧЕСКАЯ УСТАНОВКА БЕЗОПАСНОСТИ
# ========================================

set -e  # Остановка при ошибке

echo "========================================="
echo "  УСТАНОВКА БЕЗОПАСНОСТИ LOGIST2"
echo "========================================="
echo ""

# Проверка что запущено от root
if [ "$EUID" -ne 0 ]; then 
    echo "Запустите от root: sudo bash install_security.sh"
    exit 1
fi

# ================================
# 1. UFW FIREWALL
# ================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1. Настройка Firewall (UFW)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

apt-get update -qq
apt-get install -y ufw >/dev/null 2>&1

# Настраиваем правила
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH"
ufw allow 80/tcp comment "HTTP"
ufw allow 443/tcp comment "HTTPS"
ufw allow 1500/tcp comment "ISPmanager"

# Включаем (без интерактивного подтверждения)
ufw --force enable

echo "✓ UFW настроен и активирован"
ufw status numbered
echo ""

# ================================
# 2. FAIL2BAN
# ================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "2. Установка Fail2Ban..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

apt-get install -y fail2ban >/dev/null 2>&1

# Создаем конфигурацию
cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5
destemail = root@localhost
sendername = Fail2Ban
action = %(action_)s

[sshd]
enabled = true
port = 22
logpath = /var/log/auth.log
maxretry = 5

[nginx-http-auth]
enabled = true
port = http,https
logpath = /var/log/nginx/error.log

[nginx-noscript]
enabled = true
port = http,https
logpath = /var/log/nginx/access.log
maxretry = 6

[nginx-badbots]
enabled = true
port = http,https
logpath = /var/log/nginx/access.log
maxretry = 2

[nginx-noproxy]
enabled = true
port = http,https
logpath = /var/log/nginx/access.log
maxretry = 2
EOF

# Запускаем и включаем автозапуск
systemctl enable fail2ban >/dev/null 2>&1
systemctl restart fail2ban

echo "✓ Fail2Ban установлен и настроен"
fail2ban-client status
echo ""

# ================================
# 3. POSTGRESQL SECURITY
# ================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "3. Защита PostgreSQL..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Находим версию PostgreSQL
PG_VERSION=$(ls /etc/postgresql/ | head -1)
PG_CONF="/etc/postgresql/$PG_VERSION/main/postgresql.conf"
PG_HBA="/etc/postgresql/$PG_VERSION/main/pg_hba.conf"

# Убеждаемся что слушает только localhost
if ! grep -q "^listen_addresses = 'localhost'" "$PG_CONF"; then
    echo "listen_addresses = 'localhost'" >> "$PG_CONF"
fi

# Бэкап и настройка pg_hba.conf
cp "$PG_HBA" "$PG_HBA.backup"
cat > "$PG_HBA" << 'EOF'
# PostgreSQL Client Authentication Configuration File
# TYPE  DATABASE        USER            ADDRESS                 METHOD

# Local connections
local   all             postgres                                peer
local   all             arturas                                 md5

# IPv4 local connections:
host    all             arturas         127.0.0.1/32            md5

# IPv6 local connections:
host    all             arturas         ::1/128                 md5

# DENY all other connections
EOF

systemctl restart postgresql

echo "✓ PostgreSQL настроен (доступен только локально)"
echo ""

# ================================
# 4. АВТОМАТИЧЕСКИЕ БЭКАПЫ
# ================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "4. Настройка автоматических бэкапов..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Создаем директорию для бэкапов
mkdir -p /var/backups/logist2
chown root:root /var/backups/logist2
chmod 700 /var/backups/logist2

# Создаем скрипт бэкапа
cat > /usr/local/bin/backup_logist2.sh << 'EOF'
#!/bin/bash

BACKUP_DIR="/var/backups/logist2"
DB_NAME="logist2_db"
DB_USER="arturas"
RETENTION_DAYS=30

mkdir -p $BACKUP_DIR
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/backup_$TIMESTAMP.sql"

pg_dump -U $DB_USER $DB_NAME | gzip > "$BACKUP_FILE.gz"

if [ $? -eq 0 ]; then
    echo "$(date): Backup created: $BACKUP_FILE.gz" >> /var/log/logist2_backup.log
    find $BACKUP_DIR -name "backup_*.sql.gz" -mtime +$RETENTION_DAYS -delete
    echo "$(date): Old backups cleaned (>$RETENTION_DAYS days)" >> /var/log/logist2_backup.log
else
    echo "$(date): ERROR: Backup failed!" >> /var/log/logist2_backup.log
    exit 1
fi
EOF

chmod +x /usr/local/bin/backup_logist2.sh

# Создаем первый бэкап для проверки
echo "Создание тестового бэкапа..."
sudo -u postgres /usr/local/bin/backup_logist2.sh

if [ -f /var/log/logist2_backup.log ]; then
    echo "✓ Скрипт бэкапа работает"
    tail -1 /var/log/logist2_backup.log
else
    echo "⚠ Предупреждение: проверьте бэкап вручную"
fi

# Добавляем в cron (каждый день в 3:00)
CRON_JOB="0 3 * * * /usr/local/bin/backup_logist2.sh"
(crontab -l 2>/dev/null | grep -v "backup_logist2.sh"; echo "$CRON_JOB") | crontab -

echo "✓ Автоматические бэкапы настроены (каждый день в 3:00)"
echo ""

# ================================
# 5. АВТОМАТИЧЕСКИЕ ОБНОВЛЕНИЯ
# ================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "5. Настройка автоматических обновлений..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

apt-get install -y unattended-upgrades apt-listchanges >/dev/null 2>&1

# Настраиваем автообновления
cat > /etc/apt/apt.conf.d/50unattended-upgrades << 'EOF'
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}";
    "${distro_id}:${distro_codename}-security";
    "${distro_id}ESMApps:${distro_codename}-apps-security";
    "${distro_id}ESM:${distro_codename}-infra-security";
};

Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::MinimalSteps "true";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Automatic-Reboot-Time "03:00";
EOF

cat > /etc/apt/apt.conf.d/20auto-upgrades << 'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::AutocleanInterval "7";
APT::Periodic::Unattended-Upgrade "1";
EOF

systemctl enable unattended-upgrades >/dev/null 2>&1
systemctl restart unattended-upgrades

echo "✓ Автоматические обновления безопасности включены"
echo ""

# ================================
# 6. NGINX SECURITY HEADERS
# ================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "6. Добавление security headers в Nginx..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Создаем файл с security headers
cat > /etc/nginx/snippets/security-headers.conf << 'EOF'
# Security Headers
add_header X-Frame-Options "DENY" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "same-origin" always;
add_header Content-Security-Policy "default-src 'self' 'unsafe-inline' 'unsafe-eval'; img-src 'self' data: https:; font-src 'self' data:;" always;

# Hide Nginx version
server_tokens off;
EOF

# Добавляем include в основной конфиг (если еще не добавлено)
NGINX_CONF="/etc/nginx/sites-available/logist2"
if [ -f "$NGINX_CONF" ]; then
    if ! grep -q "security-headers.conf" "$NGINX_CONF"; then
        # Добавляем после server_name
        sed -i '/server_name/a \    include snippets/security-headers.conf;' "$NGINX_CONF"
    fi
    
    # Проверяем конфигурацию
    if nginx -t 2>&1 | grep -q "successful"; then
        systemctl reload nginx
        echo "✓ Security headers добавлены в Nginx"
    else
        echo "⚠ Ошибка в конфигурации Nginx, пропускаем..."
    fi
else
    echo "⚠ Nginx конфиг не найден, пропускаем..."
fi
echo ""

# ================================
# 7. СИСТЕМНЫЕ НАСТРОЙКИ
# ================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "7. Оптимизация системных настроек..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Ограничения для защиты от DDoS
cat >> /etc/sysctl.conf << 'EOF'

# Security settings
net.ipv4.conf.all.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv4.conf.all.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.conf.all.secure_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1
net.ipv4.tcp_syncookies = 1
EOF

sysctl -p >/dev/null 2>&1

echo "✓ Системные настройки безопасности применены"
echo ""

# ================================
# ФИНАЛЬНАЯ ПРОВЕРКА
# ================================
echo "========================================="
echo "  ПРОВЕРКА УСТАНОВКИ"
echo "========================================="
echo ""

echo "Статус сервисов:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
systemctl is-active ufw && echo "✓ UFW: активен" || echo "✗ UFW: не активен"
systemctl is-active fail2ban && echo "✓ Fail2Ban: активен" || echo "✗ Fail2Ban: не активен"
systemctl is-active postgresql && echo "✓ PostgreSQL: активен" || echo "✗ PostgreSQL: не активен"
systemctl is-active nginx && echo "✓ Nginx: активен" || echo "✗ Nginx: не активен"
systemctl is-active gunicorn && echo "✓ Gunicorn: активен" || echo "✗ Gunicorn: не активен"
systemctl is-active daphne && echo "✓ Daphne: активен" || echo "✗ Daphne: не активен"
echo ""

echo "Бэкапы:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
BACKUP_COUNT=$(find /var/backups/logist2 -name "backup_*.sql.gz" 2>/dev/null | wc -l)
echo "Найдено бэкапов: $BACKUP_COUNT"
if [ $BACKUP_COUNT -gt 0 ]; then
    echo "Последний бэкап:"
    ls -lth /var/backups/logist2/backup_*.sql.gz 2>/dev/null | head -1
fi
echo ""

echo "Firewall правила:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ufw status numbered
echo ""

echo "Fail2Ban jails:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fail2ban-client status
echo ""

# ================================
# ИТОГ
# ================================
echo "========================================="
echo "  ✓ УСТАНОВКА ЗАВЕРШЕНА!"
echo "========================================="
echo ""
echo "Что установлено:"
echo "  ✓ UFW Firewall (порты: 22, 80, 443, 1500)"
echo "  ✓ Fail2Ban (защита от брутфорса)"
echo "  ✓ PostgreSQL (только localhost)"
echo "  ✓ Автоматические бэкапы (каждый день в 3:00)"
echo "  ✓ Автоматические обновления безопасности"
echo "  ✓ Nginx security headers"
echo "  ✓ Системные настройки безопасности"
echo ""
echo "Рекомендации:"
echo "  1. Проверьте сайт: http://176.118.198.78/admin/"
echo "  2. Проверьте бэкапы: ls -lh /var/backups/logist2/"
echo "  3. Запустите полную проверку: ./security_check.sh"
echo ""
echo "Для дальнейшей настройки:"
echo "  - HTTPS/SSL: см. SECURITY_SETUP.md, раздел 3"
echo "  - SSH ключи: см. SECURITY_SETUP.md, раздел 1"
echo ""

