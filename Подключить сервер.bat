@echo off
chcp 65001 >nul
title Подключение к серверу BOT_CALENDAR
cd /d "%~dp0"

echo.
echo   ПОДКЛЮЧЕНИЕ К СЕРВЕРУ — делается один раз
echo   ---------------------------------------------
echo   Кладу на сервер ключ деплоя, чтобы дальше пароль не спрашивался.
echo   Сервер один раз попросит пароль root: наберите его и нажмите Enter.
echo   Символы при вводе не отображаются — это нормально.
echo.

set "BASH="
if exist "C:\Program Files\Git\bin\bash.exe" set "BASH=C:\Program Files\Git\bin\bash.exe"
if not defined BASH if exist "C:\Program Files (x86)\Git\bin\bash.exe" set "BASH=C:\Program Files (x86)\Git\bin\bash.exe"
if not defined BASH if exist "%LOCALAPPDATA%\Programs\Git\bin\bash.exe" set "BASH=%LOCALAPPDATA%\Programs\Git\bin\bash.exe"

if not defined BASH (
  echo   ОШИБКА: не найден Git Bash.
  echo   Установите Git для Windows: https://git-scm.com/download/win
  echo.
  pause
  exit /b 1
)

"%BASH%" -lc "./scripts/add_deploy_key.sh"
set CODE=%errorlevel%

echo.
if "%CODE%"=="0" (
  echo   ============================================
  echo    ГОТОВО! Ключ работает, пароль больше не нужен.
  echo    Напишите разработчику: «ключ добавил».
  echo   ============================================
) else (
  echo   Не получилось ^(код %CODE%^). Сообщение об ошибке — выше.
)
echo.
pause
