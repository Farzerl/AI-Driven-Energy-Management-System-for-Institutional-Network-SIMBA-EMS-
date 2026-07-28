from __future__ import annotations

from pathlib import Path

import pytest

from src.live.model_manager import LiveModelManager
from src.simulation.engine import SimulationEngine
from src.simulation.schemas import SimulationActionRequest

ROOT = Path(__file__).resolve().parents[1]


def engine() -> SimulationEngine:
    manager = LiveModelManager(ROOT / "models" / "institutional_multi_horizon_forecaster.json")
    assert manager.ready is True
    return SimulationEngine(manager)


def test_default_scenario_exposes_live_recommendation_and_constraints() -> None:
    simulator = engine()
    state = simulator.reset("campus_peak_replay", "ai_assisted")
    assert state["scenario"]["scenario_id"] == "campus_peak_replay"
    assert state["scenario"]["facility_count"] == 22
    assert state["model"]["ready"] is True
    assert state["model"]["model_forecast_count"] == 22
    assert state["model"]["fallback_forecast_count"] == 0
    assert state["model"]["latest_batch_inference_latency_ms"] > 0
    assert state["recommendation"]["available"] is True
    assert state["recommendation"]["facility_name"] == "Central Kitchens"
    assert state["recommendation"]["lead_time_minutes"] in {30, 120}
    assert all(action["classification"] != "critical" for action in state["recommendation"]["actions"])


def test_critical_load_action_is_rejected() -> None:
    simulator = engine()
    with pytest.raises(ValueError, match="Critical loads"):
        simulator.apply_action(
            SimulationActionRequest(
                facility_id="central_kitchens_nc1_4",
                action="defer_load",
                load_group="protected_operations",
                reduction_kva=20.0,
                duration_minutes=30,
            )
        )


def test_ai_controller_reduces_default_peak_without_critical_violation() -> None:
    simulator = engine()
    result = simulator.compare_controllers("campus_peak_replay")
    controllers = result["controllers"]
    no_control = controllers["no_control"]["metrics"]
    simple = controllers["simple_rule"]["metrics"]
    assisted = controllers["ai_assisted"]["metrics"]

    assert assisted["controlled_peak_kva"] < no_control["controlled_peak_kva"]
    assert assisted["controlled_peak_kva"] <= simple["controlled_peak_kva"]
    assert assisted["approved_actions"] > 0
    assert result["comparison"]["all_critical_load_violations"] == 0


def test_rebound_is_delayed_out_of_peak_and_bounded_by_headroom() -> None:
    simulator = engine()
    simulator.reset("campus_peak_replay", "ai_assisted")
    approved = simulator.apply_recommended_plan(operator="test-operator")
    assert approved["applied"] > 0
    final = simulator.run_to_end(auto_approve=True)
    assert final["metrics"]["critical_load_violations"] == 0
    for row in final["timeline"]:
        for facility in row["facilities"]:
            if facility["rebound_kva"] > 0:
                assert facility["tariff_period"] != "peak"
                assert facility["controlled_kva"] <= facility["limit_kva"]


def test_after_hours_anomaly_is_escalated_without_automatic_control() -> None:
    simulator = engine()
    simulator.reset("after_hours_review", "ai_assisted")
    simulator.step(2)
    state = simulator.state()

    recommendation = state["recommendation"]
    assert recommendation["available"] is False
    assert recommendation["escalation"] is True
    assert recommendation["recommendation_type"] == "investigation"
    assert recommendation["facility_id"] == "science_center"
    assert state["anomalies"][0]["control_blocked"] is True
    assert state["metrics"]["approved_actions"] == 0
    assert state["metrics"]["critical_load_violations"] == 0

    simulator.step(1)
    assert simulator.metrics()["anomaly_escalations"] >= 1
    assert any(event["event_type"] == "anomaly_escalation" for event in simulator.events())


def test_multiple_recommendations_are_safe_and_approval_is_idempotent() -> None:
    simulator = engine()
    state = simulator.reset("campus_peak_replay", "ai_assisted")
    assert len(state["recommendations"]) >= 2
    assert len({item["facility_id"] for item in state["recommendations"]}) == len(state["recommendations"])

    first = simulator.apply_recommended_plan(operator="test-operator", request_id="approval-safe-001")
    assert first["applied"] > 0
    assert first["skipped"] == []

    repeated = simulator.apply_recommended_plan(operator="test-operator", request_id="approval-safe-001")
    assert repeated["actions"] == first["actions"]
    assert repeated["applied"] == first["applied"]
