@echo off
chcp 65001 > nul
title BTC/FDUSD 現貨網格交易機器人

cd /d "%~dp0"

echo ===================================================
echo   正在啟動 BTC/FDUSD 現貨網格交易機器人...
echo ===================================================
echo.

if not exist ".venv\Scripts\python.exe" goto NO_VENV

:RUN
.venv\Scripts\python.exe grid_bot.py
if errorlevel 1 goto ERROR
goto END

:NO_VENV
echo [錯誤] 找不到虛擬環境 .venv，請確認已正確初始化環境。
pause
exit /b 1

:ERROR
echo.
echo [警告] 機器人異常退出，詳細錯誤資訊請參閱 grid_bot.log。
pause

:END
