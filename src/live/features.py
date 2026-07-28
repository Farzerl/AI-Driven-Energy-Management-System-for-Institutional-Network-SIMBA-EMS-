from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

NUMERIC_FEATURES = [
    "hour",
    "minute",
    "dayofweek",
    "month",
    "is_weekend",
    "is_peak",
    "is_offpeak",
    "target_dayofweek",
    "target_slot",
    "kva",
    "kwh",
    "power_factor",
    "kva_lag_1",
    "kva_lag_2",
    "kva_rolling_mean_4",
    "kva_rolling_max_4",
]
CATEGORICAL_FEATURES = ["facility_id"]
MODEL_FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES
MIN_HISTORY = 4
INTERVAL_MINUTES = 30


def tariff_period(timestamp: datetime | pd.Timestamp) -> str:
    hour = timestamp.hour + timestamp.minute / 60
    if 7 <= hour < 10 or 18 <= hour < 21:
        return "peak"
    if hour >= 22 or hour < 5:
        return "offpeak"
    return "standard"


def add_live_features(data: pd.DataFrame, *, include_target: bool = True) -> pd.DataFrame:
    required = {"timestamp", "facility_id", "kva", "kwh", "power_factor"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"Missing live-model columns: {sorted(missing)}")

    result = data.copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True, errors="coerce")
    result["facility_id"] = result["facility_id"].astype(str)
    for column in ["kva", "kwh", "power_factor"]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["timestamp", "facility_id", "kva", "kwh", "power_factor"])
    result = result[(result["kva"] >= 0) & (result["kwh"] >= 0) & result["power_factor"].between(-1, 1)]
    result = result.sort_values(["facility_id", "timestamp"]).drop_duplicates(
        subset=["facility_id", "timestamp"], keep="last"
    )

    result["hour"] = result["timestamp"].dt.hour
    result["minute"] = result["timestamp"].dt.minute
    result["dayofweek"] = result["timestamp"].dt.dayofweek
    result["month"] = result["timestamp"].dt.month
    result["is_weekend"] = result["dayofweek"].isin([5, 6]).astype(int)
    period = result["timestamp"].map(tariff_period)
    result["is_peak"] = (period == "peak").astype(int)
    result["is_offpeak"] = (period == "offpeak").astype(int)
    target_timestamp = result["timestamp"] + pd.Timedelta(minutes=INTERVAL_MINUTES)
    result["target_dayofweek"] = target_timestamp.dt.dayofweek
    result["target_slot"] = target_timestamp.dt.hour * 2 + (target_timestamp.dt.minute // 30)

    group = result.groupby("facility_id", group_keys=False)["kva"]
    result["kva_lag_1"] = group.shift(1)
    result["kva_lag_2"] = group.shift(2)
    result["kva_rolling_mean_4"] = (
        group.rolling(MIN_HISTORY, min_periods=MIN_HISTORY)
        .mean()
        .reset_index(level=0, drop=True)
    )
    result["kva_rolling_max_4"] = (
        group.rolling(MIN_HISTORY, min_periods=MIN_HISTORY)
        .max()
        .reset_index(level=0, drop=True)
    )
    if include_target:
        result["next_interval_kva"] = result.groupby("facility_id")["kva"].shift(-1)
        required_model_columns = MODEL_FEATURES + ["next_interval_kva"]
    else:
        required_model_columns = MODEL_FEATURES
    return result.dropna(subset=required_model_columns).reset_index(drop=True)


def feature_row_from_history(records: Iterable[Mapping[str, object]]) -> tuple[pd.DataFrame, dict[str, object]]:
    ordered = sorted(records, key=lambda item: str(item["timestamp"]))
    if len(ordered) < MIN_HISTORY:
        raise ValueError(f"At least {MIN_HISTORY} completed interval readings are required.")
    window = ordered[-MIN_HISTORY:]
    facility_ids = {str(item["facility_id"]) for item in window}
    if len(facility_ids) != 1:
        raise ValueError("A live feature window must contain one facility only.")

    current = window[-1]
    current_timestamp = pd.Timestamp(current["timestamp"])
    if current_timestamp.tzinfo is None:
        raise ValueError("Live reading timestamps must include a timezone offset.")
    kva_values = [float(item["kva"]) for item in window]
    period = tariff_period(current_timestamp)
    row = {
        "facility_id": str(current["facility_id"]),
        "hour": int(current_timestamp.hour),
        "minute": int(current_timestamp.minute),
        "dayofweek": int(current_timestamp.dayofweek),
        "month": int(current_timestamp.month),
        "is_weekend": int(current_timestamp.dayofweek >= 5),
        "is_peak": int(period == "peak"),
        "is_offpeak": int(period == "offpeak"),
        "target_dayofweek": int((current_timestamp + timedelta(minutes=INTERVAL_MINUTES)).dayofweek),
        "target_slot": int((current_timestamp + timedelta(minutes=INTERVAL_MINUTES)).hour * 2 + ((current_timestamp + timedelta(minutes=INTERVAL_MINUTES)).minute // 30)),
        "kva": float(current["kva"]),
        "kwh": float(current["kwh"]),
        "power_factor": float(current["power_factor"]),
        "kva_lag_1": kva_values[-2],
        "kva_lag_2": kva_values[-3],
        "kva_rolling_mean_4": float(np.mean(kva_values)),
        "kva_rolling_max_4": float(np.max(kva_values)),
    }
    context = {
        "reading_id": current.get("reading_id"),
        "reading_timestamp": current_timestamp.isoformat(),
        "forecast_timestamp": (current_timestamp + timedelta(minutes=INTERVAL_MINUTES)).isoformat(),
        "current_tariff_period": period,
        "forecast_tariff_period": tariff_period(current_timestamp + timedelta(minutes=INTERVAL_MINUTES)),
        "current_kva": float(current["kva"]),
        "kwh": float(current["kwh"]),
        "power_factor": float(current["power_factor"]),
    }
    return pd.DataFrame([row], columns=MODEL_FEATURES), context
