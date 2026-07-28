from __future__ import annotations

import pandas as pd


def recommend_action(row: pd.Series) -> str:
    if bool(row.get("is_anomaly", False)):
        return "Inspect facility load pattern and verify equipment state."
    if row.get("peak_risk") == "high" and row.get("tariff_period") == "peak":
        return "Defer non-critical loads and stagger kitchen or hostel equipment start times."
    if row.get("peak_risk") == "medium" and row.get("tariff_period") == "peak":
        return "Prepare supervised load coordination if demand continues rising."
    if row.get("tariff_period") == "offpeak":
        return "Schedule deferrable loads such as water heating or pumping where operationally safe."
    return "Continue monitoring. No immediate action required."


def add_recommendations(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy()
    result["recommended_action"] = result.apply(recommend_action, axis=1)
    return result
