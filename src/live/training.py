"""Scheduled autoregressive ridge baseline used for comparison and reproducibility.

The deployed SIMBA EMS model is the chronologically tuned histogram-gradient-boosting
ensemble produced by ``scripts/train_model.py`` and loaded by
``src.live.model_manager``. This module is not the production training path.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.live.features import MODEL_FEATURES, NUMERIC_FEATURES, add_live_features

CYCLICAL_FEATURES = [
    "hour_sin",
    "hour_cos",
    "day_sin",
    "day_cos",
    "month_sin",
    "month_cos",
]
RIDGE_NUMERIC_FEATURES = NUMERIC_FEATURES + CYCLICAL_FEATURES + ["schedule_mean_next"]


def _safe_mape(actual: np.ndarray, predicted: np.ndarray) -> float:
    mask = np.abs(actual) >= 1.0
    if not mask.any():
        return 0.0
    return float(np.mean(np.abs(actual[mask] - predicted[mask]) / np.abs(actual[mask])) * 100)


def _hash_frame(data: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update(str(len(data)).encode())
    digest.update("|".join(sorted(data["facility_id"].astype(str).unique())).encode())
    digest.update(str(data["timestamp"].min()).encode())
    digest.update(str(data["timestamp"].max()).encode())
    return digest.hexdigest()


def _schedule_key(facility: object, day: object, slot: object) -> str:
    return f"{facility}|{int(day)}|{int(slot)}"


def _augment(
    frame: pd.DataFrame,
    schedule_means: dict[str, float],
    facility_means: dict[str, float],
) -> pd.DataFrame:
    result = frame.copy()
    result["hour_sin"] = np.sin(2 * np.pi * (result["hour"] + result["minute"] / 60) / 24)
    result["hour_cos"] = np.cos(2 * np.pi * (result["hour"] + result["minute"] / 60) / 24)
    result["day_sin"] = np.sin(2 * np.pi * result["dayofweek"] / 7)
    result["day_cos"] = np.cos(2 * np.pi * result["dayofweek"] / 7)
    result["month_sin"] = np.sin(2 * np.pi * (result["month"] - 1) / 12)
    result["month_cos"] = np.cos(2 * np.pi * (result["month"] - 1) / 12)
    keys = [
        _schedule_key(facility, day, slot)
        for facility, day, slot in zip(
            result["facility_id"], result["target_dayofweek"], result["target_slot"]
        )
    ]
    result["schedule_mean_next"] = [
        schedule_means.get(key, facility_means.get(str(facility), float(current)))
        for key, facility, current in zip(keys, result["facility_id"], result["kva"])
    ]
    return result


def _design_matrix(
    frame: pd.DataFrame,
    facilities: list[str],
    means: dict[str, float],
    scales: dict[str, float],
    schedule_means: dict[str, float],
    facility_means: dict[str, float],
) -> np.ndarray:
    augmented = _augment(frame, schedule_means, facility_means)
    numeric = np.column_stack(
        [
            (augmented[column].to_numpy(dtype=float) - means[column]) / scales[column]
            for column in RIDGE_NUMERIC_FEATURES
        ]
    )
    facility_values = augmented["facility_id"].astype(str).to_numpy()
    one_hot = np.column_stack([(facility_values == facility).astype(float) for facility in facilities])
    return np.column_stack([np.ones(len(augmented), dtype=float), numeric, one_hot])


def _fit_payload(train: pd.DataFrame, ridge_alpha: float) -> dict[str, Any]:
    schedule_frame = train.assign(
        schedule_key=[
            _schedule_key(facility, day, slot)
            for facility, day, slot in zip(
                train["facility_id"], train["target_dayofweek"], train["target_slot"]
            )
        ]
    )
    schedule_means = (
        schedule_frame.groupby("schedule_key")["next_interval_kva"].median().round(6).to_dict()
    )
    facility_means = (
        train.groupby("facility_id")["next_interval_kva"].median().round(6).to_dict()
    )
    augmented_train = _augment(train, schedule_means, facility_means)
    facilities = sorted(train["facility_id"].astype(str).unique().tolist())
    means = {column: float(augmented_train[column].mean()) for column in RIDGE_NUMERIC_FEATURES}
    scales = {
        column: max(float(augmented_train[column].std(ddof=0)), 1e-6)
        for column in RIDGE_NUMERIC_FEATURES
    }
    x_train = _design_matrix(
        train, facilities, means, scales, schedule_means, facility_means
    )
    y_train = train["next_interval_kva"].to_numpy(dtype=float)
    gram = x_train.T @ x_train
    penalty = np.eye(gram.shape[0], dtype=float) * ridge_alpha
    penalty[0, 0] = 0.0
    coefficients = np.linalg.solve(gram + penalty, x_train.T @ y_train)
    return {
        "algorithm": "scheduled_autoregressive_ridge_with_persistence_blend",
        "ridge_alpha": ridge_alpha,
        "numeric_features": RIDGE_NUMERIC_FEATURES,
        "facilities": facilities,
        "means": means,
        "scales": scales,
        "schedule_means": {str(key): float(value) for key, value in schedule_means.items()},
        "facility_means": {str(key): float(value) for key, value in facility_means.items()},
        "coefficients": coefficients.tolist(),
        "persistence_blend_alpha": 1.0,
    }


def _base_predict(model: dict[str, Any], features: pd.DataFrame) -> np.ndarray:
    matrix = _design_matrix(
        features,
        list(model["facilities"]),
        {key: float(value) for key, value in model["means"].items()},
        {key: float(value) for key, value in model["scales"].items()},
        {key: float(value) for key, value in model["schedule_means"].items()},
        {key: float(value) for key, value in model["facility_means"].items()},
    )
    coefficients = np.asarray(model["coefficients"], dtype=float)
    return np.maximum(matrix @ coefficients, 0.0)


def predict_with_bundle(bundle: dict[str, Any], features: pd.DataFrame) -> np.ndarray:
    model = bundle["model"]
    base = _base_predict(model, features)
    persistence = features["kva"].to_numpy(dtype=float)
    alpha = float(model.get("persistence_blend_alpha", 1.0))
    return np.maximum(alpha * base + (1 - alpha) * persistence, 0.0)


def _select_blend_alpha(train: pd.DataFrame, ridge_alpha: float) -> tuple[float, dict[str, float]]:
    unique_times = np.sort(train["timestamp"].unique())
    cutoff_value = unique_times[max(1, int(len(unique_times) * 0.85)) - 1]
    cutoff = pd.Timestamp(cutoff_value)
    fit = train[train["timestamp"] <= cutoff].copy()
    validation = train[train["timestamp"] > cutoff].copy()
    if len(fit) < 500 or len(validation) < 100:
        return 0.35, {"validation_rows": float(len(validation)), "validation_mae_kva": float("nan")}
    payload = _fit_payload(fit, ridge_alpha)
    base = _base_predict(payload, validation[MODEL_FEATURES])
    persistence = validation["kva"].to_numpy(dtype=float)
    actual = validation["next_interval_kva"].to_numpy(dtype=float)
    candidates = np.linspace(0.0, 1.0, 21)
    scores = [float(np.mean(np.abs(actual - (alpha * base + (1 - alpha) * persistence)))) for alpha in candidates]
    best_index = int(np.argmin(scores))
    return float(candidates[best_index]), {
        "validation_rows": float(len(validation)),
        "validation_mae_kva": scores[best_index],
        "validation_persistence_mae_kva": float(np.mean(np.abs(actual - persistence))),
    }


def train_live_model_bundle(
    raw_data: pd.DataFrame,
    *,
    source_kind: str,
    test_start: str | None = None,
    max_train_rows: int = 220_000,
    ridge_alpha: float = 4.0,
) -> dict[str, Any]:
    featured = add_live_features(raw_data, include_target=True)
    if len(featured) < 500:
        raise ValueError("At least 500 usable rows are required to train the live model.")

    if test_start:
        cutoff = pd.Timestamp(test_start, tz="UTC")
        train = featured[featured["timestamp"] < cutoff].copy()
        test = featured[featured["timestamp"] >= cutoff].copy()
    else:
        unique_times = np.sort(featured["timestamp"].unique())
        cutoff_value = unique_times[max(1, int(len(unique_times) * 0.8)) - 1]
        cutoff = pd.Timestamp(cutoff_value)
        train = featured[featured["timestamp"] <= cutoff].copy()
        test = featured[featured["timestamp"] > cutoff].copy()
    if train.empty or test.empty:
        raise ValueError("The chronological training and test partitions must both contain rows.")

    if len(train) > max_train_rows:
        positions = np.linspace(0, len(train) - 1, max_train_rows, dtype=int)
        train = train.iloc[positions].copy()

    blend_alpha, validation_metrics = _select_blend_alpha(train, ridge_alpha)
    model_payload = _fit_payload(train, ridge_alpha)
    model_payload["persistence_blend_alpha"] = blend_alpha
    provisional = {"model": model_payload}
    predicted = predict_with_bundle(provisional, test[MODEL_FEATURES])
    actual = test["next_interval_kva"].to_numpy(dtype=float)
    persistence = test["kva"].to_numpy(dtype=float)

    train_limits = (
        train.groupby("facility_id")["kva"]
        .quantile(0.95)
        .mul(1.05)
        .clip(lower=1.0)
        .round(3)
        .to_dict()
    )
    model_mae = float(np.mean(np.abs(actual - predicted)))
    persistence_mae = float(np.mean(np.abs(actual - persistence)))
    metadata = {
        "bundle_schema_version": 4,
        "model_family": "Scheduled autoregressive ridge blended with persistence",
        "model_mode": source_kind,
        "trained_utc": datetime.now(timezone.utc).isoformat(),
        "prediction_horizon_minutes": 30,
        "minimum_history_intervals": 4,
        "feature_columns": MODEL_FEATURES,
        "facility_count": int(featured["facility_id"].nunique()),
        "usable_rows": int(len(featured)),
        "training_rows": int(len(train)),
        "test_rows": int(len(test)),
        "training_start": train["timestamp"].min().isoformat(),
        "training_end": train["timestamp"].max().isoformat(),
        "test_start": test["timestamp"].min().isoformat(),
        "test_end": test["timestamp"].max().isoformat(),
        "dataset_fingerprint": _hash_frame(featured),
        "persistence_blend_alpha": blend_alpha,
        "validation": validation_metrics,
        "metrics": {
            "model_mae_kva": model_mae,
            "model_rmse_kva": float(np.sqrt(np.mean((actual - predicted) ** 2))),
            "model_mape_percent_over_1kva": _safe_mape(actual, predicted),
            "persistence_mae_kva": persistence_mae,
            "persistence_rmse_kva": float(np.sqrt(np.mean((actual - persistence) ** 2))),
            "mae_improvement_percent": float(
                100 * (persistence_mae - model_mae) / max(persistence_mae, 1e-9)
            ),
        },
        "claim_boundary": (
            "This deployment model predicts the next completed half-hour interval. The verified research benchmark remains documented separately. Risk and recommendations remain advisory."
        ),
    }
    return {
        "model": model_payload,
        "metadata": metadata,
        "facility_limits_kva": {str(key): float(value) for key, value in train_limits.items()},
    }


def save_model_bundle(bundle: dict[str, Any], path: Path, metrics_path: Path | None = None) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    if metrics_path:
        metrics_output = Path(metrics_path)
        metrics_output.parent.mkdir(parents=True, exist_ok=True)
        metrics_output.write_text(json.dumps(bundle["metadata"], indent=2), encoding="utf-8")
    return output
