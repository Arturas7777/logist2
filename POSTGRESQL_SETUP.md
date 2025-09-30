# Настройка PostgreSQL для проекта Logist2

## 🎯 Цель
Этот документ поможет настроить PostgreSQL для локальной разработки проекта Logist2.

## 📋 Требования
- Windows 10/11
- Python 3.8+
- PostgreSQL 12+

## 🚀 Установка PostgreSQL

### 1. Скачивание и установка
1. Перейдите на [официальный сайт PostgreSQL](https://www.postgresql.org/download/windows/)
2. Скачайте установщик для Windows
3. Запустите установщик от имени администратора
4. Следуйте инструкциям установщика

### 2. Настройка при установке
- **Пароль для пользователя postgres**: `postgres` (или запомните свой)
- **Порт**: `5432` (по умолчанию)
- **Локаль**: `Default locale`
- **Stack Builder**: можно не устанавливать

## 🔧 Настройка базы данных

### 1. Создание базы данных
После установки PostgreSQL:

1. Откройте **pgAdmin** (устанавливается вместе с PostgreSQL)
2. Подключитесь к серверу (пароль: `postgres`)
3. Правый клик на **Databases** → **Create** → **Database**
4. Имя: `logist2`
5. Нажмите **Save**

### 2. Альтернативный способ (через командную строку)
```bash
# Подключение к PostgreSQL
psql -U postgres -h localhost

# Создание базы данных
CREATE DATABASE logist2;

# Проверка
\l

# Выход
\q
```

## 📝 Настройка переменных окружения

### 1. Создание файла .env
В корневой папке проекта создайте файл `.env`:

```env
# Django Settings
SECRET_KEY=django-insecure-development-key-change-in-production
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# PostgreSQL Database
DB_NAME=logist2
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432

# Redis for Channels
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
```

### 2. Важные моменты
- **DB_PASSWORD**: используйте пароль, который вы задали при установке PostgreSQL
- Если пароль отличается от `postgres`, измените его в файле `.env`

## 🐍 Установка Python зависимостей

### 1. Активация виртуального окружения
```bash
# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# Windows (Command Prompt)
.venv\Scripts\activate.bat
```

### 2. Установка PostgreSQL драйвера
```bash
pip install psycopg2-binary
```

## 🚀 Запуск проекта

### 1. Автоматический запуск
```bash
# PowerShell
.\START_ME.ps1

# Command Prompt
START_ME.bat

# Python
python start_simple.py
```

### 2. Ручной запуск
```bash
# Проверка настроек
python manage.py check

# Применение миграций
python manage.py migrate

# Создание суперпользователя
python manage.py createsuperuser

# Запуск сервера
python manage.py runserver
```

## 🔍 Диагностика проблем

### 1. Ошибка подключения к PostgreSQL
```
psycopg2.OperationalError: connection to server at "localhost" (127.0.0.1), port 5432 failed
```

**Решение:**
- Убедитесь, что PostgreSQL запущен
- Проверьте порт в настройках
- Проверьте пароль пользователя postgres

### 2. Ошибка кодировки
```
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc2
```

**Решение:**
- Проверьте настройки кодировки в PostgreSQL
- Убедитесь, что в пароле нет специальных символов
- Попробуйте использовать простой пароль (только буквы и цифры)

### 3. База данных не существует
```
psycopg2.OperationalError: database "logist2" does not exist
```

**Решение:**
- Создайте базу данных `logist2` в pgAdmin
- Или используйте автоматическое создание через скрипт `start_simple.py`

## 🛠️ Полезные команды PostgreSQL

### 1. Подключение к базе
```bash
psql -U postgres -d logist2 -h localhost
```

### 2. Просмотр таблиц
```sql
\dt
```

### 3. Просмотр структуры таблицы
```sql
\d table_name
```

### 4. Выход
```sql
\q
```

## 📚 Дополнительные ресурсы

- [Документация PostgreSQL](https://www.postgresql.org/docs/)
- [Django + PostgreSQL](https://docs.djangoproject.com/en/stable/ref/databases/#postgresql-notes)
- [psycopg2 документация](https://www.psycopg.org/docs/)

## 🆘 Получение помощи

Если у вас возникли проблемы:

1. Проверьте логи PostgreSQL
2. Убедитесь, что все зависимости установлены
3. Проверьте настройки в файле `.env`
4. Попробуйте перезапустить PostgreSQL сервис

**Команда для перезапуска PostgreSQL:**
```bash
# Остановка
net stop postgresql-x64-15

# Запуск
net start postgresql-x64-15
```

*Примечание: версия может отличаться в зависимости от установленной версии PostgreSQL*


