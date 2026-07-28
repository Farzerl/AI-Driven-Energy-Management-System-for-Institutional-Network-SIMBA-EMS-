from __future__ import annotations

import csv
import json
from pathlib import Path


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    numeric_fields = {
        "energy_kwh",
        "load_factor",
        "hours",
        "estimated_max_demand_kva",
        "estimated_total_bill_usd",
        "effective_average_tariff_usd_per_kwh",
        "peak_demand_reduction_fraction",
        "peak_to_standard_shift_fraction",
        "peak_to_offpeak_shift_fraction",
        "estimated_demand_charge_saving_usd",
        "estimated_time_shift_saving_usd",
        "estimated_total_saving_usd",
        "estimated_saving_percent_of_baseline",
    }
    for row in rows:
        for key in numeric_fields.intersection(row):
            raw = row[key]
            if raw == "":
                row[key] = None
            else:
                row[key] = float(raw)
    return rows


def load_cost_impact(cost_dir: Path) -> dict[str, object]:
    summary = _read_json(cost_dir / "cost_impact_summary.json")
    return {
        "summary": summary,
        "monthly": _read_csv(cost_dir / "monthly_bill_estimates.csv"),
        "scenarios": _read_csv(cost_dir / "scenario_savings.csv"),
        "status": summary.get("status", "not_generated"),
    }
