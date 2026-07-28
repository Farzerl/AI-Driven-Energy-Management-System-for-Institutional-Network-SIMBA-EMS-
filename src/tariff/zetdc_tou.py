from __future__ import annotations

from datetime import time

import pandas as pd


def classify_tariff_period(timestamp: pd.Timestamp) -> str:
    """Classify a timestamp into an MVP time-of-use period.

    These windows are placeholders for workflow testing. They must not be used
    for billing-grade savings claims until the applicable institutional tariff
    schedule has been formally verified.
    """
    local_time = timestamp.time()
    if time(7, 0) <= local_time < time(10, 0) or time(18, 0) <= local_time < time(21, 0):
        return "peak"
    if time(22, 0) <= local_time or local_time < time(5, 0):
        return "offpeak"
    return "standard"


def add_tariff_features(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy()
    result["tariff_period"] = result["timestamp"].apply(classify_tariff_period)
    result["is_peak"] = (result["tariff_period"] == "peak").astype(int)
    result["is_offpeak"] = (result["tariff_period"] == "offpeak").astype(int)
    return result
