@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Build SIMBA-EMS Windows 11 Launcher

echo.
echo ================================================================
echo BUILDING SIMBA-EMS WINDOWS 11 GRAPHICAL LAUNCHER
echo ================================================================
echo This build uses the Microsoft C# compiler already supplied with the
echo Windows 11 .NET Framework. It does not download build dependencies.
echo.

if not exist "windows_launcher\SIMBAEMSLauncher.cs" (
  echo [ERROR] windows_launcher\SIMBAEMSLauncher.cs was not found.
  goto :failed
)
if not exist "windows_launcher\simba-ems.ico" (
  echo [ERROR] windows_launcher\simba-ems.ico was not found.
  goto :failed
)
if not exist "windows_launcher\simba-emblem.png" (
  echo [ERROR] windows_launcher\simba-emblem.png was not found.
  goto :failed
)

set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
  echo [ERROR] The Windows .NET Framework C# compiler was not found.
  echo Enable .NET Framework 4.8 in Windows Features and run this builder again.
  goto :failed
)

set "SOURCE=%CD%\windows_launcher\SIMBAEMSLauncher.cs"
set "ICON=%CD%\windows_launcher\simba-ems.ico"
set "MANIFEST=%CD%\windows_launcher\SIMBAEMS.exe.manifest"
set "LOGO=%CD%\windows_launcher\simba-emblem.png"
set "OUTPUT=%CD%\SIMBA-EMS.exe"

if exist "%OUTPUT%" del /f /q "%OUTPUT%" >nul 2>&1
if exist "runtime\build\launcher" rmdir /s /q "runtime\build\launcher" >nul 2>&1
mkdir "runtime\build\launcher" >nul 2>&1

echo Compiling a 64-bit, no-console Windows application...

REM IMPORTANT:
REM The comma and logical resource name must remain OUTSIDE the quoted
REM logo file path. Quoting "file.png,SimbaLogo" makes CSC treat the
REM comma and resource name as part of the physical filename.
"%CSC%" ^
 /nologo ^
 /target:winexe ^
 /platform:x64 ^
 /optimize+ ^
 /debug- ^
 /out:"%OUTPUT%" ^
 /win32icon:"%ICON%" ^
 /win32manifest:"%MANIFEST%" ^
 /resource:"%LOGO%",SimbaLogo ^
 /reference:System.dll ^
 /reference:System.Core.dll ^
 /reference:System.Drawing.dll ^
 /reference:System.Windows.Forms.dll ^
 "%SOURCE%" >"runtime\build\launcher\compiler.log" 2>&1

if errorlevel 1 (
  echo [ERROR] Launcher compilation failed.
  type "runtime\build\launcher\compiler.log"
  goto :failed
)

if not exist "%OUTPUT%" (
  echo [ERROR] The compiler returned without creating SIMBA-EMS.exe.
  goto :failed
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$h=(Get-FileHash -Algorithm SHA256 'SIMBA-EMS.exe').Hash.ToLower(); Set-Content -Encoding ASCII 'runtime\build\launcher\SIMBA-EMS.exe.sha256.txt' ($h + '  SIMBA-EMS.exe'); Write-Host ('SHA-256: ' + $h)"
if errorlevel 1 goto :failed

echo.
echo ================================================================
echo BUILD SUCCESSFUL
echo ================================================================
echo Application: %OUTPUT%
echo Normal users can now double-click SIMBA-EMS.exe.
echo START_SIMBA_EMS.bat remains available as the technical fallback.
echo.
echo The launcher starts the backend without command windows, waits for
echo health checks, opens the dashboard, and remains in the notification
echo area for Open Dashboard and Stop SIMBA-EMS controls.
echo ================================================================
pause
exit /b 0

:failed
echo.
echo [FAILED] The launcher was not built. Existing SIMBA-EMS files were not changed.
pause
exit /b 1
