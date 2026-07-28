from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_downloaded_repository_contains_submission_runtime_files() -> None:
    required = [
        "README.md", "LICENSE", "pyproject.toml",
        "src/api/server.py", "src/api/evidence_store.py", "src/api/cost_store.py",
        "src/api/meter_store.py", "src/edge/collector.py", "src/edge/buffer.py",
        "src/live/service.py", "src/live/model_manager.py", "src/live/multihorizon.py",
        "src/live/dataset_loader.py", "src/live/adaptation.py", "src/config/system_settings.py", "src/costing/model.py", "src/simulation/engine.py",
        "src/simulation/profiles.py", "src/simulation/schemas.py", "data/simulation/scenarios.json", "dashboard/index.html",
        "dashboard/static/app.js", "dashboard/static/app.css", "dashboard/static/assets/simba-emblem.png",
        "evidence/public_dashboard/dashboard_evidence.json",
        "evidence/cost_impact/cost_impact_summary.json",
        "evidence/model_validation/institutional_multi_horizon_metrics.json",
        "evidence/model_validation/model_family_comparison.json",
        "models/neural/lstm_forecaster.json", "models/neural/transformer_forecaster.json",
        "models/neural/ensemble_config.json",
        "evidence/edge_runtime/edge_runtime_benchmark.json", "evidence/controller_comparison/software_in_the_loop_comparison.json",
        "sample_data/edge_demo_readings.csv", "requirements-dashboard.lock.txt",
        "requirements.lock.txt", "requirements-dev.lock.txt", "requirements-training.lock.txt",
        "models/institutional_multi_horizon_forecaster.json", "scripts/setup_and_launch.py",
        "scripts/security_scan.py", "scripts/repository_audit.py",
        "scripts/benchmark_edge_runtime.py", "scripts/train_model.py",
        "docs/DEMO_DAY_MASTER_GUIDE.md", "docs/PITCH_SCRIPT_5_MIN.md",
        "docs/JUDGE_QA.md", "docs/RUBRIC_SCORECARD.md", "documentation/design.md",
        "documentation/architecture.md", "documentation/schema.md", "documentation/rules.md", "documentation/adaptive_learning.md",
        "documentation/DESIGN-apple.md", "START_SIMBA_EMS.bat", "TRAIN_MODEL.bat",
        ".env.example",
    ]
    missing = [relative for relative in required if not (ROOT / relative).is_file()]
    assert missing == []


def test_raw_and_local_content_is_not_present() -> None:
    forbidden = [".venv", "raw_data", "runtime", "release", "ci", "proposal", "screenshots", "env.example"]
    assert [item for item in forbidden if (ROOT / item).exists()] == []
    assert not any(ROOT.glob("**/*DATA*UZ*ENERGY*.zip"))


def test_obsolete_executable_launchers_are_not_present() -> None:
    assert not any(ROOT.glob("**/AI4I_EMS_Setup_and_Launch.exe"))
