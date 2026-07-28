from __future__ import annotations

import pandas as pd


def add_simple_anomaly_flags(data: pd.DataFrame, z_threshold: float = 2.5) -> pd.DataFrame:
    result = data.sort_values(["facility_id", "timestamp"]).copy()
    group = result.groupby("facility_id", group_keys=False)["kva"]
    rolling_mean = group.rolling(8, min_periods=4).mean().reset_index(level=0, drop=True)
    rolling_std = group.rolling(8, min_periods=4).std().reset_index(level=0, drop=True)
    result["kva_z_score"] = (result["kva"] - rolling_mean) / rolling_std.replace(0, pd.NA)
    result["is_anomaly"] = result["kva_z_score"].abs() >= z_threshold
    result["is_anomaly"] = result["is_anomaly"].fillna(False)
    return result
