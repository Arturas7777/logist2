# Quick deploy script - copy files, migrate, and restart services
# Uses tar+ssh to avoid hosting provider's SSH connection rate limit (max 4 rapid connections)
# Usage: .\deploy.ps1

$SERVER = "root@176.118.198.78"
$PROJECT_DIR = "/var/www/www-root/data/www/logist2"

Write-Host "=== QUICK DEPLOY ===" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path "core/services/__init__.py")) {
    New-Item -Path "core/services/__init__.py" -ItemType File -Force | Out-Null
}

# [1/3] Create tar, upload via scp, extract + migrate + restart in one SSH session
Write-Host "[1/3] Packing files..." -ForegroundColor Yellow
$tarFile = "$env:TEMP\logist2_deploy.tar"
tar -cf $tarFile --exclude="__pycache__" --exclude="*.pyc" --exclude=".git" core logist2 templates scripts 2>$null
if (-not (Test-Path $tarFile)) {
    Write-Host "      ERROR: tar creation failed" -ForegroundColor Red
    exit 1
}
Write-Host "      Tar created ($(((Get-Item $tarFile).Length / 1KB).ToString('F0')) KB)" -ForegroundColor Green

Write-Host "[2/3] Uploading..." -ForegroundColor Yellow
scp -o ConnectTimeout=30 $tarFile "${SERVER}:${PROJECT_DIR}/_deploy.tar" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "      ERROR: Upload failed" -ForegroundColor Red
    Remove-Item $tarFile -Force
    exit 1
}
Remove-Item $tarFile -Force
Write-Host "      Uploaded" -ForegroundColor Green

Write-Host "[3/3] Extracting + migrating + restarting..." -ForegroundColor Yellow
$remoteCmd = "cd $PROJECT_DIR && tar --overwrite -xf _deploy.tar && rm _deploy.tar && chown -R www-root:www-root core/ templates/ logist2/ scripts/ && source .venv/bin/activate && python manage.py migrate --noinput 2>&1 | tail -3 && python manage.py collectstatic --noinput 2>&1 | tail -1 && systemctl restart gunicorn && systemctl restart daphne && systemctl restart celery && sleep 3 && echo GUNICORN=`$(systemctl is-active gunicorn) && echo DAPHNE=`$(systemctl is-active daphne) && echo CELERY=`$(systemctl is-active celery)"
$output = ssh -o ConnectTimeout=30 -o ServerAliveInterval=10 -o ServerAliveCountMax=6 $SERVER $remoteCmd 2>$null
Write-Host "      Done" -ForegroundColor Green

# Parse status
Write-Host "Status:" -ForegroundColor Yellow
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
