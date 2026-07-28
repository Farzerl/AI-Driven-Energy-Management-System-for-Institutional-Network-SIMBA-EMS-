from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.server import create_app
from src.live.model_manager import LiveModelManager
from src.simulation.engine import SimulationEngine

ROOT = Path(__file__).resolve().parents[1]


def test_approval_deck_persists_decisions_and_records_control_command() -> None:
    model = LiveModelManager(ROOT / "models" / "institutional_multi_horizon_forecaster.json")
    try:
        engine = SimulationEngine(model)
        initial = engine.state()
        assert initial["approval_deck"]["total"] >= 2
        card = initial["approval_deck"]["items"][0]
        assert card["decision_status"] == "not_approved"

        result = engine.decide_recommendation(
            card["recommendation_id"],
            "approve",
            operator="test-operator",
            request_id="approval-deck-test-001",
        )
        assert result["applied"] > 0
        approved = engine.state()
        approved_card = next(
            item for item in approved["approval_deck"]["items"]
            if item["recommendation_id"] == card["recommendation_id"]
        )
        assert approved_card["decision_status"] == "approved"
        assert approved["action_history"]
        assert approved["action_history"][0]["control_command"]["status"] == "simulated_acknowledged"
        assert approved["metrics"]["authorised_reduction_kva"] > 0

        engine.step(1)
        advanced = engine.state()
        assert advanced["metrics"]["current_reduction_kva"] > 0
        assert advanced["approval_deck"]["total"] > initial["approval_deck"]["total"]
    finally:
        model.close()


def test_recommendation_decision_and_integration_status_api(tmp_path: Path) -> None:
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
        state = client.get("/api/simulation/state").json()
        card = state["approval_deck"]["items"][0]
        response = client.post(
            "/api/simulation/recommendation-decision",
            json={
                "request_id": "deck-decision-test-001",
                "recommendation_id": card["recommendation_id"],
                "decision": "acknowledge",
                "operator": "test-operator",
                "note": "Reviewed during automated test.",
            },
        )
        assert response.status_code == 200
        updated = response.json()["state"]
        record = next(
            item for item in updated["approval_deck"]["items"]
            if item["recommendation_id"] == card["recommendation_id"]
        )
        assert record["decision_status"] == "acknowledged"

        integration = client.get("/api/integration/status")
        assert integration.status_code == 200
        payload = integration.json()
        assert payload["meter_ingestion"]["endpoint"] == "/api/meter-readings"
        assert payload["cleaning"]["status"] == "active"
        assert payload["control_gateway"]["mode"] == "simulation"
        assert payload["approval_boundary"]


def test_server_side_replay_autostarts_when_enabled(tmp_path: Path) -> None:
    import json
    import time

    settings_path = tmp_path / "system_settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "version": 5,
                "simulation": {
                    "scenario_id": "campus_peak_replay",
                    "controller_mode": "ai_assisted",
                    "playback_interval_seconds": 0.5,
                    "pause_on_recommendation": False,
                    "auto_compare_on_load": False,
                    "auto_start": True,
                },
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        evidence_dir=ROOT / "evidence" / "public_dashboard",
        operator_log=tmp_path / "operator.jsonl",
        meter_store_path=tmp_path / "meter.jsonl",
        edge_status_path=tmp_path / "edge.json",
        cost_impact_dir=ROOT / "evidence" / "cost_impact",
        forecast_store_path=tmp_path / "forecasts.jsonl",
        model_path=ROOT / "models" / "institutional_multi_horizon_forecaster.json",
        system_settings_path=settings_path,
        autostart_replay=True,
    )
    with TestClient(app) as client:
        start = client.get("/api/simulation/state").json()["current_timestamp"]
        time.sleep(0.7)
        later = client.get("/api/simulation/state").json()
        assert later["current_timestamp"] != start
        assert later["playback"]["playback_interval_seconds"] == 0.5
