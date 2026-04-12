# Quick deploy script - copy files, migrate, and restart services
# Usage: .\deploy.ps1
#   Flags:
#     -Force  — skip the server dirty-check and deploy anyway

param(
    [switch]$Force
)

$SERVER = "root@176.118.198.78"
$PROJECT_DIR = "/var/www/www-root/data/www/logist2"

Write-Host "=== QUICK DEPLOY ===" -ForegroundColor Cyan
Write-Host ""

# ── Step 0: Check for uncommitted changes on the server ──
if (-not $Force) {
    Write-Host "[0/4] Checking for uncommitted changes on server..." -ForegroundColor Yellow
    $serverStatus = ssh $SERVER "cd $PROJECT_DIR && git status --porcelain 2>/dev/null" 2>$null

    if ($serverStatus) {
        Write-Host ""
        Write-Host "  *** SERVER HAS UNCOMMITTED CHANGES! ***" -ForegroundColor Red
        Write-Host "  If you deploy now, those changes will be OVERWRITTEN." -ForegroundColor Red
        Write-Host ""
        Write-Host "  Options:" -ForegroundColor Yellow
        Write-Host "    1. Run .\scripts\sync_from_vps.ps1 first (recommended)" -ForegroundColor White
        Write-Host "    2. Run .\scripts\deploy.ps1 -Force to overwrite anyway" -ForegroundColor White
        Write-Host ""
        Write-Host "  Changed files on server:" -ForegroundColor Gray
        Write-Host $serverStatus -ForegroundColor Gray
        Write-Host ""
        exit 1
    } else {
        Write-Host "      Server is clean" -ForegroundColor Green
    }
}

# ── Step 1: Copy files ──
Write-Host "[1/4] Copying files..." -ForegroundColor Yellow
if (-not (Test-Path "core/services/__init__.py")) {
    New-Item -Path "core/services/__init__.py" -ItemType File -Force | Out-Null
}
scp -r core/* ${SERVER}:${PROJECT_DIR}/core/ 2>$null
scp -r logist2/* ${SERVER}:${PROJECT_DIR}/logist2/ 2>$null  
scp -r templates/* ${SERVER}:${PROJECT_DIR}/templates/ 2>$null
scp -r scripts/* ${SERVER}:${PROJECT_DIR}/scripts/ 2>$null
Write-Host "      Files copied" -ForegroundColor Green

# ── Step 2: Run migrations ──
Write-Host "[2/4] Running migrations..." -ForegroundColor Yellow
ssh $SERVER "cd $PROJECT_DIR && source .venv/bin/activate && python manage.py migrate --noinput" 2>$null
Write-Host "      Migrations applied" -ForegroundColor Green

# ── Step 3: Collect static and restart ──
Write-Host "[3/4] Collecting static & restarting..." -ForegroundColor Yellow
ssh $SERVER "cd $PROJECT_DIR && chown -R www-root:www-root core/ templates/ && source .venv/bin/activate && python manage.py collectstatic --noinput && systemctl restart gunicorn && systemctl restart daphne" 2>$null
Write-Host "      Services restarted" -ForegroundColor Green

# ── Step 4: Commit on server so it stays in sync ──
Write-Host "[4/4] Committing deployed code on server..." -ForegroundColor Yellow
ssh $SERVER "cd $PROJECT_DIR && git add -A && git diff --cached --quiet || git commit -m 'deploy: $(Get-Date -Format 'yyyy-MM-dd HH:mm')'" 2>$null
Write-Host "      Server git updated" -ForegroundColor Green

# ── Status check ──
$gunicorn = ssh $SERVER 'systemctl is-active gunicorn' 2>$null
$daphne = ssh $SERVER 'systemctl is-active daphne' 2>$null

if ($gunicorn -eq "active" -and $daphne -eq "active") {
    Write-Host ""
    Write-Host "=== DEPLOY COMPLETE ===" -ForegroundColor Green
    Write-Host "Site: https://caromoto-lt.com" -ForegroundColor Gray
} else {
    Write-Host ""
    Write-Host "=== DEPLOY COMPLETE (with warnings) ===" -ForegroundColor Yellow
    Write-Host "gunicorn: $gunicorn | daphne: $daphne" -ForegroundColor Red
    Write-Host "Site: https://caromoto-lt.com" -ForegroundColor Gray
}
Write-Host ""
