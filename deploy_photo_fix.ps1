# Deploy container photo fix to VPS
# Usage: .\deploy_photo_fix.ps1

$SERVER = "root@176.118.198.78"
$PROJECT_DIR = "/var/www/www-root/data/www/logist2"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Deploying container photo fix" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Update code
Write-Host "[1/7] Updating code from GitHub..." -ForegroundColor Yellow
ssh $SERVER "cd $PROJECT_DIR; git pull origin master"
Write-Host "      Code updated" -ForegroundColor Green
Write-Host ""

# Step 2: Check Pillow
Write-Host "[2/7] Checking Pillow..." -ForegroundColor Yellow
$pillowCheck = ssh $SERVER "cd $PROJECT_DIR; source .venv/bin/activate; python -c 'from PIL import Image; print(""OK"")' 2>&1"
if ($pillowCheck -like "*OK*") {
    Write-Host "      Pillow OK" -ForegroundColor Green
} else {
    Write-Host "      Installing dependencies..." -ForegroundColor Yellow
    ssh $SERVER "apt-get update -qq; apt-get install -y libjpeg-dev zlib1g-dev libpng-dev libtiff-dev libfreetype6-dev -qq"
    ssh $SERVER "cd $PROJECT_DIR; source .venv/bin/activate; pip install --upgrade --force-reinstall Pillow"
    Write-Host "      Pillow reinstalled" -ForegroundColor Green
}
Write-Host ""

# Step 3: Create folders
Write-Host "[3/7] Setting up folders..." -ForegroundColor Yellow
ssh $SERVER "cd $PROJECT_DIR; mkdir -p media/container_photos/thumbnails"
ssh $SERVER "cd $PROJECT_DIR; chown -R www-root:www-root media/"
ssh $SERVER "cd $PROJECT_DIR; chmod -R 775 media/container_photos/"
Write-Host "      Folders configured" -ForegroundColor Green
Write-Host ""

# Step 4: Check environment
Write-Host "[4/7] Checking environment..." -ForegroundColor Yellow
ssh $SERVER "cd $PROJECT_DIR; source .venv/bin/activate; python manage.py check_photo_environment"
Write-Host ""

# Step 5: Regenerate thumbnails
Write-Host "[5/7] Regenerating thumbnails..." -ForegroundColor Yellow
ssh $SERVER "cd $PROJECT_DIR; source .venv/bin/activate; python manage.py regenerate_thumbnails"
Write-Host ""

# Step 6: Collect static
Write-Host "[6/7] Collecting static..." -ForegroundColor Yellow
ssh $SERVER "cd $PROJECT_DIR; source .venv/bin/activate; python manage.py collectstatic --noinput"
Write-Host "      Static collected" -ForegroundColor Green
Write-Host ""

# Step 7: Restart services
Write-Host "[7/7] Restarting services..." -ForegroundColor Yellow
ssh $SERVER "systemctl restart gunicorn; systemctl restart daphne"
Write-Host "      Services restarted" -ForegroundColor Green
Write-Host ""

# Check status
Write-Host "Checking service status..." -ForegroundColor Yellow
$gunicorn = ssh $SERVER 'systemctl is-active gunicorn'
$daphne = ssh $SERVER 'systemctl is-active daphne'

if ($gunicorn -eq "active" -and $daphne -eq "active") {
    Write-Host "      All services running" -ForegroundColor Green
} else {
    Write-Host "      WARNING: Some services not running!" -ForegroundColor Red
    Write-Host "      gunicorn: $gunicorn" -ForegroundColor Gray
    Write-Host "      daphne: $daphne" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Deploy complete!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Open admin: https://caromoto-lt.com/admin/" -ForegroundColor Gray
Write-Host "  2. Upload test archive with photos" -ForegroundColor Gray
Write-Host "  3. Check that thumbnails display" -ForegroundColor Gray
Write-Host "  4. Check logs if needed" -ForegroundColor Gray
Write-Host ""

