@echo off
title FocusGuard AI Runner
echo ===================================================
echo   FocusGuard AI - He thong giam sat hoc tap bang AI
echo ===================================================
echo.

:: Buoc 1: Kiem tra va tao file .env neu chua co
if not exist .env (
    echo [INFO] Khong tim thay file .env. Dang tao tu file .env.example...
    copy .env.example .env
)

:: Buoc 2: Kiem tra va cai dat npm packages cho Firebase Emulator
if not exist node_modules (
    echo [INFO] Dang cai dat Node packages theo package-lock.json firebase-tools...
    call npm ci
)

:: Buoc 3: Kiem tra va thiet lap moi truong ao Python venv
if not exist venv (
    echo [INFO] Dang tao moi truong ao Python venv...
    py -m venv venv
    echo [INFO] Dang kich hoat venv va cai dat thu vien Python requirements.txt...
    call venv\Scripts\activate
    python -m pip install --upgrade pip
    pip install -r requirements.txt
)

echo.
echo [SUCCESS] Da chuan bi xong moi truong!
echo [INFO] Dang khoi chay Firebase Emulator o cua so moi...
start "Firebase Emulator" cmd /k "npx firebase emulators:start --import=./data/firebase --export-on-exit=./data/firebase"

echo [INFO] Dang khoi chay Flask Web Server o cua so moi...
start "Flask Web Server" cmd /k "call venv\Scripts\activate && set SOCKETIO_ASYNC_MODE=threading && python app.py"

echo.
echo ===================================================
echo   Firebase Emulator Suite: http://127.0.0.1:4000
echo   Web FocusGuard AI:        http://127.0.0.1:5001
echo ===================================================
echo.
pause
