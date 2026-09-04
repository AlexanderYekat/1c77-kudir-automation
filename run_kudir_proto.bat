@echo off
rem Прототип КУДиР: python читает каталог обмена и пишет kudir_result.csv
rem Вызов: run_kudir_proto.bat "C:\path\to\exchange"
setlocal
set "ROOT=%~dp0"
set "EXCHANGE=%~1"
if "%EXCHANGE%"=="" set "EXCHANGE=%ROOT%exchange"
set "PYTHONPATH=%ROOT%src"
cd /d "%ROOT%"
python -m kudir_proto --dir "%EXCHANGE%" --scoring "%ROOT%config\scoring.yaml"
exit /b %ERRORLEVEL%
