"""
Одноразовый скрипт для получения OAuth2 refresh_token для Gmail API.

Запускается локально один раз — открывает браузер, вы проходите Google-consent
под корпоративной учёткой, скрипт сохраняет refresh_token в файл, который потом
надо положить в .env на сервере.

Использование:
    python scripts/get_gmail_refresh_token.py <path_to_client_secret.json>

Пример:
    python scripts/get_gmail_refresh_token.py C:\\Users\\art-f\\gmail-oauth\\client_secret.json

Результат:
    - refresh_token выводится в консоль
    - сохраняется в <client_secret_dir>/token.json для справки
"""

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

# gmail.modify = readonly + изменение лейблов (нужно для синхронизации
# UNREAD при пометке писем прочитанными в карточке контейнера).
# При смене scope нужно перезапустить этот скрипт — refresh_token обновится.
SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.send',
]


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/get_gmail_refresh_token.py <path_to_client_secret.json>")
        return 1

    client_secret_path = Path(sys.argv[1]).expanduser().resolve()
    if not client_secret_path.exists():
        print(f"ERROR: файл не найден: {client_secret_path}")
        return 1

    print(f"Использую client_secret: {client_secret_path}")
    print(f"Запрашиваю scopes: {SCOPES}")
    print()
    print("Сейчас откроется браузер. Войдите под КОРПОРАТИВНОЙ учёткой (caromoto.com)")
    print("и подтвердите доступ Logist2 Email Sync к Gmail.")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)

    creds = flow.run_local_server(
        port=0,
        access_type='offline',
        prompt='consent',
        open_browser=True,
        success_message=(
            'Готово! Можно закрыть вкладку и вернуться в терминал.'
        ),
    )

    if not creds.refresh_token:
        print()
        print("ВНИМАНИЕ: refresh_token пустой. Обычно это значит, что вы уже проходили")
        print("consent раньше и Google не выдал новый токен. Удалите приложение")
        print("в https://myaccount.google.com/permissions и запустите скрипт снова")
        print("с флагом prompt='consent'.")
        return 1

    token_path = client_secret_path.parent / 'token.json'
    token_data = {
        'refresh_token': creds.refresh_token,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'token_uri': creds.token_uri,
        'scopes': creds.scopes,
        'account': creds.id_token.get('email') if creds.id_token else None,
    }
    token_path.write_text(json.dumps(token_data, indent=2, ensure_ascii=False), encoding='utf-8')

    print()
    print("=" * 70)
    print("УСПЕХ. REFRESH TOKEN ПОЛУЧЕН:")
    print("=" * 70)
    print()
    print(creds.refresh_token)
    print()
    print("=" * 70)
    print(f"Полный токен сохранён в: {token_path}")
    print()
    print("Следующий шаг — добавить в .env на сервере:")
    print()
    print(f"    GMAIL_CLIENT_ID={creds.client_id}")
    print(f"    GMAIL_CLIENT_SECRET=<из client_secret.json>")
    print(f"    GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print()
    print("ВНИМАНИЕ: НЕ коммитьте client_secret.json и token.json в git!")
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
