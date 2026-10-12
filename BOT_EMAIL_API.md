# Bot Email API — Read-Only доступ к email из БД

## Проблема

AI-боты читали почту напрямую через Gmail API и вместе с CRM-синком исчерпывали quota Google (HTTP 429 "Total Query Cost"). Это блокировало даже отправку писем владельцем.

## Решение

Боты теперь читают почту только из БД CRM (ContainerEmail), а не из Gmail. CRM-синк остался единственным потребителем Gmail API.

---

## Для ботов: Как использовать

Подключитесь к серверу по SSH и запускайте management-команды:

### 1. Чтение новых писем

```bash
ssh root@176.118.198.78
cd /var/www/www-root/data/www/logist2

# Новые письма за последние 2 часа (JSON на stdout)
python manage.py export_emails_json --since=2h

# Фильтр по отправителю
python manage.py export_emails_json --since=24h --sender=maersk.com

# Фильтр по контейнеру
python manage.py export_emails_json --container=MSKU9754460

# Фильтр по VIN машины
python manage.py export_emails_json --car-vin=5YJSA1E14HF123456

# Фильтр по заявке на автовоз
python manage.py export_emails_json --transport-request=TR-20261012-001

# С полным телом письма (body_text)
python manage.py export_emails_json --since=1h --include-body

# Лимит результатов (default: 100)
python manage.py export_emails_json --since=12h --limit=50

# Компактный JSON (без отступов)
python manage.py export_emails_json --since=2h --compact
```

#### Формат вывода

```json
{
  "count": 2,
  "timestamp": "2026-10-12T14:30:00Z",
  "emails": [
    {
      "id": 12345,
      "gmail_id": "18bcd...",
      "thread_id": "18bcd...",
      "message_id": "<abc@maersk.com>",
      "direction": "INCOMING",
      "from": "notifications@maersk.com",
      "to": "lithuania@caromoto.com",
      "cc": "",
      "subject": "Container MSKU9754460 - Gate Out",
      "received_at": "2026-10-12T12:15:00Z",
      "snippet": "Your container has been released...",
      "attachments": [
        {
          "filename": "release.pdf",
          "size": 123456,
          "content_type": "application/pdf",
          "attachment_id": "ANGjd...",
          "is_inline": false,
          "available": true
        }
      ],
      "linked_containers": ["MSKU9754460"],
      "linked_cars": ["5YJSA1E14HF123456"],
      "linked_transport_requests": []
    }
  ]
}
```

**Примечания:**
- `body_text` включается только с флагом `--include-body`
- `body_html` не возвращается (очищается после EMAIL_RETENTION_DAYS)
- `snippet` — короткое превью от Gmail (~200 символов)
- `available: true` в attachments означает что файл закэширован (можно скачать)

### 2. Скачивание вложений

```bash
# Скачать вложение по индексу (0-based)
python manage.py get_email_attachment <email_id> <attachment_index> > file.pdf

# Пример: первое вложение письма #12345
python manage.py get_email_attachment 12345 0 > release.pdf

# Только метаданные (без скачивания файла)
python manage.py get_email_attachment 12345 0 --info-only
```

**Как работает кэширование:**
- Если файл уже скачан из Gmail — отдаётся мгновенно из кэша
- Если нет — скачивается один раз из Gmail и сохраняется
- После EMAIL_RETENTION_DAYS (default: 90) файлы удаляются с диска
- Метаданные (filename, size, content_type) остаются навсегда

---

## Retention policy (очистка старых данных)

Чтобы контролировать рост диска (~18 GB свободно из 50), CRM автоматически очищает старые данные:

- **body_html** старше 90 дней → удаляется (остаётся `body_text` + `snippet`)
- **Закэшированные вложения** старше 90 дней → удаляются с диска
- **Метаданные** (from/to/subject/received_at/links) остаются навсегда

Настройка через `.env`:
```bash
EMAIL_RETENTION_DAYS=90  # default
```

Задача `cleanup-old-email-data-daily` запускается в 04:50 UTC (после ночного бэкапа).

---

## Улучшения синка Gmail

### 1. Exponential backoff при 429

CRM-синк теперь обрабатывает 429 (Rate Limit Exceeded) с автоматическим retry:
- 1-я попытка: пауза 2 секунды
- 2-я попытка: пауза 4 секунды
- 3-я попытка: пауза 8 секунд

### 2. Инкрементальный sync уже был

Синк уже использовал `users.history.list` для инкрементальной загрузки (только новые письма с момента `last_history_id`). Это эффективно:
- Полный re-sync (`messages.list`) — только при первом запуске или если history протухла (>7 дней)
- Иначе — только новые письма

**Гипотеза подтверждена:** CRM-синк и боты делили одну квоту Gmail. Теперь только CRM обращается к Gmail (каждые 2 минуты), боты читают из БД → quota в 10-20 раз меньше.

---

## Disk usage

### Проверка текущего использования

```python
from core.tasks_email_cleanup import report_email_disk_usage

report = report_email_disk_usage()
# {
#   "total_emails": 12500,
#   "emails_with_attachments": 3200,
#   "cached_attachments_count": 4500,
#   "cached_attachments_size_mb": 1250.5
# }
```

### Ручной запуск cleanup

```bash
# В Django shell
python manage.py shell
>>> from core.tasks_email_cleanup import cleanup_old_email_data
>>> report = cleanup_old_email_data()
>>> print(report)
```

---

## Backward compatibility

- Existing code не тронут — все существующие функции email работают как раньше
- Добавлены новые management-команды (не API endpoints) — боты подключаются через SSH
- Celery beat schedule обновлён (новая задача cleanup)
- Миграций БД нет — используем существующие поля

---

## Tests

Добавлены тесты:
- `core/tests/test_export_emails_json.py` — фильтры, форматирование, exclusions
- `core/tests/test_get_email_attachment.py` — кэширование, fetch from Gmail
- `core/tests/test_email_cleanup.py` — retention policy, disk usage

Запуск:
```bash
pytest core/tests/test_export_emails_json.py
pytest core/tests/test_get_email_attachment.py
pytest core/tests/test_email_cleanup.py
```
