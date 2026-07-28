from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split

FEATURE_COLUMNS = [
    "hour",
    "minute",
    "dayofweek",
    "month",
    "is_weekend",
    "is_peak",
    "is_offpeak",
    "kva_lag_1",
    "kva_lag_2",
    "kva_rolling_mean_4",
    "kva_rolling_max_4",
]


def train_random_forest_forecaster(data: pd.DataFrame) -> tuple[RandomForestRegressor, dict[str, float]]:
    """Train a small interpretable baseline forecaster for next-interval kVA."""
    model_data = data.dropna(subset=FEATURE_COLUMNS + ["kva"]).copy()
    if len(model_data) < 8:
        raise ValueError("Not enough rows after feature engineering to train the model.")

    x = model_data[FEATURE_COLUMNS]
    y = model_data["kva"]
    x_train, x_test, y_train, y_test = train_test_split(x, y, test_size=0.25, shuffle=False)

    model = RandomForestRegressor(n_estimators=100, random_state=42, min_samples_leaf=1)
    model.fit(x_train, y_train)

    prediction = model.predict(x_test)
    mae = mean_absolute_error(y_test, prediction)
    mape = float(np.mean(np.abs((y_test - prediction) / y_test)) * 100)
    return model, {"mae_kva": float(mae), "mape_percent": mape, "test_rows": float(len(y_test))}


def previous_value_baseline(data: pd.DataFrame) -> dict[str, float]:
    """Use previous interval kVA as a simple non-AI baseline."""
    model_data = data.dropna(subset=["kva", "kva_lag_1"]).copy()
    error = mean_absolute_error(model_data["kva"], model_data["kva_lag_1"])
    mape = float(np.mean(np.abs((model_data["kva"] - model_data["kva_lag_1"]) / model_data["kva"])) * 100)
    return {"mae_kva": float(error), "mape_percent": mape, "test_rows": float(len(model_data))}
