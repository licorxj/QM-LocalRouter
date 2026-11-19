@echo off
setlocal
set PORT=%~1
if "%PORT%"=="" set PORT=9222

tasklist /FI "IMAGENAME eq ZCode.exe" 2>nul | find /I "ZCode.exe" >nul
if not errorlevel 1 (
  echo [!] ZCode 正在运行。请先从托盘完全退出，再运行本脚本。
  echo     ^(带 --remote-debugging-port 启动必须先退出已有实例^)
  pause
  exit /b 1
)

echo 启动 ZCode，调试端口 %PORT% ...
start "" "D:\Program files\ZCode\ZCode.exe" --remote-debugging-port=%PORT% --remote-allow-origins=*
echo.
echo 等窗口加载完成后，想领的时候另开一个终端执行一次：
echo     node claim-now.mjs --port %PORT%
endlocal
