@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if exist "SIMBA-EMS.exe" (
  start "" /wait "SIMBA-EMS.exe" --stop
  exit /b %ERRORLEVEL%
)

echo SIMBA-EMS.exe has not been built yet.
echo Run BUILD_SIMBA_EMS_LAUNCHER.bat, or stop the fallback API window with Ctrl+C.
pause
exit /b 1
