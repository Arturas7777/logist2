# PowerShell скрипт для запуска проекта Logist2
# Временно разрешаем выполнение скриптов для текущей сессии

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "🚀 ЗАПУСК ПРОЕКТА LOGIST2" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Временно разрешаем выполнение скриптов
Write-Host "🔧 Настройка PowerShell..." -ForegroundColor Yellow
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process -Force

Write-Host "🔧 Активация виртуального окружения..." -ForegroundColor Yellow
& .\.venv\Scripts\Activate.ps1

Write-Host ""
Write-Host "🚀 Запуск проекта с PostgreSQL..." -ForegroundColor Green
python start_simple.py

Write-Host ""
Write-Host "Нажмите любую клавишу для выхода..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
