import pandas as pd

from src.recommendations.engine import recommend_action


def test_high_peak_risk_output():
    row = pd.Series({"is_anomaly": False, "peak_risk": "high", "tariff_period": "peak"})
    result = recommend_action(row)
    assert "Defer" in result


def test_anomaly_output():
    row = pd.Series({"is_anomaly": True, "peak_risk": "low", "tariff_period": "standard"})
    result = recommend_action(row)
    assert "Inspect" in result
