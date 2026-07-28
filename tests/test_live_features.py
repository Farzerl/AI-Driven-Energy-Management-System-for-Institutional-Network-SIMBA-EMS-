from __future__ import annotations

import pytest

from src.live.features import feature_row_from_history


def record(index: int) -> dict[str, object]:
    return {
        "reading_id": f"r{index}",
        "timestamp": f"2026-07-14T{9 + index // 2:02d}:{'30' if index % 2 else '00'}:00+02:00",
        "facility_id": "F17",
        "kva": 460.0 + index * 12,
        "kwh": 220.0 + index * 5,
        "power_factor": 0.91,
    }


def test_live_features_require_four_intervals() -> None:
    with pytest.raises(ValueError):
        feature_row_from_history([record(0), record(1), record(2)])


def test_live_features_match_training_columns() -> None:
    frame, context = feature_row_from_history([record(i) for i in range(4)])
    assert frame.iloc[0]["facility_id"] == "F17"
    assert frame.iloc[0]["kva_lag_1"] == 484.0
    assert frame.iloc[0]["kva_lag_2"] == 472.0
    assert context["forecast_timestamp"].startswith("2026-07-14T11:00:00")
