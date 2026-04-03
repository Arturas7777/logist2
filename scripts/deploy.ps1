# Quick deploy script - copy files, migrate, and restart services
# Usage: .\deploy.ps1

$SERVER = "root@176.118.198.78"
$PROJECT_DIR = "/var/www/www-root/data/www/logist2"

Write-Host "=== QUICK DEPLOY ===" -ForegroundColor Cyan
Write-Host ""

# Copy files
Write-Host "[1/4] Copying files..." -ForegroundColor Yellow
if (-not (Test-Path "core/services/__init__.py")) {
    New-Item -Path "core/services/__init__.py" -ItemType File -Force | Out-Null
}
scp -r core/* ${SERVER}:${PROJECT_DIR}/core/ 2>$null
scp -r logist2/* ${SERVER}:${PROJECT_DIR}/logist2/ 2>$null  
scp -r templates/* ${SERVER}:${PROJECT_DIR}/templates/ 2>$null
scp -r scripts/* ${SERVER}:${PROJECT_DIR}/scripts/ 2>$null
Write-Host "      Files copied" -ForegroundColor Green

# Run migrations
Write-Host "[2/4] Running migrations..." -ForegroundColor Yellow
ssh $SERVER "cd $PROJECT_DIR && source .venv/bin/activate && python manage.py migrate --noinput" 2>$null
Write-Host "      Migrations applied" -ForegroundColor Green

# Collect static and restart
Write-Host "[3/4] Collecting static & restarting..." -ForegroundColor Yellow
ssh $SERVER "cd $PROJECT_DIR && chown -R www-root:www-root core/ templates/ && source .venv/bin/activate && python manage.py collectstatic --noinput && systemctl restart gunicorn && systemctl restart daphne" 2>$null
Write-Host "      Services restarted" -ForegroundColor Green

# Check status
Write-Host "[4/4] Checking status..." -ForegroundColor Yellow
$gunicorn = ssh $SERVER 'systemctl is-active gunicorn'
$daphne = ssh $SERVER 'systemctl is-active daphne'

if ($gunicorn -eq "active" -and $daphne -eq "active") {
    Write-Host "      All services running" -ForegroundColor Green
} else {
    Write-Host "      WARNING: Some services not running!" -ForegroundColor Red
    Write-Host "      gunicorn: $gunicorn | daphne: $daphne" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== DEPLOY COMPLETE ===" -ForegroundColor Cyan
Write-Host "Site: https://caromoto-lt.com" -ForegroundColor Gray
Write-Host ""

