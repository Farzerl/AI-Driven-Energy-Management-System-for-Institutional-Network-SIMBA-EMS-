from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.live.dataset_loader import load_dataset_archive
from src.live.power_quality_adapter import HORIZON_STEPS, PowerQualityChronosAdapter


def run(project_root: Path) -> dict[str, object]:
    project_root = Path(project_root).resolve()
    state_path = project_root / "runtime" / "power_quality_setup_state.json"
    metrics_path = project_root / "evidence" / "model_validation" / "power_quality_model_comparison.json"
    routing_path = project_root / "models" / "power_quality" / "routing.json"
    if not state_path.exists() or not metrics_path.exists() or not routing_path.exists():
        raise RuntimeError("Power-quality setup outputs are incomplete.")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    routing = json.loads(routing_path.read_text(encoding="utf-8"))
    if state.get("status") != "success":
        raise RuntimeError(f"Power-quality setup status is {state.get('status')!r}.")
    if not bool(dict(state.get("training", {})).get("succeeded", False)):
        raise RuntimeError("Required multi-target LoRA training did not succeed.")
    if metrics.get("status") != "pass" or not routing.get("eligible"):
        raise RuntimeError("Power-quality evidence or routing is not eligible.")

    selected = dict(routing.get("selected_by_target_horizon", {}))
    for target in ("active_power_kw", "reactive_power_kvar"):
        if set(dict(selected.get(target, {}))) != set(HORIZON_STEPS):
            raise RuntimeError(f"Routing is incomplete for {target}.")

    adapter = PowerQualityChronosAdapter(project_root)
    if not adapter.ready:
        raise RuntimeError(f"Runtime adapter is not ready: {adapter.status().get('error')}")

    archives = sorted((project_root / "training_data").glob("*.zip"))
    smoke: dict[str, object] = {"status": "not_run", "reason": "Dataset ZIP was not present."}
    if len(archives) == 1:
        processed = project_root / "runtime" / "power_quality_verify_data"
        data, _ = load_dataset_archive(archives[0], processed)
        facility = str(sorted(data["facility_id"].unique())[0])
        group = data[data["facility_id"] == facility].sort_values("timestamp").tail(336)
        payload = adapter.predict_batch({facility: group.to_dict("records")})
        if payload.get("status") != "success" or len(payload.get("items", [])) != 1:
            raise RuntimeError("One-facility trained-model smoke forecast failed.")
        item = dict(payload["items"][0])
        for horizon in HORIZON_STEPS:
            row = dict(dict(item.get("forecasts", {})).get(horizon, {}))
            required = (
                "forecast_active_power_kw",
                "forecast_reactive_power_kvar",
                "forecast_power_factor",
                "forecast_interval_energy_kwh",
                "forecast_interval_reactive_energy_kvarh_estimated",
            )
            if not all(math.isfinite(float(row[name])) for name in required):
                raise RuntimeError(f"Invalid derived output at {horizon}.")
            if not 0 <= float(row["forecast_power_factor"]) <= 1:
                raise RuntimeError(f"Power factor is outside 0–1 at {horizon}.")
        smoke = {
            "status": "pass",
            "facility": facility,
            "horizons": list(HORIZON_STEPS),
            "latency_ms": payload.get("latency_ms"),
        }
        import shutil

        shutil.rmtree(processed, ignore_errors=True)
    adapter.close()
    return {
        "status": "pass",
        "training": state.get("training"),
        "deployment_variant": routing.get("deployment_variant"),
        "targets": routing.get("targets"),
        "derived_outputs": routing.get("derived_outputs"),
        "smoke_inference": smoke,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.project_root), indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
