@echo off
setlocal enabledelayedexpansion
title LocalRouter - Production Startup
cd /d "%~dp0"

echo ========================================
echo   LocalRouter - Production Startup
echo ========================================
echo.

REM Windows counterpart of start-prod.sh.
REM Runs ONE uvicorn process serving the built frontend (frontend\dist)
REM and the API on the same port - no Vite dev server, no Node at runtime.

if "%BACKEND_PORT%"=="" set "BACKEND_PORT=12002"
if "%HOST%"=="" set "HOST=0.0.0.0"

set "PYTHON=%~dp0backend\venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [ERROR] backend venv not found. Run install.bat first.
    pause
    exit /b 1
)

if not exist "run" mkdir "run"
set "PIDFILE=%~dp0run\prod-backend.pid"

REM ---- [1/2] Frontend build ----
if /I "%~1"=="rebuild" goto build
if exist "frontend\dist\index.html" (
    echo [1/2] Frontend build found ^(frontend\dist^), skipping build. Use "start-prod.bat rebuild" to rebuild.
    goto serve
)
if not exist "frontend\node_modules" (
    echo [ERROR] frontend\dist is missing and frontend is not installed. Run install.bat first.
    pause
    exit /b 1
)

:build
if not exist "frontend\node_modules" (
    echo [ERROR] frontend\node_modules missing. Run install.bat first.
    pause
    exit /b 1
)
echo [1/2] Building frontend (npm run build^)...
pushd frontend
call npm run build
if !ERRORLEVEL! NEQ 0 (
    popd
    echo [ERROR] Frontend build failed. Check the output above.
    pause
    exit /b 1
)
popd

:serve
REM ---- [2/2] Start server (API + built frontend on one port) ----
if exist "%PIDFILE%" (
    set /p OLDPID=<"%PIDFILE%"
    tasklist /FI "PID eq !OLDPID!" 2>nul | findstr /C:"!OLDPID!" >nul
    if !ERRORLEVEL! EQU 0 (
        echo Production server already running ^(PID: !OLDPID!^). Use stop-prod.bat first.
        pause
        exit /b 0
    )
    del /f "%PIDFILE%" >nul 2>&1
)

echo [2/2] Starting uvicorn on port %BACKEND_PORT% (API + frontend^)...
for /f %%i in ('powershell -NoProfile -Command "(Start-Process -FilePath '%PYTHON%' -ArgumentList '-m','uvicorn','app.main:app','--host','%HOST%','--port','%BACKEND_PORT%' -WorkingDirectory '%~dp0backend' -WindowStyle Minimized -PassThru).Id"') do set "NEWPID=%%i"
if not defined NEWPID (
    echo [ERROR] Failed to start server process.
    pause
    exit /b 1
)
> "%PIDFILE%" echo !NEWPID!
echo   Production server started ^(PID: !NEWPID!^)
echo.
echo   UI:       http://localhost:%BACKEND_PORT%
echo   API docs: http://localhost:%BACKEND_PORT%/docs
echo   Stop:     stop-prod.bat
echo.
pause
exit /b 0
