from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import numpy as np

HARARE = ZoneInfo("Africa/Harare")
LAGS = (1, 2, 3, 4, 6, 12, 24, 48)
WINDOWS = (2, 4, 8, 12, 24, 48)
MINIMUM_HISTORY = 49
EXPECTED_INTERVAL_MINUTES = 30
INTERVAL_TOLERANCE_MINUTES = 7.5


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=HARARE)
    return parsed.astimezone(HARARE)


def _ordered(records: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    rows = sorted(records, key=lambda row: _timestamp(row["timestamp"]))
    if not rows:
        raise ValueError("At least one meter reading is required.")
    if len(rows) < MINIMUM_HISTORY:
        raise ValueError(
            f"Facility requires {MINIMUM_HISTORY} completed half-hour readings; "
            f"only {len(rows)} are available."
        )
    window = rows[-MINIMUM_HISTORY:]
    timestamps = [_timestamp(row["timestamp"]) for row in window]
    for previous, current in zip(timestamps, timestamps[1:]):
        gap_minutes = (current - previous).total_seconds() / 60.0
        if abs(gap_minutes - EXPECTED_INTERVAL_MINUTES) > INTERVAL_TOLERANCE_MINUTES:
            raise ValueError(
                "Irregular meter interval detected: "
                f"{gap_minutes:.1f} minutes between {previous.isoformat()} and "
                f"{current.isoformat()}; expected approximately 30 minutes."
            )
    return window


def _history_values(rows: list[Mapping[str, object]]) -> np.ndarray:
    values = np.asarray([max(float(row["kva"]), 0.0) for row in rows], dtype=float)
    if len(values) < MINIMUM_HISTORY:
        raise ValueError(
            f"Facility requires {MINIMUM_HISTORY} completed readings; only {len(values)} available."
        )
    return values[-MINIMUM_HISTORY:]


def feature_vector(
    records: Iterable[Mapping[str, object]],
    *,
    facility_id: str,
    horizon_steps: int,
    facilities: list[str],
) -> np.ndarray:
    rows = _ordered(records)
    values = _history_values(rows)
    latest = rows[-1]
    current = float(values[-1])
    kwh_is_measured = bool(latest.get("kwh_is_measured", True))
    features: list[float] = [
        current,
        max(float(latest.get("kwh", current * 0.5)), 0.0),
        float(kwh_is_measured),
        min(max(abs(float(latest.get("power_factor", 0.95))), 0.0), 1.0),
    ]
    for lag in LAGS:
        features.append(float(values[-1 - lag]))
    for window in WINDOWS:
        selected = values[-window:]
        features.extend(
            [
                float(selected.mean()),
                float(selected.std(ddof=0)),
                float(selected.max()),
                float(selected.min()),
            ]
        )
    features.extend(
        [
            current - float(values[-2]),
            current - float(values[-3]),
            current - float(values[-5]),
            current / max(float(values[-4:].mean()), 1e-3),
        ]
    )
    target_time = _timestamp(latest["timestamp"]) + timedelta(minutes=30 * horizon_steps)
    hour = target_time.hour + target_time.minute / 60.0
    day = target_time.weekday()
    month = target_time.month - 1
    features.extend(
        [
            float(np.sin(2 * np.pi * hour / 24)),
            float(np.cos(2 * np.pi * hour / 24)),
            float(np.sin(2 * np.pi * day / 7)),
            float(np.cos(2 * np.pi * day / 7)),
            float(np.sin(2 * np.pi * month / 12)),
            float(np.cos(2 * np.pi * month / 12)),
            float(day >= 5),
            float(target_time.hour * 2 + target_time.minute // 30),
        ]
    )
    features.extend([1.0 if facility_id == item else 0.0 for item in facilities])
    return np.asarray(features, dtype=float)


def _tree_predict(tree: Mapping[str, Any], vector: np.ndarray) -> float:
    node = 0
    is_leaf = tree["is_leaf"]
    while not is_leaf[node]:
        index = int(tree["feature_idx"][node])
        value = float(vector[index])
        if np.isnan(value):
            node = int(tree["left"][node] if tree["missing_left"][node] else tree["right"][node])
        else:
            node = int(tree["left"][node] if value <= tree["threshold"][node] else tree["right"][node])
    return float(tree["value"][node])


def predict_portable(model: Mapping[str, Any], vector: np.ndarray) -> float:
    prediction = float(model["baseline"])
    for tree in model["trees"]:
        prediction += _tree_predict(tree, vector)
    return max(prediction, 0.0)
