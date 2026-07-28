@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title SIMBA EMS Demonstration

echo ================================================================
echo SIMBA EMS - Institutional Energy Management Demonstration
echo ================================================================
echo.
echo This launcher will reuse the existing .venv when it is ready,
echo start the API, replay an authorised Central Kitchens sequence,
echo generate 30-minute to 24-hour forecasts, and open the dashboard.
echo.

call :prepare_environment
if errorlevel 1 goto :setup_failed

"%CD%\.venv\Scripts\python.exe" scripts\reset_demo_runtime.py

call :api_is_ready
if not errorlevel 1 goto :api_ready

echo Starting the API and dashboard service...
start "SIMBA EMS API - Ctrl+C to stop" cmd /k ""%CD%\.venv\Scripts\python.exe" -m uvicorn src.api.server:create_app --factory --host 127.0.0.1 --port 8000"
call :wait_for_api
if errorlevel 1 goto :api_failed

:api_ready
echo Replaying the historical Central Kitchens meter sequence...
"%CD%\.venv\Scripts\python.exe" -m src.edge.collector --config config\edge.example.json --once
if errorlevel 1 echo WARNING: Meter replay returned an error. Check the API window.

echo Waiting for the trained multi-horizon forecast...
call :wait_for_forecast
if errorlevel 1 (
    echo WARNING: No live forecast was detected within 75 seconds.
    echo The dashboard and simulation remain available. Check the API window.
) else (
    echo Trained multi-horizon forecast: READY
)

echo Opening the dashboard...
start "" "http://127.0.0.1:8000/?tab=demo"

echo.
echo Dashboard: http://127.0.0.1:8000
echo API docs:  http://127.0.0.1:8000/docs
echo Keep the SIMBA EMS API window open. Press Ctrl+C there to stop.
echo.
pause
exit /b 0

:prepare_environment
where py >nul 2>nul
if not errorlevel 1 (
    for %%V in (3.13 3.12 3.11) do (
        py -%%V -c "import sys" >nul 2>nul
        if not errorlevel 1 (
            py -%%V scripts\setup_and_launch.py --setup-only
            if errorlevel 1 exit /b 1
            exit /b 0
        )
    )
)
where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 14) else 1)" >nul 2>nul
    if not errorlevel 1 (
        python scripts\setup_and_launch.py --setup-only
        if errorlevel 1 exit /b 1
        exit /b 0
    )
)
echo ERROR: Python 3.11, 3.12, or 3.13 was not found.
exit /b 1

:api_is_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2; if ($r.status -eq 'online') { exit 0 } } catch {}; exit 1"
exit /b %ERRORLEVEL%

:wait_for_api
powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline = (Get-Date).AddSeconds(60); do { try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 3; if ($r.status -eq 'online') { exit 0 } } catch {}; Start-Sleep -Seconds 1 } while ((Get-Date) -lt $deadline); exit 1"
exit /b %ERRORLEVEL%

:wait_for_forecast
powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline = (Get-Date).AddSeconds(75); do { try { $p = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/live-forecasts?limit=1' -TimeoutSec 3; if ($null -ne $p.items -and @($p.items).Count -gt 0) { exit 0 } } catch {}; Start-Sleep -Seconds 1 } while ((Get-Date) -lt $deadline); exit 1"
exit /b %ERRORLEVEL%

:setup_failed
echo SETUP FAILED. Review the terminal error above and README.md.
pause
exit /b 1

:api_failed
echo API STARTUP FAILED. Port 8000 may already be in use.
pause
exit /b 1
