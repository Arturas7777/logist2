#!/usr/bin/env bash
#
# Доступ ботов к почте CRM по SSH без root: отдельный пользователь, ключ с
# принудительной командой scripts/bot_mail_gateway.py, запуск от www-root
# (тот же владелец media/, что у gunicorn/celery, — иначе ночная очистка не
# сможет удалить скачанные ботом вложения).
#
# Запускать на сервере под root, по одному разу на каждый ключ бота:
#
#   sudo /var/www/www-root/data/www/logist2/scripts/install_bot_mail_access.sh /root/bot_key.pub
#
# Idempotent: повторный запуск с тем же ключом ничего не дублирует.
# Отозвать ключ: удалить его строку из /home/$BOT_USER/.ssh/authorized_keys.

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/var/www/www-root/data/www/logist2}"
BOT_USER="${BOT_USER:-logist2-mail}"
APP_USER="${APP_USER:-www-root}"
GATEWAY="$PROJECT_DIR/scripts/bot_mail_gateway.py"
SUDOERS_FILE="/etc/sudoers.d/logist2-bot-mail"
PUBKEY_FILE="${1:-}"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: запускать под root (sudo)" >&2
    exit 1
fi
if [[ -z "$PUBKEY_FILE" || ! -f "$PUBKEY_FILE" ]]; then
    echo "Usage: $0 /path/to/bot_key.pub" >&2
    exit 1
fi
PUBKEY="$(grep -m1 -E '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+) ' "$PUBKEY_FILE" || true)"
if [[ -z "$PUBKEY" ]]; then
    echo "ERROR: в $PUBKEY_FILE нет публичного ключа OpenSSH" >&2
    exit 1
fi
if [[ ! -f "$GATEWAY" ]]; then
    echo "ERROR: нет $GATEWAY — сначала задеплойте код" >&2
    exit 1
fi
PYTHON3="$(command -v python3)"

if ! id "$BOT_USER" >/dev/null 2>&1; then
    echo "[install] useradd $BOT_USER"
    useradd --system --create-home --shell /bin/sh "$BOT_USER"
fi
passwd -l "$BOT_USER" >/dev/null

echo "[install] $SUDOERS_FILE"
TMP_SUDOERS="$(mktemp)"
cat >"$TMP_SUDOERS" <<EOF
# Боты почты: только SSH-шлюз от $APP_USER. Аргументы в правиле sudo
# сравнивает точно, так что дописать к команде что-то ещё нельзя.
Defaults:$BOT_USER env_keep += "SSH_ORIGINAL_COMMAND"
Defaults:$BOT_USER !requiretty
$BOT_USER ALL=($APP_USER) NOPASSWD: $PYTHON3 $GATEWAY
EOF
visudo -cf "$TMP_SUDOERS" >/dev/null
install -m 0440 -o root -g root "$TMP_SUDOERS" "$SUDOERS_FILE"
rm -f "$TMP_SUDOERS"

HOME_DIR="$(getent passwd "$BOT_USER" | cut -d: -f6)"
AUTH_KEYS="$HOME_DIR/.ssh/authorized_keys"
install -d -m 0700 -o "$BOT_USER" -g "$BOT_USER" "$HOME_DIR/.ssh"
touch "$AUTH_KEYS"
KEY_BODY="$(echo "$PUBKEY" | awk '{print $2}')"
if grep -qF "$KEY_BODY" "$AUTH_KEYS"; then
    echo "[install] ключ уже есть в $AUTH_KEYS"
else
    echo "[install] add key → $AUTH_KEYS"
    echo "restrict,command=\"sudo -n -u $APP_USER $PYTHON3 $GATEWAY\" $PUBKEY" >>"$AUTH_KEYS"
fi
chown "$BOT_USER:$BOT_USER" "$AUTH_KEYS"
chmod 0600 "$AUTH_KEYS"

if command -v sshd >/dev/null 2>&1; then
    ALLOW="$(sshd -T 2>/dev/null | grep -iE '^(allowusers|allowgroups) ' || true)"
    if [[ -n "$ALLOW" ]]; then
        echo
        echo "WARNING: в sshd задан фильтр пользователей, $BOT_USER может не пустить:"
        echo "$ALLOW"
    fi
fi

echo
echo "[install] DONE."
echo
echo "Проверка с машины бота:"
echo "  ssh -i bot_key $BOT_USER@<server> email_disk_usage --pretty"
echo "  ssh -i bot_key $BOT_USER@<server> export_emails_json --since 1d --limit 5 --pretty"
echo "Должно отказать (код 2):"
echo "  ssh -i bot_key $BOT_USER@<server> shell"
