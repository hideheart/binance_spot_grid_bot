@echo off
title Binance DCA Bot

cd /d "%~dp0"

echo ===================================================
echo   Starting Binance DCA Bot...
echo ===================================================
echo.

if not exist ".venv\Scripts\python.exe" goto NO_VENV

:RUN
.venv\Scripts\python.exe dca_bot.py
if errorlevel 1 goto ERROR
goto END

:NO_VENV
echo [Error] Cannot find virtual environment .venv.
pause
exit /b 1

:ERROR
echo.
echo [Warning] Bot exited unexpectedly. Check log\dca_bot.log.
pause

:END
