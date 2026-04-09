# Deploy via git pull — keeps server in sync with GitHub
# Usage: .\deploy.ps1
#
# Prerequisites: server repo has 'origin' pointing to GitHub
# and all local changes are committed + pushed before running this.

$SERVER = "root@176.118.198.78"
$PROJECT_DIR = "/var/www/www-root/data/www/logist2"

Write-Host "=== GIT DEPLOY ===" -ForegroundColor Cyan
Write-Host ""

# [0] Guard: ensure local branch is pushed
$ahead = git rev-list --count "@{u}..HEAD" 2>$null
if ($LASTEXITCODE -ne 0 -or [int]$ahead -gt 0) {
    Write-Host "ERROR: Local commits not pushed to GitHub. Run 'git push' first." -ForegroundColor Red
    exit 1
}
Write-Host "[ok] Local branch is up-to-date with remote" -ForegroundColor Green

# [1/2] SSH: pull + migrate + collectstatic + restart
Write-Host ""
Write-Host "[1/2] Pulling & restarting on server..." -ForegroundColor Yellow

$remoteCmd = "cd $PROJECT_DIR && git fetch origin && git reset --hard origin/master && chown -R www-root:www-root . && source .venv/bin/activate && python manage.py migrate --noinput 2>&1 | tail -5 && python manage.py collectstatic --noinput 2>&1 | tail -1 && systemctl restart gunicorn && systemctl restart daphne && systemctl restart celery && sleep 3 && echo GUNICORN=`$(systemctl is-active gunicorn) && echo DAPHNE=`$(systemctl is-active daphne) && echo CELERY=`$(systemctl is-active celery)"

$output = ssh -o ConnectTimeout=30 -o ServerAliveInterval=10 -o ServerAliveCountMax=6 $SERVER $remoteCmd 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: SSH command failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}

# [2/2] Parse status
Write-Host "[2/2] Checking services..." -ForegroundColor Yellow
$outputStr = $output -join "`n"
Write-Host $outputStr -ForegroundColor Gray

$gunicornOk = $outputStr -match "GUNICORN=active"
$daphneOk   = $outputStr -match "DAPHNE=active"
$celeryOk   = $outputStr -match "CELERY=active"

Write-Host ""
if ($gunicornOk -and $daphneOk -and $celeryOk) {
    Write-Host "All services running" -ForegroundColor Green
} else {
    Write-Host "WARNING: Some services may be down - check manually!" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== DEPLOY COMPLETE ===" -ForegroundColor Cyan
Write-Host "Site: https://caromoto-lt.com" -ForegroundColor Gray
Write-Host ""
