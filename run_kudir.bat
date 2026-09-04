@echo off
rem Matcher КУДиР цикла 1: python читает каталог обмена и пишет kudir_result.csv
rem Вызов: run_kudir.bat "C:\path\to\exchange\<run_id>"
setlocal
set "ROOT=%~dp0"
set "EXCHANGE=%~1"
if "%EXCHANGE%"=="" set "EXCHANGE=%ROOT%exchange"
set "PYTHONPATH=%ROOT%src"
set "LOG=%EXCHANGE%\python_run.log"
if not exist "%EXCHANGE%" mkdir "%EXCHANGE%"
cd /d "%ROOT%"

echo [%DATE% %TIME%] ROOT=%ROOT%> "%LOG%"
echo [%DATE% %TIME%] PYTHONPATH=%PYTHONPATH%>> "%LOG%"
echo [%DATE% %TIME%] python -m kudir --dir "%EXCHANGE%" --scoring "%ROOT%config\scoring.yaml">> "%LOG%"

python -m kudir --dir "%EXCHANGE%" --scoring "%ROOT%config\scoring.yaml" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%DATE% %TIME%] exit=%RC%>> "%LOG%"

if not exist "%EXCHANGE%\run_status.csv" (
	if exist "%EXCHANGE%\kudir_result.csv" del /q "%EXCHANGE%\kudir_result.csv"
	echo key;value> "%EXCHANGE%\run_status.csv"
	echo status;FAILED>> "%EXCHANGE%\run_status.csv"
	echo tax_ready;0>> "%EXCHANGE%\run_status.csv"
	echo error;Python did not create run_status.csv, code %RC%. See python_run.log>> "%EXCHANGE%\run_status.csv"
)

exit /b %RC%
