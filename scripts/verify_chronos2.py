from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.live.chronos2_adapter import Chronos2Adapter

OFFICIAL_MODEL_SHA256 = "ddcda3c7508bf2528087723e98a20707cc04b7f370ae275a9fd88078ddba4f42"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--require-finetuned", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    metrics_path = root / "evidence" / "model_validation" / "chronos2_model_comparison.json"
    routing_path = root / "models" / "chronos2" / "routing.json"
    state_path = root / "runtime" / "chronos2_setup_state.json"
    failures: list[str] = []
    for path in (metrics_path, routing_path, state_path):
        if not path.exists():
            failures.append(f"Missing {path.relative_to(root)}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    routing = json.loads(routing_path.read_text(encoding="utf-8"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    config_path = root / "config" / "chronos2_training.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    if metrics.get("status") != "pass":
        failures.append("Metrics status is not pass.")
    if state.get("status") != "success":
        failures.append("Setup state is not success.")
    lora_required = bool(dict(config.get("lora", {})).get("enabled", False))
    training_succeeded = bool(dict(state.get("training", {})).get("succeeded", False))
    if lora_required and not training_succeeded:
        failures.append("LoRA fine-tuning is enabled but did not complete successfully.")
    if args.require_finetuned and not training_succeeded:
        failures.append("A fine-tuned checkpoint was explicitly required but training did not succeed.")
    finetuned_dir = root / "models" / "chronos-2-finetuned"
    if args.require_finetuned:
        if not (finetuned_dir / "config.json").exists() or not any(finetuned_dir.rglob("*.safetensors")):
            failures.append("The required fine-tuned Chronos-2 checkpoint is incomplete or missing.")
    model_metrics = dict(metrics.get("models", {}))
    for model_name in ("chronos_zero_shot", "chronos_lora"):
        if args.require_finetuned and model_name not in model_metrics:
            failures.append(f"Evidence is missing {model_name} metrics.")
        elif model_name in model_metrics:
            for split_name in ("validation", "test"):
                horizons = dict(dict(model_metrics[model_name]).get(split_name, {}))
                if set(horizons) != {"30_minutes", "2_hours", "6_hours", "24_hours"}:
                    failures.append(f"{model_name} {split_name} metrics do not contain all four horizons.")
    selected = routing.get("selected_by_horizon", {})
    expected = {"30_minutes", "2_hours", "6_hours", "24_hours"}
    if set(selected) != expected:
        failures.append("Routing does not contain all four horizons.")
    for horizon, item in selected.items():
        test = dict(item).get("test_metrics", {})
        for field in ("mae_kva", "high_risk_recall", "high_risk_f1"):
            value = float(dict(test).get(field, float("nan")))
            if not math.isfinite(value):
                failures.append(f"{horizon} has invalid {field}.")
    base_weights = root / "models" / "chronos-2-base" / "model.safetensors"
    if not base_weights.exists():
        failures.append("Official Chronos-2 base weights are missing.")
    elif sha256(base_weights) != OFFICIAL_MODEL_SHA256:
        failures.append("Official Chronos-2 base-weight checksum does not match amazon/chronos-2.")
    adapter = Chronos2Adapter(root)
    status = adapter.status()
    if not status["installed"]:
        failures.append("Chronos-2 model files are not installed.")
    if not status["package_available"]:
        failures.append("chronos-forecasting is not importable.")
    if failures:
        print("Chronos-2 verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "pass", "chronos2": status, "routing": selected}, indent=2))
    adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
