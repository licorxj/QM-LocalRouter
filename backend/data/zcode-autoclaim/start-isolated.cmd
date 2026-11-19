@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem ZCode isolated-profile launcher (ASCII-only to avoid cmd codepage issues)
rem Usage:
rem   start-isolated.cmd acct_a 9223
rem   start-isolated.cmd "D:\zcode-profiles\acct_a" 9223
rem
rem Optional env overrides:
rem   ZCODE_EXE                 path to ZCode.exe
rem   ZCODE_ISOLATED_DRY_RUN=1 print the launch command without starting ZCode
rem
rem First launch of each profile is a clean account; sign in manually once.

set "PROFILE=%~1"
set "PORT=%~2"
if "%PROFILE%"=="" (
  echo Usage: start-isolated.cmd ^<profile-name-or-dir^> [port]
  echo Example: start-isolated.cmd acct_a 9223
  exit /b 1
)
if "%PORT%"=="" set "PORT=9223"

set "ISPATH="
if not "%PROFILE:\=%"=="%PROFILE%" set "ISPATH=1"
if not "%PROFILE:/=%"=="%PROFILE%" set "ISPATH=1"

set "ROOT=D:\zcode-profiles"
if defined ISPATH (
  set "DIR=%PROFILE%"
) else (
  set "DIR=%ROOT%\%PROFILE%"
)

if not defined ZCODE_EXE set "ZCODE_EXE=D:\Program files\ZCode\ZCode.exe"
if not exist "%ZCODE_EXE%" (
  echo [!] ZCode not found: %ZCODE_EXE%
  exit /b 1
)

if not exist "%DIR%" mkdir "%DIR%" 2>nul
if not exist "%DIR%\home" mkdir "%DIR%\home" 2>nul
if not exist "%DIR%\user" mkdir "%DIR%\user" 2>nul
if not exist "%DIR%\electron-user-data" mkdir "%DIR%\electron-user-data" 2>nul
if not exist "%DIR%\electron-session-data" mkdir "%DIR%\electron-session-data" 2>nul

set "HOME=%DIR%\home"
set "USERPROFILE=%DIR%\user"
set "ZCODE_DATA_BASE_DIR=%DIR%"
set "ZCODE_DESKTOP_HOME_DIR=%DIR%\home"
set "ZCODE_DESKTOP_USER_DATA_DIR=%DIR%\electron-user-data"
set "ZCODE_DESKTOP_SESSION_DATA_DIR=%DIR%\electron-session-data"
set "ZCODE_DESKTOP_APPLICATION_NAME=ZCode-%~nx1"

echo Profile : %DIR%
echo Port    : %PORT%
echo Logs    : %DIR%\.zcode\v2\logs
echo.

if "%ZCODE_ISOLATED_DRY_RUN%"=="1" (
  echo [dry-run] "%ZCODE_EXE%" --remote-debugging-port=%PORT% --remote-allow-origins=* --user-data-dir="%DIR%\electron-user-data"
  echo [dry-run] HOME=%HOME%
  echo [dry-run] USERPROFILE=%USERPROFILE%
  echo [dry-run] ZCODE_DATA_BASE_DIR=%ZCODE_DATA_BASE_DIR%
  echo [dry-run] ZCODE_DESKTOP_USER_DATA_DIR=%ZCODE_DESKTOP_USER_DATA_DIR%
  echo [dry-run] ZCODE_DESKTOP_APPLICATION_NAME=%ZCODE_DESKTOP_APPLICATION_NAME%
  exit /b 0
)

start "ZCode isolated %~nx1" "%ZCODE_EXE%" --remote-debugging-port=%PORT% --remote-allow-origins=* --user-data-dir="%DIR%\electron-user-data"

echo Started. After the window loads and you sign in, run in another terminal:
echo     node claim-now.mjs --port %PORT% --profile "%DIR%"
echo.
echo Note: each profile must use a different --remote-debugging-port.
endlocal
