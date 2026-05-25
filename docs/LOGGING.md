# Логирование — Logist2

> M7 (ROADMAP_2026-05): structured logging + RotatingFileHandler + JSON.

## TL;DR

```bash
# .env на сервере
LOG_FORMAT=json
LOG_LEVEL=INFO
LOG_DIR=/var/log/logist2
```

После этого:

- `/var/log/logist2/app.log` пишется в формате JSON, ротация — 50 MB × 10 файлов
  (≈500 MB на диск максимум);
- к каждой записи лога автоматически добавляются поля `request_id`,
  `user_id`, `path`, `method` (см. ниже);
- консоль остаётся читаемой (текстовый формат, тот же контекст в `[...]`);
- в Sentry уходят только события уровня `ERROR+`, а `INFO/WARNING`
  остаются как breadcrumbs.

## Архитектура

```
HTTP request
    │
    ▼
core.middleware_logging.RequestContextMiddleware
    ├─ X-Request-ID  (из заголовка nginx или uuid4)
    ├─ user_id        (из request.user)
    ├─ path           (request.path)
    └─ method         (request.method)
        │
        ▼ contextvars.ContextVar
        │
        ▼
logging.getLogger("core.something").info("payment received", extra={...})
    │
    ▼
RequestContextFilter ─── добавляет request_id/user_id/path/method к record
    │
    ├─► StreamHandler (console, text, всегда)
    └─► RotatingFileHandler (file, JSON, только если LOG_DIR задан)
            │
            ▼
        /var/log/logist2/app.log
```

`contextvars` корректно работает и в async-коде (Channels / Daphne) и
в thread-pool gunicorn'а — каждый запрос получает свой набор переменных.

## Env-переменные

| Переменная | По умолчанию | Описание |
|---|---|---|
| `LOG_FORMAT` | `verbose` | `verbose` (текст) или `json` для console-handler'а. |
| `LOG_LEVEL` | `INFO` (если `DEBUG=True`) / `WARNING` | Уровень `core.*` логгеров и file-handler'а. |
| `LOG_DIR` | _(не задана)_ | Если указана — добавляется RotatingFileHandler. На проде → `/var/log/logist2`. |
| `LOG_MAX_BYTES` | `52428800` (50 MB) | Размер одного файла лога. |
| `LOG_BACKUP_COUNT` | `10` | Сколько ротационных файлов хранить. |

## Использование в коде

### Обычное логирование (используем готовый контекст)

```python
import logging

logger = logging.getLogger(__name__)


def pay_invoice(invoice):
    logger.info("paying invoice %s for %s EUR", invoice.number, invoice.total)
    # request_id / user_id / path подцепятся из contextvars автоматически
```

### Дополнительные поля (extra)

```python
logger.info(
    "payment received",
    extra={"invoice_id": invoice.id, "amount": str(invoice.total), "domain": "billing"},
)
```

В JSON-формате `extra`-поля попадут в верхний уровень JSON-объекта:

```json
{"ts": "2026-05-25 21:34:18,001", "level": "INFO", "logger": "core.billing",
 "message": "payment received", "request_id": "ab12cd34", "user_id": 42,
 "path": "/admin/core/newinvoice/123/change/", "method": "POST",
 "invoice_id": 123, "amount": "1500.00", "domain": "billing"}
```

### Контекст в Celery / management-командах

В фоновых задачах HTTP-запроса нет, поэтому `request_id` будет `"-"`.
Если задача порождена из веб-запроса и хочется сохранить корреляцию —
передаём явно:

```python
from core.middleware_logging import get_request_id, set_request_id

# producer (внутри HTTP-запроса)
my_task.delay(rid=get_request_id(), invoice_id=invoice.id)

# consumer
@shared_task
def my_task(rid, invoice_id):
    set_request_id(rid)
    logger.info("started invoice %s", invoice_id)
```

## Просмотр логов на сервере

```bash
# tail в реальном времени, человекочитаемо
tail -f /var/log/logist2/app.log | jq -r '"\(.ts) \(.level) \(.logger) — \(.message)"'

# отфильтровать все события одного запроса
grep -F '"request_id": "abc-123"' /var/log/logist2/app.log | jq .

# top-N логгеров за последний час
jq -r .logger /var/log/logist2/app.log | sort | uniq -c | sort -rn | head
```

## Связка с Sentry

`LoggingIntegration` настроен так:

- `level=logging.INFO` → все `INFO+` записи становятся **breadcrumbs**
  (видны на странице события в Sentry);
- `event_level=logging.ERROR` → только `ERROR/CRITICAL` создают
  отдельные events.

Соответственно `logger.warning(...)` НЕ создаёт событие в Sentry,
но сохранится в breadcrumbs и попадёт в файловый лог.

## Что НЕ делаем

- Не используем `print()` — он не попадает в `RequestContextFilter`
  и не уходит ни в файл, ни в Sentry.
- Не пишем чувствительные данные (пароли, токены, CVV) даже в `extra` —
  они окажутся в JSON-логе. Для тайн используем `logger.info("…", extra={"masked": True})`
  или вообще не логируем.
- Не создаём `FileHandler` руками в коде — только централизованно через
  `LOGGING` в `base.py`.

## Откат

Если что-то пошло не так в проде:

```bash
# в .env
LOG_DIR=               # пустая строка → file-handler не подключается
LOG_FORMAT=verbose     # консоль обратно в текст
sudo systemctl restart gunicorn daphne celery celerybeat
```

Логи перестанут писаться в файл, останется только journald/console.
