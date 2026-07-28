from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "README.md", "LICENSE",
    "START_SIMBA_EMS.bat", "TRAIN_MODEL.bat", "requirements-training.lock.txt",
    "models/institutional_multi_horizon_forecaster.json", ".env.example", ".github/workflows/quality.yml",
    "documentation/design.md", "documentation/architecture.md", "documentation/schema.md",
    "documentation/rules.md", "documentation/DESIGN-apple.md", "documentation/adaptive_learning.md",
    "docs/guideline_alignment.md", "docs/submission_evidence_index.md", "docs/architecture.md",
    "docs/api_reference.md", "docs/dataset_statement.md", "docs/ai_justification.md",
    "docs/model_card.md", "docs/operator_workflow.md", "docs/accessibility_check.md",
    "docs/business_model.md", "docs/deployment_plan.md", "docs/security_and_compliance.md",
    "docs/known_limitations.md", "docs/demo_walkthrough.md", "docs/DEMO_DAY_MASTER_GUIDE.md",
    "docs/DEMO_DAY_CHEAT_SHEET.md", "docs/TEAM_DEMONSTRATION_ROLES.md",
    "docs/PITCH_SCRIPT_5_MIN.md", "docs/JUDGE_QA.md", "docs/RUBRIC_SCORECARD.md",
    "docs/SOFTWARE_IN_THE_LOOP_SPEC.md", "docs/OPENDSS_INTEGRATION_PLAN.md",
    "docs/team_and_ownership.md", "docs/claim_register.md",
    "docs/diagrams/system_architecture.svg", "docs/diagrams/data_flow.svg",
    "evidence/public_dashboard/dashboard_evidence.json", "evidence/cost_impact/cost_impact_summary.json",
    "evidence/model_validation/institutional_multi_horizon_metrics.json",
    "evidence/model_validation/model_family_comparison.json",
    "models/neural/lstm_forecaster.json", "models/neural/transformer_forecaster.json",
    "models/neural/ensemble_config.json", "docs/MODEL_COMPARISON_REPORT.md",
    "docs/ADMIN_GUIDE.md", "docs/CLIENT_OPERATOR_GUIDE.md", "docs/DEFENSIVE_BACKEND.md",
    "evidence/edge_runtime/edge_runtime_benchmark.json", "evidence/controller_comparison/software_in_the_loop_comparison.json", "dashboard/index.html",
    "dashboard/static/app.js", "dashboard/static/app.css", "dashboard/static/assets/simba-emblem.png", "src/api/server.py",
    "src/simulation/engine.py", "src/simulation/profiles.py", "src/simulation/schemas.py", "data/simulation/scenarios.json",
    "src/edge/collector.py", "src/live/service.py", "src/live/model_manager.py", "src/live/adaptation.py", "src/config/system_settings.py",
    "src/live/multihorizon.py", "src/live/dataset_loader.py", "src/costing/model.py",
    "scripts/benchmark_edge_runtime.py", "scripts/train_model.py",
    "scripts/repository_audit.py", "scripts/security_scan.py", "tests/test_repository_hygiene.py",
]

FORBIDDEN_ROOT_ENTRIES = {
    ".venv",
    "release",
    "ci",
    "proposal",
    "screenshots",
    "env.example",
}
TRANSIENT_DIRECTORY_NAMES = {"runtime", "logs", ".venv", "venv"}
STALE_TEXT = {
    "cost_backtest\":\"deferred": "Public evidence still says cost work is deferred.",
    "User validation required before submission": "Obsolete mandatory validation wording remains.",
    "Place the final Track 3 proposal": "Empty proposal placeholder wording remains.",
    "open the release folder": "Duplicate release-launch instructions remain.",
    "No verified electricity-bill savings are claimed.": (
        "Ambiguous old wording remains; use 'no realised savings' and distinguish the planning estimate."
    ),
    "not verified savings": "Ambiguous cost wording remains; use 'not realised savings'.",
    "verified financial savings": "Ambiguous cost wording remains; distinguish planning evidence from realised savings.",
}
LINK_PATTERN = re.compile(r"\[[^\]]+\]\((?!https?://|mailto:|#)([^)]+)\)")


def _relative_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and not {part.lower() for part in path.relative_to(root).parts}.intersection(
            TRANSIENT_DIRECTORY_NAMES
        )
    )


def _check_markdown_links(root: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in _relative_files(root):
        if path.suffix.lower() != ".md":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in LINK_PATTERN.finditer(text):
            target = match.group(1).split("#", 1)[0].strip()
            if not target or target.startswith("<"):
                continue
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                findings.append(
                    {
                        "severity": "medium",
                        "path": path.relative_to(root).as_posix(),
                        "message": f"Link escapes repository: {target}",
                    }
                )
                continue
            if not resolved.exists():
                findings.append(
                    {
                        "severity": "high",
                        "path": path.relative_to(root).as_posix(),
                        "message": f"Broken local link: {target}",
                    }
                )
    return findings


def audit(root: Path = ROOT) -> dict[str, object]:
    findings: list[dict[str, str]] = []

    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            findings.append(
                {
                    "severity": "high",
                    "path": relative,
                    "message": "Required guideline-alignment file is missing.",
                }
            )

    for entry in FORBIDDEN_ROOT_ENTRIES:
        if (root / entry).exists():
            findings.append(
                {
                    "severity": "high",
                    "path": entry,
                    "message": "Duplicate, local, stale, or placeholder root entry is present.",
                }
            )

    for directory in sorted(path for path in root.rglob("*") if path.is_dir()):
        if directory.name.lower() in TRANSIENT_DIRECTORY_NAMES:
            findings.append(
                {
                    "severity": "high",
                    "path": directory.relative_to(root).as_posix(),
                    "message": "Transient cache, log, or runtime directory is present.",
                }
            )

    text_files = []
    for path in _relative_files(root):
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() not in {".md", ".txt", ".json", ".html", ".js", ".py"}:
            continue
        if relative == "scripts/repository_audit.py" or relative.startswith("evidence/validation/"):
            continue
        if relative.startswith("models/") and path.suffix.lower() == ".json":
            continue
        text_files.append(path)
    for path in text_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for stale, message in STALE_TEXT.items():
            if stale in text:
                findings.append(
                    {
                        "severity": "medium",
                        "path": path.relative_to(root).as_posix(),
                        "message": message,
                    }
                )

    findings.extend(_check_markdown_links(root))

    screenshots = list((root / "docs" / "screenshots").glob("*.png")) if (root / "docs" / "screenshots").is_dir() else []

    evidence = root / "evidence" / "public_dashboard" / "dashboard_evidence.json"
    if evidence.is_file():
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        if payload.get("dataset_quality", {}).get("facilities") != 22:
            findings.append(
                {
                    "severity": "high",
                    "path": evidence.relative_to(root).as_posix(),
                    "message": "Verified facility count does not match the submission claim.",
                }
            )
        cost_status = payload.get("submission_status", {}).get("cost_backtest", "")
        if "public-source" not in cost_status:
            findings.append(
                {
                    "severity": "medium",
                    "path": evidence.relative_to(root).as_posix(),
                    "message": "Cost evidence status is not aligned with Stage 4.",
                }
            )

    total_bytes = sum(path.stat().st_size for path in _relative_files(root))
    if total_bytes > 25 * 1024 * 1024:
        findings.append(
            {
                "severity": "medium",
                "path": ".",
                "message": f"Repository package is unexpectedly large: {total_bytes / 1024 / 1024:.1f} MB.",
            }
        )

    counts = {
        level: sum(item["severity"] == level for item in findings)
        for level in ("critical", "high", "medium", "low")
    }
    status = "pass" if counts["critical"] + counts["high"] == 0 else "fail"
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "files_checked": len(_relative_files(root)),
        "package_size_bytes": total_bytes,
        "screenshot_count": len(screenshots),
        "counts": counts,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit AI4I submission structure, documentation, and repository hygiene."
    )
    parser.add_argument("--output-dir", default="evidence/validation/repository")
    args = parser.parse_args()

    result = audit()
    output = ROOT / args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / "repository_audit_report.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    lines = [
        "# Repository Alignment Audit",
        "",
        f"- Status: **{str(result['status']).upper()}**",
        f"- Files checked: {result['files_checked']}",
        f"- Package size: {result['package_size_bytes'] / 1024 / 1024:.2f} MB",
        f"- Dashboard screenshots: {result['screenshot_count']}",
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
        lines.append("No alignment findings.")
    (output / "repository_audit_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
