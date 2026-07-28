from __future__ import annotations

import time
from pathlib import Path

from src.live.model_manager import LiveModelManager
from src.simulation.engine import SimulationEngine
from src.simulation.playback import SimulationPlaybackController

ROOT = Path(__file__).resolve().parents[1]


def test_backend_playback_pauses_for_review_then_advances_after_approval() -> None:
    model = LiveModelManager(ROOT / "models" / "institutional_multi_horizon_forecaster.json")
    engine = SimulationEngine(model)
    settings = {
        "simulation": {
            "playback_interval_seconds": 0.5,
            "pause_on_recommendation": True,
        }
    }
    playback = SimulationPlaybackController(engine, lambda: settings)
    start_time = engine.state()["current_timestamp"]

    playback.start()
    time.sleep(0.1)
    assert playback.status()["reason"] == "operator_approval_required"

    approved = engine.apply_recommended_plan(operator="test-operator", request_id="playback-approval-001")
    assert approved["applied"] > 0
    playback.resume_after_approval()
    time.sleep(0.7)

    assert engine.state()["current_timestamp"] != start_time
    playback.stop("test_complete")
