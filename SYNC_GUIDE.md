# Инструкция по синхронизации проекта Logist2

---

## Сценарий 1: Поработал на компе/ноуте → обновить сервер

> Ты поменял код локально и хочешь, чтобы изменения появились на VPS (caromoto-lt.com)

**На том компьютере, где работал:**

```powershell
cd C:\Users\art-f\PycharmProjects\logist2
git add -A
git commit -m "описание что сделал"
git push origin master
```

**Затем на сервере (через SSH или Cursor):**

```bash
cd /var/www/www-root/data/www/logist2
git pull origin master
source .venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
systemctl restart gunicorn
systemctl restart daphne
```

Или одной строкой:

```bash
cd /var/www/www-root/data/www/logist2 && git pull origin master && source .venv/bin/activate && python manage.py migrate && python manage.py collectstatic --noinput && systemctl restart gunicorn && systemctl restart daphne
```

---

## Сценарий 2: Работал на ноуте → сел за стационарный

> На сервере уже актуальная версия, нужно подтянуть на стационарный

**На стационарном:**

```powershell
cd C:\Users\art-f\PycharmProjects\logist2
git stash
git pull origin master
```

Если ругается на конфликтующие файлы:

```powershell
git clean -fd
git pull origin master
```

Готово — код актуальный.

---

## Сценарий 3: Работал на стационарном → сел за ноут

> На сервере уже актуальная версия, нужно подтянуть на ноут

**На ноуте** — всё то же самое:

```powershell
cd C:\Users\art-f\PycharmProjects\logist2
git stash
git pull origin master
```

Если ругается на конфликтующие файлы:

```powershell
git clean -fd
git pull origin master
```

Готово — код актуальный.

---

## Сценарий 4: Работал в админке на сервере → нужна свежая БД локально

> Данные в базе изменились через админку (добавил контейнеры, ТС и т.д.), хочешь иметь такую же БД на компе/ноуте

**На компе или ноуте (3 команды по очереди):**

```powershell
ssh root@176.118.198.78 "PGPASSWORD='7154032tut' pg_dump -U arturas -h localhost -d logist2_db -F c -b -f /tmp/logist2_sync_backup.dump"
```

```powershell
scp root@176.118.198.78:/tmp/logist2_sync_backup.dump .
```

```powershell
$env:PGPASSWORD='7154032tut'; pg_restore -U arturas -h localhost -d logist2_db --clean --if-exists logist2_sync_backup.dump
```

После восстановления можно удалить дамп:

```powershell
Remove-Item logist2_sync_backup.dump
```

---

## Шпаргалка

| Что нужно | Команда |
|-----------|---------|
| Подтянуть код с git | `git stash; git pull origin master` |
| Запушить код в git | `git add -A; git commit -m "..."; git push origin master` |
| Скачать БД с сервера | 3 команды из сценария 4 |
| Обновить сервер из git | SSH → `git pull` + `migrate` + `restart gunicorn` |

## Если SSH не подключается (таймаут)

Подожди 1-2 минуты и попробуй снова. VPS иногда ограничивает подключения при большом количестве SSH-сессий подряд.

## Первоначальная настройка на новом компьютере

```powershell
git clone https://github.com/Arturas7777/logist2.git
cd logist2
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Затем создай файл `.env` (скопировав с рабочей машины или с сервера) и выполни сценарий 4 для БД.
