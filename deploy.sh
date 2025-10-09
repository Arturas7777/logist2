#!/bin/bash
# Скрипт автоматического развертывания Caromoto Lithuania на VPS

set -e  # Остановка при ошибке

echo "🚀 Начинаем развертывание Caromoto Lithuania..."

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Переменные
PROJECT_DIR="/var/www/caromoto-lt"
VENV_DIR="$PROJECT_DIR/venv"
USER="www-data"
GROUP="www-data"

echo -e "${YELLOW}📂 Переходим в директорию проекта...${NC}"
cd $PROJECT_DIR

echo -e "${YELLOW}🔄 Обновляем код из Git...${NC}"
git pull origin master

echo -e "${YELLOW}📦 Активируем виртуальное окружение...${NC}"
source $VENV_DIR/bin/activate

echo -e "${YELLOW}📥 Устанавливаем/обновляем зависимости...${NC}"
pip install -r requirements.txt --upgrade

echo -e "${YELLOW}🗃️ Применяем миграции базы данных...${NC}"
python manage.py migrate --noinput

echo -e "${YELLOW}📁 Собираем статические файлы...${NC}"
python manage.py collectstatic --noinput --clear

echo -e "${YELLOW}🌍 Компилируем переводы...${NC}"
python manage.py compilemessages

echo -e "${YELLOW}🖼️ Генерируем миниатюры для фото (если есть новые)...${NC}"
python manage.py generate_thumbnails || echo "Команда generate_thumbnails не найдена или завершилась с ошибкой"

echo -e "${YELLOW}🔧 Устанавливаем права доступа...${NC}"
chown -R $USER:$GROUP $PROJECT_DIR
chmod -R 755 $PROJECT_DIR
chmod -R 775 $PROJECT_DIR/media
chmod -R 775 $PROJECT_DIR/staticfiles

echo -e "${YELLOW}🔄 Перезапускаем Gunicorn...${NC}"
sudo systemctl restart caromoto-lt

echo -e "${YELLOW}🔄 Перезапускаем Nginx...${NC}"
sudo systemctl reload nginx

echo -e "${GREEN}✅ Развертывание завершено успешно!${NC}"
echo -e "${GREEN}🌐 Сайт доступен по адресу: https://caromoto-lt.com${NC}"

# Проверка статуса сервисов
echo -e "\n${YELLOW}📊 Статус сервисов:${NC}"
sudo systemctl status caromoto-lt --no-pager -l | head -10

