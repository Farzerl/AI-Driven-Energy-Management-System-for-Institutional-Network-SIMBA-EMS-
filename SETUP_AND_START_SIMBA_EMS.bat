@echo off
setlocal EnableExtensions EnableDelayedExpansion
title SIMBA-EMS Setup and Start

REM ============================================================
REM SIMBA-EMS one-click setup for a downloaded/extracted repo
REM - Creates a fresh local .venv
REM - Installs application and Chronos-2 dependencies
REM - Verifies the trained model and key Python imports
REM - Starts SIMBA-EMS
REM ============================================================

cd /d "%~dp0"
set "ROOT=%CD%"
set "VENV=%ROOT%\.venv"
set "PYEXE=%VENV%\Scripts\python.exe"
set "STATE_DIR=%ROOT%\.setup_state"
set "STATE_FILE=%STATE_DIR%\dependency_hashes.txt"

echo.
echo ============================================================
echo                 SIMBA-EMS SETUP AND START
echo ============================================================
echo Repository: %ROOT%
echo.

REM ---- Basic repository checks ----
if not exist "%ROOT%\START_SIMBA_EMS.bat" (
    echo [ERROR] START_SIMBA_EMS.bat was not found.
    echo Extract the full repository first, then run this file from the repository root.
    goto :fail
)

if not exist "%ROOT%\src" (
    echo [ERROR] The src folder was not found.
    echo This file must be placed in the main SIMBA-EMS repository folder.
    goto :fail
)

REM ---- Find a compatible 64-bit Python ----
set "BOOTSTRAP_PY="

where py >nul 2>&1
if not errorlevel 1 (
    py -3.12 -c "import struct,sys; assert struct.calcsize('P')*8==64; print(sys.executable)" >nul 2>&1
    if not errorlevel 1 set "BOOTSTRAP_PY=py -3.12"
)

if not defined BOOTSTRAP_PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import struct,sys; assert sys.version_info[:2]==(3,12); assert struct.calcsize('P')*8==64" >nul 2>&1
        if not errorlevel 1 set "BOOTSTRAP_PY=python"
    )
)

if not defined BOOTSTRAP_PY (
    echo [ERROR] Python 3.12 64-bit was not found.
    echo Install Python 3.12 from python.org, enable "Add Python to PATH",
    echo then run this file again.
    goto :fail
)

echo [1/6] Python 3.12 64-bit detected.

REM ---- Create or validate the virtual environment ----
if not exist "%PYEXE%" (
    echo [2/6] Creating a fresh virtual environment...
    %BOOTSTRAP_PY% -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv.
        goto :fail
    )
) else (
    echo [2/6] Existing .venv found.
)

"%PYEXE%" -c "import sys,struct; assert sys.version_info[:2]==(3,12); assert struct.calcsize('P')*8==64" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] The existing .venv is not Python 3.12 64-bit.
    echo Delete the .venv folder and run this file again.
    goto :fail
)

REM ---- Compute dependency lock hashes ----
if not exist "%STATE_DIR%" mkdir "%STATE_DIR%" >nul 2>&1

set "APP_REQ="
if exist "%ROOT%\requirements.lock.txt" set "APP_REQ=%ROOT%\requirements.lock.txt"
if not defined APP_REQ if exist "%ROOT%\requirements.txt" set "APP_REQ=%ROOT%\requirements.txt"

if not defined APP_REQ (
    echo [ERROR] requirements.lock.txt or requirements.txt was not found.
    goto :fail
)

set "CHRONOS_REQ="
if exist "%ROOT%\requirements-chronos.lock.txt" set "CHRONOS_REQ=%ROOT%\requirements-chronos.lock.txt"

for /f "tokens=1" %%H in ('certutil -hashfile "%APP_REQ%" SHA256 ^| findstr /r /v "hash CertUtil"') do (
    set "APP_HASH=%%H"
    goto :apphashdone
)
:apphashdone

set "CHRONOS_HASH=none"
if defined CHRONOS_REQ (
    for /f "tokens=1" %%H in ('certutil -hashfile "%CHRONOS_REQ%" SHA256 ^| findstr /r /v "hash CertUtil"') do (
        set "CHRONOS_HASH=%%H"
        goto :chronoshashdone
    )
)
:chronoshashdone

set "NEED_INSTALL=1"
if exist "%STATE_FILE%" (
    set "OLD_APP_HASH="
    set "OLD_CHRONOS_HASH="
    for /f "tokens=1,2 delims==" %%A in (%STATE_FILE%) do (
        if /i "%%A"=="APP_HASH" set "OLD_APP_HASH=%%B"
        if /i "%%A"=="CHRONOS_HASH" set "OLD_CHRONOS_HASH=%%B"
    )
    if /i "!OLD_APP_HASH!"=="!APP_HASH!" if /i "!OLD_CHRONOS_HASH!"=="!CHRONOS_HASH!" (
        set "NEED_INSTALL=0"
    )
)

if "%NEED_INSTALL%"=="1" (
    echo [3/6] Installing required Python packages...
    "%PYEXE%" -m pip install --upgrade pip setuptools wheel
    if errorlevel 1 goto :pipfail

    "%PYEXE%" -m pip install --prefer-binary -r "%APP_REQ%"
    if errorlevel 1 goto :pipfail

    if defined CHRONOS_REQ (
        "%PYEXE%" -m pip install --prefer-binary -r "%CHRONOS_REQ%"
        if errorlevel 1 goto :pipfail
    )

    >"%STATE_FILE%" echo APP_HASH=!APP_HASH!
    >>"%STATE_FILE%" echo CHRONOS_HASH=!CHRONOS_HASH!
) else (
    echo [3/6] Dependencies already match the lock files.
)

REM ---- Verify core imports ----
echo [4/6] Verifying Python environment...
"%PYEXE%" -c "import fastapi,uvicorn,pandas,numpy,sklearn,torch,openpyxl; from chronos import Chronos2Pipeline; print('Dependency verification passed.')" 
if errorlevel 1 (
    echo [ERROR] One or more required Python packages could not be imported.
    echo Delete .setup_state and run this file again.
    goto :fail
)

REM ---- Verify trained model and routing evidence ----
echo [5/6] Checking trained Chronos-2 files...

if not exist "%ROOT%\models\chronos-2-finetuned" (
    echo [WARNING] models\chronos-2-finetuned was not found.
    echo SIMBA-EMS may fall back to the base or existing validated models.
)

if not exist "%ROOT%\models\chronos2\routing.json" (
    echo [WARNING] models\chronos2\routing.json was not found.
    echo Forecast routing evidence may be unavailable.
)

if not exist "%ROOT%\evidence\model_validation\chronos2_model_comparison.json" (
    echo [WARNING] Chronos-2 comparison evidence was not found.
)

REM ---- Start application ----
echo [6/6] Setup complete.
echo.
echo Starting SIMBA-EMS...
echo Close this window only after the application has opened.
echo.

call "%ROOT%\START_SIMBA_EMS.bat"
set "APP_EXIT=%ERRORLEVEL%"

if not "%APP_EXIT%"=="0" (
    echo.
    echo [ERROR] START_SIMBA_EMS.bat returned exit code %APP_EXIT%.
    goto :fail
)

exit /b 0

:pipfail
echo.
echo [ERROR] Dependency installation failed.
echo Check the internet connection, available disk space, and antivirus permissions.
goto :fail

:fail
echo.
echo Setup did not complete.
echo Review the message above, then run this file again.
pause
exit /b 1
