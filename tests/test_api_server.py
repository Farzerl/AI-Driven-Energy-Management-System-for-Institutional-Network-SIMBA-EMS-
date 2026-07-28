from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.server import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_api_health_and_summary(tmp_path: Path) -> None:
    app = create_app(evidence_dir=ROOT / "evidence" / "public_dashboard", operator_log=tmp_path / "operator.jsonl", api_key=None)
    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["evidence_ready"] is True
    summary = client.get("/api/summary")
    assert summary.status_code == 200
    assert summary.json()["dataset"]["facilities"] == 22


def test_alerts_and_operator_decision_log(tmp_path: Path) -> None:
    app = create_app(evidence_dir=ROOT / "evidence" / "public_dashboard", operator_log=tmp_path / "operator.jsonl", api_key=None)
    client = TestClient(app)
    alerts = client.get("/api/alerts").json()["alerts"]
    assert alerts
    response = client.post("/api/operator-decisions", json={"alert_id": alerts[0]["alert_id"], "decision": "confirm", "operator": "test-operator", "note": "test", "requested_reduction_kva": alerts[0]["planning_reduction_kva"]})
    assert response.status_code == 200
    assert client.get("/api/operator-decisions").json()["items"][0]["decision"] == "confirm"


def test_operator_write_requires_key_when_configured(tmp_path: Path) -> None:
    test_api_key = "test-api-key-value"
    app = create_app(evidence_dir=ROOT / "evidence" / "public_dashboard", operator_log=tmp_path / "operator.jsonl", api_key=test_api_key)
    client = TestClient(app)
    payload = {"alert_id": "12345678", "decision": "defer", "operator": "operator", "note": ""}
    assert client.post("/api/operator-decisions", json=payload).status_code == 401
    assert client.post("/api/operator-decisions", json=payload, headers={"X-API-Key": test_api_key}).status_code == 200
