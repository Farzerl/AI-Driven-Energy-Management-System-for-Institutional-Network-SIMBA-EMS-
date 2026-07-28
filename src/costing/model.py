from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class TariffProxy:
    average_usd_per_kwh: float
    demand_usd_per_kva_month: float
    peak_usd_per_kwh: float
    standard_usd_per_kwh: float
    off_peak_usd_per_kwh: float


def estimate_months(
    monthly_data: Iterable[Mapping[str, object]],
    tariff: TariffProxy,
) -> list[dict[str, float | int | str]]:
    rows: list[dict[str, float | int | str]] = []
    for item in monthly_data:
        month = str(item["month"])
        energy = float(item["energy_kwh"])
        load_factor = float(item["load_factor"])
        hours = int(item["hours"])
        if energy < 0:
            raise ValueError(f"{month}: energy_kwh cannot be negative")
        if not 0 < load_factor <= 1:
            raise ValueError(f"{month}: load_factor must be in (0, 1]")
        if hours <= 0:
            raise ValueError(f"{month}: hours must be positive")
        maximum_demand = energy / (hours * load_factor)
        rows.append(
            {
                "month": month,
                "energy_kwh": round(energy, 3),
                "load_factor": round(load_factor, 4),
                "hours": hours,
                "estimated_max_demand_kva": round(maximum_demand, 2),
                "estimated_total_bill_usd": round(
                    energy * tariff.average_usd_per_kwh, 2
                ),
                "effective_average_tariff_usd_per_kwh": tariff.average_usd_per_kwh,
            }
        )
    return rows


def run_scenarios(
    monthly_rows: Iterable[Mapping[str, object]],
    tariff: TariffProxy,
    scenarios: Mapping[str, Mapping[str, float]],
) -> list[dict[str, float | str]]:
    rows = list(monthly_rows)
    baseline = sum(float(row["estimated_total_bill_usd"]) for row in rows)
    output: list[dict[str, float | str]] = []
    for name, assumptions in scenarios.items():
        peak_reduction = float(assumptions["peak_demand_reduction_fraction"])
        peak_to_standard = float(
            assumptions["peak_to_standard_shift_fraction"]
        )
        peak_to_off_peak = float(
            assumptions["peak_to_offpeak_shift_fraction"]
        )
        for value in (peak_reduction, peak_to_standard, peak_to_off_peak):
            if not 0 <= value <= 1:
                raise ValueError(f"{name}: scenario fractions must be between 0 and 1")
        demand_saving = sum(
            float(row["estimated_max_demand_kva"])
            * peak_reduction
            * tariff.demand_usd_per_kva_month
            for row in rows
        )
        shift_saving = sum(
            float(row["energy_kwh"])
            * (
                peak_to_standard
                * (tariff.peak_usd_per_kwh - tariff.standard_usd_per_kwh)
                + peak_to_off_peak
                * (tariff.peak_usd_per_kwh - tariff.off_peak_usd_per_kwh)
            )
            for row in rows
        )
        total = demand_saving + shift_saving
        output.append(
            {
                "scenario": name,
                "peak_demand_reduction_fraction": peak_reduction,
                "peak_to_standard_shift_fraction": peak_to_standard,
                "peak_to_offpeak_shift_fraction": peak_to_off_peak,
                "estimated_demand_charge_saving_usd": round(demand_saving, 2),
                "estimated_time_shift_saving_usd": round(shift_saving, 2),
                "estimated_total_saving_usd": round(total, 2),
                "estimated_saving_percent_of_baseline": round(
                    100 * total / baseline if baseline else 0, 2
                ),
            }
        )
    return output
