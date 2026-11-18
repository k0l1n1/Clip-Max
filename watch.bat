@echo off
setlocal

:: Detect Python executable, prioritizing real Python 3.12 over Windows Store alias
set "PYTHON_BIN="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYTHON_BIN=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%LOCALAPPDATA%\Programs\Python\Python312;%PATH%"
) else if exist "C:\Users\BIJU\AppData\Local\Programs\Python\Python312\python.exe" (
    set "PYTHON_BIN=C:\Users\BIJU\AppData\Local\Programs\Python\Python312\python.exe"
    set "PATH=C:\Users\BIJU\AppData\Local\Programs\Python\Python312\Scripts;C:\Users\BIJU\AppData\Local\Programs\Python\Python312;%PATH%"
) else (
    where py >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set "PYTHON_BIN=py -3"
    ) else (
        where python >nul 2>nul
        if %ERRORLEVEL% equ 0 (
            set "PYTHON_BIN=python"
        )
    )
)

if "%PYTHON_BIN%"=="" (
    echo [Error] Python 3 was not found. Please install Python or add it to PATH.
    exit /b 1
)

if "%~1"=="" (
    echo Usage: watch.bat ^<twitch_channel^> [options]
    echo.
    echo Examples:
    echo   watch.bat shroud
    echo   watch.bat xqc --min 5
    echo   watch.bat tarik --min 10 --quality 720p60 --model base
    echo.
    echo Tips:
    echo   - Quiet chats need --min 5
    echo   - It only works while the channel is live
    echo   - Clip streamers who allow it
    exit /b 1
)

set "PYTHONUNBUFFERED=1"
"%PYTHON_BIN%" "%~dp0clip_watcher.py" %*
