# `ENCRYPTION_KEY` — шифрование банковских токенов

## Зачем

Все credentials внешних интеграций хранятся в БД зашифрованными:

| Модель | Зашифрованные поля |
|---|---|
| `BankConnection` (Revolut/Paysera) | `client_id`, `refresh_token`, `access_token`, `jwt_assertion` |
| `SiteProConnection` | `username`, `password`, `api_key`, `private_key`, `access_token` |

Используется `cryptography.fernet.MultiFernet`. Ключ хранится в переменной окружения `ENCRYPTION_KEY` отдельно от `SECRET_KEY`.

**Почему отдельно от `SECRET_KEY`:** если злоумышленник получит `SECRET_KEY` (например, через утечку логов Sentry или git-history), он сможет подписывать токены сессий — это плохо, но он **не** должен иметь возможности расшифровать ваши OAuth-токены Revolut и слить деньги. Отдельный ключ изолирует радиус поражения.

## Где задать

`.env` на сервере (и локально, если работаете с настоящими данными):

```env
ENCRYPTION_KEY=<48+ символов>
ENCRYPTION_KEY_FALLBACKS=
ENCRYPTION_KEY_REQUIRED=False
```

Сгенерировать ключ:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Режимы

| Значение | Поведение |
|---|---|
| `ENCRYPTION_KEY` пуст, `ENCRYPTION_KEY_REQUIRED=False` | Fallback на `SECRET_KEY`. В проде — warning. Так работает старая инсталляция. |
| `ENCRYPTION_KEY` задан, `ENCRYPTION_KEY_REQUIRED=False` | Шифруем новым ключом; при расшифровке пробуем `ENCRYPTION_KEY` → fallbacks → `SECRET_KEY`. |
| `ENCRYPTION_KEY` задан, `ENCRYPTION_KEY_REQUIRED=True` | Fail-fast в проде, если ключ пуст / короче 32 / совпадает с `SECRET_KEY`. Включать после миграции. |

## Миграция со старой инсталляции (где `ENCRYPTION_KEY` не был задан)

Сейчас токены зашифрованы fallback'ом на `SECRET_KEY`. Чтобы перейти на отдельный ключ без потери данных:

### Шаг 1. Сгенерировать новый ключ

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Шаг 2. Положить в `.env` (на сервере)

```env
ENCRYPTION_KEY=<новый ключ>
# SECRET_KEY как fallback всегда подмешивается автоматически,
# но если у вас были другие исторические ключи — перечислите:
ENCRYPTION_KEY_FALLBACKS=
ENCRYPTION_KEY_REQUIRED=False
```

### Шаг 3. Перезапустить сервисы

```bash
systemctl restart logist2 daphne celery
```

С этого момента: новые токены шифруются новым ключом, старые читаются через fallback на `SECRET_KEY`.

### Шаг 4. Перешифровать существующие токены

```bash
# Сначала dry-run:
python manage.py rotate_encryption_key --dry-run
# Если устраивает — реальный прогон:
python manage.py rotate_encryption_key
```

Команда пройдёт по всем `BankConnection` и `SiteProConnection`, расшифрует каждое поле любым из известных ключей и зашифрует первым (новым) ключом.

### Шаг 5. Включить fail-fast

```env
ENCRYPTION_KEY_REQUIRED=True
```

Перезапустить сервисы. Если кто-то случайно сотрёт `ENCRYPTION_KEY` — settings упадут на старте, а не молча начнут работать с `SECRET_KEY` (что было бы catastrophic).

## Ротация ключа (плановая, раз в N месяцев)

1. Сгенерировать новый ключ.
2. В `.env`:
   ```env
   ENCRYPTION_KEY=<новый>
   ENCRYPTION_KEY_FALLBACKS=<старый, который только что был ENCRYPTION_KEY>
   ```
3. Перезапустить сервисы.
4. `python manage.py rotate_encryption_key`.
5. Удалить `ENCRYPTION_KEY_FALLBACKS`.
6. Перезапустить сервисы.

## Что НЕ делать

- НЕ менять `ENCRYPTION_KEY` без `ENCRYPTION_KEY_FALLBACKS` и без `rotate_encryption_key` — все токены станут нечитаемыми, придётся переподключать Revolut/site.pro заново.
- НЕ коммитить `ENCRYPTION_KEY` в git (`.env` уже в `.gitignore`).
- НЕ хранить `ENCRYPTION_KEY` равным `SECRET_KEY` — теряется весь смысл разделения.

## Связанные файлы

- `core/encryption.py` — хелперы `encrypt_value` / `decrypt_value` / `rotate_value`.
- `core/management/commands/rotate_encryption_key.py` — команда ротации.
- `core/tests/test_encryption.py` — тесты.
- `logist2/settings/base.py` — переменные `ENCRYPTION_KEY`, `ENCRYPTION_KEY_FALLBACKS`, `ENCRYPTION_KEY_REQUIRED` + fail-fast guard.
