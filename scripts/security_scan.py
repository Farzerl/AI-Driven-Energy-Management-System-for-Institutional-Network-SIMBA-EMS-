from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_DIRECTORY_NAMES = {
    ".venv",
    "venv",
    "raw_data",
    "runtime",
    "logs",
}
FORBIDDEN_SUFFIXES = {
    ".pkl",
    ".joblib",
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".rar",
}
FORBIDDEN_ARCHIVE_PATTERNS = (
    re.compile(r"(?i)data.*uz.*energy.*\.zip$"),
)
PRIVATE_KEY_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
]
ASSIGNED_SECRET_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|password|passwd|secret|access[_-]?token)"
    r"\s*[:=]\s*[\"'](?P<value>[^\"']{8,})[\"']"
)
SAFE_PLACEHOLDER_PREFIXES = (
    "replace-with",
    "test-",
    "dummy-",
    "example-",
    "not-a-secret",
    "<configured",
)
TEXT_SUFFIXES = {
    ".py",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".md",
    ".txt",
    ".ps1",
    ".bat",
    ".js",
    ".html",
    ".css",
    ".csv",
}


def repository_files() -> list[str]:
    try:
        output = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True, timeout=20, stderr=subprocess.DEVNULL
        )
        return [line.strip() for line in output.splitlines() if line.strip()]
    except Exception:
        return sorted(
            path.relative_to(ROOT).as_posix()
            for path in ROOT.rglob("*")
            if path.is_file()
            and ".git" not in path.parts
            and not {part.lower() for part in path.relative_to(ROOT).parts}.intersection(
                FORBIDDEN_DIRECTORY_NAMES
            )
        )


def scan(root: Path = ROOT) -> dict[str, object]:
    findings: list[dict[str, object]] = []

    for directory in sorted(path for path in root.rglob("*") if path.is_dir()):
        if directory.name.lower() in FORBIDDEN_DIRECTORY_NAMES:
            findings.append(
                {
                    "severity": "critical",
                    "path": directory.relative_to(root).as_posix(),
                    "message": "Local, runtime, or cache directory is present.",
                }
            )

    files = repository_files() if root == ROOT else sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )

    for relative in files:
        path = root / relative
        parts_lower = {part.lower() for part in Path(relative).parts}

        bad_dirs = sorted(parts_lower.intersection(FORBIDDEN_DIRECTORY_NAMES))
        if bad_dirs:
            findings.append(
                {
                    "severity": "critical",
                    "path": relative,
                    "message": f"Local, runtime, or cache directory is included: {bad_dirs[0]}",
                }
            )
            continue

        lower = relative.lower()
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(
                {
                    "severity": "high",
                    "path": relative,
                    "message": "Unsafe binary, key, or unsupported model artefact is included.",
                }
            )

        if any(pattern.search(lower) for pattern in FORBIDDEN_ARCHIVE_PATTERNS):
            findings.append(
                {
                    "severity": "critical",
                    "path": relative,
                    "message": "Potential private institutional data archive is included.",
                }
            )

        if path.name == ".env" or (
            path.name.startswith(".env.") and path.name != ".env.example"
        ):
            findings.append(
                {
                    "severity": "high",
                    "path": relative,
                    "message": "Completed environment file is included.",
                }
            )

        if path.suffix.lower() not in TEXT_SUFFIXES or not path.exists():
            continue
        if relative.startswith("models/") and path.suffix.lower() == ".json":
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            continue

        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in PRIVATE_KEY_PATTERNS):
            findings.append(
                {
                    "severity": "high",
                    "path": relative,
                    "message": "Potential private key, cloud key, or access token.",
                }
            )
            continue

        for match in ASSIGNED_SECRET_PATTERN.finditer(text):
            value = match.group("value").strip().lower()
            if value.startswith(SAFE_PLACEHOLDER_PREFIXES):
                continue
            findings.append(
                {
                    "severity": "high",
                    "path": relative,
                    "message": "Potential committed secret assignment.",
                }
            )
            break

    required = [
        "README.md", "LICENSE",
        "src/api/server.py", "src/simulation/engine.py", "src/simulation/profiles.py",
        "src/simulation/schemas.py", "src/edge/collector.py", "src/edge/buffer.py",
        "src/live/service.py", "src/live/model_manager.py", "src/live/multihorizon.py",
        "src/live/dataset_loader.py", "src/costing/model.py", "dashboard/index.html",
        "dashboard/static/app.js", "dashboard/static/app.css",
        "evidence/public_dashboard/dashboard_evidence.json",
        "evidence/cost_impact/cost_impact_summary.json",
        "evidence/model_validation/institutional_multi_horizon_metrics.json",
        "evidence/model_validation/model_family_comparison.json",
        "models/neural/lstm_forecaster.json", "models/neural/transformer_forecaster.json",
        "models/neural/ensemble_config.json",
        "evidence/edge_runtime/edge_runtime_benchmark.json",
        "sample_data/edge_demo_readings.csv", "requirements-dashboard.lock.txt",
        "requirements.lock.txt", "requirements-dev.lock.txt", "requirements-training.lock.txt",
        "models/institutional_multi_horizon_forecaster.json", "scripts/setup_and_launch.py",
        "scripts/benchmark_edge_runtime.py", "scripts/train_model.py",
        "scripts/repository_audit.py", "docs/DEMO_DAY_MASTER_GUIDE.md",
        "docs/DEMO_DAY_CHEAT_SHEET.md", "docs/TEAM_DEMONSTRATION_ROLES.md",
        "docs/PITCH_SCRIPT_5_MIN.md", "docs/JUDGE_QA.md", "docs/RUBRIC_SCORECARD.md",
        "docs/SOFTWARE_IN_THE_LOOP_SPEC.md", "docs/OPENDSS_INTEGRATION_PLAN.md",
        "documentation/design.md", "documentation/architecture.md", "documentation/schema.md",
        "documentation/rules.md", "START_SIMBA_EMS.bat", "TRAIN_MODEL.bat",
        ".env.example",
    ]
    for relative in required:
        if not (root / relative).is_file():
            findings.append(
                {
                    "severity": "high",
                    "path": relative,
                    "message": "Required submission file is missing.",
                }
            )

    counts = {
        level: sum(item["severity"] == level for item in findings)
        for level in ("critical", "high", "medium", "low")
    }
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if counts["critical"] + counts["high"] == 0 else "fail",
        "files_checked": len(files),
        "counts": counts,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan the submission repository for secrets, local files and unsafe artefacts."
    )
    parser.add_argument("--output-dir", default="evidence/validation/security")
    args = parser.parse_args()

    result = scan()
    output = ROOT / args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / "security_scan_report.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )

    lines = [
        "# Security and Private-Data Scan",
        "",
        f"- Status: **{str(result['status']).upper()}**",
        f"- Files checked: {result['files_checked']}",
    ]
    for level, count in result["counts"].items():
        lines.append(f"- {level.title()}: {count}")
    lines.extend(["", "## Findings", ""])
    if result["findings"]:
        lines.extend(
            f"- **{item['severity']}** `{item['path']}`: {item['message']}"
            for item in result["findings"]
        )
    else:
        lines.append("No blocking findings.")

    (output / "security_scan_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
