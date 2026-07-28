from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_launcher_patch_files_exist():
    expected = [
        ROOT / "BUILD_SIMBA_EMS_LAUNCHER.bat",
        ROOT / "STOP_SIMBA_EMS.bat",
        ROOT / "START_SIMBA_EMS.bat",
        ROOT / "windows_launcher" / "SIMBAEMSLauncher.cs",
        ROOT / "windows_launcher" / "SIMBAEMS.exe.manifest",
        ROOT / "windows_launcher" / "simba-emblem.png",
        ROOT / "windows_launcher" / "simba-ems.ico",
    ]
    assert all(path.is_file() for path in expected)


def test_launcher_is_loopback_only_and_has_health_check():
    source = (ROOT / "windows_launcher" / "SIMBAEMSLauncher.cs").read_text(encoding="utf-8")
    assert "--host 127.0.0.1 --port 8000" in source
    assert "http://127.0.0.1:8000/api/health" in source
    assert "CreateNoWindow = true" in source
    assert "UseShellExecute = false" in source


def test_launcher_has_splash_browser_tray_and_safe_shutdown():
    source = (ROOT / "windows_launcher" / "SIMBAEMSLauncher.cs").read_text(encoding="utf-8")
    for token in [
        "SplashForm",
        "NotifyIcon",
        "Open dashboard",
        "Stop SIMBA-EMS",
        "Process.Start(new ProcessStartInfo(DashboardUrl)",
        "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
        "EventWaitHandle",
    ]:
        assert token in source


def test_launcher_checks_windows_11_x64():
    source = (ROOT / "windows_launcher" / "SIMBAEMSLauncher.cs").read_text(encoding="utf-8")
    assert "dwBuildNumber < 22000" in source
    assert "Environment.Is64BitOperatingSystem" in source
    assert "/platform:x64" in (ROOT / "BUILD_SIMBA_EMS_LAUNCHER.bat").read_text(encoding="utf-8")


def test_build_uses_windows_compiler_without_dependency_downloads():
    builder = (ROOT / "BUILD_SIMBA_EMS_LAUNCHER.bat").read_text(encoding="utf-8").lower()
    assert "microsoft.net\\framework64\\v4.0.30319\\csc.exe" in builder
    assert "pip install" not in builder
    assert "curl " not in builder
    assert "invoke-webrequest" not in builder


def test_existing_bat_fallback_is_retained():
    fallback = (ROOT / "START_SIMBA_EMS.bat").read_text(encoding="utf-8")
    assert "uvicorn src.api.server:create_app" in fallback
    assert "127.0.0.1" in fallback
