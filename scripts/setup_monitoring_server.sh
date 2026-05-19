#!/usr/bin/env bash
# Enable pg_stat_statements for /admin/system-monitor/ slow queries panel.
# Safe to re-run; идемпотентен.
set -euo pipefail

echo '=== STEP 1: Detect postgresql.conf path ==='
PG_CONF=$(sudo -u postgres psql -tA -c 'SHOW config_file;' 2>/dev/null || true)
if [ -z "$PG_CONF" ]; then
  echo 'Could not detect postgresql.conf via psql; falling back to /etc/postgresql/*/main/postgresql.conf'
  PG_CONF=$(ls /etc/postgresql/*/main/postgresql.conf 2>/dev/null | head -1)
fi
echo "postgresql.conf: $PG_CONF"
test -f "$PG_CONF"

echo
echo '=== STEP 2: Ensure pg_stat_statements in shared_preload_libraries ==='
if grep -E '^[[:space:]]*shared_preload_libraries[[:space:]]*=' "$PG_CONF" | grep -q 'pg_stat_statements'; then
  echo 'already enabled in shared_preload_libraries'
else
  cp "$PG_CONF" "${PG_CONF}.bak.$(date +%Y%m%d-%H%M%S)"

  if grep -qE "^[[:space:]]*shared_preload_libraries[[:space:]]*=" "$PG_CONF"; then
    sed -i -E "s/^[[:space:]]*shared_preload_libraries[[:space:]]*=[[:space:]]*'([^']*)'/shared_preload_libraries = '\1,pg_stat_statements'/" "$PG_CONF"
    sed -i "s/,,/,/g; s/'',/'/g; s/^shared_preload_libraries = ',/shared_preload_libraries = '/" "$PG_CONF"
  else
    echo "shared_preload_libraries = 'pg_stat_statements'" >> "$PG_CONF"
  fi
  echo 'updated postgresql.conf:'
  grep -E '^[[:space:]]*shared_preload_libraries' "$PG_CONF"

  echo 'Restarting postgresql (needed to load shared library)...'
  systemctl restart postgresql
  sleep 3
  systemctl is-active postgresql
fi

echo
echo '=== STEP 3: CREATE EXTENSION pg_stat_statements ==='
DB_NAME=$(grep -E '^DB_NAME=' /var/www/www-root/data/www/logist2/.env 2>/dev/null | cut -d= -f2 || echo logist2_db)
DB_NAME=${DB_NAME:-logist2_db}
echo "Target DB: $DB_NAME"
sudo -u postgres psql -d "$DB_NAME" -c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements;'

echo
echo '=== STEP 4: Verify extension is queryable ==='
sudo -u postgres psql -d "$DB_NAME" -c "SELECT count(*) AS sample FROM pg_stat_statements LIMIT 1;"

echo
echo '=== STEP 5: Install psutil into project venv ==='
VENV=/var/www/www-root/data/www/logist2/.venv
if [ -x "$VENV/bin/pip" ]; then
  "$VENV/bin/pip" install --no-warn-script-location 'psutil>=5.9.0'
else
  echo 'venv pip not found, skip'
fi

echo
echo '=== STEP 6: Sanity check — celery user can read systemctl status ==='
sudo -u www-root systemctl is-active gunicorn || true
sudo -u www-root systemctl show gunicorn -p MemoryCurrent --value || true

echo
echo 'Done. После git pull / deploy: миграция core.0170 создаст таблицы метрик.'
