@echo off
rem Скопируйте этот файл в корень своего проекта (рядом с папками 1cv77 и 1cv77-extforms).
rem Укажите путь к sync_modules.py из репозитория 1cv77-devtools.
set "SYNC_PY=C:\Users\Enduro\Documents\1C77 Async Mark Check\1cv77-devtools\sync_modules.py"

cd /d "%~dp0"
python "%SYNC_PY%"
if errorlevel 1 pause
