@echo off
REM Build the LocalRouter Windows installer (Inno Setup .exe).
REM Wrapper so the PowerShell script can be started by double-click / cmd.
REM Usage: scripts\build-windows-installer.bat [-SkipFrontend] [-SkipBackend] [-Version x.y.z]
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-windows-installer.ps1" %*
set "EC=%ERRORLEVEL%"
endlocal & exit /b %EC%
