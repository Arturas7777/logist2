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

# Run migrations + collectstatic + restart + check in one SSH session
Write-Host "[2/4] Migrating + restarting..." -ForegroundColor Yellow
$remoteCmd = 'cd /var/www/www-root/data/www/logist2 && chown -R www-root:www-root core/ templates/ && source .venv/bin/activate && python manage.py migrate --noinput 2>&1 | tail -3 && python manage.py collectstatic --noinput 2>&1 | tail -1 && systemctl restart gunicorn && systemctl restart daphne && systemctl restart celery && sleep 2 && echo GUNICORN=$(systemctl is-active gunicorn) && echo DAPHNE=$(systemctl is-active daphne) && echo CELERY=$(systemctl is-active celery)'
$output = ssh -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 $SERVER $remoteCmd 2>$null
Write-Host "      Done" -ForegroundColor Green

# Parse status from output
Write-Host "[3/4] Status:" -ForegroundColor Yellow
$outputStr = $output -join "`n"
$gunicornOk = $outputStr -match "GUNICORN=active"
$daphneOk = $outputStr -match "DAPHNE=active"
$celeryOk = $outputStr -match "CELERY=active"

if ($gunicornOk -and $daphneOk -and $celeryOk) {
    Write-Host "      All services running" -ForegroundColor Green
} else {
    Write-Host "      WARNING: Check services manually!" -ForegroundColor Red
    $output | ForEach-Object { Write-Host "      $_" -ForegroundColor Gray }
}

Write-Host ""
Write-Host "=== DEPLOY COMPLETE ===" -ForegroundColor Cyan
Write-Host "Site: https://caromoto-lt.com" -ForegroundColor Gray
Write-Host ""
