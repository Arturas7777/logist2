# PowerShell скрипт для загрузки обновленных файлов на VPS

$SERVER_IP = "176.118.198.78"
$SERVER_USER = "root"
$SERVER_PASSWORD = "lOaKcFF100O26nm3oC"
$PROJECT_DIR = "/var/www/www-root/data/www/logist2"

Write-Host "=== Загрузка проекта на VPS ===" -ForegroundColor Green

# Проверка наличия архива
$latestArchive = Get-ChildItem -Path . -Filter "logist2_deploy_*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -First 1

if (-not $latestArchive) {
    Write-Host "[ОШИБКА] Архив не найден. Запустите prepare_for_deploy.py" -ForegroundColor Red
    exit 1
}

Write-Host "[1/4] Найден архив: $($latestArchive.Name)" -ForegroundColor Cyan

# Загрузка архива через SCP
Write-Host "[2/4] Загрузка архива на сервер..." -ForegroundColor Cyan
Write-Host "Используйте WinSCP или выполните команду вручную:" -ForegroundColor Yellow
Write-Host ""
Write-Host "scp $($latestArchive.Name) ${SERVER_USER}@${SERVER_IP}:/tmp/" -ForegroundColor White
Write-Host ""
Write-Host "Пароль: $SERVER_PASSWORD" -ForegroundColor Yellow
Write-Host ""

# Инструкции для распаковки
Write-Host "[3/4] После загрузки выполните на сервере:" -ForegroundColor Cyan
Write-Host ""
Write-Host "ssh ${SERVER_USER}@${SERVER_IP}" -ForegroundColor White
Write-Host "cd $PROJECT_DIR" -ForegroundColor White
Write-Host "unzip -o /tmp/$($latestArchive.Name)" -ForegroundColor White
Write-Host "chmod +x update_server.sh" -ForegroundColor White
Write-Host "./update_server.sh" -ForegroundColor White
Write-Host ""

Write-Host "[4/4] Или используйте PuTTY/другой SSH клиент" -ForegroundColor Cyan
Write-Host ""
Write-Host "Готово! Следуйте инструкциям выше." -ForegroundColor Green

