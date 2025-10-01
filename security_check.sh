#!/bin/bash

# ===================================
# SECURITY CHECK SCRIPT FOR LOGIST2
# ===================================

echo "========================================="
echo "   ПРОВЕРКА БЕЗОПАСНОСТИ LOGIST2"
echo "========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

check_ok() {
    echo -e "${GREEN}✓${NC} $1"
}

check_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

check_error() {
    echo -e "${RED}✗${NC} $1"
}

# 1. Проверка UFW (Firewall)
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1. Firewall (UFW)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if sudo ufw status | grep -q "Status: active"; then
    check_ok "UFW активен"
    sudo ufw status numbered
else
    check_error "UFW НЕ АКТИВЕН! Запустите: sudo ufw enable"
fi
echo ""

# 2. Проверка SSH конфигурации
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "2. SSH Security"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if grep -q "^PermitRootLogin no" /etc/ssh/sshd_config; then
    check_ok "Root login отключен"
else
    check_error "Root login ВКЛЮЧЕН! Отключите: PermitRootLogin no"
fi

if grep -q "^PasswordAuthentication no" /etc/ssh/sshd_config; then
    check_ok "Password authentication отключен"
else
    check_warning "Password authentication включен (рекомендуется отключить)"
fi

if grep -q "^PubkeyAuthentication yes" /etc/ssh/sshd_config; then
    check_ok "SSH keys включены"
else
    check_warning "SSH keys не настроены"
fi
echo ""

# 3. Проверка Fail2Ban
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "3. Fail2Ban"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if systemctl is-active --quiet fail2ban; then
    check_ok "Fail2Ban активен"
    sudo fail2ban-client status | grep "Jail list:"
    
    # Показываем забаненные IP
    BANNED_SSH=$(sudo fail2ban-client status sshd 2>/dev/null | grep "Currently banned:" | awk '{print $NF}')
    if [ "$BANNED_SSH" != "0" ]; then
        check_warning "Забанено SSH попыток: $BANNED_SSH"
    fi
else
    check_error "Fail2Ban НЕ АКТИВЕН! Установите и запустите"
fi
echo ""

# 4. Проверка открытых портов
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "4. Открытые порты"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
LISTENING_PORTS=$(sudo netstat -tulpn | grep LISTEN)
echo "$LISTENING_PORTS" | grep -E ":(22|80|443|1500) " > /dev/null && check_ok "Стандартные порты открыты"

# Проверяем что PostgreSQL и приложение слушают только localhost
if echo "$LISTENING_PORTS" | grep "127.0.0.1:5432" > /dev/null; then
    check_ok "PostgreSQL доступен только локально"
else
    check_error "PostgreSQL доступен извне! ОПАСНО!"
fi

if echo "$LISTENING_PORTS" | grep "127.0.0.1:8000" > /dev/null; then
    check_ok "Gunicorn доступен только локально"
else
    check_warning "Gunicorn может быть доступен извне"
fi

echo ""
echo "Все listening порты:"
echo "$LISTENING_PORTS"
echo ""

# 5. Проверка сервисов
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "5. Статус сервисов"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

services=("nginx" "gunicorn" "daphne" "postgresql" "fail2ban")
for service in "${services[@]}"; do
    if systemctl is-active --quiet "$service"; then
        check_ok "$service запущен"
    else
        check_error "$service НЕ ЗАПУЩЕН!"
    fi
done
echo ""

# 6. Проверка SSL/HTTPS
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "6. SSL/HTTPS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if sudo nginx -T 2>/dev/null | grep -q "ssl_certificate"; then
    check_ok "SSL сертификат настроен в Nginx"
    sudo nginx -T 2>/dev/null | grep "ssl_certificate"
else
    check_error "SSL сертификат НЕ НАСТРОЕН!"
fi
echo ""

# 7. Проверка бэкапов
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "7. Бэкапы"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ -d "/var/backups/logist2" ]; then
    BACKUP_COUNT=$(find /var/backups/logist2 -name "backup_*.sql.gz" 2>/dev/null | wc -l)
    if [ "$BACKUP_COUNT" -gt 0 ]; then
        check_ok "Найдено бэкапов: $BACKUP_COUNT"
        echo "Последний бэкап:"
        ls -lth /var/backups/logist2/backup_*.sql.gz 2>/dev/null | head -1
    else
        check_warning "Бэкапы не найдены"
    fi
else
    check_error "Директория бэкапов не существует!"
fi
echo ""

# 8. Проверка обновлений
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "8. Системные обновления"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
UPDATES=$(apt list --upgradable 2>/dev/null | grep -v "Listing..." | wc -l)
if [ "$UPDATES" -eq 0 ]; then
    check_ok "Система обновлена"
else
    check_warning "Доступно обновлений: $UPDATES"
fi

if systemctl is-active --quiet unattended-upgrades; then
    check_ok "Автоматические обновления включены"
else
    check_warning "Автоматические обновления не настроены"
fi
echo ""

# 9. Проверка Django Security
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "9. Django Security Check"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cd /var/www/www-root/data/www/logist2
source .venv/bin/activate

echo "Запускаем: python manage.py check --deploy"
python manage.py check --deploy
if [ $? -eq 0 ]; then
    check_ok "Django security check пройден"
else
    check_error "Django security check ПРОВАЛЕН! Исправьте ошибки"
fi
echo ""

# 10. Проверка последних попыток входа
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "10. Последние попытки входа (SSH)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Неудачные попытки за последние 24 часа:"
sudo grep "Failed password" /var/log/auth.log | grep "$(date +%b\ %d)" | wc -l
echo ""
echo "Последние 5 неудачных попыток:"
sudo grep "Failed password" /var/log/auth.log | tail -5
echo ""

# 11. Проверка дискового пространства
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "11. Дисковое пространство"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
DISK_USAGE=$(df -h / | tail -1 | awk '{print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -lt 80 ]; then
    check_ok "Диск: $DISK_USAGE% использовано"
else
    check_error "Диск переполнен: $DISK_USAGE%! Очистите место"
fi
df -h /
echo ""

# 12. Проверка памяти
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "12. Использование памяти"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
free -h
echo ""

# Итоговый отчет
echo "========================================="
echo "   ПРОВЕРКА ЗАВЕРШЕНА"
echo "========================================="
echo ""
echo "Рекомендации:"
echo "1. Исправьте все найденные ошибки (✗)"
echo "2. Обратите внимание на предупреждения (⚠)"
echo "3. Регулярно проверяйте логи: sudo tail -f /var/log/auth.log"
echo "4. Обновляйте систему: sudo apt update && sudo apt upgrade"
echo "5. Проверяйте бэкапы: ls -lh /var/backups/logist2/"
echo ""

