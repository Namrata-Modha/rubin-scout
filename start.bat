@echo off
title Rubin Scout
color 0B

echo.
echo  ============================================
echo    RUBIN SCOUT - Starting all services
echo  ============================================
echo.

:: Check if venv exists
if not exist "venv\Scripts\activate.bat" (
    echo  [!] No virtual environment found. Creating one...
    py -3.13 -m venv venv
    call venv\Scripts\activate.bat
    cd backend
    pip install -r requirements.txt
    cd ..
    echo  [OK] Dependencies installed
) else (
    call venv\Scripts\activate.bat
)

:: Start Docker PostgreSQL
echo  [1/3] Starting PostgreSQL...
docker compose up -d db 2>nul
if %errorlevel% neq 0 (
    echo  [!] Docker not running. Open Docker Desktop first, then retry.
    echo  [!] Backend will start but database queries will fail.
) else (
    echo  [OK] PostgreSQL running
)

:: Start backend in background
echo  [2/3] Starting backend on http://localhost:8000 ...
start "Rubin Scout Backend" cmd /k "cd /d %~dp0 && call venv\Scripts\activate.bat && cd backend && uvicorn app.main:app --reload --port 8000"

:: Wait a moment for backend to start
timeout /t 3 /nobreak >nul

:: Start frontend
echo  [3/3] Starting frontend on http://localhost:5173 ...
start "Rubin Scout Frontend" cmd /k "cd /d %~dp0\frontend && npm run dev"

:: Wait for frontend
timeout /t 4 /nobreak >nul

echo.
echo  ============================================
echo    ALL SERVICES RUNNING
echo  ============================================
echo.
echo    Dashboard:  http://localhost:5173
echo    API Docs:   http://localhost:8000/docs
echo    Database:   localhost:5432
echo.
echo    Close this window to keep services running.
echo    Or press any key to open the dashboard.
echo  ============================================
echo.

pause >nul
start http://localhost:5173
