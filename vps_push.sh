#!/bin/bash
# ============================================
# VPS PUSH - Коммит и пуш изменений с VPS
# ============================================
# Использование (на VPS):
#   bash /var/www/www-root/data/www/logist2/vps_push.sh
#   или: ./vps_push.sh "описание изменений"
# ============================================

cd /var/www/www-root/data/www/logist2

# Проверяем есть ли изменения
if [ -z "$(git status --porcelain)" ]; then
    echo "✓ Нет изменений для коммита"
    exit 0
fi

echo "=== Незакоммиченные изменения ==="
git status --short
echo ""

# Формируем сообщение коммита
if [ -n "$1" ]; then
    MSG="$1"
else
    MSG="VPS update: $(date '+%Y-%m-%d %H:%M')"
fi

# Коммитим и пушим
git add -A
git commit -m "$MSG"
git push origin master

echo ""
echo "✓ Изменения запушены в git"
