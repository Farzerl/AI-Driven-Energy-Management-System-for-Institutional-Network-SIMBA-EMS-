from __future__ import annotations

import json
from pathlib import Path

from src.api.cost_store import load_cost_impact


def test_cost_store_loads_summary_and_tables(tmp_path: Path) -> None:
    cost_dir = tmp_path / "cost"
    cost_dir.mkdir()
    (cost_dir / "cost_impact_summary.json").write_text(
        json.dumps({"status": "public_source_planning_estimate", "estimated_baseline_bill_usd": 123.0}),
        encoding="utf-8",
    )
    (cost_dir / "monthly_bill_estimates.csv").write_text(
        "month,energy_kwh,estimated_total_bill_usd\nApr 2026,510294,82055.28\n",
        encoding="utf-8",
    )
    (cost_dir / "scenario_savings.csv").write_text(
        "scenario,estimated_total_saving_usd,estimated_saving_percent_of_baseline\nCentral planning,14170.36,2.35\n",
        encoding="utf-8",
    )
    payload = load_cost_impact(cost_dir)
    assert payload["status"] == "public_source_planning_estimate"
    assert payload["monthly"][0]["energy_kwh"] == 510294.0
    assert payload["scenarios"][0]["estimated_total_saving_usd"] == 14170.36
