@echo off
setlocal

:: Detect Python executable
set "PYTHON_BIN="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYTHON_BIN=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%LOCALAPPDATA%\Programs\Python\Python312;%PATH%"
) else if exist "C:\Users\BIJU\AppData\Local\Programs\Python\Python312\python.exe" (
    set "PYTHON_BIN=C:\Users\BIJU\AppData\Local\Programs\Python\Python312\python.exe"
    set "PATH=C:\Users\BIJU\AppData\Local\Programs\Python\Python312\Scripts;C:\Users\BIJU\AppData\Local\Programs\Python\Python312;%PATH%"
) else (
    set "PYTHON_BIN=python"
)

set "PYTHONUNBUFFERED=1"
echo Starting clip-watcher Studio at http://127.0.0.1:8000 ...
"%PYTHON_BIN%" "%~dp0server.py" %*
