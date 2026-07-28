from __future__ import annotations

import pandas as pd


def add_time_features(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy()
    result["hour"] = result["timestamp"].dt.hour
    result["minute"] = result["timestamp"].dt.minute
    result["dayofweek"] = result["timestamp"].dt.dayofweek
    result["month"] = result["timestamp"].dt.month
    result["is_weekend"] = result["dayofweek"].isin([5, 6]).astype(int)
    return result


def add_lag_features(data: pd.DataFrame, target: str = "kva") -> pd.DataFrame:
    result = data.sort_values(["facility_id", "timestamp"]).copy()
    group = result.groupby("facility_id", group_keys=False)[target]
    result[f"{target}_lag_1"] = group.shift(1)
    result[f"{target}_lag_2"] = group.shift(2)
    result[f"{target}_rolling_mean_4"] = group.rolling(4).mean().reset_index(level=0, drop=True)
    result[f"{target}_rolling_max_4"] = group.rolling(4).max().reset_index(level=0, drop=True)
    return result
