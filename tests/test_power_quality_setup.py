from __future__ import annotations

from datetime import datetime, timedelta

from scripts.power_quality_setup import derived_metrics, select_routes


def _rows(*, target: str, actual: float, forecast: float, horizons: tuple[str, ...] = ("30_minutes", "2_hours", "6_hours", "24_hours")) -> list[dict[str, object]]:
    origin = datetime(2026, 3, 1)
    return [
        {
            "facility": "facility-a",
            "item_id": "facility-a",
            "origin": origin.isoformat(),
            "horizon": horizon,
            "target": target,
            "actual": actual + index,
            "forecast": forecast + index,
            "lower": forecast + index - 0.5,
            "upper": forecast + index + 0.5,
            "latency_ms_per_facility": 1.0,
        }
        for index, horizon in enumerate(horizons)
    ]


def test_derived_metrics_mark_classification_not_applicable_without_low_pf_events() -> None:
    rows = _rows(target="active_power_kw", actual=100.0, forecast=100.0) + _rows(
        target="reactive_power_kvar", actual=10.0, forecast=10.0
    )
    metrics = derived_metrics(rows, low_pf_threshold=0.95)
    power_factor = metrics["power_factor"]
    assert power_factor["low_pf_events"] == 0
    assert power_factor["low_pf_recall"] is None
    assert power_factor["low_pf_f1"] is None
    assert power_factor["metric_status"] == "not_applicable_no_events"
    assert metrics["interval_energy_kwh"]["mae"] == 0.0


def test_route_selection_is_independent_for_active_and_reactive_targets() -> None:
    baseline_validation = _rows(target="active_power_kw", actual=100.0, forecast=90.0) + _rows(
        target="reactive_power_kvar", actual=20.0, forecast=19.9
    )
    model_validation = _rows(target="active_power_kw", actual=100.0, forecast=99.5) + _rows(
        target="reactive_power_kvar", actual=20.0, forecast=15.0
    )
    baseline_test = [dict(row) for row in baseline_validation]
    model_test = [dict(row) for row in model_validation]
    routing, selected_validation, selected_test = select_routes(
        baseline_validation,
        model_validation,
        baseline_test,
        model_test,
        weights=[0.5],
        minimum_improvement=0.5,
    )
    assert all(routing["active_power_kw"][horizon]["model"] != "seasonal_persistence" for horizon in routing["active_power_kw"])
    assert all(routing["reactive_power_kvar"][horizon]["model"] == "seasonal_persistence" for horizon in routing["reactive_power_kvar"])
    assert selected_validation
    assert selected_test
