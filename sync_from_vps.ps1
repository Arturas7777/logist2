#!/usr/bin/env pwsh
# ============================================
# SYNC FROM VPS - Синхронизация проекта с VPS
# ============================================
# Использование: .\sync_from_vps.ps1
#   Флаги:
#     -NoDB       — пропустить синхронизацию базы данных
#     -DBOnly     — только база данных (без кода)
# ============================================

param(
    [switch]$NoDB,
    [switch]$DBOnly
)

$ErrorActionPreference = "Stop"

# --- Настройки ---
$VPS_HOST = "root@176.118.198.78"
$VPS_PROJECT = "/var/www/www-root/data/www/logist2"
$LOCAL_PROJECT = $PSScriptRoot
$DB_NAME = "logist2_db"
$DB_USER = "arturas"
$DB_PASSWORD = "7154032tut"
$DUMP_FILE = "logist2_sync_backup.dump"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  LOGIST2 - Sync from VPS" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Шаг 1: Коммит и пуш на VPS ---
if (-not $DBOnly) {
    Write-Host "[1/5] Коммит и пуш изменений на VPS..." -ForegroundColor Yellow
    
    # Создаём скрипт для VPS
    $vpsScript = @"
#!/bin/bash
cd $VPS_PROJECT
if [ -n "`$(git status --porcelain)" ]; then
    git add -A
    git commit -m "VPS auto-sync: `$(date '+%Y-%m-%d %H:%M')"
    git push origin master
    echo "PUSHED"
else
    echo "CLEAN"
fi
"@
    
    # Записываем скрипт во временный файл
    $tempScript = Join-Path $env:TEMP "vps_sync.sh"
    $vpsScript | Out-File -FilePath $tempScript -Encoding utf8NoBOM
    
    scp $tempScript "${VPS_HOST}:/tmp/vps_sync.sh" 2>$null
    $result = ssh $VPS_HOST "bash /tmp/vps_sync.sh"
    
    if ($result -match "PUSHED") {
        Write-Host "  VPS: изменения закоммичены и запушены" -ForegroundColor Green
    } elseif ($result -match "CLEAN") {
        Write-Host "  VPS: нет изменений для коммита" -ForegroundColor Green
    } else {
        Write-Host "  VPS результат: $result" -ForegroundColor Gray
    }

    # --- Шаг 2: Сброс локальных изменений и pull ---
    Write-Host "[2/5] Подтягиваем код из git..." -ForegroundColor Yellow
    
    Set-Location $LOCAL_PROJECT
    
    # Сохраняем локальные изменения если есть
    $localChanges = git status --porcelain
    if ($localChanges) {
        Write-Host "  Сохраняем локальные изменения в git stash..." -ForegroundColor Gray
        git stash --quiet
    }
    
    # Удаляем неотслеживаемые файлы, которые могут конфликтовать
    git clean -fd --quiet 2>$null
    
    # Подтягиваем
    git pull origin master
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Код синхронизирован" -ForegroundColor Green
    } else {
        Write-Host "  ОШИБКА при git pull!" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[1/5] Пропуск кода (режим -DBOnly)" -ForegroundColor Gray
    Write-Host "[2/5] Пропуск кода (режим -DBOnly)" -ForegroundColor Gray
}

# --- Шаг 3: Синхронизация БД ---
if (-not $NoDB) {
    Write-Host "[3/5] Создаём дамп БД на VPS..." -ForegroundColor Yellow
    
    ssh $VPS_HOST "PGPASSWORD='$DB_PASSWORD' pg_dump -U $DB_USER -h localhost -d $DB_NAME -F c -b -f /tmp/$DUMP_FILE"
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Дамп создан на VPS" -ForegroundColor Green
    } else {
        Write-Host "  ОШИБКА при создании дампа!" -ForegroundColor Red
        exit 1
    }

    Write-Host "[4/5] Скачиваем дамп..." -ForegroundColor Yellow
    
    $localDump = Join-Path $LOCAL_PROJECT $DUMP_FILE
    scp "${VPS_HOST}:/tmp/$DUMP_FILE" $localDump
    
    $dumpSize = [math]::Round((Get-Item $localDump).Length / 1KB)
    Write-Host "  Скачан: $DUMP_FILE ($dumpSize KB)" -ForegroundColor Green

    Write-Host "[5/5] Восстанавливаем БД локально..." -ForegroundColor Yellow
    
    # Закрываем все подключения к БД
    $env:PGPASSWORD = $DB_PASSWORD
    psql -U $DB_USER -h localhost -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB_NAME' AND pid <> pg_backend_pid()" 2>$null | Out-Null
    
    # Восстанавливаем
    pg_restore -U $DB_USER -h localhost -d $DB_NAME --clean --if-exists $localDump 2>$null
    
    if ($LASTEXITCODE -le 1) {
        Write-Host "  БД восстановлена" -ForegroundColor Green
    } else {
        Write-Host "  Предупреждения при восстановлении (это нормально)" -ForegroundColor Yellow
    }
    
    # Удаляем дамп
    Remove-Item $localDump -ErrorAction SilentlyContinue
    Write-Host "  Временный файл дампа удалён" -ForegroundColor Gray
    
} else {
    Write-Host "[3/5] Пропуск БД (режим -NoDB)" -ForegroundColor Gray
    Write-Host "[4/5] Пропуск БД (режим -NoDB)" -ForegroundColor Gray
    Write-Host "[5/5] Пропуск БД (режим -NoDB)" -ForegroundColor Gray
}

# --- Готово ---
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Синхронизация завершена!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Для запуска проекта: .\START_ME.bat" -ForegroundColor Cyan
Write-Host ""
