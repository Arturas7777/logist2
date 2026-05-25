#!/usr/bin/env bash
#
# Bootstrap: устанавливает systemd unit'ы для logist2 (gunicorn, daphne,
# celery, celerybeat) на сервере. Запускать ОДИН раз под root:
#
#   sudo PROJECT_DIR=/var/www/www-root/data/www/logist2 \
#        scripts/install_systemd.sh
#
# Idempotent: можно перезапускать сколько угодно раз. Если unit-файл в
# репозитории совпадает с уже установленным — ничего не меняется. Если
# отличается — старая версия сохраняется как .bak.YYYYMMDD-HHMMSS.
#
# По умолчанию PROJECT_DIR = текущий путь к репо. Это удобно, если
# скрипт запускают через `git clone && cd logist2 && sudo ./scripts/install_systemd.sh`.
# Иначе нужно явно задать PROJECT_DIR (например для другой инсталляции).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SYSTEMD_DIR="/etc/systemd/system"
UNITS=(gunicorn daphne celery celerybeat)

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: запускать под root (sudo)" >&2
    exit 1
fi

if [[ ! -d "$PROJECT_DIR" ]]; then
    echo "ERROR: PROJECT_DIR не существует: $PROJECT_DIR" >&2
    exit 1
fi

echo "[install_systemd] PROJECT_DIR=$PROJECT_DIR"

# Все unit-файлы в репозитории жёстко указывают путь
# /var/www/www-root/data/www/logist2. Если разворачиваем в другую
# директорию — подставим её в копии (через sed, не трогая исходник).
NEED_SUBST="no"
if [[ "$PROJECT_DIR" != "/var/www/www-root/data/www/logist2" ]]; then
    NEED_SUBST="yes"
    echo "[install_systemd] нестандартный путь — будем подставлять $PROJECT_DIR в unit-файлы"
fi

TS="$(date +%Y%m%d-%H%M%S)"
changed=0

for unit in "${UNITS[@]}"; do
    src="$SCRIPT_DIR/${unit}.service"
    dst="$SYSTEMD_DIR/${unit}.service"

    if [[ ! -f "$src" ]]; then
        echo "  [WARN] нет файла $src — пропускаю" >&2
        continue
    fi

    tmp="$(mktemp)"
    if [[ "$NEED_SUBST" == "yes" ]]; then
        sed "s|/var/www/www-root/data/www/logist2|${PROJECT_DIR}|g" "$src" > "$tmp"
    else
        cp "$src" "$tmp"
    fi

    if [[ -f "$dst" ]] && cmp -s "$tmp" "$dst"; then
        echo "  [ok ] ${unit}.service не изменился"
        rm -f "$tmp"
        continue
    fi

    if [[ -f "$dst" ]]; then
        echo "  [bak] ${unit}.service отличается — backup в ${unit}.service.bak.${TS}"
        cp -p "$dst" "${dst}.bak.${TS}"
    fi

    install -m 0644 -o root -g root "$tmp" "$dst"
    rm -f "$tmp"
    echo "  [new] установлен ${unit}.service"
    changed=1
done

if [[ "$changed" == "1" ]]; then
    echo "[install_systemd] daemon-reload"
    systemctl daemon-reload
fi

echo "[install_systemd] enable ${UNITS[*]}"
for unit in "${UNITS[@]}"; do
    systemctl enable "${unit}.service" >/dev/null
done

echo
echo "[install_systemd] DONE."
echo
echo "Status:"
for unit in "${UNITS[@]}"; do
    state="$(systemctl is-active "${unit}.service" 2>&1 || true)"
    enabled="$(systemctl is-enabled "${unit}.service" 2>&1 || true)"
    printf "  %-15s active=%-10s enabled=%-10s\n" "$unit" "$state" "$enabled"
done

echo
echo "Чтобы стартовать впервые:"
for unit in "${UNITS[@]}"; do
    echo "  systemctl start ${unit}.service"
done
echo
echo "Перезапустить (после обновления кода):"
echo "  systemctl restart ${UNITS[*]/%/.service}"
