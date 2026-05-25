#!/usr/bin/env bash
#
# Daily PostgreSQL backup for Logist2.
#
# Запускается через cron (см. scripts/logist2-backup.cron):
#   30 3 * * * root /var/www/www-root/data/www/logist2/scripts/server_pg_backup.sh
#
# Что делает:
#   1) Читает DB_NAME / DB_USER / DB_PASSWORD / DB_HOST / DB_PORT из .env проекта.
#   2) pg_dump -Fc → /var/backups/logist2/${DB_NAME}_YYYY-MM-DD.dump
#      (custom-format, сжатый, --no-owner --no-acl — можно восстановить под
#       любым пользователем).
#   3) Smoke check: `pg_restore --list` на свежий файл — гарантия, что дамп
#      не битый. Если check падает — временный файл удаляется, exit 3.
#   4) Retention: удаляет .dump старше RETENTION_DAYS (по умолчанию 30).
#   5) Логирует всё в /var/log/logist2/backup.log.
#
# Установка: см. scripts/install_logist2_backup.sh.
# Восстановление: см. docs/BACKUPS.md.

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/var/www/www-root/data/www/logist2}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/logist2}"
LOG_FILE="${BACKUP_LOG:-/var/log/logist2/backup.log}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"

mkdir -p "$BACKUP_DIR" "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

if [[ ! -f "$ENV_FILE" ]]; then
    log "ERROR: $ENV_FILE не найден"
    exit 1
fi

# Читаем нужные переменные из .env без `source` (чтобы не подтягивать в shell
# случайные переменные с переносами строк / спецсимволами).
get_env() {
    local key="$1"
    local default="${2:-}"
    local val
    val="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE" | tail -n1 \
        | sed -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*(.*)[[:space:]]*$/\1/" || true)"
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    echo "${val:-$default}"
}

DB_NAME="$(get_env DB_NAME)"
DB_USER="$(get_env DB_USER)"
DB_PASSWORD="$(get_env DB_PASSWORD)"
DB_HOST="$(get_env DB_HOST localhost)"
DB_PORT="$(get_env DB_PORT 5432)"

if [[ -z "$DB_NAME" || -z "$DB_USER" || -z "$DB_PASSWORD" ]]; then
    log "ERROR: DB_NAME / DB_USER / DB_PASSWORD не заданы в $ENV_FILE"
    exit 2
fi

TODAY="$(date +%F)"
DUMP_FILE="$BACKUP_DIR/${DB_NAME}_${TODAY}.dump"
TMP_FILE="${DUMP_FILE}.tmp"

log "=== Backup start: ${DB_NAME}@${DB_HOST}:${DB_PORT} → $DUMP_FILE ==="

# pg_dump в custom-format (-Fc): сжатый бинарный, поддерживает selective restore.
# --no-owner / --no-acl: дамп можно восстановить под любым пользователем
# (важно при rsync на запасной сервер с другой схемой ролей).
PGPASSWORD="$DB_PASSWORD" pg_dump \
    -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" \
    -Fc --no-owner --no-acl \
    -f "$TMP_FILE" "$DB_NAME"

# Smoke check: pg_restore --list должен прочитать оглавление без ошибок.
# Если структура битая (например, pg_dump оборвался) — это поймается тут.
if ! pg_restore --list "$TMP_FILE" > /dev/null 2>&1; then
    log "ERROR: smoke check (pg_restore --list) failed, дамп битый — удаляю $TMP_FILE"
    rm -f "$TMP_FILE"
    exit 3
fi

# Атомарная замена: только если smoke check прошёл — публикуем как итоговый файл.
mv "$TMP_FILE" "$DUMP_FILE"
SIZE_HUMAN="$(du -h "$DUMP_FILE" | cut -f1)"
log "OK: dump created, size=$SIZE_HUMAN"

# Retention: удаляем .dump старше RETENTION_DAYS дней.
DELETED_COUNT=0
while IFS= read -r -d '' f; do
    rm -f "$f"
    DELETED_COUNT=$((DELETED_COUNT + 1))
    log "  retention: deleted $(basename "$f")"
done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name "*.dump" -mtime "+${RETENTION_DAYS}" -print0)
log "Retention: deleted $DELETED_COUNT files older than ${RETENTION_DAYS} days"

# Финальная сводка.
REMAINING_COUNT="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "*.dump" | wc -l | tr -d ' ')"
TOTAL_SIZE="$(du -sh "$BACKUP_DIR" | cut -f1)"
log "Stats: $REMAINING_COUNT files in $BACKUP_DIR, total $TOTAL_SIZE"

log "=== Backup done ==="
