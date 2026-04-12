# Deploy via git pull — fast, reliable, no scp overhead
# Usage: .\scripts\deploy.ps1
#   Flags:
#     -Force  — discard uncommitted server changes and deploy anyway

param(
    [switch]$Force
)

$SERVER = "root@176.118.198.78"
$PROJECT_DIR = "/var/www/www-root/data/www/logist2"

Write-Host ""
Write-Host "=== DEPLOY (git pull) ===" -ForegroundColor Cyan
Write-Host ""

# ── Pre-check: local commits pushed? ──
$localStatus = git status --porcelain 2>$null
if ($localStatus) {
    Write-Host "  WARNING: You have uncommitted local changes!" -ForegroundColor Red
    Write-Host "  Commit and push first, then deploy." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

$ahead = git status -sb 2>$null | Select-String "ahead"
if ($ahead) {
    Write-Host "  WARNING: Local commits not pushed to origin!" -ForegroundColor Red
    Write-Host "  Run: git push origin master" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# ── Step 1: Check server state ──
Write-Host "[1/4] Checking server..." -ForegroundColor Yellow

if (-not $Force) {
    $serverDirty = ssh $SERVER "cd $PROJECT_DIR && git status --porcelain 2>/dev/null" 2>$null
    if ($serverDirty) {
        Write-Host ""
        Write-Host "  *** SERVER HAS UNCOMMITTED CHANGES ***" -ForegroundColor Red
        Write-Host ""
        Write-Host "  Options:" -ForegroundColor Yellow
        Write-Host "    1. .\scripts\sync_from_vps.ps1  (pull server changes first)" -ForegroundColor White
        Write-Host "    2. .\scripts\deploy.ps1 -Force   (discard server changes)" -ForegroundColor White
        Write-Host ""
        Write-Host "  Changed files:" -ForegroundColor Gray
        Write-Host $serverDirty -ForegroundColor Gray
        Write-Host ""
        exit 1
    }
    Write-Host "      Server is clean" -ForegroundColor Green
} else {
    Write-Host "      -Force: discarding server changes" -ForegroundColor Yellow
    ssh $SERVER "cd $PROJECT_DIR && git checkout -- . && git clean -fd" 2>$null
    Write-Host "      Server reset to last commit" -ForegroundColor Green
}

# ── Step 2: git pull ──
Write-Host "[2/4] Pulling latest code..." -ForegroundColor Yellow
$pullOutput = ssh $SERVER "cd $PROJECT_DIR && git pull origin master 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "      git pull FAILED:" -ForegroundColor Red
    Write-Host $pullOutput -ForegroundColor Red
    Write-Host ""
    exit 1
}
Write-Host "      Code updated" -ForegroundColor Green

# ── Step 3: migrate + collectstatic ──
Write-Host "[3/4] Migrate & collectstatic..." -ForegroundColor Yellow
ssh $SERVER "cd $PROJECT_DIR && source .venv/bin/activate && pip install -r requirements.txt --quiet 2>/dev/null; python manage.py migrate --noinput && python manage.py collectstatic --noinput --verbosity 0" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "      Migrate/collectstatic had issues (check server logs)" -ForegroundColor Yellow
} else {
    Write-Host "      Done" -ForegroundColor Green
}

# ── Step 4: restart services ──
Write-Host "[4/4] Restarting services..." -ForegroundColor Yellow
ssh $SERVER "cd $PROJECT_DIR && chown -R www-root:www-root core/ templates/ staticfiles/ logist2/ 2>/dev/null; systemctl restart gunicorn && systemctl restart daphne" 2>$null

$gunicorn = ssh $SERVER "systemctl is-active gunicorn" 2>$null
$daphne = ssh $SERVER "systemctl is-active daphne" 2>$null

if ($gunicorn -eq "active" -and $daphne -eq "active") {
    Write-Host "      gunicorn: active | daphne: active" -ForegroundColor Green
    Write-Host ""
    Write-Host "=== DEPLOY COMPLETE ===" -ForegroundColor Green
} else {
    Write-Host "      gunicorn: $gunicorn | daphne: $daphne" -ForegroundColor Red
    Write-Host ""
    Write-Host "=== DEPLOY COMPLETE (with warnings) ===" -ForegroundColor Yellow
}
Write-Host "Site: https://caromoto-lt.com" -ForegroundColor Gray
Write-Host ""
