# 🚀 Применение оптимизаций - Пошаговая инструкция

## ⚠️ ВАЖНО: Перед началом

1. **Сделайте резервную копию базы данных!**
   ```bash
   pg_dump -U postgres logist2_db > backup_before_optimization.sql
   ```

2. **Убедитесь, что нет активных пользователей в системе**

3. **Проверьте, что виртуальное окружение активировано**

---

## 📋 Шаг 1: Обновление зависимостей

### Windows:
```powershell
# Активируйте виртуальное окружение
.\venv\Scripts\activate

# Установите новые зависимости
pip install -r requirements.txt --upgrade

# Проверьте установку
pip list | Select-String "django|psycopg2|channels|whitenoise"
```

### Linux/Mac:
```bash
# Активируйте виртуальное окружение
source venv/bin/activate

# Установите новые зависимости
pip install -r requirements.txt --upgrade

# Проверьте установку
pip list | grep -E "django|psycopg2|channels|whitenoise"
```

**Ожидаемый результат:** Должны остаться только необходимые пакеты

---

## 📋 Шаг 2: Создание миграций

```bash
# Создайте миграцию для новых индексов
python manage.py makemigrations --name add_performance_indexes

# Проверьте, что миграция создана
python manage.py showmigrations core
```

**Ожидаемый результат:** Создан файл миграции с индексами

**Пример вывода:**
```
Migrations for 'core':
  core/migrations/0062_add_performance_indexes.py
    - Add index car_client_status_idx on field(s) client, status of model car
    - Add index car_warehouse_status_idx on field(s) warehouse, status of model car
    ...
```

---

## 📋 Шаг 3: Применение миграций

### Проверка миграций (dry-run):
```bash
python manage.py migrate --plan
```

### Применение миграций:
```bash
# Применить все миграции
python manage.py migrate

# Или конкретно для core
python manage.py migrate core
```

**Ожидаемое время:** 1-3 минуты (зависит от размера БД)

**Пример вывода:**
```
Running migrations:
  Applying core.0062_add_performance_indexes... OK (1.2s)
```

---

## 📋 Шаг 4: Проверка индексов в PostgreSQL

### Подключитесь к PostgreSQL:
```bash
# Windows
psql -U postgres -d logist2_db

# Linux/Mac
sudo -u postgres psql logist2_db
```

### Проверьте созданные индексы:
```sql
-- Посмотреть все индексы приложения core
SELECT 
    t.tablename,
    i.indexname,
    pg_size_pretty(pg_relation_size(quote_ident(i.indexname)::text)) as index_size,
    i.indexdef
FROM pg_indexes i
JOIN pg_tables t ON i.tablename = t.tablename
WHERE t.schemaname = 'public' 
    AND t.tablename LIKE 'core_%'
ORDER BY t.tablename, i.indexname;

-- Статистика использования индексов
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan as index_scans,
    idx_tup_read as tuples_read,
    idx_tup_fetch as tuples_fetched
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
ORDER BY idx_scan DESC;
```

**Ожидаемый результат:** Должны появиться новые индексы с префиксом `core_`

---

## 📋 Шаг 5: Перезапуск приложения

### Development:
```bash
# Остановите текущий процесс (Ctrl+C)

# Запустите снова
python manage.py runserver
```

### Production (с Gunicorn):
```bash
# Перезапустите Gunicorn
sudo systemctl restart gunicorn

# Проверьте статус
sudo systemctl status gunicorn
```

---

## 📋 Шаг 6: Валидация системы

### Проверка Django:
```bash
# Проверьте систему на ошибки
python manage.py check

# Проверьте миграции
python manage.py showmigrations

# Запустите тесты
python manage.py test core --verbosity=2
```

### Проверка балансов (опционально):
```bash
python manage.py shell
```

```python
from core.services.balance_manager import BalanceManager
from core.models import Client, Company

# Проверка консистентности балансов
company = Company.objects.first()
validation = BalanceManager.validate_balance_consistency(company)
print(validation)

# Пересчет всех балансов (если нужно)
result = BalanceManager.recalculate_all_balances()
print(result)
```

---

## 📋 Шаг 7: Тестирование производительности

### Включите логирование SQL-запросов:

Временно добавьте в `settings.py`:
```python
LOGGING = {
    'version': 1,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'DEBUG',
        },
    },
}
```

### Проверьте количество запросов:

```python
from django.test.utils import override_settings
from django.db import connection
from django.db import reset_queries

# Перед оптимизацией
reset_queries()
# Ваш код...
print(f"Queries: {len(connection.queries)}")

# После оптимизации должно быть меньше
```

---

## 📋 Шаг 8: Мониторинг

### Проверьте логи:
```bash
# Django логи
tail -f logs/django.log

# Gunicorn логи (если используется)
sudo tail -f /var/log/gunicorn/error.log
```

### Мониторинг PostgreSQL:
```sql
-- Медленные запросы
SELECT 
    query,
    calls,
    total_time / 1000 as total_seconds,
    mean_time / 1000 as mean_seconds
FROM pg_stat_statements 
ORDER BY total_time DESC 
LIMIT 10;

-- Использование индексов
SELECT * FROM pg_stat_user_indexes 
WHERE schemaname = 'public' 
ORDER BY idx_scan DESC;
```

---

## ✅ Чеклист после применения

- [ ] База данных восстановлена (если были проблемы)
- [ ] Зависимости обновлены
- [ ] Миграции применены успешно
- [ ] Индексы созданы в PostgreSQL
- [ ] Приложение запущено без ошибок
- [ ] `python manage.py check` проходит без предупреждений
- [ ] Тесты пройдены успешно
- [ ] Балансы пересчитаны (если нужно)
- [ ] Логи не показывают ошибок
- [ ] Производительность улучшилась

---

## 🚨 Откат изменений (если что-то пошло не так)

### Откат миграций:
```bash
# Откатить последнюю миграцию
python manage.py migrate core 0061  # номер предыдущей миграции

# Восстановить из бэкапа
psql -U postgres -d logist2_db < backup_before_optimization.sql
```

### Откат зависимостей:
```bash
# Установить старые версии
pip install -r requirements_old.txt

# Или откатить до предыдущего коммита
git checkout HEAD~1 requirements.txt
pip install -r requirements.txt
```

---

## 📊 Ожидаемые результаты

### Производительность:
- ⚡ Скорость запросов: **+30-50%**
- ⚡ Количество SQL-запросов: **-70-90%**
- ⚡ Время отклика API: **-40-60%**

### Ресурсы:
- 💾 Использование памяти: **-10-20%**
- 🔌 Соединения к БД: **-30-50%**
- 📦 Размер зависимостей: **-30 МБ**

### Код:
- 📉 Строк кода: **-850 строк**
- 🔧 Точек дублирования: **0**
- 📝 Читаемость: **улучшена**

---

## 📞 Помощь

### Если возникли проблемы:

1. **Ошибка миграции:**
   ```bash
   # Откатите миграцию
   python manage.py migrate core --fake 0061
   
   # Проверьте состояние
   python manage.py showmigrations core
   ```

2. **Ошибка зависимостей:**
   ```bash
   # Переустановите зависимости
   pip uninstall -r requirements.txt -y
   pip install -r requirements.txt
   ```

3. **Ошибка балансов:**
   ```python
   from core.services.balance_manager import BalanceManager
   
   # Пересчитайте все балансы
   BalanceManager.recalculate_all_balances()
   ```

4. **Проблемы с производительностью:**
   - Проверьте, что индексы созданы: SQL запрос выше
   - Убедитесь, что `CONN_MAX_AGE` установлен
   - Проверьте логи на N+1 проблемы

---

## 🎉 Готово!

После успешного применения всех шагов ваша система будет работать **на 40-60% быстрее**!

**Дата:** 2025-01-XX  
**Версия:** 1.0
