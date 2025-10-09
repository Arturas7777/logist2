# 📤 Инструкция по передаче файлов на VPS

## Способ 1: Через SCP (Windows -> Linux сервер)

### Из PowerShell на вашем компьютере:

```powershell
# Перейдите в папку проекта
cd C:\Users\art-f\PycharmProjects\logist2

# Загрузите все файлы на сервер
scp -r * root@ваш-server-ip:/var/www/caromoto-lt/
```

### Исключить ненужные файлы
Создайте архив без виртуального окружения и других временных файлов:

```powershell
# Создать архив проекта
Compress-Archive -Path * -DestinationPath caromoto-lt.zip -Exclude .venv,staticfiles,media,*.pyc,__pycache__

# Загрузить архив на сервер
scp caromoto-lt.zip root@ваш-server-ip:/var/www/

# На сервере распаковать
ssh root@ваш-server-ip
cd /var/www
unzip caromoto-lt.zip -d caromoto-lt/
```

## Способ 2: Через Git (Рекомендуется)

### 1. Создайте Git репозиторий (если еще не создан)

```powershell
# На вашем компьютере
cd C:\Users\art-f\PycharmProjects\logist2

# Инициализация Git (если не сделано)
git init
git add .
git commit -m "Initial commit"

# Добавить удаленный репозиторий (GitHub/GitLab/Bitbucket)
git remote add origin https://github.com/ваш-username/caromoto-lt.git
git branch -M master
git push -u origin master
```

### 2. На сервере склонируйте репозиторий

```bash
ssh root@ваш-server-ip

cd /var/www/caromoto-lt
git clone https://github.com/ваш-username/caromoto-lt.git .
```

### Преимущества Git:
- ✅ Легко обновлять сайт (`git pull`)
- ✅ История изменений
- ✅ Откат к предыдущей версии
- ✅ Не нужно каждый раз загружать все файлы

## Способ 3: Через FileZilla / WinSCP (GUI)

### FileZilla:
1. Скачайте FileZilla Client
2. Подключитесь:
   - **Хост**: ваш-server-ip
   - **Протокол**: SFTP
   - **Пользователь**: root
   - **Пароль**: ваш-пароль-от-сервера
3. Перетащите файлы из локальной папки в `/var/www/caromoto-lt/`

### WinSCP:
1. Скачайте WinSCP
2. Создайте новое подключение (SFTP)
3. Загрузите файлы

## ⚠️ Важно!

### НЕ загружайте на сервер:
- `.venv/` - виртуальное окружение (создается на сервере)
- `staticfiles/` - собирается на сервере
- `*.pyc`, `__pycache__/` - компилированные файлы Python
- `*.log` - файлы логов
- `.env` - создается на сервере с реальными данными

### ОБЯЗАТЕЛЬНО загрузите:
- Все `.py` файлы
- `templates/` - шаблоны
- `static/` - статические файлы (CSS, JS, изображения)
- `requirements.txt`
- Конфигурационные файлы (`nginx_caromoto.conf`, `gunicorn_config.py`, и т.д.)
- `env.example` - пример настроек

## 🔐 Безопасность при передаче файлов

1. **Не загружайте .env с реальными паролями!**
   - Используйте `env.example` как шаблон
   - Создайте `.env` на сервере вручную

2. **Используйте SSH ключи вместо паролей**
   ```powershell
   # Генерация SSH ключа на Windows
   ssh-keygen -t rsa -b 4096

   # Копирование на сервер
   type $env:USERPROFILE\.ssh\id_rsa.pub | ssh root@ваш-server-ip "cat >> ~/.ssh/authorized_keys"
   ```

## 📝 После загрузки файлов

На сервере выполните:
```bash
cd /var/www/caromoto-lt

# Установите права доступа
chown -R www-data:www-data /var/www/caromoto-lt
chmod -R 755 /var/www/caromoto-lt

# Следуйте дальнейшим инструкциям в DEPLOYMENT.md
```

---

**Готово! Файлы переданы на сервер.** 🎉

Теперь следуйте **DEPLOY_CHECKLIST.md** для завершения развертывания.

