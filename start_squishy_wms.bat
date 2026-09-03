@echo off
setlocal enabledelayedexpansion
REM Squishy WMS -- daily startup.
REM Starts the backend and frontend servers, waits for both to come up,
REM then opens the app in your default browser. Just double-click this
REM file each day; nothing else to type.
REM
REM One-time setup (Python, Node, venv, npm install, admin password) must
REM already be done -- see the "Local setup" section in README.md. This
REM script does NOT do that part.

cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
    echo.
    echo ==============================================================
    echo   Can't find Node.js. Either it isn't installed, or it was
    echo   just installed and this computer hasn't been restarted yet
    echo   ^(Windows needs a restart to pick up new PATH entries^).
    echo   See the "Local setup" section in README.md.
    echo ==============================================================
    echo.
    pause
    exit /b 1
)

if not exist "venv\Scripts\activate.bat" (
    echo.
    echo ==============================================================
    echo   Setup isn't finished yet: venv\ doesn't exist.
    echo   See the "Local setup" section in README.md and run the
    echo   one-time setup steps first, then try this again.
    echo ==============================================================
    echo.
    pause
    exit /b 1
)

if not exist "frontend\node_modules" (
    echo.
    echo ==============================================================
    echo   Setup isn't finished yet: frontend\node_modules doesn't exist.
    echo   See the "Local setup" section in README.md and run
    echo   "npm install" in the frontend folder first, then try again.
    echo ==============================================================
    echo.
    pause
    exit /b 1
)

echo Starting Squishy WMS...
echo.

start "Squishy WMS - Backend" /D "%~dp0" cmd /k "call venv\Scripts\activate.bat && uvicorn app.api:app --reload --port 8010"
start "Squishy WMS - Frontend" /D "%~dp0frontend" cmd /k "npm run dev"

echo Backend and frontend are starting in their own windows.
echo Waiting for the app to be ready...

set READY_BACKEND=0
set READY_FRONTEND=0
set /a ATTEMPTS=0

:waitloop
set /a ATTEMPTS+=1

if %READY_BACKEND%==0 (
    curl -s -o NUL -w "%%{http_code}" http://localhost:8010/wall-sets > "%TEMP%\squishy_backend_status.txt" 2>NUL
    set /p BACKEND_STATUS=<"%TEMP%\squishy_backend_status.txt"
    if "!BACKEND_STATUS!"=="200" set READY_BACKEND=1
)

if %READY_FRONTEND%==0 (
    curl -s -o NUL -w "%%{http_code}" http://localhost:5173 > "%TEMP%\squishy_frontend_status.txt" 2>NUL
    set /p FRONTEND_STATUS=<"%TEMP%\squishy_frontend_status.txt"
    if "!FRONTEND_STATUS!"=="200" set READY_FRONTEND=1
)

if %READY_BACKEND%==1 if %READY_FRONTEND%==1 goto ready

if %ATTEMPTS% GEQ 60 goto timeout

timeout /t 1 /nobreak >NUL
goto waitloop

:ready
echo Both are up. Opening the app in your browser...
start http://localhost:5173
goto done

:timeout
echo.
echo ==============================================================
echo   Took longer than expected to start. Check the two new
echo   windows that opened (Backend / Frontend) for error messages.
echo   Opening the app anyway -- it may just need a few more seconds.
echo ==============================================================
echo.
start http://localhost:5173

:done
echo.
echo You can close this window. Keep the "Squishy WMS - Backend" and
echo "Squishy WMS - Frontend" windows open while you're using the app --
echo closing either of those will stop it.
echo.
pause
