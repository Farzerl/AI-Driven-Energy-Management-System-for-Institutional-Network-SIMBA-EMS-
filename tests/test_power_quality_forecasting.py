from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.live.dataset_loader import load_dataset_archive
from src.live.power_quality_adapter import PowerQualityChronosAdapter

ROOT = Path(__file__).resolve().parents[1]


class FakePowerQualityPipeline:
    def predict_df(self, context: pd.DataFrame, **_: object) -> pd.DataFrame:
        output: list[dict[str, object]] = []
        for facility in sorted(context["item_id"].astype(str).unique()):
            facility_rows = context[context["item_id"].astype(str) == facility].sort_values("timestamp")
            latest = pd.Timestamp(facility_rows["timestamp"].max())
            active = float(facility_rows.iloc[-1]["active_power_kw"])
            reactive = float(facility_rows.iloc[-1]["reactive_power_kvar"])
            for target, base in (("active_power_kw", active), ("reactive_power_kvar", reactive)):
                for step in range(1, 49):
                    point = base + (0.05 * step if target == "active_power_kw" else 0.01 * step)
                    output.append(
                        {
                            "item_id": facility,
                            "target_name": target,
                            "timestamp": latest + pd.Timedelta(minutes=30 * step),
                            "0.1": point - 0.5,
                            "0.5": point,
                            "0.9": point + 0.5,
                        }
                    )
        return pd.DataFrame(output)


def _meter_rows(count: int = 96) -> list[dict[str, object]]:
    start = datetime(2026, 4, 1)
    return [
        {
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "facility_id": "facility-a",
            "facility_name": "Facility A",
            "kva": 103.0 + index * 0.05,
            "kwh": 50.0 + index * 0.025,
            "active_power_kw": 100.0 + index * 0.05,
            "reactive_power_kvar": 20.0 + index * 0.01,
            "power_factor": 0.98,
        }
        for index in range(count)
    ]


def _adapter_root(tmp_path: Path) -> Path:
    model = tmp_path / "models" / "chronos-2-power-quality-finetuned"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"test")
    route_dir = tmp_path / "models" / "power_quality"
    route_dir.mkdir(parents=True)
    selected = {
        target: {
            horizon: {
                "model": "chronos",
                "chronos_weight": 1.0,
                "test_metrics": {"mae": 1.0},
            }
            for horizon in ("30_minutes", "2_hours", "6_hours", "24_hours")
        }
        for target in ("active_power_kw", "reactive_power_kvar")
    }
    (route_dir / "routing.json").write_text(
        json.dumps(
            {
                "eligible": True,
                "deployment_variant": "power_quality_finetuned",
                "model_path": "models/chronos-2-power-quality-finetuned",
                "selected_by_target_horizon": selected,
                "targets": ["active_power_kw", "reactive_power_kvar"],
                "derived_outputs": ["interval_energy_kwh", "power_factor"],
                "thresholds": {"low_power_factor": 0.95, "critical_power_factor": 0.85},
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_dataset_loader_preserves_independent_targets_and_derives_energy_and_pf(tmp_path: Path) -> None:
    timestamps = [(datetime(2026, 4, 1) + timedelta(minutes=30 * index)).strftime("%m/%d/%Y, %H:%M:%S") for index in range(4)]
    frame = pd.DataFrame(
        {
            "Date/Time": timestamps,
            "Power (kW)": [80.0, 82.0, 84.0, 86.0],
            "Reactive energy (kVAR)": [20.0, -21.0, 22.0, -23.0],
            "Demand (kVA)": [82.4621, 84.6463, 86.8332, 89.0225],
            "Power factor": [0.9701, -0.9687, 0.9674, -0.9660],
            "Consumption (kWh)": [40.0, 41.0, 42.0, 43.0],
            "Temperature": [25.0, None, 26.0, None],
            "Humidity": [60.0, None, 61.0, None],
        }
    )
    csv_path = tmp_path / "University-of-Zimbabwe-Test-PM1.csv"
    frame.to_csv(csv_path, index=False)
    archive = tmp_path / "dataset.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(csv_path, csv_path.name)

    loaded, aliases = load_dataset_archive(archive, tmp_path / "processed")
    assert len(loaded) == 4
    assert aliases
    assert loaded["active_power_kw"].tolist() == [80.0, 82.0, 84.0, 86.0]
    assert loaded["reactive_power_kvar"].tolist() == [20.0, -21.0, 22.0, -23.0]
    assert loaded["reactive_energy_kvarh_estimated"].tolist() == [10.0, 10.5, 11.0, 11.5]
    assert loaded["kwh"].tolist() == [40.0, 41.0, 42.0, 43.0]
    assert loaded["power_factor"].between(0, 1).all()
    assert abs(float(loaded.iloc[0]["power_factor"]) - 80.0 / 82.4621) < 1e-4


def test_power_quality_adapter_forecasts_kw_kvar_and_physically_derived_outputs(tmp_path: Path) -> None:
    pipeline = FakePowerQualityPipeline()
    adapter = PowerQualityChronosAdapter(
        _adapter_root(tmp_path),
        pipeline_factory=lambda _path, _device: pipeline,
    )
    try:
        payload = adapter.predict_batch({"facility-a": _meter_rows()})
    finally:
        adapter.close()
    assert payload["status"] == "success"
    item = payload["items"][0]
    assert item["model_source"] == "validated_multivariate_power_quality_model"
    assert set(item["forecasts"]) == {"30_minutes", "2_hours", "6_hours", "24_hours"}
    for row in item["forecasts"].values():
        assert row["forecast_active_power_kw"] >= 0
        assert 0 <= row["forecast_power_factor"] <= 1
        assert row["forecast_interval_energy_kwh"] == round(row["forecast_active_power_kw"] * 0.5, 4)
        assert row["forecast_interval_reactive_energy_kvarh_estimated"] == round(abs(row["forecast_reactive_power_kvar"]) * 0.5, 4)
        assert row["routes"] == {"active_power_kw": "chronos", "reactive_power_kvar": "chronos"}


def test_power_quality_endpoint_remains_available_before_local_training(tmp_path: Path) -> None:
    app = create_app(
        evidence_dir=ROOT / "evidence" / "public_dashboard",
        operator_log=tmp_path / "operator.jsonl",
        api_key=None,
    )
    client = TestClient(app)
    response = client.get("/api/power-quality-forecasts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"waiting", "fallback", "success"}
    assert "model" in payload
