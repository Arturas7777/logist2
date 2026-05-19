#!/usr/bin/env bash
# One-shot server setup for /admin/system-monitor/.
# Idempotent — можно перезапускать.
#
# Что делает:
#   1. Включает pg_stat_statements в postgresql.conf, перезапускает Postgres.
#   2. Создаёт extension в боевой БД (через временный SUPERUSER на DB_USER).
#   3. Ставит psutil в venv проекта.
#   4. Проставляет MONITORING_HEALTH_URL в .env (через nginx, не unix socket).
#   5. Перезапускает celery + celerybeat, чтобы они подцепили новые задачи.
#   6. Делает разовый collect/ping, чтобы дашборд не был пустым.
set -euo pipefail

PROJECT=/var/www/www-root/data/www/logist2
cd "$PROJECT"

DB_USER=$(grep -E '^DB_USER='     .env | head -1 | cut -d= -f2-)
DB_NAME=$(grep -E '^DB_NAME='     .env | head -1 | cut -d= -f2-)
DB_PASS=$(grep -E '^DB_PASSWORD=' .env | head -1 | cut -d= -f2-)

echo "Project: $PROJECT"
echo "DB:      $DB_USER@$DB_NAME"

echo
echo '=== 1. Detect postgresql.conf ==='
PG_CONF=$(sudo -u postgres psql -tA -c 'SHOW config_file;' 2>/dev/null || true)
[ -z "$PG_CONF" ] && PG_CONF=$(ls /etc/postgresql/*/main/postgresql.conf 2>/dev/null | head -1)
echo "  $PG_CONF"
test -f "$PG_CONF"

echo
echo '=== 2. shared_preload_libraries += pg_stat_statements ==='
if grep -E '^[[:space:]]*shared_preload_libraries[[:space:]]*=' "$PG_CONF" | grep -q 'pg_stat_statements'; then
  echo '  already enabled'
else
  cp "$PG_CONF" "${PG_CONF}.bak.$(date +%Y%m%d-%H%M%S)"
  if grep -qE "^[[:space:]]*shared_preload_libraries[[:space:]]*=" "$PG_CONF"; then
    sed -i -E "s/^[[:space:]]*shared_preload_libraries[[:space:]]*=[[:space:]]*'([^']*)'/shared_preload_libraries = '\1,pg_stat_statements'/" "$PG_CONF"
    sed -i "s/'',/'/g; s/^shared_preload_libraries = ',/shared_preload_libraries = '/" "$PG_CONF"
  else
    echo "shared_preload_libraries = 'pg_stat_statements'" >> "$PG_CONF"
  fi
  systemctl restart postgresql
  sleep 3
fi
systemctl is-active postgresql

echo
echo '=== 3. CREATE EXTENSION pg_stat_statements (with temp SUPERUSER) ==='
if PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" \
     -tA -c "SELECT 1 FROM pg_extension WHERE extname='pg_stat_statements';" | grep -q 1
then
  echo '  extension already exists'
else
  sudo -u postgres psql -d postgres -c "ALTER ROLE \"$DB_USER\" SUPERUSER;"
  PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" \
    -c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements;'
  sudo -u postgres psql -d postgres -c "ALTER ROLE \"$DB_USER\" NOSUPERUSER;"
fi
PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" \
  -c 'SELECT count(*) AS samples FROM pg_stat_statements LIMIT 1;'

echo
echo '=== 4. Install psutil into venv ==='
.venv/bin/pip install --no-warn-script-location 'psutil>=5.9.0' 2>&1 | tail -3
.venv/bin/python -c 'import psutil; print("  psutil", psutil.__version__, "OK")'

echo
echo '=== 5. Ensure MONITORING_HEALTH_URL points at nginx, not unix socket ==='
TARGET_URL='https://caromoto-lt.com/health/'
if grep -q '^MONITORING_HEALTH_URL=' .env; then
  sed -i "s|^MONITORING_HEALTH_URL=.*|MONITORING_HEALTH_URL=$TARGET_URL|" .env
else
  echo "MONITORING_HEALTH_URL=$TARGET_URL" >> .env
fi
grep MONITORING .env

echo
echo '=== 6. Restart celery + celerybeat ==='
systemctl restart celery
systemctl restart celerybeat 2>/dev/null || true
sleep 3
systemctl is-active celery
systemctl is-active celerybeat 2>/dev/null || true

echo
echo '=== 7. Seed one snapshot + one ping so dashboard is not empty ==='
sudo -u www-root bash -c "
cd $PROJECT
export DJANGO_SETTINGS_MODULE=logist2.settings.prod
.venv/bin/python -c \"
import django; django.setup()
from core.tasks_monitoring import collect_system_metrics, ping_uptime
print('collect:', collect_system_metrics())
print('ping   :', ping_uptime())
\"
"

echo
echo '=== 8. Health checks ==='
curl -sS -o /dev/null -w '  /health/                  : HTTP %{http_code}\n' https://caromoto-lt.com/health/
curl -sS -o /dev/null -w '  /admin/system-monitor/    : HTTP %{http_code}  (302 = login redirect, expected)\n' https://caromoto-lt.com/admin/system-monitor/

echo
echo 'Done. Открой https://caromoto-lt.com/admin/system-monitor/ — данные появятся через ~5 минут.'
