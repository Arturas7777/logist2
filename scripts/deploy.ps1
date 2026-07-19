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

# SAFE_PATHS — untracked файлы/папки, которые НИКОГДА не должны удаляться при -Force,
# даже если их нет в .gitignore. Критично для секретов и пользовательских данных.
# Прецедент 15.05.2026: git clean -fd удалил certs/privatecert.pem (Revolut JWT key),
# что сломало синхронизацию и потребовало полного re-setup с Revolut Business.
$SAFE_PATHS = "-e certs/ -e .env -e .env.* -e privatecert.pem -e publiccert.cer -e media/ -e staticfiles/ -e *.sqlite3"

# Игнорируем только untracked файлы из SAFE_PATHS — но если сервер модифицировал их,
# об этом нужно знать (порчей deploy не лечится). Поэтому фильтруем серверный
# git status по leading "??" (untracked) и убираем безопасные пути.
$serverDirtyRaw = ssh $SERVER "cd $PROJECT_DIR && git status --porcelain 2>/dev/null" 2>$null
$serverDirty = $serverDirtyRaw | Where-Object {
    $line = $_
    $isSafeUntracked = $false
    if ($line -match '^\?\?\s+(certs/|\.env|privatecert\.pem|publiccert\.cer|media/|staticfiles/)') {
        $isSafeUntracked = $true
    }
    -not $isSafeUntracked
}

if (-not $Force) {
    if ($serverDirty) {
        Write-Host ""
        Write-Host "  *** SERVER HAS UNCOMMITTED CHANGES ***" -ForegroundColor Red
        Write-Host ""
        Write-Host "  Options:" -ForegroundColor Yellow
        Write-Host "    1. .\scripts\sync_from_vps.ps1  (pull server changes first)" -ForegroundColor White
        Write-Host "    2. .\scripts\deploy.ps1 -Force   (discard server changes)" -ForegroundColor White
        Write-Host ""
        Write-Host "  Changed files (excluding safe untracked paths like certs/, .env):" -ForegroundColor Gray
        Write-Host ($serverDirty -join "`n") -ForegroundColor Gray
        Write-Host ""
        exit 1
    }
    Write-Host "      Server is clean (или только безопасные untracked: certs/, .env)" -ForegroundColor Green
} else {
    Write-Host "      -Force: discarding server changes (sparing certs/, .env, media/, etc.)" -ForegroundColor Yellow
    # checkout сбрасывает изменения tracked файлов
    # clean -fd с явными -e исключениями — НИКОГДА не трогает SAFE_PATHS
    ssh $SERVER "cd $PROJECT_DIR && git checkout -- . && git clean -fd $SAFE_PATHS" 2>$null
    Write-Host "      Server reset to last commit (safe paths preserved)" -ForegroundColor Green
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
# DJANGO_SETTINGS_MODULE=logist2.settings.prod выставляем явно: с H2 дефолт
# manage.py локально — `logist2.settings.dev`, и без переменной миграции
# на сервере побежали бы с DEBUG=True. systemd-юниты (gunicorn/daphne/celery)
# имеют свой Environment=… и не зависят от этой команды.
Write-Host "[3/4] Migrate & collectstatic..." -ForegroundColor Yellow
$migrateOutput = ssh $SERVER "cd $PROJECT_DIR && source .venv/bin/activate && export DJANGO_SETTINGS_MODULE=logist2.settings.prod && pip install -r requirements.txt --quiet 2>/dev/null; python manage.py migrate --noinput && python manage.py collectstatic --noinput --verbosity 0" 2>&1
if ($LASTEXITCODE -ne 0) {
    # Fail-fast: рестартить сервисы поверх сломанной схемы БД нельзя —
    # старый код продолжит работать на старой схеме, а мы разберёмся.
    Write-Host "      Migrate/collectstatic FAILED — deploy aborted, services NOT restarted:" -ForegroundColor Red
    Write-Host ($migrateOutput -join "`n") -ForegroundColor Red
    Write-Host ""
    Write-Host "  Server still runs the previous code. Fix the migration and re-deploy." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
Write-Host "      Done" -ForegroundColor Green

# ── Step 4: restart services ──
Write-Host "[4/5] Restarting services..." -ForegroundColor Yellow
ssh $SERVER "cd $PROJECT_DIR && chown -R www-root:www-root core/ templates/ staticfiles/ logist2/ media/ 2>/dev/null; systemctl restart gunicorn daphne celery celerybeat" 2>$null

$gunicorn = ssh $SERVER "systemctl is-active gunicorn" 2>$null
$daphne = ssh $SERVER "systemctl is-active daphne" 2>$null
$celery = ssh $SERVER "systemctl is-active celery" 2>$null
$beat = ssh $SERVER "systemctl is-active celerybeat" 2>$null

$servicesOk = ($gunicorn -eq "active" -and $daphne -eq "active" -and $celery -eq "active" -and $beat -eq "active")
if ($servicesOk) {
    Write-Host "      gunicorn/daphne/celery/celerybeat: all active" -ForegroundColor Green
} else {
    Write-Host "      gunicorn:$gunicorn  daphne:$daphne  celery:$celery  celerybeat:$beat" -ForegroundColor Red
}

# ── Step 5: post-deploy smoke (R7) ──
# Проверяем, что сайт реально отвечает: /health/ должен вернуть 200,
# страница логина админки — 200/302. Gunicorn может стартовать несколько
# секунд, поэтому до 6 попыток с паузой 5с.
Write-Host "[5/5] Post-deploy smoke..." -ForegroundColor Yellow
$BASE_URL = "https://caromoto-lt.com"
$smokeOk = $false
for ($i = 1; $i -le 6; $i++) {
    try {
        $health = Invoke-WebRequest -Uri "$BASE_URL/health/" -Method GET -TimeoutSec 10 -UseBasicParsing
        if ($health.StatusCode -eq 200) {
            $smokeOk = $true
            break
        }
    } catch {
        # сервис ещё поднимается — ждём и пробуем снова
    }
    Start-Sleep -Seconds 5
}
$adminOk = $false
if ($smokeOk) {
    try {
        # MaximumRedirection 0: для /admin/ корректен и 200, и 302 на логин
        $admin = Invoke-WebRequest -Uri "$BASE_URL/admin/login/" -Method GET -TimeoutSec 10 -UseBasicParsing -MaximumRedirection 0 -ErrorAction SilentlyContinue
        if ($admin.StatusCode -in 200, 302) { $adminOk = $true }
    } catch {
        if ($_.Exception.Response -and ([int]$_.Exception.Response.StatusCode -in 200, 302)) { $adminOk = $true }
    }
}

if ($smokeOk -and $adminOk) {
    Write-Host "      /health/ 200, /admin/login/ OK" -ForegroundColor Green
} elseif ($smokeOk) {
    Write-Host "      /health/ 200, но /admin/login/ не отвечает 200/302!" -ForegroundColor Red
} else {
    Write-Host "      SMOKE FAILED: /health/ не вернул 200 за 30 секунд!" -ForegroundColor Red
}

Write-Host ""
if ($servicesOk -and $smokeOk -and $adminOk) {
    Write-Host "=== DEPLOY COMPLETE ===" -ForegroundColor Green
} else {
    Write-Host "=== DEPLOY COMPLETE (with warnings) ===" -ForegroundColor Yellow
}
Write-Host "Site: https://caromoto-lt.com" -ForegroundColor Gray
Write-Host ""
if (-not ($smokeOk -and $adminOk)) { exit 1 }
