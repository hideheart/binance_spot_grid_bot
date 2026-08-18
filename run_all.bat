@echo off
chcp 65001 > nul
title Spot Grid + DCA + Dashboard

cd /d "%~dp0"

echo ===================================================
echo   Starting grid + DCA + Dashboard
echo   Open http://localhost:5000 in browser
echo ===================================================
echo.

if not exist ".venv\Scripts\python.exe" goto NO_VENV

.venv\Scripts\python.exe run_all.py
if errorlevel 1 goto ERROR
goto END

:NO_VENV
echo [ERROR] Cannot find .venv\Scripts\python.exe
pause
exit /b 1

:ERROR
echo.
echo [ERROR] Process exited unexpectedly. Check log\grid_bot.log and log\dca_bot.log
pause
exit /b 1

:END
echo.
pause
