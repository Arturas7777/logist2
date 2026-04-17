# Debug / ad-hoc скрипты

Одноразовые скрипты для ручных проверок и разовых операций.
**Не импортируются из кода**, не запускаются из Celery/Cron.

Все файлы здесь игнорируются git (`_*.py` в `.gitignore`) — использовать локально.

## Запуск

```powershell
.venv\Scripts\activate
$env:DJANGO_SETTINGS_MODULE="logist2.settings.dev"
python scripts/debug/_server_check.py
```

## Назначение скриптов

| Скрипт | Что делает |
|---|---|
| `_server_check.py` | Быстрая проверка: количество банк-транзакций, подключений, инвойсов, клиентов |
| `_check_invoice.py` | Проверка конкретного инвойса (статус, позиции, транзакции) |
| `_check_fact.py` | Проверка FACT-инвойсов |
| `_check_fact_items.py` | Позиции FACT-инвойсов |
| `_check_fanu.py` | Проверка конкретного контрагента Fanu |
| `_sync_fanu.py` | Ручная синхронизация Fanu |
| `_check_cats.py` | Проверка категорий расходов |
| `_cleanup_av.py` | Массовая очистка AVBLC-инвойсов |
| `_gen_malibu_spec.py` | Генерация спецификации для Malibu |

Если скрипт перестал быть актуален — удаляй. Если нужен постоянно — переноси в `core/management/commands/`.
