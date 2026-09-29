@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=backend\.venv311\Scripts\python.exe"
if not exist "%PYTHON%" (
	echo Missing backend Python environment: %PYTHON%
	exit /b 1
)

if "%~1"=="" (
	"%PYTHON%" manage-local.py start
) else (
	"%PYTHON%" manage-local.py %*
)
exit /b %errorlevel%
endlocal
