# Bot CLI — Справочник команд

> Интерфейс командной строки для автоматизации операций через bot-токены.
> Каждая команда возвращает JSON со schema_version, принципалом, ролью и результатом.

## Аутентификация

```bash
python manage.py bot <command> --token %BOT_TOKEN%
```

Токены выдаются с привязкой к роли (`chief`, `accountant`, `neto`, `handover`, `parser`).

## Информация о машинах и документах

### `car` — Детали автомобиля

Возвращает информацию об автомобиле: VIN, марка/модель, клиент, контейнер, статус, склад, даты.

**Параметры:**
- `--vin <VIN>` — поиск по VIN (рекомендуется)
- `--car-id <id>` — поиск по ID машины

**Роли:** `chief`, `neto`, `handover`, `accountant`

**Пример:**
```bash
python manage.py bot car --token %BOT_TOKEN% --vin 3N1CP5BVXRL481232
```

**Результат:**
```json
{
  "schema_version": 1,
  "ok": true,
  "result": {
    "vin": "3N1CP5BVXRL481232",
    "id": 42,
    "brand": "Nissan Kicks",
    "year": 2018,
    "client": "CAROMOTO MOLDOVA",
    "container": "TCLU8824843",
    "status": "UNLOADED",
    "warehouse": "NETO Terminal",
    "weight_kg": "1200.50",
    "eta": "2026-10-01",
    "unload_date": "2026-10-15",
    "transfer_date": null,
    "has_title": true
  }
}
```

### `car-docs` — Список документов машины

Возвращает список документов машины (TITLE и другие).

**Параметры:**
- `--vin <VIN>` — поиск по VIN
- `--car-id <id>` — поиск по ID машины

**Роли:** `chief`, `neto`, `handover`, `accountant`

**Пример:**
```bash
python manage.py bot car-docs --token %BOT_TOKEN% --vin 3N1CP5BVXRL481232
```

**Результат:**
```json
{
  "schema_version": 1,
  "ok": true,
  "result": {
    "vin": "3N1CP5BVXRL481232",
    "car_id": 42,
    "documents": [
      {
        "id": "car_42_title",
        "type": "TITLE",
        "filename": "nissan_kicks_title.pdf",
        "size": 245678,
        "uploaded_date": null
      }
    ]
  }
}
```

### `download-car-doc` — Скачать документ машины

Копирует документ машины в temp-директорию бота (`bot_tmp/<principal_name>/`).

**Параметры:**
- `--doc-id <id>` — ID документа из `car-docs` (формат: `car_42_title`)
- **или** `--vin <VIN> --type TITLE` — поиск по VIN + тип документа
- `--out <filename>` (опционально) — имя выходного файла (только внутри tempdir)

**Роли:** `chief`, `neto`, `handover`

**Примеры:**
```bash
# По ID документа
python manage.py bot download-car-doc --token %BOT_TOKEN% --doc-id car_42_title

# По VIN + тип
python manage.py bot download-car-doc --token %BOT_TOKEN% --vin 3N1CP5BVXRL481232 --type TITLE

# С кастомным именем файла
python manage.py bot download-car-doc --token %BOT_TOKEN% --vin 3N1CP5BVXRL481232 --type TITLE --out my_title.pdf
```

**Результат:**
```json
{
  "schema_version": 1,
  "ok": true,
  "result": {
    "filename": "nissan_kicks_title.pdf",
    "path": "/var/www/.../media/bot_tmp/neto-bot/nissan_kicks_title.pdf",
    "size": 245678
  }
}
```

После этого файл можно скопировать по SCP или использовать локально.

**Безопасность:**
- `--out` путь проверяется — разрешены только пути внутри temp-директории бота
- Попытка записи в `/etc/passwd` или другие системные пути будет отклонена

## Использование с re-export (без TR)

Для реэкспорта (например, в Украину) нет Transit Request, но можно получить title через эти команды:

```bash
# 1. Проверяем информацию о машине
python manage.py bot car --token %NETO_TOKEN% --vin 3N1CP5BVXRL481232

# 2. Смотрим какие документы есть
python manage.py bot car-docs --token %NETO_TOKEN% --vin 3N1CP5BVXRL481232

# 3. Скачиваем title
python manage.py bot download-car-doc --token %NETO_TOKEN% --vin 3N1CP5BVXRL481232 --type TITLE

# 4. Копируем на свой сервер
scp /path/from/result bot-server:/destination/
```

## Другие команды

- `capabilities` — список доступных команд для роли
- `dictionary` — справочник терминов и статусов
- `tempdir` — путь к temp-директории бота
- `emails` — список писем контейнеров
- `download-attachment` — скачать вложение из email
- `neto-bundle` — сформировать ZIP-пакет документов для Transit Request
- ...и другие (см. `python manage.py bot capabilities --token %BOT_TOKEN%`)

## Ошибки

При ошибке команда возвращает `"ok": false` и поле `"error"`:

```json
{
  "schema_version": 1,
  "ok": false,
  "error": "Машина не найдена (VIN=INVALID, id=None)."
}
```

Exit code при ошибке будет ≠ 0.
