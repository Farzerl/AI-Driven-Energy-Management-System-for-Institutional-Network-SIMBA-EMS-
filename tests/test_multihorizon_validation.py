from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.live.multihorizon import feature_vector


def rows(count: int = 49, gap_index: int | None = None) -> list[dict[str, object]]:
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    output = []
    offset = timedelta()
    for index in range(count):
        if gap_index is not None and index == gap_index:
            offset += timedelta(hours=2)
        output.append({
            "timestamp": (start + timedelta(minutes=30 * index) + offset).isoformat(),
            "facility_id": "Facility A",
            "kva": 100.0 + index,
            "kwh": 50.0,
            "kwh_is_measured": True,
            "power_factor": 0.95,
        })
    return output


def test_cold_start_is_explicitly_rejected() -> None:
    with pytest.raises(ValueError, match="requires 49"):
        feature_vector(rows(20), facility_id="Facility A", horizon_steps=1, facilities=["Facility A"])


def test_irregular_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="Irregular meter interval"):
        feature_vector(rows(gap_index=30), facility_id="Facility A", horizon_steps=1, facilities=["Facility A"])
