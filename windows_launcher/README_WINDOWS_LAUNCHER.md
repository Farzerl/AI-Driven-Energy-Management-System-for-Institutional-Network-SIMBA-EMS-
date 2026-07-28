# SIMBA-EMS Windows 11 Graphical Launcher

## Purpose

This patch replaces the normal visible BAT startup experience with a small Windows 11 launcher while retaining `START_SIMBA_EMS.bat` as the technical fallback.

The launcher does not package the entire Python environment or models into the EXE. It starts the existing installed SIMBA-EMS repository and `.venv` without displaying Command Prompt windows.

## Included capabilities

- SIMBA-EMS application icon generated from the existing project emblem.
- Branded splash screen with startup progress.
- Windows 11 x64 compatibility check.
- Required-file and virtual-environment checks.
- Backend startup with `CreateNoWindow=true` and hidden process windows.
- Loopback-only service at `127.0.0.1:8000`.
- Startup health check against `/api/health`.
- Optional live-forecast readiness check.
- Automatic opening of the Home dashboard in the default browser.
- System-tray controls for:
  - Open dashboard.
  - Open runtime logs.
  - Stop SIMBA-EMS.
- One running launcher instance at a time.
- Windows Job Object ownership so the launcher does not leave an orphan backend process.
- Runtime logs in `runtime\logs`.
- `STOP_SIMBA_EMS.bat` as a recovery stop command.
- Existing `START_SIMBA_EMS.bat` retained unchanged.

## Build once on Windows 11

1. Ensure the current SIMBA-EMS repository already works with `START_SIMBA_EMS.bat`.
2. Run `BUILD_SIMBA_EMS_LAUNCHER.bat`.
3. The builder uses the Microsoft C# compiler included with the Windows .NET Framework. It does not download packages.
4. After the build succeeds, double-click `SIMBA-EMS.exe`.

The build output hash is written to:

`runtime\build\launcher\SIMBA-EMS.exe.sha256.txt`

## Normal operation

1. Double-click `SIMBA-EMS.exe`.
2. The branded loading screen appears.
3. The launcher validates Windows and the local runtime.
4. The local backend starts without console windows.
5. Health checks complete.
6. The dashboard opens in the default browser.
7. The launcher remains in the Windows notification area.

Double-click the tray icon to reopen the dashboard. Right-click it to open logs or stop the system.

## Safe shutdown

Preferred methods:

- Right-click the SIMBA-EMS tray icon and select **Stop SIMBA-EMS**.
- Run `STOP_SIMBA_EMS.bat` as a fallback.
- Run `SIMBA-EMS.exe --stop` from a technical terminal.

The launcher owns the backend process through a Windows Job Object. Closing the launcher therefore stops the launcher-owned backend process tree instead of leaving an invisible process running.

## Security and antivirus posture

The launcher:

- Uses the Windows-supplied C# compiler.
- Is not packed, obfuscated or self-extracting.
- Does not download or execute remote code.
- Requires no administrator rights during normal use.
- Binds SIMBA-EMS to `127.0.0.1`, not all network interfaces.
- Starts only repository-local Python commands.
- Writes logs and a small state record only inside the SIMBA-EMS repository.

These choices reduce false-positive risk, but an unsigned executable cannot be guaranteed to avoid every Microsoft Defender or SmartScreen warning. Institutional distribution should use a trusted code-signing certificate and timestamped signatures.

## Recovery

`START_SIMBA_EMS.bat` remains the authoritative technical fallback. It is not removed or replaced by this patch.
