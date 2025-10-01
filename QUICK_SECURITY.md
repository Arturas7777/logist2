# 🚀 БЫСТРАЯ НАСТРОЙКА БЕЗОПАСНОСТИ

## ⏱️ 15 МИНУТ ДО БАЗОВОЙ ЗАЩИТЫ

### 📋 ЧТО СДЕЛАЕМ:
1. ✅ Firewall (UFW)
2. ✅ Fail2Ban
3. ✅ Обновления Django Security
4. ✅ Автоматические бэкапы
5. ✅ Проверка безопасности

---

## 🎯 ШАГ 1: FIREWALL (2 минуты)

```bash
ssh root@176.118.198.78

# Настраиваем UFW
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 1500/tcp
sudo ufw enable

# Проверяем
sudo ufw status
```

**Результат:** Защита от нежелательных соединений ✅

---

## 🎯 ШАГ 2: FAIL2BAN (3 минуты)

```bash
# Устанавливаем
sudo apt install fail2ban -y

# Создаем конфиг
sudo nano /etc/fail2ban/jail.local
```

**Вставьте:**
```ini
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port = 22
logpath = /var/log/auth.log

[nginx-http-auth]
enabled = true
port = http,https
logpath = /var/log/nginx/error.log
```

**Сохраните:** `Ctrl+O`, `Enter`, `Ctrl+X`

```bash
# Запускаем
sudo systemctl enable fail2ban
sudo systemctl start fail2ban

# Проверяем
sudo fail2ban-client status
```

**Результат:** Защита от брутфорса ✅

---

## 🎯 ШАГ 3: ОБНОВЛЯЕМ DJANGO (5 минут)

### На локальной машине (Windows):

```powershell
cd C:\Users\art-f\PycharmProjects\logist2

# Скачиваем обновленные файлы
# (они уже готовы - settings_prod.py обновлен)

# Отправляем на сервер через Git
git add -A
git commit -m "Security improvements: HTTPS, HSTS, secure cookies"
git push origin master
```

### На сервере:

```bash
ssh root@176.118.198.78
cd /var/www/www-root/data/www/logist2

# Применяем обновления
./auto_deploy.sh

# Проверяем безопасность Django
source .venv/bin/activate
python manage.py check --deploy
```

**Если видите предупреждения о HTTPS** - это нормально, пока не настроен SSL.

**Результат:** Django security headers активированы ✅

---

## 🎯 ШАГ 4: АВТОМАТИЧЕСКИЕ БЭКАПЫ (3 минуты)

```bash
ssh root@176.118.198.78

# Создаем скрипт бэкапа
sudo nano /usr/local/bin/backup_logist2.sh
```

**Вставьте:**
```bash
#!/bin/bash
BACKUP_DIR="/var/backups/logist2"
DB_NAME="logist2_db"
DB_USER="arturas"
RETENTION_DAYS=30

mkdir -p $BACKUP_DIR
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/backup_$TIMESTAMP.sql"

pg_dump -U $DB_USER $DB_NAME | gzip > "$BACKUP_FILE.gz"

if [ $? -eq 0 ]; then
    echo "$(date): Backup OK: $BACKUP_FILE.gz" >> /var/log/logist2_backup.log
    find $BACKUP_DIR -name "backup_*.sql.gz" -mtime +$RETENTION_DAYS -delete
else
    echo "$(date): Backup FAILED!" >> /var/log/logist2_backup.log
fi
```

**Сохраните:** `Ctrl+O`, `Enter`, `Ctrl+X`

```bash
# Делаем исполняемым
sudo chmod +x /usr/local/bin/backup_logist2.sh

# Тестируем
sudo /usr/local/bin/backup_logist2.sh

# Проверяем
ls -lh /var/backups/logist2/

# Настраиваем автозапуск (каждый день в 3:00)
sudo crontab -e
# Добавьте строку:
0 3 * * * /usr/local/bin/backup_logist2.sh
```

**Результат:** Ежедневные автобэкапы настроены ✅

---

## 🎯 ШАГ 5: ПРОВЕРКА БЕЗОПАСНОСТИ (2 минуты)

Загрузите скрипт проверки на сервер через WinSCP:
- Локальный файл: `C:\Users\art-f\PycharmProjects\logist2\security_check.sh`
- Удаленный путь: `/var/www/www-root/data/www/logist2/security_check.sh`

```bash
ssh root@176.118.198.78
cd /var/www/www-root/data/www/logist2

# Делаем исполняемым
chmod +x security_check.sh

# Запускаем полную проверку
./security_check.sh
```

**Результат:** Видите статус всей безопасности ✅

---

## ✅ ГОТОВО! БАЗОВАЯ ЗАЩИТА АКТИВНА

### Что теперь работает:
- ✅ Firewall блокирует нежелательные соединения
- ✅ Fail2Ban банит атакующих
- ✅ Django security headers защищают приложение
- ✅ Автоматические бэкапы каждый день
- ✅ Можете проверить безопасность одной командой

---

## 📊 РЕГУЛЯРНЫЕ ПРОВЕРКИ

### Каждую неделю (5 минут):

```bash
ssh root@176.118.198.78
cd /var/www/www-root/data/www/logist2

# Запускаем проверку
./security_check.sh

# Проверяем последние бэкапы
ls -lth /var/backups/logist2/ | head -5

# Проверяем логи на подозрительную активность
sudo tail -50 /var/log/auth.log | grep "Failed password"
```

### Каждый месяц (10 минут):

```bash
# Обновляем систему
sudo apt update && sudo apt upgrade -y

# Очищаем старые логи
sudo journalctl --vacuum-time=30d

# Проверяем размер бэкапов
du -sh /var/backups/logist2/
```

---

## 🚨 ДОПОЛНИТЕЛЬНАЯ ЗАЩИТА (ОПЦИОНАЛЬНО)

### Если хотите максимальную безопасность:

1. **SSH по ключам (без паролей)** - см. `SECURITY_SETUP.md`, раздел 1
2. **HTTPS/SSL сертификат** - см. `SECURITY_SETUP.md`, раздел 3
3. **Изменить SSH порт** - см. `SECURITY_SETUP.md`, раздел 1.5
4. **Автоматические обновления** - см. `SECURITY_SETUP.md`, раздел 7

---

## 📞 ЕСЛИ ЧТО-ТО ПОШЛО НЕ ТАК

### Сайт не открывается после настройки:

```bash
# Проверяем статус сервисов
sudo systemctl status nginx
sudo systemctl status gunicorn

# Смотрим логи
sudo tail -50 /var/log/nginx/error.log

# Если проблема с HTTPS:
# Временно отключите редирект в settings_prod.py:
# SECURE_SSL_REDIRECT = False
```

### Не можете зайти по SSH:

```bash
# Если забанили себя в Fail2Ban:
# Зайдите через ISPmanager Shell и выполните:
sudo fail2ban-client set sshd unbanip ВАШ_IP

# Если изменили порт SSH и потеряли доступ:
# Зайдите через ISPmanager и верните порт 22 в /etc/ssh/sshd_config
```

---

## 🎯 ИТОГОВЫЙ ЧЕКЛИСТ

```
✅ UFW firewall активен
✅ Fail2Ban установлен и работает
✅ Django security settings применены
✅ Автоматические бэкапы настроены
✅ Скрипт проверки работает
✅ Знаю как проверять безопасность еженедельно
```

---

**🎉 ПОЗДРАВЛЯЮ! Ваш сервер теперь защищен от 90% типичных атак!**

Для углубленной настройки читайте полное руководство: **`SECURITY_SETUP.md`**

