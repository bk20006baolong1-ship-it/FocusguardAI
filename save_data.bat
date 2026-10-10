@echo off
title FocusGuard AI - Save Data
echo ===================================================
echo   Dang luu du lieu Firebase Emulator vao o dia...
echo ===================================================
echo.
call npx firebase emulators:export ./data/firebase
echo.
echo [SUCCESS] Da luu du lieu tu RAM vao thu muc ./data/firebase thanh cong!
pause
