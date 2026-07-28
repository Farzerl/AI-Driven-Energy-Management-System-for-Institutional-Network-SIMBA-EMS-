from __future__ import annotations

import csv
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.server import create_app

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "institutional_multi_horizon_forecaster.json"


def readings() -> list[dict[str, object]]:
    with (ROOT / "sample_data" / "edge_demo_readings.csv").open(encoding="utf-8", newline="") as handle:
        return [
            {
                "timestamp": row["timestamp"],
                "facility_id": row["facility_id"],
                "kva": float(row["kva"]),
                "kwh": float(row["kwh"]),
                "power_factor": float(row["power_factor"]),
                "source": row["source"],
            }
            for row in csv.DictReader(handle)
        ]


def app_client(tmp_path: Path) -> TestClient:
    app = create_app(
        evidence_dir=ROOT / "evidence" / "public_dashboard",
        operator_log=tmp_path / "operator.jsonl",
        meter_store_path=tmp_path / "meter.jsonl",
        edge_status_path=tmp_path / "edge.json",
        cost_impact_dir=ROOT / "evidence" / "cost_impact",
        forecast_store_path=tmp_path / "forecasts.jsonl",
        model_path=MODEL,
        api_key=None,
    )
    return TestClient(app)


def test_history_generates_multi_horizon_forecast(tmp_path: Path) -> None:
    client = app_client(tmp_path)
    batch = readings()
    response = client.post("/api/meter-readings", json={"readings": batch})
    assert response.status_code == 200
    assert response.json()["accepted"] == 49
    assert response.json()["live_inference"]["generated"] == 1

    payload = client.get("/api/live-forecasts").json()
    assert payload["model"]["ready"] is True
    assert payload["mode"] == "multi_horizon_demand_forecast"
    assert len(payload["items"]) == 1
    forecast = payload["items"][0]
    assert forecast["facility_id"] == "Central Kitchens NC1 4"
    assert set(forecast["forecasts"]) == {"30_minutes", "2_hours", "6_hours", "24_hours"}
    assert forecast["operating_mode"] == "advisory"


def test_model_status_reports_trained_model(tmp_path: Path) -> None:
    payload = app_client(tmp_path).get("/api/model-status").json()
    assert payload["ready"] is True
    assert payload["source"] == "validated_institutional_models"
    assert payload["prediction_horizons"]["30_minutes"] == 30
    assert payload["prediction_horizons"]["24_hours"] == 1440
