#!/bin/bash
# Скрипт начальной настройки сервера для Caromoto Lithuania
# Запускается один раз при первом развертывании

set -e

echo "🔧 Начинаем настройку сервера для Caromoto Lithuania..."

# Цвета
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}1/10 Обновление системы...${NC}"
apt update && apt upgrade -y

echo -e "${YELLOW}2/10 Установка Python и инструментов...${NC}"
apt install -y python3 python3-pip python3-venv python3-dev build-essential libpq-dev gettext

echo -e "${YELLOW}3/10 Установка PostgreSQL...${NC}"
apt install -y postgresql postgresql-contrib

echo -e "${YELLOW}4/10 Установка Nginx...${NC}"
apt install -y nginx

echo -e "${YELLOW}5/10 Установка Git...${NC}"
apt install -y git

echo -e "${YELLOW}6/10 Установка Certbot для SSL...${NC}"
apt install -y certbot python3-certbot-nginx

echo -e "${YELLOW}7/10 Настройка Firewall...${NC}"
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo -e "${YELLOW}8/10 Создание директорий проекта...${NC}"
mkdir -p /var/www/caromoto-lt
mkdir -p /var/log/gunicorn
chown -R www-data:www-data /var/log/gunicorn

echo -e "${YELLOW}9/10 Настройка PostgreSQL...${NC}"
echo -e "${GREEN}Создайте базу данных вручную:${NC}"
echo "sudo -u postgres psql"
echo "CREATE DATABASE logist2_db;"
echo "CREATE USER logist2_user WITH PASSWORD 'ваш-пароль';"
echo "GRANT ALL PRIVILEGES ON DATABASE logist2_db TO logist2_user;"
echo "\q"

echo -e "${YELLOW}10/10 Настройка времени сервера...${NC}"
timedatectl set-timezone Europe/Vilnius

echo -e "${GREEN}✅ Базовая настройка сервера завершена!${NC}"
echo -e "${GREEN}📝 Следующие шаги:${NC}"
echo "1. Создайте базу данных PostgreSQL (команды выше)"
echo "2. Загрузите файлы проекта в /var/www/caromoto-lt"
echo "3. Следуйте инструкциям в DEPLOYMENT.md или DEPLOY_CHECKLIST.md"

