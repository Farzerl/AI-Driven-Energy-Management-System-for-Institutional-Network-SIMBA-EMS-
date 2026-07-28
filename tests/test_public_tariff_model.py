from __future__ import annotations

import pytest

from src.costing.model import TariffProxy, estimate_months, run_scenarios


TARIFF = TariffProxy(
    average_usd_per_kwh=0.1608,
    demand_usd_per_kva_month=7.78,
    peak_usd_per_kwh=0.2173,
    standard_usd_per_kwh=0.1150,
    off_peak_usd_per_kwh=0.0588,
)


def test_estimate_month_uses_energy_and_load_factor() -> None:
    rows = estimate_months(
        [{"month": "Apr 2026", "energy_kwh": 510294, "load_factor": 0.58, "hours": 720}],
        TARIFF,
    )
    assert rows[0]["estimated_max_demand_kva"] == pytest.approx(1221.97, abs=0.01)
    assert rows[0]["estimated_total_bill_usd"] == pytest.approx(82055.28, abs=0.01)


def test_central_scenario_matches_public_evidence() -> None:
    monthly = estimate_months(
        [
            {"month": "Jan 2026", "energy_kwh": 315129, "load_factor": 0.57, "hours": 744},
            {"month": "Apr 2026", "energy_kwh": 510294, "load_factor": 0.58, "hours": 720},
        ],
        TARIFF,
    )
    scenarios = run_scenarios(
        monthly,
        TARIFF,
        {
            "Central planning": {
                "peak_demand_reduction_fraction": 0.05,
                "peak_to_standard_shift_fraction": 0.02,
                "peak_to_offpeak_shift_fraction": 0.005,
            }
        },
    )
    assert scenarios[0]["estimated_total_saving_usd"] > 0
    assert scenarios[0]["estimated_saving_percent_of_baseline"] < 10


def test_invalid_load_factor_is_rejected() -> None:
    with pytest.raises(ValueError):
        estimate_months(
            [{"month": "Bad", "energy_kwh": 100, "load_factor": 0, "hours": 720}],
            TARIFF,
        )
