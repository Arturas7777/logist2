@echo off
chcp 65001 >nul
echo.
echo ========================================
echo 🚀 ЗАПУСК ПРОЕКТА LOGIST2
echo ========================================
echo.

echo 🔧 Активация виртуального окружения...
call .venv\Scripts\activate.bat

echo.
echo 🚀 Запуск проекта с PostgreSQL...
python start_simple.py

echo.
pause
