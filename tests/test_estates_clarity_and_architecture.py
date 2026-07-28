from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.server import create_app
from src.live.model_manager import LiveModelManager
from src.simulation.engine import SimulationEngine

ROOT = Path(__file__).resolve().parents[1]


def test_approved_response_estimate_exists_before_physical_control() -> None:
    model = LiveModelManager(ROOT / "models" / "institutional_multi_horizon_forecaster.json")
    try:
        engine = SimulationEngine(model)
        card = engine.state()["approval_deck"]["items"][0]
        engine.decide_recommendation(card["recommendation_id"], "approve", operator="test", request_id="impact-plan-001")
        metrics = engine.state()["metrics"]
        assert metrics["approved_peak_reduction_plan_kva"] > 0
        assert metrics["approved_demand_charge_saving_estimate_usd"] > 0
        assert metrics["approved_total_cost_saving_estimate_usd"] > 0
        assert metrics["current_reduction_kva"] == 0
    finally:
        model.close()


def test_action_queue_has_dynamic_multi_facility_priority(tmp_path: Path) -> None:
    app = create_app(
        evidence_dir=ROOT / "evidence" / "public_dashboard",
        operator_log=tmp_path / "operator.jsonl",
        meter_store_path=tmp_path / "meter.jsonl",
        edge_status_path=tmp_path / "edge.json",
        cost_impact_dir=ROOT / "evidence" / "cost_impact",
        forecast_store_path=tmp_path / "forecasts.jsonl",
        model_path=ROOT / "models" / "institutional_multi_horizon_forecaster.json",
        autostart_replay=False,
    )
    with TestClient(app) as client:
        payload = client.get("/api/alerts").json()
        alerts = payload["alerts"]
        assert len(alerts) >= 2
        assert len({row["facility_name"] for row in alerts}) >= 2
        assert [row["priority_rank"] for row in alerts] == list(range(1, len(alerts) + 1))
        assert all("No facility has permanent priority" in row["priority_reason"] for row in alerts)


def test_readiness_exposes_institutional_case_without_overclaim(tmp_path: Path) -> None:
    app = create_app(
        evidence_dir=ROOT / "evidence" / "public_dashboard",
        operator_log=tmp_path / "operator.jsonl",
        meter_store_path=tmp_path / "meter.jsonl",
        edge_status_path=tmp_path / "edge.json",
        cost_impact_dir=ROOT / "evidence" / "cost_impact",
        forecast_store_path=tmp_path / "forecasts.jsonl",
        model_path=ROOT / "models" / "institutional_multi_horizon_forecaster.json",
        autostart_replay=False,
    )
    with TestClient(app) as client:
        case = client.get("/api/readiness-evidence").json()["institutional_case"]
        assert case["annual_electricity_expenditure_usd"] == 1200000
        assert case["conservative_saving_target_percent"] == {"minimum": 12.0, "maximum": 15.0}
        assert "not a realised saving" in case["claim_boundary"]


def test_dashboard_estates_clarity_assets() -> None:
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "dashboard" / "static" / "app.css").read_text(encoding="utf-8")
    js = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    assert "Measurement boundary" not in html
    assert 'id="institutional-case-evidence"' in html
    assert "Approved-response saving estimate" in html
    assert 'font-family:Inter' in css
    assert '.recommendation-numbers.compact>div{display:grid' in css
    assert 'approved_total_cost_saving_estimate_usd' in js
    assert 'Priority ${Number(alert.priority_rank || 1)}' in js


def test_architecture_diagrams_include_data_and_audit_layers() -> None:
    architecture = (ROOT / "docs" / "diagrams" / "system_architecture.svg").read_text(encoding="utf-8")
    data_flow = (ROOT / "docs" / "diagrams" / "data_flow.svg").read_text(encoding="utf-8")
    assert "Time-series DB" in architecture
    assert "Audit + evidence DB" in architecture
    assert "model registry" in architecture
    assert "Operator approval" in data_flow
    assert "Impact service" in data_flow
