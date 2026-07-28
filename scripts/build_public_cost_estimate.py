from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.costing.model import TariffProxy, estimate_months, run_scenarios



def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"No rows were produced for {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild the public-source tariff and cost-impact evidence."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "evidence" / "cost_impact",
    )
    args = parser.parse_args()

    study = load_json(ROOT / "config" / "study_cost_inputs.json")
    tariff_data = load_json(ROOT / "config" / "tariff.public-estimate.json")
    scenario_data = load_json(ROOT / "config" / "cost_impact_scenarios.json")
    rates = tariff_data["study_calibrated_planning_parameters"]
    tariff = TariffProxy(
        average_usd_per_kwh=tariff_data["official_public_anchor"][
            "average_end_user_tariff_usd_per_kwh"
        ],
        demand_usd_per_kva_month=rates["demand_charge_usd_per_kva_month"],
        peak_usd_per_kwh=rates["peak_energy_usd_per_kwh"],
        standard_usd_per_kwh=rates["standard_energy_usd_per_kwh"],
        off_peak_usd_per_kwh=rates["off_peak_energy_usd_per_kwh"],
    )
    monthly = estimate_months(study["monthly_data"], tariff)
    scenarios = run_scenarios(monthly, tariff, scenario_data)
    output_dir = args.output_dir
    write_csv(output_dir / "monthly_bill_estimates.csv", monthly)
    write_csv(output_dir / "scenario_savings.csv", scenarios)

    baseline = sum(row["estimated_total_bill_usd"] for row in monthly)
    summary = {
        "status": "public_source_planning_estimate",
        "currency": "USD",
        "study_period": study["study_period"],
        "months_modelled": len(monthly),
        "total_energy_kwh": sum(row["energy_kwh"] for row in monthly),
        "estimated_baseline_bill_usd": round(baseline, 2),
        "average_end_user_tariff_usd_per_kwh": tariff.average_usd_per_kwh,
        "estimated_monthly_bill_range_usd": {
            "minimum": min(row["estimated_total_bill_usd"] for row in monthly),
            "maximum": max(row["estimated_total_bill_usd"] for row in monthly),
        },
        "estimated_peak_demand_range_kva": {
            "minimum": min(row["estimated_max_demand_kva"] for row in monthly),
            "maximum": max(row["estimated_max_demand_kva"] for row in monthly),
        },
        "scenarios": scenarios,
        "jan_2026_cost_breakdown": {
            key: value / 100
            for key, value in study["cost_breakdown_percent"]["Jan 2026"].items()
        },
        "apr_2026_cost_breakdown": {
            key: value / 100
            for key, value in study["cost_breakdown_percent"]["Apr 2026"].items()
        },
        "claim_boundary": (
            "Planning estimate only. It does not reproduce or disclose the UZ utility "
            "bill. Savings require validation against the applicable account tariff, "
            "meter demand register and measured controllable-load response during a pilot."
        ),
    }
    (output_dir / "cost_impact_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
