from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.server import create_app

ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
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
        admin_auth_path=tmp_path / "admin_auth.json",
        admin_test_log_path=tmp_path / "admin_tests.jsonl",
    )
    return TestClient(app)


def _login(api: TestClient) -> dict[str, str]:
    response = api.post("/api/admin/login", json={"password": "admin"})
    assert response.status_code == 200
    assert response.json()["must_change_password"] is True
    return {"X-Admin-Token": response.json()["token"]}


def test_admin_routes_require_session_and_diagnostics_are_sanitised(tmp_path: Path) -> None:
    api = _client(tmp_path)
    assert api.get("/api/system-settings").status_code == 401
    assert api.get("/api/admin/diagnostics").status_code == 401

    headers = _login(api)
    diagnostics = api.get("/api/admin/diagnostics", headers=headers)
    assert diagnostics.status_code == 200
    body = diagnostics.json()
    assert body["secret_fields_included"] is False
    assert body["approval_channel"] == "dashboard_only"
    assert body["model"]["neural_models_ready"] == {"lstm": True, "transformer": True}
    assert "gmail_app_password" not in str(body)


def test_controlled_proof_test_is_idempotent_and_isolated(tmp_path: Path) -> None:
    api = _client(tmp_path)
    headers = _login(api)
    facilities = api.get("/api/admin/test-facilities", headers=headers).json()["items"]
    assert facilities
    payload = {
        "request_id": "proof-fixed-request-001",
        "facility_id": facilities[0],
        "values_kva": [40.0, 55.0, 72.0, 91.0],
        "model_mode": "hybrid_all",
    }
    first = api.post("/api/admin/test-forecast", json=payload, headers=headers)
    assert first.status_code == 200
    result = first.json()
    assert result["idempotent_replay"] is False
    assert result["production_state_changed"] is False
    assert result["adaptive_learning_updated"] is False
    assert result["official_metrics_updated"] is False
    assert set(result["forecasts"]) == {"30_minutes", "2_hours", "6_hours", "24_hours"}
    assert "gradient_boosting" in result["forecasts"]["30_minutes"]["model_predictions"]
    assert "lstm" in result["forecasts"]["30_minutes"]["model_predictions"]
    assert "transformer" in result["forecasts"]["30_minutes"]["model_predictions"]

    replay = api.post("/api/admin/test-forecast", json=payload, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True

    conflict = api.post(
        "/api/admin/test-forecast",
        json={**payload, "values_kva": [40.0, 55.0, 72.0, 120.0]},
        headers=headers,
    )
    assert conflict.status_code == 422
    assert "already used with different test values" in conflict.json()["detail"]


def test_operational_guardrails_validate_and_apply(tmp_path: Path) -> None:
    api = _client(tmp_path)
    headers = _login(api)
    current = api.get("/api/system-settings", headers=headers).json()["settings"]
    current["operational"] = {
        "campus_limit_override_kva": 1300.0,
        "facility_limit_overrides_kva": {"central_kitchens_nc1_4": 500.0},
        "critical_floor_overrides_kva": {"central_kitchens_nc1_4": 250.0},
        "risk_medium_ratio": 0.8,
        "risk_high_ratio": 0.9,
        "peak_energy_usd_per_kwh": 0.22,
        "standard_energy_usd_per_kwh": 0.12,
        "offpeak_energy_usd_per_kwh": 0.06,
        "demand_charge_usd_per_kva_month": 8.0,
    }
    response = api.put("/api/system-settings", json=current, headers=headers)
    assert response.status_code == 200
    state = response.json()["simulation"]
    assert state["campus"]["limit_kva"] == 1300.0
    kitchen = next(item for item in state["facilities"] if item["facility_id"] == "central_kitchens_nc1_4")
    assert kitchen["limit_kva"] == 500.0
    assert kitchen["critical_floor_kva"] == 250.0

    invalid = {**current, "operational": {**current["operational"], "risk_medium_ratio": 0.95, "risk_high_ratio": 0.9}}
    rejected = api.put("/api/system-settings", json=invalid, headers=headers)
    assert rejected.status_code == 422


def test_wrong_password_is_rejected(tmp_path: Path) -> None:
    api = _client(tmp_path)
    response = api.post("/api/admin/login", json={"password": "wrong"})
    assert response.status_code == 401
    assert "incorrect" in response.json()["detail"].lower()


def test_chronos_admin_status_requires_session_and_is_safe_when_not_installed(tmp_path: Path) -> None:
    api = _client(tmp_path)
    assert api.get("/api/admin/chronos2/status").status_code == 401
    headers = _login(api)
    response = api.get("/api/admin/chronos2/status", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["approval_channel"] == "dashboard_only"
    assert "INSTALL_AND_TRAIN_CHRONOS2.bat" in body["installation_command"]
    assert "runtime" in body
