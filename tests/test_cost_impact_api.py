from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.server import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_cost_impact_endpoint(tmp_path: Path) -> None:
    app = create_app(
        evidence_dir=ROOT / "evidence" / "public_dashboard",
        operator_log=tmp_path / "operator.jsonl",
        api_key=None,
        meter_store_path=tmp_path / "meter.jsonl",
        edge_status_path=tmp_path / "edge.json",
        cost_impact_dir=ROOT / "evidence" / "cost_impact",
    )
    response = TestClient(app).get("/api/cost-impact")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "public_source_planning_estimate"
    assert payload["summary"]["estimated_baseline_bill_usd"] > 500000
    assert len(payload["monthly"]) == 8
