@echo off
title FocusGuard AI Runner
cd /d "%~dp0"
echo ===================================================
echo   FocusGuard AI - He thong giam sat hoc tap bang AI
echo ===================================================
echo.

:: Buoc 1: Kiem tra va tao file .env neu chua co
if not exist .env (
    echo [INFO] Khong tim thay file .env. Dang tao tu file .env.example...
    copy .env.example .env
)

:: Buoc 2: Kiem tra moi truong ao Python venv
if not exist venv (
    echo [INFO] Dang tao moi truong ao Python venv...
    py -m venv venv
    echo [INFO] Dang cai dat thu vien Python requirements.txt...
    call venv\Scripts\activate
    python -m pip install --upgrade pip
    pip install -r requirements.txt
)

:: Buoc 3: Kiem tra loai co so du lieu
findstr /i "DATABASE_TYPE=firestore" .env >nul
if %errorlevel% == 0 (
    echo [INFO] Phat hien DATABASE_TYPE=firestore. Dang khoi chay Firebase Emulator...
    start "Firebase Emulator" cmd /k "npx firebase emulators:start --import=./data/firebase --export-on-exit=./data/firebase"
) else (
    echo [INFO] Co so du lieu: SQLite (cuc bo, khong can chay Firebase Emulator).
)

echo.
echo [INFO] Dang khoi chay FocusGuard AI Web Server...
start "FocusGuard AI Web Server" cmd /k "call venv\Scripts\activate && python app.py"

:: Tu dong mo trinh duyet sau 3 giay
echo [INFO] Dang mo trinh duyet toi http://127.0.0.1:5001 ...
timeout /t 3 /nobreak >nul
start http://127.0.0.1:5001

echo.
echo ===================================================
echo   Web FocusGuard AI: http://127.0.0.1:5001
echo   (Server dang chay o cua so rieng, khong tat cua so do khi dang su dung)
echo ===================================================
echo.
pause
