from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.server import create_app

ROOT = Path(__file__).resolve().parents[1]


def client(tmp_path: Path, api_key: str | None = None) -> TestClient:
    app = create_app(
        evidence_dir=ROOT / "evidence" / "public_dashboard",
        operator_log=tmp_path / "operator.jsonl",
        meter_store_path=tmp_path / "meter.jsonl",
        edge_status_path=tmp_path / "edge.json",
        cost_impact_dir=ROOT / "evidence" / "cost_impact",
        forecast_store_path=tmp_path / "forecasts.jsonl",
        model_path=ROOT / "models" / "institutional_multi_horizon_forecaster.json",
        api_key=api_key,
    )
    return TestClient(app)


def admin_headers(api: TestClient, *, api_key: str | None = None) -> dict[str, str]:
    login = api.post("/api/admin/login", json={"password": "admin"})
    assert login.status_code == 200
    headers = {"X-Admin-Token": login.json()["token"]}
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def test_simulation_workflow_api(tmp_path: Path) -> None:
    api = client(tmp_path)
    headers = admin_headers(api)
    scenarios = api.get("/api/simulation/scenarios", headers=headers)
    assert scenarios.status_code == 200
    assert len(scenarios.json()["items"]) >= 3

    reset = api.post(
        "/api/simulation/reset",
        json={"scenario_id": "campus_peak_replay", "controller_mode": "ai_assisted"},
        headers=headers,
    )
    assert reset.status_code == 200
    assert reset.json()["cursor"] == 0

    step = api.post("/api/simulation/step", json={"count": 1}, headers=headers)
    assert step.status_code == 200
    assert step.json()["state"]["recommendation"]["available"] is True

    approve = api.post("/api/simulation/approve-recommendation")
    assert approve.status_code == 200
    assert approve.json()["applied"] > 0

    metrics = api.get("/api/simulation/metrics", headers=headers)
    assert metrics.status_code == 200
    assert "claim_boundary" in metrics.json()

    events = api.get("/api/simulation/events", headers=headers)
    assert events.status_code == 200
    assert events.json()["items"]


def test_simulation_comparison_api(tmp_path: Path) -> None:
    api = client(tmp_path)
    headers = admin_headers(api)
    payload = api.get(
        "/api/simulation/comparison",
        params={"scenario_id": "campus_peak_replay"},
        headers=headers,
    )
    assert payload.status_code == 200
    assert set(payload.json()["controllers"]) == {"no_control", "simple_rule", "ai_assisted"}
    assert payload.json()["comparison"]["all_critical_load_violations"] == 0


def test_simulation_write_requires_key_when_configured(tmp_path: Path) -> None:
    api = client(tmp_path, api_key="test-api-key-value")
    assert api.post("/api/simulation/step", json={"count": 1}).status_code == 401
    headers = admin_headers(api, api_key="test-api-key-value")
    assert api.post(
        "/api/simulation/step",
        json={"count": 1},
        headers=headers,
    ).status_code == 200


def test_invalid_critical_action_returns_422(tmp_path: Path) -> None:
    api = client(tmp_path)
    headers = admin_headers(api)
    response = api.post(
        "/api/simulation/action",
        json={
            "facility_id": "central_kitchens_nc1_4",
            "action": "defer_load",
            "load_group": "protected_operations",
            "reduction_kva": 20,
            "duration_minutes": 30,
            "approved_by_operator": True,
            "operator": "test-operator",
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert "Critical loads" in response.json()["detail"]


def test_public_impact_and_multiple_recommendation_approval(tmp_path: Path) -> None:
    api = client(tmp_path)
    state = api.get("/api/simulation/state")
    assert state.status_code == 200
    recommendations = state.json()["recommendations"]
    assert len(recommendations) >= 2

    approval = api.post(
        "/api/simulation/approve-recommendation",
        json={
            "request_id": "dashboard-approval-001",
            "recommendation_ids": [recommendations[0]["recommendation_id"]],
            "operator": "dashboard-operator",
        },
    )
    assert approval.status_code == 200
    assert approval.json()["applied"] > 0
    assert approval.json()["skipped"] == []

    impact = api.get("/api/simulation/impact")
    assert impact.status_code == 200
    assert impact.json()["metrics"]["approved_actions"] > 0
    assert impact.json()["actions"]
