@echo off
:: ============================================================================
:: Row-Bot Launcher â€“ starts Ollama (if needed) and the system tray app
:: ============================================================================
title Row-Bot - Starting...

set "APP_DIR=%~dp0app"
set "PYTHON_DIR=%~dp0python"
set "PYTHON=%PYTHON_DIR%\python.exe"
set "PATH=%PYTHON_DIR%\Scripts;%PYTHON_DIR%;%PATH%"

:: â”€â”€ Disable user site-packages to avoid conflicts with system Python â”€â”€â”€â”€â”€â”€â”€â”€
set "PYTHONNOUSERSITE=1"

:: â”€â”€ Force UTF-8 for Python I/O so emoji in print() never crash on cp1252 â”€â”€â”€â”€
set "PYTHONIOENCODING=utf-8"

:: â”€â”€ Point tkinter at its bundled Tcl/Tk runtime data â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
set "TCL_LIBRARY=%PYTHON_DIR%\tcl\tcl8.6"
set "TK_LIBRARY=%PYTHON_DIR%\tcl\tk8.6"

:: â”€â”€ Point Playwright at the bundled Chromium browsers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
set "PLAYWRIGHT_BROWSERS_PATH=%PYTHON_DIR%\playwright-browsers"

:: â”€â”€ Find Ollama (optional â€” only needed for local models) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
set "OLLAMA_APP="
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" (
    set "OLLAMA_APP=%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
)

:: â”€â”€ Start Ollama if installed (launcher.py skips this for cloud defaults) â”€â”€â”€
if not defined OLLAMA_APP (
    :: Ollama not installed â€” this is fine for cloud-only setups
    goto :launch_app
)

echo Checking Ollama service...
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
if %ERRORLEVEL% NEQ 0 (
    echo Starting Ollama...
    echo NOTE: The Ollama window may appear briefly â€” you can safely close it.
    start "" "%OLLAMA_APP%"
    :: Give Ollama a few seconds to start up
    timeout /t 5 /nobreak >NUL
) else (
    echo Ollama is already running.
)

:: â”€â”€ Launch Row-Bot via system tray launcher â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
:launch_app
cd /d "%APP_DIR%"
"%PYTHON%" launcher.py
