#!/usr/bin/env bash
#
# Bootstrap: устанавливает cron-задачу логи2-бэкапа и подготавливает директории.
# Запускать на сервере ОДИН раз под root:
#
#   sudo /var/www/www-root/data/www/logist2/scripts/install_logist2_backup.sh
#
# Idempotent: можно перезапускать сколько угодно раз, ничего не сломает.

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/var/www/www-root/data/www/logist2}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/logist2}"
LOG_DIR="${LOG_DIR:-/var/log/logist2}"
CRON_SRC="$PROJECT_DIR/scripts/logist2-backup.cron"
CRON_DST="/etc/cron.d/logist2-backup"
SCRIPT="$PROJECT_DIR/scripts/server_pg_backup.sh"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: запускать под root (sudo)" >&2
    exit 1
fi

echo "[install] mkdir $BACKUP_DIR $LOG_DIR"
mkdir -p "$BACKUP_DIR" "$LOG_DIR"
chmod 750 "$BACKUP_DIR"

echo "[install] chmod +x $SCRIPT"
chmod +x "$SCRIPT"

echo "[install] copy $CRON_SRC → $CRON_DST"
install -m 0644 -o root -g root "$CRON_SRC" "$CRON_DST"

echo "[install] reload cron"
if command -v systemctl >/dev/null 2>&1; then
    systemctl reload cron 2>/dev/null || systemctl restart cron 2>/dev/null || true
else
    service cron reload 2>/dev/null || service cron restart 2>/dev/null || true
fi

echo
echo "[install] DONE."
echo
echo "Verify:"
echo "  cat $CRON_DST"
echo "  ls -la $BACKUP_DIR $LOG_DIR"
echo
echo "Test manually right now:"
echo "  $SCRIPT"
echo "  tail -n 30 $LOG_DIR/backup.log"
echo "  ls -lh $BACKUP_DIR"
