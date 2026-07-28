from __future__ import annotations

import csv
from pathlib import Path

from src.live.model_manager import LiveModelManager

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "institutional_multi_horizon_forecaster.json"


def replay_records() -> list[dict[str, object]]:
    with (ROOT / "sample_data" / "edge_demo_readings.csv").open(encoding="utf-8", newline="") as handle:
        return [
            {
                "timestamp": row["timestamp"],
                "facility_id": row["facility_id"],
                "kva": float(row["kva"]),
                "kwh": float(row["kwh"]),
                "power_factor": float(row["power_factor"]),
            }
            for row in csv.DictReader(handle)
        ]


def test_trained_model_loads_and_predicts_all_horizons() -> None:
    manager = LiveModelManager(MODEL)
    assert manager.ready is True
    records = replay_records()
    predictions = manager.predict_horizons(records, "Central Kitchens NC1 4")
    assert set(predictions) == {"30_minutes", "2_hours", "6_hours", "24_hours"}
    assert all(item["forecast_kva"] >= 0 for item in predictions.values())
    status = manager.status()
    assert status["source"] == "validated_institutional_models"
    assert status["facility_count"] == 22
    assert status["metrics"]["30_minutes"]["r2"] > 0.95
