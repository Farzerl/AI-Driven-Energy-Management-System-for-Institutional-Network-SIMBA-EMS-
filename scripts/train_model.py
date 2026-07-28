from __future__ import annotations

import argparse
import gc
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import (
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.live.dataset_loader import load_dataset_archive

HORIZONS = {"30_minutes": 1, "2_hours": 4, "6_hours": 12, "24_hours": 48}
LAGS = (1, 2, 3, 4, 6, 12, 24, 48)
WINDOWS = (2, 4, 8, 12, 24, 48)
TEST_CUTOFF = pd.Timestamp("2026-04-01")
VALIDATION_CUTOFF = pd.Timestamp("2026-03-18")
BLEND_ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)
UNCERTAINTY_QUANTILE = 0.85
ACTUAL_HIGH_RATIO = 0.95
TARGET_ALERT_RECALL = 0.90

HORIZON_CANDIDATES: dict[str, list[dict[str, Any]]] = {
    "30_minutes": [
        {"max_iter": 160, "max_leaf_nodes": 63, "min_samples_leaf": 40},
        {"max_iter": 140, "max_leaf_nodes": 31, "min_samples_leaf": 35},
        {"max_iter": 200, "max_leaf_nodes": 63, "min_samples_leaf": 45},
        {"max_iter": 260, "max_leaf_nodes": 47, "min_samples_leaf": 60},
    ],
    "2_hours": [
        {"max_iter": 120, "max_leaf_nodes": 63, "min_samples_leaf": 40},
        {"max_iter": 140, "max_leaf_nodes": 31, "min_samples_leaf": 50},
        {"max_iter": 190, "max_leaf_nodes": 47, "min_samples_leaf": 60},
    ],
    "6_hours": [
        {"max_iter": 120, "max_leaf_nodes": 63, "min_samples_leaf": 40},
        {"max_iter": 140, "max_leaf_nodes": 31, "min_samples_leaf": 65},
        {"max_iter": 190, "max_leaf_nodes": 47, "min_samples_leaf": 80},
    ],
    "24_hours": [
        {"max_iter": 120, "max_leaf_nodes": 63, "min_samples_leaf": 50},
        {"max_iter": 140, "max_leaf_nodes": 31, "min_samples_leaf": 80},
        {"max_iter": 190, "max_leaf_nodes": 47, "min_samples_leaf": 100},
    ],
}


def find_archive(input_dir: Path) -> Path:
    archives = sorted(input_dir.glob("*.zip"))
    if len(archives) != 1:
        raise ValueError(f"Place exactly one dataset ZIP in {input_dir}. Found {len(archives)}.")
    return archives[0]


def feature_frame(data: pd.DataFrame, horizon_steps: int) -> pd.DataFrame:
    frame = data.rename(columns={"facility_id": "facility"}).copy()
    frame["timestamp_local"] = (
        pd.to_datetime(frame["timestamp"], utc=True)
        .dt.tz_convert("Africa/Harare")
        .dt.tz_localize(None)
    )
    frame = frame.drop(columns=["timestamp"])
    if "kwh_is_measured" not in frame:
        frame["kwh_is_measured"] = 1.0
    group = frame.groupby("facility", group_keys=False)
    for lag in LAGS:
        frame[f"lag_{lag}"] = group["kva"].shift(lag)
    for window in WINDOWS:
        rolling = group["kva"].rolling(window, min_periods=window)
        frame[f"mean_{window}"] = rolling.mean().reset_index(level=0, drop=True)
        frame[f"std_{window}"] = rolling.std(ddof=0).reset_index(level=0, drop=True)
        frame[f"max_{window}"] = rolling.max().reset_index(level=0, drop=True)
        frame[f"min_{window}"] = rolling.min().reset_index(level=0, drop=True)
    frame["trend_1"] = frame["kva"] - frame["lag_1"]
    frame["trend_2"] = frame["kva"] - frame["lag_2"]
    frame["trend_4"] = frame["kva"] - frame["lag_4"]
    frame["ratio_mean4"] = frame["kva"] / (frame["mean_4"] + 1e-3)
    target_time = frame["timestamp_local"] + pd.Timedelta(minutes=30 * horizon_steps)
    hour = target_time.dt.hour + target_time.dt.minute / 60
    day = target_time.dt.dayofweek
    month = target_time.dt.month - 1
    frame["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    frame["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    frame["dow_sin"] = np.sin(2 * np.pi * day / 7)
    frame["dow_cos"] = np.cos(2 * np.pi * day / 7)
    frame["month_sin"] = np.sin(2 * np.pi * month / 12)
    frame["month_cos"] = np.cos(2 * np.pi * month / 12)
    frame["is_weekend"] = (day >= 5).astype(float)
    frame["slot"] = (target_time.dt.hour * 2 + target_time.dt.minute // 30).astype(float)
    frame["target"] = group["kva"].shift(-horizon_steps)
    return frame.dropna().reset_index(drop=True)


def matrix(frame: pd.DataFrame, numeric: list[str], facilities: list[str]) -> np.ndarray:
    facility_values = frame["facility"].to_numpy()
    one_hot = np.column_stack(
        [(facility_values == facility).astype(np.float32) for facility in facilities]
    )
    return np.column_stack([frame[numeric].to_numpy(np.float32), one_hot])


def export_tree(stage: list[object]) -> dict[str, object]:
    nodes = stage[0].nodes
    return {
        "feature_idx": nodes["feature_idx"].astype(int).tolist(),
        "threshold": np.round(nodes["num_threshold"].astype(float), 7).tolist(),
        "missing_left": nodes["missing_go_to_left"].astype(int).tolist(),
        "left": nodes["left"].astype(int).tolist(),
        "right": nodes["right"].astype(int).tolist(),
        "value": np.round(nodes["value"].astype(float), 9).tolist(),
        "is_leaf": nodes["is_leaf"].astype(int).tolist(),
    }


def regression_metrics(actual: np.ndarray, predicted: np.ndarray, persistence: np.ndarray) -> dict[str, float]:
    errors = actual - predicted
    absolute = np.abs(errors)
    mae = float(mean_absolute_error(actual, predicted))
    baseline_mae = float(mean_absolute_error(actual, persistence))
    return {
        "mae_kva": round(mae, 4),
        "rmse_kva": round(float(mean_squared_error(actual, predicted) ** 0.5), 4),
        "wape_percent": round(float(absolute.sum() / max(np.abs(actual).sum(), 1e-9) * 100), 3),
        "r2": round(float(r2_score(actual, predicted)), 4),
        "p90_abs_error_kva": round(float(np.percentile(absolute, 90)), 4),
        "p95_abs_error_kva": round(float(np.percentile(absolute, 95)), 4),
        "p99_abs_error_kva": round(float(np.percentile(absolute, 99)), 4),
        "mean_bias_kva": round(float(errors.mean()), 4),
        "under_forecast_fraction": round(float((errors > 0).mean()), 4),
        "persistence_mae_kva": round(baseline_mae, 4),
        "mae_improvement_vs_persistence_percent": round(
            (baseline_mae - mae) / max(baseline_mae, 1e-9) * 100,
            2,
        ),
    }


def choose_blends(frame: pd.DataFrame, predicted: np.ndarray) -> dict[str, float]:
    output: dict[str, float] = {}
    checked = frame.assign(model_prediction=predicted)
    for facility, group in checked.groupby("facility"):
        actual = group["target"].to_numpy(float)
        model_pred = group["model_prediction"].to_numpy(float)
        persistence = group["kva"].to_numpy(float)
        scores = {
            alpha: float(
                np.abs(actual - (alpha * model_pred + (1.0 - alpha) * persistence)).mean()
            )
            for alpha in BLEND_ALPHAS
        }
        best_alpha = min(scores, key=scores.get)
        persistence_mae = scores[0.0]
        minimum_material_gain = max(0.02, 0.01 * persistence_mae)
        if persistence_mae - scores[best_alpha] < minimum_material_gain:
            best_alpha = 0.0
        output[str(facility)] = float(best_alpha)
    return output


def apply_blends(frame: pd.DataFrame, predicted: np.ndarray, blends: dict[str, float]) -> np.ndarray:
    facilities = frame["facility"].astype(str).to_numpy()
    persistence = frame["kva"].to_numpy(float)
    alpha = np.asarray([blends.get(facility, 1.0) for facility in facilities], dtype=float)
    return np.maximum(alpha * predicted + (1.0 - alpha) * persistence, 0.0)


def uncertainty_margins(
    frame: pd.DataFrame,
    predicted: np.ndarray,
) -> tuple[float, dict[str, float]]:
    residual = np.maximum(frame["target"].to_numpy(float) - predicted, 0.0)
    global_margin = float(np.quantile(residual, UNCERTAINTY_QUANTILE))
    per_facility: dict[str, float] = {}
    checked = frame.assign(under_residual=residual)
    for facility, group in checked.groupby("facility"):
        if len(group) >= 96:
            per_facility[str(facility)] = float(
                np.quantile(group["under_residual"].to_numpy(float), UNCERTAINTY_QUANTILE)
            )
    return global_margin, per_facility


def upper_forecasts(
    frame: pd.DataFrame,
    predicted: np.ndarray,
    global_margin: float,
    per_facility: dict[str, float],
) -> np.ndarray:
    margins = np.asarray(
        [per_facility.get(str(facility), global_margin) for facility in frame["facility"]],
        dtype=float,
    )
    return predicted + margins


def tune_alert_threshold(
    frame: pd.DataFrame,
    upper: np.ndarray,
    limits: dict[str, float],
) -> tuple[float, dict[str, float]]:
    facility_limits = np.asarray(
        [max(float(limits[str(facility)]), 1e-9) for facility in frame["facility"]],
        dtype=float,
    )
    actual_high = frame["target"].to_numpy(float) / facility_limits >= ACTUAL_HIGH_RATIO
    upper_ratio = upper / facility_limits
    candidates: list[tuple[float, float, float, float]] = []
    for threshold in np.arange(0.80, 1.001, 0.01):
        warning = upper_ratio >= threshold
        precision = float(precision_score(actual_high, warning, zero_division=0))
        recall = float(recall_score(actual_high, warning, zero_division=0))
        f1 = float(f1_score(actual_high, warning, zero_division=0))
        candidates.append((float(threshold), precision, recall, f1))
    eligible = [row for row in candidates if row[2] >= TARGET_ALERT_RECALL]
    selected = max(eligible or candidates, key=lambda row: (row[3], row[2], row[1], row[0]))
    return selected[0], {
        "precision": round(selected[1], 4),
        "recall": round(selected[2], 4),
        "f1": round(selected[3], 4),
        "target_recall": TARGET_ALERT_RECALL,
        "actual_high_ratio": ACTUAL_HIGH_RATIO,
    }


def alert_metrics(
    frame: pd.DataFrame,
    upper: np.ndarray,
    limits: dict[str, float],
    threshold: float,
) -> dict[str, float | int]:
    facility_limits = np.asarray(
        [max(float(limits[str(facility)]), 1e-9) for facility in frame["facility"]],
        dtype=float,
    )
    actual_high = frame["target"].to_numpy(float) / facility_limits >= ACTUAL_HIGH_RATIO
    warning = upper / facility_limits >= threshold
    tp = int(np.sum(actual_high & warning))
    fp = int(np.sum(~actual_high & warning))
    fn = int(np.sum(actual_high & ~warning))
    tn = int(np.sum(~actual_high & ~warning))
    return {
        "precision": round(float(precision_score(actual_high, warning, zero_division=0)), 4),
        "recall": round(float(recall_score(actual_high, warning, zero_division=0)), 4),
        "f1": round(float(f1_score(actual_high, warning, zero_division=0)), 4),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "positive_events": int(np.sum(actual_high)),
        "warning_events": int(np.sum(warning)),
        "alert_threshold_ratio": round(float(threshold), 4),
        "actual_high_ratio": ACTUAL_HIGH_RATIO,
    }


def top_permutation_importance(
    estimator: HistGradientBoostingRegressor,
    x: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
) -> dict[str, float]:
    if len(x) > 2500:
        index = np.linspace(0, len(x) - 1, 2500, dtype=int)
        x = x[index]
        y = y[index]
    result = permutation_importance(
        estimator,
        x,
        y,
        scoring="neg_mean_absolute_error",
        n_repeats=2,
        random_state=42,
        n_jobs=1,
    )
    ordered = np.argsort(result.importances_mean)[::-1][:12]
    return {
        feature_names[int(index)]: round(float(result.importances_mean[int(index)]), 6)
        for index in ordered
        if result.importances_mean[int(index)] > 0
    }


def base_parameters(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "learning_rate": 0.06,
        "l2_regularization": 5.0,
        "early_stopping": False,
        "random_state": 42,
        **candidate,
    }


def train(data: pd.DataFrame) -> tuple[dict[str, object], dict[str, object]]:
    facilities = sorted(data["facility_id"].astype(str).unique())
    pre_test = data[pd.to_datetime(data["timestamp"], utc=True) < pd.Timestamp(TEST_CUTOFF, tz="Africa/Harare").tz_convert("UTC")]
    limits = (pre_test.groupby("facility_id")["kva"].quantile(0.95) * 1.05).round(3).to_dict()
    limits = {str(key): float(value) for key, value in limits.items()}

    horizons: dict[str, object] = {}
    validation_report: dict[str, object] = {}
    feature_names: list[str] | None = None
    per_facility_test: dict[str, object] = {}

    for name, steps in HORIZONS.items():
        started = time.perf_counter()
        frame = feature_frame(data, steps)
        inner_train = frame[frame["timestamp_local"] < VALIDATION_CUTOFF]
        validation = frame[
            (frame["timestamp_local"] >= VALIDATION_CUTOFF)
            & (frame["timestamp_local"] < TEST_CUTOFF)
        ]
        final_train = frame[frame["timestamp_local"] < TEST_CUTOFF]
        test_set = frame[frame["timestamp_local"] >= TEST_CUTOFF]
        numeric = [column for column in frame.columns if column not in {"timestamp_local", "facility", "target"}]
        names = numeric + [f"facility::{facility}" for facility in facilities]
        feature_names = feature_names or names

        x_inner = matrix(inner_train, numeric, facilities)
        x_validation = matrix(validation, numeric, facilities)
        y_inner = inner_train["target"].to_numpy(float)
        y_validation = validation["target"].to_numpy(float)

        search_rows: list[dict[str, object]] = []
        best_params: dict[str, Any] | None = None
        best_mae = float("inf")
        best_validation_prediction: np.ndarray | None = None
        best_validation_estimator: HistGradientBoostingRegressor | None = None
        for candidate in HORIZON_CANDIDATES[name]:
            params = base_parameters(candidate)
            estimator = HistGradientBoostingRegressor(**params).fit(x_inner, y_inner)
            prediction = np.maximum(estimator.predict(x_validation), 0.0)
            mae = float(mean_absolute_error(y_validation, prediction))
            search_rows.append({
                "parameters": {key: value for key, value in params.items() if key != "random_state"},
                "validation_mae_kva": round(mae, 4),
                "iterations": int(estimator.n_iter_),
            })
            if mae < best_mae:
                best_mae = mae
                best_params = params
                best_validation_prediction = prediction
                best_validation_estimator = estimator

        assert best_params is not None
        assert best_validation_prediction is not None
        assert best_validation_estimator is not None
        blends = choose_blends(validation, best_validation_prediction)
        blended_validation = apply_blends(validation, best_validation_prediction, blends)
        global_margin, facility_margins = uncertainty_margins(validation, blended_validation)
        validation_upper = upper_forecasts(
            validation,
            blended_validation,
            global_margin,
            facility_margins,
        )
        alert_threshold, validation_alert = tune_alert_threshold(
            validation,
            validation_upper,
            limits,
        )
        importance = top_permutation_importance(
            best_validation_estimator,
            x_validation,
            y_validation,
            names,
        )

        x_final = matrix(final_train, numeric, facilities)
        x_test = matrix(test_set, numeric, facilities)
        final_estimator = HistGradientBoostingRegressor(**best_params).fit(
            x_final,
            final_train["target"].to_numpy(float),
        )
        raw_test_prediction = np.maximum(final_estimator.predict(x_test), 0.0)
        test_prediction = apply_blends(test_set, raw_test_prediction, blends)
        test_upper = upper_forecasts(test_set, test_prediction, global_margin, facility_margins)
        actual = test_set["target"].to_numpy(float)
        persistence = test_set["kva"].to_numpy(float)
        metrics = regression_metrics(actual, test_prediction, persistence)
        classification = alert_metrics(test_set, test_upper, limits, alert_threshold)
        metrics.update(
            {
                "training_rows": int(len(final_train)),
                "validation_rows": int(len(validation)),
                "test_rows": int(len(test_set)),
                "training_seconds": round(time.perf_counter() - started, 2),
                "uncertainty_coverage_target": UNCERTAINTY_QUANTILE,
                "upper_margin_kva": round(global_margin, 4),
                "high_risk_precision": classification["precision"],
                "high_risk_recall": classification["recall"],
                "high_risk_f1": classification["f1"],
            }
        )

        horizons[name] = {
            "steps": steps,
            "minutes": steps * 30,
            "metrics": metrics,
            "classification": classification,
            "validation_classification": validation_alert,
            "parameters": {key: value for key, value in best_params.items() if key != "random_state"},
            "parameter_search": search_rows,
            "facility_blend_alpha": blends,
            "uncertainty": {
                "method": "chronological_conformal_upper_margin",
                "quantile": UNCERTAINTY_QUANTILE,
                "global_underforecast_margin_kva": round(global_margin, 6),
                "facility_underforecast_margin_kva": {
                    key: round(value, 6) for key, value in facility_margins.items()
                },
            },
            "risk_policy": {
                "actual_high_ratio": ACTUAL_HIGH_RATIO,
                "high_alert_threshold_ratio": round(alert_threshold, 4),
                "medium_alert_threshold_ratio": round(max(alert_threshold - 0.10, 0.75), 4),
                "calibration_target_recall": TARGET_ALERT_RECALL,
            },
            "top_permutation_importance": importance,
            "model": {
                "baseline": float(final_estimator._baseline_prediction.ravel()[0]),
                "iterations": int(final_estimator.n_iter_),
                "trees": [export_tree(stage) for stage in final_estimator._predictors],
            },
        }
        validation_report[name] = {
            **metrics,
            "classification": classification,
            "validation_classification": validation_alert,
            "selected_parameters": horizons[name]["parameters"],
            "parameter_search": search_rows,
            "top_permutation_importance": importance,
        }

        checked = test_set.assign(prediction=test_prediction)
        for facility, group in checked.groupby("facility"):
            facility_name = str(facility)
            actual_values = group["target"].to_numpy(float)
            predicted_values = group["prediction"].to_numpy(float)
            persistence_values = group["kva"].to_numpy(float)
            row = per_facility_test.setdefault(facility_name, {})
            row[name] = {
                "rows": int(len(group)),
                "blend_alpha": blends.get(facility_name, 1.0),
                "mae_kva": round(float(np.abs(actual_values - predicted_values).mean()), 4),
                "persistence_mae_kva": round(float(np.abs(actual_values - persistence_values).mean()), 4),
                "beats_persistence": bool(
                    np.abs(actual_values - predicted_values).mean()
                    <= np.abs(actual_values - persistence_values).mean()
                ),
            }

        del (
            frame,
            inner_train,
            validation,
            final_train,
            test_set,
            x_inner,
            x_validation,
            x_final,
            x_test,
            best_validation_estimator,
            final_estimator,
            raw_test_prediction,
            test_prediction,
            test_upper,
        )
        gc.collect()

    fingerprint = hashlib.sha256(
        f"{len(data)}|{data['timestamp'].min()}|{data['timestamp'].max()}|{'|'.join(facilities)}".encode()
    ).hexdigest()
    bundle = {
        "schema_version": 3,
        "model_name": "SIMBA Multi-Horizon Demand Forecaster",
        "model_family": "Chronologically tuned histogram gradient boosting with facility-level blending",
        "trained_on": "University of Zimbabwe half-hourly smart-meter data",
        "interval_minutes": 30,
        "minimum_history_intervals": 49,
        "facilities": facilities,
        "feature_names": feature_names,
        "horizons": horizons,
        "facility_limits_kva": limits,
        "training_period": {
            "start": "2025-09-01 00:00:00 Africa/Harare",
            "end": "2026-03-31 23:30:00 Africa/Harare",
        },
        "chronological_validation_period": {
            "start": "2026-03-18 00:00:00 Africa/Harare",
            "end": "2026-03-31 23:30:00 Africa/Harare",
        },
        "chronological_test_period": {
            "start": "2026-04-01 00:00:00 Africa/Harare",
            "end": "2026-04-30 23:30:00 Africa/Harare",
        },
        "data_quality": {
            "clean_rows": int(len(data)),
            "facility_count": len(facilities),
            "interval_minutes": 30,
            "kwh_measured_fraction": round(float(data["kwh_is_measured"].mean()), 4),
        },
        "dataset_fingerprint": fingerprint,
    }
    report = {
        "status": "pass",
        "model_name": bundle["model_name"],
        "model_family": bundle["model_family"],
        "training_period": bundle["training_period"],
        "chronological_validation_period": bundle["chronological_validation_period"],
        "chronological_test_period": bundle["chronological_test_period"],
        "data_quality": bundle["data_quality"],
        "horizons": validation_report,
        "per_facility": per_facility_test,
        "selection_note": (
            "Hyperparameters, facility blending, uncertainty margins and alert thresholds were "
            "selected on the final two weeks of March. April 2026 remained untouched until the "
            "final held-out evaluation."
        ),
    }
    return bundle, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Train and chronologically test the SIMBA institutional demand model.")
    parser.add_argument("--input-dir", type=Path, default=ROOT / "training_data")
    parser.add_argument("--archive", type=Path)
    parser.add_argument(
        "--model-output",
        type=Path,
        default=ROOT / "models" / "institutional_multi_horizon_forecaster.json",
    )
    parser.add_argument(
        "--metrics-output",
        type=Path,
        default=ROOT / "evidence" / "model_validation" / "institutional_multi_horizon_metrics.json",
    )
    args = parser.parse_args()
    archive = args.archive or find_archive(args.input_dir)
    with tempfile.TemporaryDirectory(prefix="simba_ems_training_") as temp_dir:
        data, _ = load_dataset_archive(archive, Path(temp_dir))
        bundle, report = train(data)
    args.model_output.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
    args.model_output.write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf-8")
    args.metrics_output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("SIMBA demand model training and chronological testing: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
