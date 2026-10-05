@echo off
REM ============================================================
REM  CloudRive Private Cloud Drive - Local Launcher
REM  Pure ASCII: no encoding issues on any Windows.
REM ============================================================
setlocal
title CloudRive

cd /d "%~dp0"

echo ============================================================
echo    CloudRive - Private Cloud Drive
echo ============================================================
echo.

REM ---- locate python ----
set "PYEXE="
where python >nul 2>nul && set "PYEXE=python"
where py     >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
    echo [ERROR] Python not found.
    echo.
    echo Please install Python 3.10 or newer from:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANT: tick "Add Python to PATH" during setup.
    echo.
    pause
    exit /b 1
)

echo [1/3] Preparing environment...

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment, please wait 1-2 minutes...
    %PYEXE% -m venv venv
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to create virtual environment.
        echo Make sure you installed the FULL version of Python.
        echo.
        pause
        exit /b 1
    )
)

set "VENV_PY=venv\Scripts\python.exe"

echo [2/3] Installing dependencies, internet required on first run...
"%VENV_PY%" -m pip install --upgrade pip -q
"%VENV_PY%" -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install dependencies.
    echo Please check your internet connection, or run manually:
    echo   venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist "document" mkdir document
if not exist "data"     mkdir data

echo [3/3] Starting server...
echo.
echo   Panel:http://127.0.0.1:8000
echo   Storage:   %cd%\document
echo   Log file:   %cd%\data\server.log
echo.
echo   Keep this window open. Press Ctrl+C to stop.
echo ============================================================
echo.

REM Do NOT use --reload: it restarts the process and breaks
REM in-flight multipart uploads.
"%VENV_PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info

echo.
echo Server stopped.
pause