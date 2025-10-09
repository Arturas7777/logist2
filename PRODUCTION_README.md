# 🌐 Caromoto Lithuania - Развертывание на Production

## 📦 Файлы для развертывания

В проекте есть все необходимые файлы для развертывания на VPS:

| Файл | Описание |
|------|----------|
| `DEPLOYMENT.md` | 📖 Полная подробная инструкция по развертыванию |
| `DEPLOY_CHECKLIST.md` | ✅ Краткий чек-лист для быстрого развертывания |
| `requirements.txt` | 📦 Список Python пакетов с версиями |
| `env.example` | ⚙️ Пример файла с переменными окружения |
| `nginx_caromoto.conf` | 🌐 Конфигурация Nginx |
| `gunicorn_config.py` | 🦄 Конфигурация Gunicorn (WSGI сервер) |
| `caromoto-lt.service` | 🔧 Systemd сервис для автозапуска |
| `deploy.sh` | 🚀 Скрипт автоматического обновления сайта |
| `server_setup.sh` | 🛠️ Скрипт первичной настройки сервера |

## 🎯 Быстрый старт

### Вариант 1: Для опытных пользователей
Следуйте **DEPLOY_CHECKLIST.md** - это краткий пошаговый чек-лист.

### Вариант 2: Для подробного изучения
Следуйте **DEPLOYMENT.md** - это полная инструкция с пояснениями.

## 🔑 Что нужно подготовить ДО развертывания

1. **VPS сервер** (Ubuntu 20.04+, минимум 2GB RAM)
2. **Домен** caromoto-lt.com с настроенной A-записью на IP сервера
3. **Пароли**:
   - Для PostgreSQL базы данных
   - Для Django SECRET_KEY (можно сгенерировать)
4. **API ключи** (опционально):
   - OpenAI API key (для AI чата)
   - Email настройки (для уведомлений)

## 📊 Архитектура продакшн окружения

```
Интернет
    ↓
Nginx (порт 443, HTTPS)
    ↓
Gunicorn (порт 8000)
    ↓
Django Application
    ↓
PostgreSQL Database
```

## 🔒 Безопасность

- ✅ HTTPS с Let's Encrypt сертификатами
- ✅ Firewall (UFW)
- ✅ Безопасные заголовки (HSTS, X-Frame-Options, etc.)
- ✅ Отключен DEBUG режим
- ✅ Секретные данные в .env файле (не в Git)

## 📝 Основные команды на сервере

### Обновление сайта после изменений
```bash
cd /var/www/caromoto-lt
./deploy.sh
```

### Просмотр логов
```bash
# Django логи
journalctl -u caromoto-lt -f

# Nginx логи
tail -f /var/log/nginx/caromoto-lt-error.log
```

### Перезапуск сервисов
```bash
sudo systemctl restart caromoto-lt  # Django
sudo systemctl reload nginx          # Nginx
```

### Управление базой данных
```bash
# Подключение к БД
sudo -u postgres psql logist2_db

# Бэкап БД
pg_dump -U logist2_user logist2_db > backup.sql

# Восстановление БД
psql -U logist2_user logist2_db < backup.sql
```

## 🆘 Помощь и поддержка

Если возникли проблемы:
1. Проверьте логи (команды выше)
2. Убедитесь что все сервисы запущены: `systemctl status caromoto-lt nginx postgresql`
3. Проверьте DNS записи домена
4. Проверьте firewall: `ufw status`

## 📞 Контакты

- Email: info@caromoto-lt.com
- Сайт: https://caromoto-lt.com

---

**Удачи с развертыванием! 🚀**

