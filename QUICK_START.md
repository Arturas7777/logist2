# 🚀 Быстрый старт проекта Logist2

## 🎯 Цель
Запустить проект Logist2 за 5 минут с минимальными настройками.

## 📋 Предварительные требования
- ✅ Python 3.8+ установлен
- ✅ Виртуальное окружение создано (`.venv` папка)
- ✅ Зависимости установлены (`pip install -r requirements.txt`)

## 🗄️ Настройка базы данных

### Вариант 1: PostgreSQL (рекомендуется)

1. **Установите PostgreSQL:**
   - Скачайте с [postgresql.org](https://www.postgresql.org/download/windows/)
   - При установке используйте пароль: `postgres`

2. **Создайте файл .env:**
   ```bash
   python create_env.py
   ```

3. **Или создайте вручную:**
   - Создайте файл `.env` в корне проекта
   - Добавьте содержимое из `env_template.txt`

### Вариант 2: SQLite (для быстрой разработки)
```bash
python start_sqlite.py
```

## 🚀 Запуск проекта

### Способ 1: Автоматический (рекомендуется)
```bash
# PowerShell
.\START_ME.ps1

# Command Prompt
START_ME.bat
```

### Способ 2: Python скрипт
```bash
# PostgreSQL
python start_simple.py

# SQLite
python start_sqlite.py
```

### Способ 3: Ручной запуск
```bash
# Активация окружения
.\.venv\Scripts\Activate.ps1

# Проверка настроек
python manage.py check

# Миграции
python manage.py migrate

# Суперпользователь
python manage.py createsuperuser

# Запуск сервера
python manage.py runserver
```

## 🌐 Доступ к проекту

После успешного запуска:

- **Главная страница:** http://127.0.0.1:8000/
- **Админка Django:** http://127.0.0.1:8000/admin/
- **Логин:** admin
- **Пароль:** admin123

## 🔧 Решение проблем

### Проблема: "Выполнение сценариев отключено"
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Проблема: "PostgreSQL connection failed"
1. Убедитесь, что PostgreSQL запущен
2. Проверьте файл `.env`
3. Создайте базу данных `logist2`

### Проблема: "Port 8000 is already in use"
```bash
# Найти процесс
netstat -ano | findstr :8000

# Остановить процесс
taskkill /PID <PID> /F
```

## 📚 Дополнительная документация

- `POSTGRESQL_SETUP.md` - подробная настройка PostgreSQL
- `SOLUTION_GUIDE.md` - решение всех проблем
- `README.md` - основная документация

## 🆘 Быстрая помощь

Если ничего не работает:

1. **Проверьте PostgreSQL:**
   ```bash
   net start | findstr postgresql
   ```

2. **Проверьте зависимости:**
   ```bash
   pip list | findstr psycopg2
   ```

3. **Проверьте настройки:**
   ```bash
   python -c "import os; print(os.environ.get('DB_NAME'))"
   ```

4. **Используйте SQLite для тестирования:**
   ```bash
   python start_sqlite.py
   ```

---

**💡 Совет:** Используйте `START_ME.ps1` или `START_ME.bat` - они автоматически решают большинство проблем!
