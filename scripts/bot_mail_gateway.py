#!/usr/bin/env python3
"""SSH-шлюз ботов к почте CRM: принудительная команда (forced command) ключа бота.

Бот подключается как отдельный пользователь, sshd вместо любой команды
запускает этот скрипт (через sudo от www-root, см.
install_bot_mail_access.sh), а запрошенная ботом команда приходит в
SSH_ORIGINAL_COMMAND. Скрипт пропускает только команды почты и только
опции из белого списка. Сравнение точное: argparse принимает сокращения
(``--set`` = ``--settings``), а ``--settings``/``--pythonpath`` позволили
бы выполнить произвольный код.

    ssh -i bot_key logist2-mail@<server> export_emails_json --after-id 123

Только стандартная библиотека — запускается системным python3.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
MAX_COMMAND_CHARS = 2000

ALLOWED_OPTIONS = {
    "export_emails_json": {
        "--after-id",
        "--since",
        "--sender",
        "--subject",
        "--direction",
        "--container",
        "--car-vin",
        "--transport-request",
        "--include-body",
        "--include-hidden",
        "--limit",
        "--pretty",
    },
    # --output не пропускаем: бот получает файл через stdout и не должен
    # писать в файловую систему сервера от имени www-root.
    "get_email_attachment": {"--info"},
    "email_disk_usage": {"--pretty"},
}
HELP_OPTIONS = {"-h", "--help"}


class Rejected(ValueError):
    pass


def parse_command(raw: str) -> list[str]:
    """Разбирает и проверяет команду бота; возвращает аргументы для manage.py."""
    if not raw or not raw.strip():
        raise Rejected("пустая команда; разрешены: " + ", ".join(sorted(ALLOWED_OPTIONS)))
    if len(raw) > MAX_COMMAND_CHARS:
        raise Rejected("слишком длинная команда")
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        raise Rejected(f"не удалось разобрать команду: {exc}") from None
    command, args = argv[0], argv[1:]
    if command not in ALLOWED_OPTIONS:
        raise Rejected(f"команда {command!r} запрещена; разрешены: " + ", ".join(sorted(ALLOWED_OPTIONS)))
    allowed = ALLOWED_OPTIONS[command] | HELP_OPTIONS
    for arg in args:
        if arg.startswith("-"):
            name = arg.split("=", 1)[0]
            if name not in allowed:
                raise Rejected(f"опция {name!r} запрещена для {command}")
    return [command, *args]


def main() -> int:
    try:
        argv = parse_command(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    except Rejected as exc:
        print(f"bot_mail_gateway: {exc}", file=sys.stderr)
        return 2

    python = PROJECT_DIR / ".venv" / "bin" / "python"
    env = {
        "PATH": f"{python.parent}:/usr/bin:/bin",
        "DJANGO_SETTINGS_MODULE": "logist2.settings.prod",
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "HOME": str(PROJECT_DIR),
    }
    os.chdir(PROJECT_DIR)
    os.execve(str(python), [str(python), "manage.py", *argv], env)
    return 0  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
