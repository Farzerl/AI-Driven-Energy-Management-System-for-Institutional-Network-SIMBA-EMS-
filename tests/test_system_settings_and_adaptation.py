from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.server import create_app
from src.live.adaptation import AdaptiveForecastCalibrator
from src.config.system_settings import SystemSettingsStore
from src.simulation.profiles import SCENARIOS

ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            evidence_dir=ROOT / "evidence" / "public_dashboard",
            operator_log=tmp_path / "operator.jsonl",
            meter_store_path=tmp_path / "meter.jsonl",
            edge_status_path=tmp_path / "edge.json",
            cost_impact_dir=ROOT / "evidence" / "cost_impact",
            forecast_store_path=tmp_path / "forecasts.jsonl",
            model_path=ROOT / "models" / "institutional_multi_horizon_forecaster.json",
            notification_store_path=tmp_path / "notifications.jsonl",
            notification_settings_path=tmp_path / "notification_settings.json",
            system_settings_path=tmp_path / "system_settings.json",
            adaptation_state_path=tmp_path / "adaptive.json",
        )
    )


def test_system_settings_persist_and_reset_simulation(tmp_path: Path) -> None:
    api = _client(tmp_path)
    payload = {
        "simulation": {
            "scenario_id": "evening_residential_replay",
            "controller_mode": "ai_assisted",
            "playback_interval_seconds": 1.5,
            "pause_on_recommendation": True,
            "auto_compare_on_load": False,
        },
        "adaptive_learning": {
            "enabled": True,
            "minimum_observations": 8,
            "correction_gain": 0.55,
            "maximum_correction_percent_of_limit": 5.0,
            "residual_window": 192,
            "retraining_interval_new_readings": 336,
        },
    }
    login = api.post("/api/admin/login", json={"password": "admin"})
    assert login.status_code == 200
    response = api.put(
        "/api/system-settings",
        json=payload,
        headers={"X-Admin-Token": login.json()["token"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["settings"]["simulation"]["scenario_id"] == "evening_residential_replay"
    assert body["simulation"]["scenario"]["scenario_id"] == "evening_residential_replay"
    assert (tmp_path / "system_settings.json").exists()


def test_adaptive_calibration_is_bounded_and_requires_observations(tmp_path: Path) -> None:
    settings = SystemSettingsStore(tmp_path / "system.json", set(SCENARIOS))
    settings.update(
        {
            "adaptive_learning": {
                "enabled": True,
                "minimum_observations": 4,
                "correction_gain": 1.0,
                "maximum_correction_percent_of_limit": 5.0,
                "residual_window": 48,
                "retraining_interval_new_readings": 96,
            }
        }
    )
    calibrator = AdaptiveForecastCalibrator(tmp_path / "adaptive.json", settings.snapshot)
    before = calibrator.apply(
        facility="facility-a",
        horizon="30_minutes",
        forecast_kva=100.0,
        upper_kva=105.0,
        limit_kva=200.0,
    )
    assert before["adaptive_correction_kva"] == 0

    forecasts = []
    readings = []
    for index in range(4):
        timestamp = f"2026-04-01T0{index}:30:00+02:00"
        forecasts.append(
            {
                "forecast_id": f"forecast-{index}",
                "facility_id": "facility-a",
                "facility_limit_kva": 200.0,
                "forecasts": {
                    "30_minutes": {
                        "target_timestamp": timestamp,
                        "forecast_kva": 100.0,
                        "validation_p95_abs_error_kva": 5.0,
                    }
                },
            }
        )
        readings.append({"facility_id": "facility-a", "timestamp": timestamp, "kva": 130.0})
    result = calibrator.observe_readings(readings, forecasts)
    assert result["updated"] == 4
    after = calibrator.apply(
        facility="facility-a",
        horizon="30_minutes",
        forecast_kva=100.0,
        upper_kva=105.0,
        limit_kva=200.0,
    )
    assert after["adaptive_correction_kva"] == 10.0
    assert after["forecast_kva"] == 110.0
    assert after["forecast_upper_kva"] >= after["forecast_kva"]


def test_simulation_scenarios_are_full_campus_measured_replays() -> None:
    assert len(SCENARIOS) >= 3
    for scenario in SCENARIOS.values():
        assert len(scenario.facilities) == 22
        assert all(len(facility.preroll_kva) >= 49 for facility in scenario.facilities)
        assert all(len(facility.baseline_kva) == len(scenario.facilities[0].baseline_kva) for facility in scenario.facilities)
        assert scenario.source == "authorised_half_hour_meter_data"
