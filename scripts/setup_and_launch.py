from __future__ import annotations

import argparse
import hashlib
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCKFILE = ROOT / "requirements-dashboard.lock.txt"
VENV = ROOT / ".venv"
MARKER = VENV / ".ai4i_dashboard_setup"
SUPPORTED_MIN = (3, 11)
SUPPORTED_MAX_EXCLUSIVE = (3, 14)
DEFAULT_PIP_TIMEOUT = 120
DEFAULT_PIP_RETRIES = 10
INSTALL_ATTEMPTS = 3


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(
    command: list[str],
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    print("\n> " + " ".join(str(x) for x in command))
    return subprocess.run(command, cwd=ROOT, text=True, check=check, env=env)


def ensure_supported_python() -> None:
    current = sys.version_info[:2]
    if not (SUPPORTED_MIN <= current < SUPPORTED_MAX_EXCLUSIVE):
        raise RuntimeError(
            "Python 3.11, 3.12, or 3.13 is required. "
            f"Detected {sys.version.split()[0]}."
        )


def runtime_import_check(python: Path, *, announce: bool = False) -> bool:
    command = [
        str(python),
        "-c",
        (
            "import fastapi, numpy, pandas, pydantic, uvicorn; "
            "print('Dashboard dependencies: READY')"
        ),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=None if announce else subprocess.DEVNULL,
        stderr=None if announce else subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def pip_install_command(python: Path) -> list[str]:
    timeout = os.getenv("AI4I_PIP_TIMEOUT", str(DEFAULT_PIP_TIMEOUT)).strip()
    retries = os.getenv("AI4I_PIP_RETRIES", str(DEFAULT_PIP_RETRIES)).strip()
    command = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--prefer-binary",
        "--timeout",
        timeout,
        "--retries",
        retries,
        "-r",
        str(LOCKFILE),
    ]
    index_url = os.getenv("AI4I_PIP_INDEX_URL", "").strip()
    if index_url:
        command.extend(["--index-url", index_url])
    return command


def install_dashboard_dependencies(python: Path) -> None:
    print(
        "Installing pinned dashboard dependencies. "
        "The first setup requires internet access and may take several minutes."
    )
    command = pip_install_command(python)
    pip_env = os.environ.copy()
    pip_env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    pip_env.setdefault("PIP_DEFAULT_TIMEOUT", str(DEFAULT_PIP_TIMEOUT))

    for attempt in range(1, INSTALL_ATTEMPTS + 1):
        print(f"\nDependency installation attempt {attempt} of {INSTALL_ATTEMPTS} ...")
        result = run(command, check=False, env=pip_env)
        if result.returncode == 0 or runtime_import_check(python):
            print("Dashboard dependencies installed successfully.")
            return
        if attempt < INSTALL_ATTEMPTS:
            delay = 10 * attempt
            print(
                f"Package download did not complete. Waiting {delay} seconds before retrying."
            )
            time.sleep(delay)

    raise RuntimeError(
        "Dependency download failed after three extended attempts. "
        "This usually means the connection to pypi.org timed out, not that the pinned "
        "package versions are missing. Check internet access or a proxy/firewall, then "
        "run START_SIMBA_EMS.bat again. Advanced users may set AI4I_PIP_INDEX_URL to an "
        "approved Python package mirror."
    )


def ensure_environment(force: bool = False) -> Path:
    ensure_supported_python()
    if not LOCKFILE.exists():
        raise FileNotFoundError(f"Missing dependency lockfile: {LOCKFILE}")

    expected = digest(LOCKFILE)
    python = venv_python()
    if not python.exists():
        print("Creating isolated Python environment in .venv ...")
        run([sys.executable, "-m", "venv", str(VENV)])

    installed = MARKER.read_text(encoding="utf-8").strip() if MARKER.exists() else ""
    dependencies_ready = runtime_import_check(python)

    if force or installed != expected or not dependencies_ready:
        install_dashboard_dependencies(python)
        if not runtime_import_check(python, announce=True):
            raise RuntimeError("Dependencies installed, but the dashboard import check still failed.")
        MARKER.write_text(expected, encoding="utf-8")
    else:
        print("Pinned dashboard environment is already installed.")

    return python


def smoke_test(python: Path) -> None:
    run([
        str(python),
        "-c",
        "from src.api.server import create_app; app = create_app(); "
        "assert app.title == 'SIMBA Institutional Energy Management API'; "
        "print('API import check: PASS')",
    ])


def choose_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free local port was found between 8000 and 8019.")


def wait_for_health(port: int, timeout: float = 45.0) -> None:
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"Dashboard did not become ready at {url}: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set up and launch the AI4I Institutional EMS dashboard."
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument("--force-install", action="store_true")
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args()

    try:
        os.chdir(ROOT)
        python = ensure_environment(force=args.force_install)
        smoke_test(python)
        if args.run_tests:
            dev_lock = ROOT / "requirements-dev.lock.txt"
            if not dev_lock.exists():
                raise FileNotFoundError(f"Missing development lockfile: {dev_lock}")
            run([
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--timeout",
                str(DEFAULT_PIP_TIMEOUT),
                "--retries",
                str(DEFAULT_PIP_RETRIES),
                "-r",
                str(dev_lock),
            ])
            run([str(python), "-m", "pytest", "-q"])
        if args.setup_only:
            print("\nSetup completed successfully.")
            return 0

        port = choose_port(args.port)
        url = f"http://127.0.0.1:{port}/?tab=demo"
        print(f"\nStarting the dashboard at {url}")
        process = subprocess.Popen([
            str(python),
            "-m",
            "uvicorn",
            "src.api.server:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ], cwd=ROOT)
        wait_for_health(port)
        print("Dashboard status: READY")
        if not args.no_browser:
            webbrowser.open(url)
        print("Keep this window open while using the dashboard. Press Ctrl+C to stop it.")
        try:
            return process.wait()
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            print("\nDashboard stopped.")
            return 0
    except Exception as exc:
        print(f"\nSETUP OR STARTUP FAILED: {exc}", file=sys.stderr)
        print("Review README.md and the terminal error above for troubleshooting.", file=sys.stderr)
        if os.name == "nt" and sys.stdin.isatty():
            input("Press Enter to close...")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
