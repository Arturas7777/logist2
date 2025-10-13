@echo off
chcp 65001 >nul
echo.
echo ========================================
echo LOGIST2 - Django Development Server
echo ========================================
echo.

echo Activating virtual environment...
call .venv\Scripts\activate.bat

echo.
echo Starting Django server on http://localhost:8000
echo.
python manage.py runserver 0.0.0.0:8000

echo.
pause
