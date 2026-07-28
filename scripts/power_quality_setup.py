from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import shutil
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.chronos2_setup import (  # noqa: E402
    atomic_json,
    calendar_features,
    log_line,
    one_zip,
    safe_extract_model,
    save_pipeline,
    sha256,
    utc_now,
)
from src.live.dataset_loader import load_dataset_archive  # noqa: E402

TARGETS = ["active_power_kw", "reactive_power_kvar"]
TARGET_UNITS = {"active_power_kw": "kW", "reactive_power_kvar": "kVAR"}
HORIZONS = {"30_minutes": 1, "2_hours": 4, "6_hours": 12, "24_hours": 48}
KNOWN_COVARIATES = ["half_hour_slot", "day_of_week", "is_weekend", "month", "tariff_period"]
PAST_COVARIATES = [
    "demand_kva",
    "power_factor",
    "measurement_quality",
    "gap_imputed",
]
SETUP_TRANSACTION_VERSION = 1


@dataclass(frozen=True)
class PowerQualitySample:
    facility: str
    item_id: str
    origin: pd.Timestamp
    context: pd.DataFrame
    actual: dict[str, dict[str, float]]


def _complete_model(path: Path) -> bool:
    return (path / "config.json").is_file() and any(path.rglob("*.safetensors"))


def resolve_source_model(project_root: Path, log_path: Path) -> tuple[Path, str, bool]:
    demand_adapted = project_root / "models" / "chronos-2-finetuned"
    base = project_root / "models" / "chronos-2-base"
    if _complete_model(demand_adapted):
        return demand_adapted, "demand_finetuned_source", False
    if _complete_model(base):
        return base, "official_base_source", False

    input_dir = project_root / "chronos_input"
    model_zip = one_zip(input_dir, "Chronos-2 model input")
    safe_extract_model(model_zip, base)
    log_line(log_path, f"Extracted and verified Chronos-2 from {model_zip.name} because no local source model was installed.")
    return base, "official_base_source", True


def _longest_valid_segments(frame: pd.DataFrame, minimum_rows: int) -> list[pd.DataFrame]:
    valid = frame[TARGETS].notna().all(axis=1)
    group_id = (valid != valid.shift(fill_value=False)).cumsum()
    segments: list[pd.DataFrame] = []
    for _, segment in frame[valid].groupby(group_id[valid], sort=False):
        if len(segment) >= minimum_rows:
            segments.append(segment.copy())
    return segments


def regularise_frame(data: pd.DataFrame, *, interpolation_limit: int, minimum_rows: int) -> tuple[pd.DataFrame, dict[str, object]]:
    rows: list[pd.DataFrame] = []
    facility_quality: dict[str, object] = {}
    for facility, raw in data.groupby("facility_id", sort=True):
        group = raw.sort_values("timestamp").drop_duplicates("timestamp", keep="last").copy()
        timestamp = pd.to_datetime(group["timestamp"], errors="coerce")
        if timestamp.dt.tz is not None:
            timestamp = timestamp.dt.tz_convert("Africa/Harare").dt.tz_localize(None)
        group["timestamp"] = timestamp
        group = group.dropna(subset=["timestamp"]).set_index("timestamp")
        full_index = pd.date_range(group.index.min(), group.index.max(), freq="30min")
        reindexed = group.reindex(full_index)
        reindexed.index.name = "timestamp"
        reindexed["facility_id"] = str(facility)
        originally_missing = reindexed[TARGETS].isna().any(axis=1)

        interpolation_columns = [
            *TARGETS,
            "kva",
            "power_factor",
            "data_points",
            "active_power_kw_is_measured",
            "reactive_power_kvar_is_measured",
        ]
        for column in interpolation_columns:
            if column not in reindexed:
                reindexed[column] = np.nan
            numeric = pd.to_numeric(reindexed[column], errors="coerce")
            reindexed[column] = numeric.interpolate(
                method="time",
                limit=max(interpolation_limit, 0),
                limit_area="inside",
            )

        reindexed["gap_imputed"] = originally_missing.astype(float)
        reindexed["measurement_quality"] = (
            1.0
            - 0.35 * reindexed["gap_imputed"].clip(0, 1)
            - 0.15 * (1.0 - reindexed["active_power_kw_is_measured"].fillna(0).clip(0, 1))
            - 0.15 * (1.0 - reindexed["reactive_power_kvar_is_measured"].fillna(0).clip(0, 1))
        ).clip(0, 1)
        reindexed["demand_kva"] = pd.to_numeric(reindexed["kva"], errors="coerce")
        derived_pf = (
            reindexed["active_power_kw"].abs()
            / np.sqrt(reindexed["active_power_kw"].pow(2) + reindexed["reactive_power_kvar"].pow(2)).replace(0, np.nan)
        ).clip(0, 1)
        reindexed["power_factor"] = derived_pf.fillna(reindexed["power_factor"]).fillna(0.95).clip(0, 1)
        reindexed["demand_kva"] = reindexed["demand_kva"].fillna(
            np.sqrt(reindexed["active_power_kw"].pow(2) + reindexed["reactive_power_kvar"].pow(2))
        )

        segments = _longest_valid_segments(reindexed.reset_index(), minimum_rows)
        if not segments:
            facility_quality[str(facility)] = {
                "status": "excluded",
                "reason": "No continuous target segment met the minimum history requirement.",
                "raw_rows": int(len(group)),
                "grid_rows": int(len(reindexed)),
            }
            continue
        for index, segment in enumerate(segments, start=1):
            segment = segment.sort_values("timestamp").copy()
            segment["item_id"] = str(facility) if len(segments) == 1 else f"{facility}::segment_{index}"
            timestamps = pd.to_datetime(segment["timestamp"], errors="coerce")
            segment["half_hour_slot"] = (timestamps.dt.hour * 2 + timestamps.dt.minute // 30).astype(int)
            segment["day_of_week"] = timestamps.dt.dayofweek.astype(int)
            segment["is_weekend"] = (timestamps.dt.dayofweek >= 5).astype(int)
            segment["month"] = timestamps.dt.month.astype(int)
            hours = timestamps.dt.hour
            segment["tariff_period"] = np.select(
                [hours.isin([7, 8, 17, 18]), (hours >= 22) | (hours < 5)],
                ["peak", "offpeak"],
                default="standard",
            )
            rows.append(segment)
        facility_quality[str(facility)] = {
            "status": "included",
            "raw_rows": int(len(group)),
            "grid_rows": int(len(reindexed)),
            "imputed_target_rows": int(originally_missing.sum()),
            "continuous_segments": int(len(segments)),
            "retained_rows": int(sum(len(item) for item in segments)),
            "reactive_sign_preference": -1 if float(group["reactive_power_kvar"].median()) < 0 else 1,
            "median_power_factor": round(float(derived_pf.median()), 6),
        }

    if not rows:
        raise ValueError("No facility had a valid continuous power-quality series after cleaning.")
    frame = pd.concat(rows, ignore_index=True)
    frame = frame.sort_values(["item_id", "timestamp"]).reset_index(drop=True)
    return frame, facility_quality


def split_months(frame: pd.DataFrame) -> dict[str, pd.Timestamp]:
    months = sorted(frame["timestamp"].dt.to_period("M").unique())
    if len(months) < 3:
        raise ValueError("Power-quality training requires at least three chronological months.")
    validation_month = months[-2]
    test_month = months[-1]
    return {
        "training_start": frame["timestamp"].min(),
        "validation_start": validation_month.start_time,
        "test_start": test_month.start_time,
        "test_end": (test_month + 1).start_time,
    }


def _context_for(group: pd.DataFrame, origin: pd.Timestamp, context_length: int) -> pd.DataFrame | None:
    context = group[group["timestamp"] <= origin].tail(context_length)
    if len(context) < 49:
        return None
    gaps = context["timestamp"].diff().dropna().dt.total_seconds().div(60)
    if not gaps.empty and bool((gaps.sub(30).abs() > 0.1).any()):
        return None
    return context


def build_samples(
    frame: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    stride: int,
    maximum_origins: int,
    context_length: int,
) -> tuple[list[pd.Timestamp], list[PowerQualitySample]]:
    timestamps = sorted(frame.loc[(frame["timestamp"] >= start) & (frame["timestamp"] < end), "timestamp"].unique())
    candidates = [pd.Timestamp(value) for value in timestamps[:: max(stride, 1)]]
    if len(candidates) > maximum_origins:
        positions = np.linspace(0, len(candidates) - 1, maximum_origins, dtype=int)
        candidates = [candidates[int(position)] for position in sorted(set(positions.tolist()))]

    samples: list[PowerQualitySample] = []
    accepted_origins: set[pd.Timestamp] = set()
    for item_id, raw_group in frame.groupby("item_id", sort=True):
        group = raw_group.sort_values("timestamp").reset_index(drop=True)
        timestamp_index = pd.DatetimeIndex(group["timestamp"])
        # The regularisation stage guarantees 30-minute spacing within each retained segment.
        for origin in candidates:
            position = int(timestamp_index.searchsorted(origin))
            if position >= len(group) or timestamp_index[position] != origin:
                continue
            context_start = max(0, position - context_length + 1)
            if position - context_start + 1 < 49 or position + max(HORIZONS.values()) >= len(group):
                continue
            context = group.iloc[context_start : position + 1].copy()
            actual: dict[str, dict[str, float]] = {target: {} for target in TARGETS}
            valid = True
            for horizon, step in HORIZONS.items():
                target_row = group.iloc[position + step]
                expected = origin + pd.Timedelta(minutes=30 * step)
                if pd.Timestamp(target_row["timestamp"]) != expected or float(target_row.get("gap_imputed", 0.0)) > 0:
                    valid = False
                    break
                for target in TARGETS:
                    value = float(target_row[target])
                    if not math.isfinite(value):
                        valid = False
                        break
                    actual[target][horizon] = value
                if not valid:
                    break
            if not valid:
                continue
            facility = str(context.iloc[-1]["facility_id"])
            samples.append(
                PowerQualitySample(
                    facility=facility,
                    item_id=str(item_id),
                    origin=origin,
                    context=context,
                    actual=actual,
                )
            )
            accepted_origins.add(origin)
    if not samples:
        raise ValueError("No complete chronological power-quality benchmark samples could be prepared.")
    return sorted(accepted_origins), samples


def prediction_frames(samples: list[PowerQualitySample]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, PowerQualitySample]]:
    contexts: list[pd.DataFrame] = []
    futures: list[dict[str, object]] = []
    mapping: dict[str, PowerQualitySample] = {}
    columns = ["item_id", "timestamp", *TARGETS, *PAST_COVARIATES, *KNOWN_COVARIATES]
    for sample in samples:
        sample_id = f"{sample.facility}::{sample.origin.isoformat()}"
        mapping[sample_id] = sample
        context = sample.context.copy()
        context["item_id"] = sample_id
        contexts.append(context[columns])
        for step in range(1, 49):
            timestamp = sample.origin + pd.Timedelta(minutes=30 * step)
            futures.append({"item_id": sample_id, "timestamp": timestamp, **calendar_features(timestamp)})
    return pd.concat(contexts, ignore_index=True), pd.DataFrame(futures), mapping


def _forecast_values(row: pd.Series, target: str) -> tuple[float, float, float]:
    point = float(row.get("0.5", row.get("predictions")))
    lower = float(row.get("0.1", point))
    upper = float(row.get("0.9", point))
    values = sorted([lower, point, upper])
    if target == "active_power_kw":
        values = [max(value, 0.0) for value in values]
    if not all(math.isfinite(value) for value in values):
        raise RuntimeError(f"Chronos-2 returned an invalid {target} forecast.")
    return values[1], values[0], values[2]


def chronos_predictions(
    pipeline: Any,
    samples: list[PowerQualitySample],
    *,
    batch_origins: int,
    inference_batch_size: int,
    cross_learning: bool,
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    origins = sorted({sample.origin for sample in samples})
    for index in range(0, len(origins), max(batch_origins, 1)):
        origin_set = set(origins[index : index + max(batch_origins, 1)])
        batch = [sample for sample in samples if sample.origin in origin_set]
        context, future, mapping = prediction_frames(batch)
        started = time.perf_counter()
        predicted = pipeline.predict_df(
            context,
            future_df=future,
            prediction_length=48,
            quantile_levels=[0.1, 0.5, 0.9],
            id_column="item_id",
            timestamp_column="timestamp",
            target=TARGETS,
            batch_size=max(inference_batch_size, len(TARGETS)),
            context_length=336,
            cross_learning=cross_learning,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        for sample_id, sample_group in predicted.groupby("item_id", sort=False):
            sample = mapping[str(sample_id)]
            for target in TARGETS:
                target_group = sample_group[sample_group["target_name"] == target].sort_values("timestamp").reset_index(drop=True)
                if len(target_group) < 48:
                    raise RuntimeError(f"Chronos-2 returned {len(target_group)} {target} steps; 48 required.")
                for horizon, step in HORIZONS.items():
                    point, lower, upper = _forecast_values(target_group.iloc[step - 1], target)
                    output.append(
                        {
                            "facility": sample.facility,
                            "item_id": sample.item_id,
                            "origin": sample.origin.isoformat(),
                            "horizon": horizon,
                            "target": target,
                            "actual": sample.actual[target][horizon],
                            "forecast": point,
                            "lower": lower,
                            "upper": upper,
                            "latency_ms_per_facility": elapsed_ms / max(len(batch), 1),
                        }
                    )
    return output


def seasonal_predictions(samples: list[PowerQualitySample]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sample in samples:
        context = sample.context.set_index("timestamp")
        for target in TARGETS:
            latest = float(sample.context.iloc[-1][target])
            for horizon, step in HORIZONS.items():
                target_time = sample.origin + pd.Timedelta(minutes=30 * step)
                seasonal_time = target_time - pd.Timedelta(hours=24)
                forecast = float(context.loc[seasonal_time, target]) if seasonal_time in context.index else latest
                rows.append(
                    {
                        "facility": sample.facility,
                        "item_id": sample.item_id,
                        "origin": sample.origin.isoformat(),
                        "horizon": horizon,
                        "target": target,
                        "actual": sample.actual[target][horizon],
                        "forecast": max(forecast, 0.0) if target == "active_power_kw" else forecast,
                        "lower": max(forecast, 0.0) if target == "active_power_kw" else forecast,
                        "upper": max(forecast, 0.0) if target == "active_power_kw" else forecast,
                        "latency_ms_per_facility": 0.0,
                    }
                )
    return rows


def _metric_status(event_count: int) -> str:
    return "defined" if event_count > 0 else "not_applicable_no_events"


def metric_rows(rows: Iterable[Mapping[str, object]], target: str) -> dict[str, object]:
    items = list(rows)
    if not items:
        return {"samples": 0, "status": "not_available", "unit": TARGET_UNITS[target]}
    actual = np.asarray([float(item["actual"]) for item in items], dtype=float)
    forecast = np.asarray([float(item["forecast"]) for item in items], dtype=float)
    errors = actual - forecast
    absolute = np.abs(errors)
    scale = max(float(np.percentile(actual, 90) - np.percentile(actual, 10)), 1e-6)
    latency = np.asarray([float(item.get("latency_ms_per_facility", 0.0)) for item in items], dtype=float)
    return {
        "status": "defined",
        "samples": len(items),
        "unit": TARGET_UNITS[target],
        "mae": round(float(absolute.mean()), 4),
        "rmse": round(float(np.sqrt(np.mean(errors**2))), 4),
        "wape_percent": round(float(absolute.sum() / max(np.abs(actual).sum(), 1e-9) * 100), 4),
        "mean_bias": round(float(errors.mean()), 4),
        "p90_abs_error": round(float(np.percentile(absolute, 90)), 4),
        "p99_abs_error": round(float(np.percentile(absolute, 99)), 4),
        "under_forecast_fraction": round(float(np.mean(errors > 0)), 4),
        "normalised_mae_percent_of_p90_p10_range": round(float(absolute.mean() / scale * 100), 4),
        "median_latency_ms_per_facility": round(float(np.median(latency)), 4),
        "p95_latency_ms_per_facility": round(float(np.percentile(latency, 95)), 4),
    }


def metrics_by_target_horizon(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        target: {
            horizon: metric_rows(
                [item for item in rows if item["target"] == target and item["horizon"] == horizon],
                target,
            )
            for horizon in HORIZONS
        }
        for target in TARGETS
    }


def _derived_pairs(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    keyed: dict[tuple[str, str, str], dict[str, dict[str, object]]] = {}
    for item in rows:
        key = (str(item["facility"]), str(item["origin"]), str(item["horizon"]))
        keyed.setdefault(key, {})[str(item["target"])] = item
    output: list[dict[str, object]] = []
    for (facility, origin, horizon), values in keyed.items():
        if not all(target in values for target in TARGETS):
            continue
        p = values["active_power_kw"]
        q = values["reactive_power_kvar"]
        actual_p = max(float(p["actual"]), 0.0)
        actual_q = float(q["actual"])
        forecast_p = max(float(p["forecast"]), 0.0)
        forecast_q = float(q["forecast"])
        lower_p = max(float(p["lower"]), 0.0)
        q_bound = max(abs(float(q["lower"])), abs(float(q["upper"])))
        actual_kva = math.hypot(actual_p, actual_q)
        forecast_kva = math.hypot(forecast_p, forecast_q)
        conservative_kva = math.hypot(lower_p, q_bound)
        actual_pf = actual_p / actual_kva if actual_kva > 1e-9 else 1.0
        forecast_pf = forecast_p / forecast_kva if forecast_kva > 1e-9 else 1.0
        conservative_pf = lower_p / conservative_kva if conservative_kva > 1e-9 else 1.0
        output.append(
            {
                "facility": facility,
                "origin": origin,
                "horizon": horizon,
                "actual_power_factor": actual_pf,
                "forecast_power_factor": forecast_pf,
                "conservative_power_factor": conservative_pf,
                "actual_energy_kwh": actual_p * 0.5,
                "forecast_energy_kwh": forecast_p * 0.5,
                "actual_reactive_energy_kvarh_estimated": abs(actual_q) * 0.5,
                "forecast_reactive_energy_kvarh_estimated": abs(forecast_q) * 0.5,
            }
        )
    return output


def derived_metrics(rows: list[dict[str, object]], *, low_pf_threshold: float) -> dict[str, object]:
    pairs = _derived_pairs(rows)
    actual_pf = np.asarray([float(item["actual_power_factor"]) for item in pairs], dtype=float)
    forecast_pf = np.asarray([float(item["forecast_power_factor"]) for item in pairs], dtype=float)
    conservative_pf = np.asarray([float(item["conservative_power_factor"]) for item in pairs], dtype=float)
    pf_error = np.abs(actual_pf - forecast_pf)
    actual_low = actual_pf < low_pf_threshold
    predicted_low = conservative_pf < low_pf_threshold
    tp = int(np.sum(actual_low & predicted_low))
    fp = int(np.sum(~actual_low & predicted_low))
    fn = int(np.sum(actual_low & ~predicted_low))
    event_count = int(np.sum(actual_low))
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)) if precision is not None and recall is not None and (precision + recall) else None

    actual_kwh = np.asarray([float(item["actual_energy_kwh"]) for item in pairs], dtype=float)
    forecast_kwh = np.asarray([float(item["forecast_energy_kwh"]) for item in pairs], dtype=float)
    actual_qh = np.asarray([float(item["actual_reactive_energy_kvarh_estimated"]) for item in pairs], dtype=float)
    forecast_qh = np.asarray([float(item["forecast_reactive_energy_kvarh_estimated"]) for item in pairs], dtype=float)
    return {
        "samples": len(pairs),
        "power_factor": {
            "mae": round(float(pf_error.mean()), 6) if len(pf_error) else None,
            "p90_abs_error": round(float(np.percentile(pf_error, 90)), 6) if len(pf_error) else None,
            "threshold": low_pf_threshold,
            "low_pf_events": event_count,
            "low_pf_precision": round(float(precision), 4) if precision is not None else None,
            "low_pf_recall": round(float(recall), 4) if recall is not None else None,
            "low_pf_f1": round(float(f1), 4) if f1 is not None else None,
            "metric_status": _metric_status(event_count),
        },
        "interval_energy_kwh": {
            "mae": round(float(np.mean(np.abs(actual_kwh - forecast_kwh))), 4) if len(actual_kwh) else None,
            "wape_percent": round(float(np.abs(actual_kwh - forecast_kwh).sum() / max(np.abs(actual_kwh).sum(), 1e-9) * 100), 4) if len(actual_kwh) else None,
        },
        "interval_reactive_energy_kvarh_estimated": {
            "mae": round(float(np.mean(np.abs(actual_qh - forecast_qh))), 4) if len(actual_qh) else None,
            "wape_percent": round(float(np.abs(actual_qh - forecast_qh).sum() / max(np.abs(actual_qh).sum(), 1e-9) * 100), 4) if len(actual_qh) else None,
        },
    }


def blend_rows(baseline: list[dict[str, object]], model: list[dict[str, object]], weight: float) -> list[dict[str, object]]:
    key = lambda item: (item["facility"], item["origin"], item["horizon"], item["target"])
    baseline_map = {key(item): item for item in baseline}
    output: list[dict[str, object]] = []
    for item in model:
        base = baseline_map.get(key(item))
        if base is None:
            continue
        target = str(item["target"])
        forecast = (1 - weight) * float(base["forecast"]) + weight * float(item["forecast"])
        lower = (1 - weight) * float(base["lower"]) + weight * float(item["lower"])
        upper = (1 - weight) * float(base["upper"]) + weight * float(item["upper"])
        values = sorted([lower, forecast, upper])
        if target == "active_power_kw":
            values = [max(value, 0.0) for value in values]
        output.append(
            {
                **item,
                "forecast": values[1],
                "lower": values[0],
                "upper": values[2],
                "latency_ms_per_facility": float(item.get("latency_ms_per_facility", 0.0)),
            }
        )
    return output


def _selection_score(rows: list[dict[str, object]]) -> float:
    metrics = metrics_by_target_horizon(rows)
    values = [
        float(metrics[target][horizon]["normalised_mae_percent_of_p90_p10_range"])
        for target in TARGETS
        for horizon in HORIZONS
    ]
    return float(np.mean(values))


def choose_variant(
    validation: Mapping[str, list[dict[str, object]]],
    *,
    minimum_improvement: float,
    maximum_recall_drop: float,
    low_pf_threshold: float,
) -> tuple[str, dict[str, object]]:
    source_rows = list(validation["chronos_source"])
    lora_rows = list(validation.get("chronos_power_quality_lora", []))
    source_score = _selection_score(source_rows)
    source_derived = derived_metrics(source_rows, low_pf_threshold=low_pf_threshold)
    if not lora_rows:
        return "source", {
            "selected": "source",
            "reason": "Power-quality LoRA metrics were unavailable, so the installed source Chronos model was retained.",
            "source_normalised_validation_score": round(source_score, 4),
            "lora_normalised_validation_score": None,
            "improvement_percent": None,
            "low_pf_recall_drop": None,
        }
    lora_score = _selection_score(lora_rows)
    lora_derived = derived_metrics(lora_rows, low_pf_threshold=low_pf_threshold)
    improvement = (source_score - lora_score) / max(source_score, 1e-9) * 100
    source_recall = source_derived["power_factor"]["low_pf_recall"]
    lora_recall = lora_derived["power_factor"]["low_pf_recall"]
    recall_drop = 0.0 if source_recall is None or lora_recall is None else float(source_recall) - float(lora_recall)
    selected = "power_quality_finetuned" if improvement >= minimum_improvement and recall_drop <= maximum_recall_drop else "source"
    reason = (
        "The power-quality LoRA model was selected because it improved the normalised chronological validation score without exceeding the low-power-factor recall guardrail."
        if selected == "power_quality_finetuned"
        else "The installed source Chronos model was retained because the power-quality LoRA model did not clear both validation and low-power-factor recall guardrails. The LoRA checkpoint and metrics remain available."
    )
    return selected, {
        "selected": selected,
        "reason": reason,
        "source_normalised_validation_score": round(source_score, 4),
        "lora_normalised_validation_score": round(lora_score, 4),
        "improvement_percent": round(improvement, 4),
        "source_low_pf_recall": source_recall,
        "lora_low_pf_recall": lora_recall,
        "low_pf_recall_drop": round(recall_drop, 4),
        "minimum_improvement_percent": minimum_improvement,
        "maximum_recall_drop": maximum_recall_drop,
    }


def select_routes(
    baseline_validation: list[dict[str, object]],
    model_validation: list[dict[str, object]],
    baseline_test: list[dict[str, object]],
    model_test: list[dict[str, object]],
    *,
    weights: list[float],
    minimum_improvement: float,
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    routing: dict[str, object] = {target: {} for target in TARGETS}
    selected_validation: list[dict[str, object]] = []
    selected_test: list[dict[str, object]] = []
    for target in TARGETS:
        for horizon in HORIZONS:
            base_val = [item for item in baseline_validation if item["target"] == target and item["horizon"] == horizon]
            model_val = [item for item in model_validation if item["target"] == target and item["horizon"] == horizon]
            base_metrics = metric_rows(base_val, target)
            candidates: list[tuple[str, float, list[dict[str, object]], dict[str, object]]] = [
                ("seasonal_persistence", 0.0, base_val, base_metrics),
                ("chronos", 1.0, model_val, metric_rows(model_val, target)),
            ]
            for weight in weights:
                blended = blend_rows(base_val, model_val, float(weight))
                candidates.append(("hybrid_chronos_seasonal", float(weight), blended, metric_rows(blended, target)))
            baseline_mae = float(base_metrics["mae"])
            eligible = [
                candidate
                for candidate in candidates[1:]
                if (baseline_mae - float(candidate[3]["mae"])) / max(baseline_mae, 1e-9) * 100 >= minimum_improvement
            ]
            winner = min(eligible, key=lambda item: float(item[3]["mae"])) if eligible else candidates[0]
            model_name, weight, winner_val_rows, winner_metrics = winner

            base_test_rows = [item for item in baseline_test if item["target"] == target and item["horizon"] == horizon]
            model_test_rows = [item for item in model_test if item["target"] == target and item["horizon"] == horizon]
            if model_name == "seasonal_persistence":
                winner_test_rows = base_test_rows
            elif model_name == "chronos":
                winner_test_rows = model_test_rows
            else:
                winner_test_rows = blend_rows(base_test_rows, model_test_rows, weight)
            selected_validation.extend(winner_val_rows)
            selected_test.extend(winner_test_rows)
            routing[target][horizon] = {
                "model": model_name,
                "chronos_weight": round(weight, 2),
                "validation_metrics": winner_metrics,
                "test_metrics": metric_rows(winner_test_rows, target),
                "reason": (
                    "The Chronos route improved chronological validation MAE over same-time previous-day persistence."
                    if model_name != "seasonal_persistence"
                    else "Previous-day persistence remained the safest default because no Chronos route cleared the validation improvement threshold."
                ),
            }
    return routing, selected_validation, selected_test


def _model_metrics(validation: list[dict[str, object]], test: list[dict[str, object]], low_pf_threshold: float) -> dict[str, object]:
    return {
        "validation": {
            "targets": metrics_by_target_horizon(validation),
            "derived": derived_metrics(validation, low_pf_threshold=low_pf_threshold),
        },
        "test": {
            "targets": metrics_by_target_horizon(test),
            "derived": derived_metrics(test, low_pf_threshold=low_pf_threshold),
        },
    }


def run(project_root: Path, *, force: bool = False, require_lora: bool = False) -> dict[str, object]:
    project_root = Path(project_root).resolve()
    runtime = project_root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    log_path = runtime / "power_quality_setup.log"
    state_path = runtime / "power_quality_setup_state.json"
    config_path = project_root / "config" / "power_quality_training.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    dataset_zip = one_zip(project_root / "training_data", "Power-quality training dataset")
    source_model, source_label, extracted_model_zip = resolve_source_model(project_root, log_path)
    transaction = {
        "status": "running",
        "started_utc": utc_now(),
        "dataset_zip_sha256": sha256(dataset_zip),
        "source_model": source_model.relative_to(project_root).as_posix(),
        "source_model_config_sha256": sha256(source_model / "config.json"),
        "config_sha256": sha256(config_path),
        "setup_code_sha256": sha256(Path(__file__).resolve()),
        "setup_transaction_version": SETUP_TRANSACTION_VERSION,
        "force_requested": bool(force),
        "require_lora": bool(require_lora),
    }
    previous: dict[str, object] = {}
    if state_path.exists():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}
    fingerprint = ("dataset_zip_sha256", "source_model_config_sha256", "config_sha256", "setup_code_sha256", "setup_transaction_version")
    same = previous.get("status") == "success" and all(previous.get(key) == transaction.get(key) for key in fingerprint)
    previous_lora = bool(dict(previous.get("training", {})).get("succeeded", False))
    if same and not force and (not require_lora or previous_lora):
        log_line(log_path, "Idempotent replay: matching power-quality setup transaction already completed.")
        return previous
    atomic_json(state_path, transaction)
    log_line(log_path, "Starting multi-target power-quality Chronos-2 setup transaction.")

    processed_dir = runtime / "power_quality_processed"
    shutil.rmtree(processed_dir, ignore_errors=True)
    data, aliases = load_dataset_archive(dataset_zip, processed_dir)
    frame, facility_quality = regularise_frame(
        data,
        interpolation_limit=int(config.get("short_gap_interpolation_limit_intervals", 6)),
        minimum_rows=int(config.get("minimum_past_intervals", 96)) + int(config.get("prediction_length_intervals", 48)),
    )
    splits = split_months(frame)
    context_length = int(config.get("context_length_intervals", 336))
    validation_origins, validation_samples = build_samples(
        frame,
        start=splits["validation_start"],
        end=splits["test_start"],
        stride=int(config.get("validation_stride_intervals", 48)),
        maximum_origins=int(config.get("maximum_validation_origins", 40)),
        context_length=context_length,
    )
    test_origins, test_samples = build_samples(
        frame,
        start=splits["test_start"],
        end=splits["test_end"],
        stride=int(config.get("test_stride_intervals", 48)),
        maximum_origins=int(config.get("maximum_test_origins", 40)),
        context_length=context_length,
    )
    log_line(log_path, f"Loaded {len(frame):,} regularised readings across {frame['facility_id'].nunique()} facilities.")
    log_line(log_path, f"Prepared {len(validation_samples):,} validation and {len(test_samples):,} untouched final-test facility origins.")

    from chronos import Chronos2Pipeline
    from chronos.chronos2 import preprocess
    import torch

    configured_device = str(config.get("device", "auto")).lower()
    device = configured_device if configured_device in {"cpu", "cuda"} else ("cuda" if torch.cuda.is_available() else "cpu")
    source_pipeline = Chronos2Pipeline.from_pretrained(str(source_model), device_map=device)
    log_line(log_path, f"Loaded {source_label} Chronos-2 on {device}.")

    baseline_validation = seasonal_predictions(validation_samples)
    baseline_test = seasonal_predictions(test_samples)
    source_validation = chronos_predictions(
        source_pipeline,
        validation_samples,
        batch_origins=4,
        inference_batch_size=int(config.get("inference_batch_size", 96)),
        cross_learning=bool(config.get("cross_learning", True)),
    )
    source_test = chronos_predictions(
        source_pipeline,
        test_samples,
        batch_origins=4,
        inference_batch_size=int(config.get("inference_batch_size", 96)),
        cross_learning=bool(config.get("cross_learning", True)),
    )
    log_line(log_path, "Completed seasonal-persistence and pre-adaptation Chronos benchmarks.")

    base_validation: list[dict[str, object]] | None = None
    base_test: list[dict[str, object]] | None = None
    base_path = project_root / "models" / "chronos-2-base"
    if _complete_model(base_path) and base_path.resolve() != source_model.resolve():
        base_pipeline = Chronos2Pipeline.from_pretrained(str(base_path), device_map=device)
        base_validation = chronos_predictions(
            base_pipeline,
            validation_samples,
            batch_origins=4,
            inference_batch_size=int(config.get("inference_batch_size", 96)),
            cross_learning=bool(config.get("cross_learning", True)),
        )
        base_test = chronos_predictions(
            base_pipeline,
            test_samples,
            batch_origins=4,
            inference_batch_size=int(config.get("inference_batch_size", 96)),
            cross_learning=bool(config.get("cross_learning", True)),
        )
        del base_pipeline
        log_line(log_path, "Completed official-base zero-shot comparison for the new targets.")

    lora_config = dict(config.get("lora", {}))
    finetuned_dir = project_root / "models" / "chronos-2-power-quality-finetuned"
    lora_validation: list[dict[str, object]] | None = None
    lora_test: list[dict[str, object]] | None = None
    training_status: dict[str, object] = {"attempted": bool(lora_config.get("enabled", True)), "succeeded": False}
    if lora_config.get("enabled", True):
        training_output = runtime / "power_quality_training_output"
        shutil.rmtree(training_output, ignore_errors=True)
        try:
            train_frame = frame[frame["timestamp"] < splits["validation_start"]].copy()
            validation_frame = frame[(frame["timestamp"] >= splits["validation_start"]) & (frame["timestamp"] < splits["test_start"])].copy()
            training_columns = ["item_id", "timestamp", *TARGETS, *PAST_COVARIATES, *KNOWN_COVARIATES]
            train_inputs = preprocess.from_data_frame(
                train_frame[training_columns],
                target_columns=TARGETS,
                prediction_length=48,
                id_column="item_id",
                timestamp_column="timestamp",
                known_covariates_names=KNOWN_COVARIATES,
                use_target_encoding=False,
            )
            validation_inputs = preprocess.from_data_frame(
                validation_frame[training_columns],
                target_columns=TARGETS,
                prediction_length=48,
                id_column="item_id",
                timestamp_column="timestamp",
                known_covariates_names=KNOWN_COVARIATES,
                use_target_encoding=False,
            )
            steps = int(lora_config.get("gpu_num_steps", 600) if device == "cuda" else lora_config.get("cpu_num_steps", 200))
            batch_size = int(lora_config.get("gpu_batch_size", 32) if device == "cuda" else lora_config.get("cpu_batch_size", 4))
            log_line(log_path, f"Starting multi-target LoRA fine-tuning: {steps} steps, batch size {batch_size}.")
            started = time.perf_counter()
            finetuned = source_pipeline.fit(
                inputs=train_inputs,
                validation_inputs=validation_inputs,
                prediction_length=48,
                context_length=context_length,
                min_past=int(config.get("minimum_past_intervals", 96)),
                num_steps=steps,
                learning_rate=float(lora_config.get("learning_rate", 1e-5)),
                batch_size=batch_size,
                logging_steps=int(lora_config.get("logging_steps", 25)),
                finetune_mode="lora",
                output_dir=training_output,
                report_to="none",
            )
            duration = time.perf_counter() - started
            lora_validation = chronos_predictions(
                finetuned,
                validation_samples,
                batch_origins=4,
                inference_batch_size=int(config.get("inference_batch_size", 96)),
                cross_learning=bool(config.get("cross_learning", True)),
            )
            lora_test = chronos_predictions(
                finetuned,
                test_samples,
                batch_origins=4,
                inference_batch_size=int(config.get("inference_batch_size", 96)),
                cross_learning=bool(config.get("cross_learning", True)),
            )
            checkpoint_format = save_pipeline(finetuned, finetuned_dir)
            training_status = {
                "attempted": True,
                "succeeded": True,
                "device": device,
                "steps": steps,
                "batch_size": batch_size,
                "duration_seconds": round(duration, 2),
                "checkpoint_format": checkpoint_format,
                "checkpoint_path": finetuned_dir.relative_to(project_root).as_posix(),
                "targets": TARGETS,
            }
            log_line(log_path, f"Multi-target LoRA fine-tuning completed in {duration / 60:.1f} minutes.")
        except Exception as exc:
            training_status = {
                "attempted": True,
                "succeeded": False,
                "device": device,
                "error": str(exc),
                "fallback": source_model.relative_to(project_root).as_posix(),
            }
            log_line(log_path, f"Power-quality LoRA failed safely; the existing demand model and prior power-quality outputs remain unchanged: {exc}")
            if require_lora:
                raise RuntimeError(f"Required power-quality LoRA fine-tuning failed: {exc}") from exc
        finally:
            shutil.rmtree(training_output, ignore_errors=True)

    validation_variants: dict[str, list[dict[str, object]]] = {
        "seasonal_persistence": baseline_validation,
        "chronos_source": source_validation,
    }
    test_variants: dict[str, list[dict[str, object]]] = {
        "seasonal_persistence": baseline_test,
        "chronos_source": source_test,
    }
    if base_validation is not None and base_test is not None:
        validation_variants["chronos_official_base_zero_shot"] = base_validation
        test_variants["chronos_official_base_zero_shot"] = base_test
    if lora_validation is not None and lora_test is not None:
        validation_variants["chronos_power_quality_lora"] = lora_validation
        test_variants["chronos_power_quality_lora"] = lora_test

    route_config = dict(config.get("routing", {}))
    low_pf_threshold = float(config.get("low_power_factor_threshold", 0.95))
    deployment_variant, variant_selection = choose_variant(
        validation_variants,
        minimum_improvement=float(route_config.get("minimum_power_quality_lora_improvement_percent", 0.0)),
        maximum_recall_drop=float(route_config.get("maximum_low_pf_recall_drop", 0.02)),
        low_pf_threshold=low_pf_threshold,
    )
    deployed_key = "chronos_power_quality_lora" if deployment_variant == "power_quality_finetuned" else "chronos_source"
    deployed_model_path = finetuned_dir if deployment_variant == "power_quality_finetuned" else source_model
    routing, selected_validation, selected_test = select_routes(
        baseline_validation,
        validation_variants[deployed_key],
        baseline_test,
        test_variants[deployed_key],
        weights=[float(value) for value in route_config.get("hybrid_weight_grid", [0.25, 0.5, 0.75])],
        minimum_improvement=float(route_config.get("minimum_validation_mae_improvement_percent", 0.5)),
    )

    model_metrics = {
        name: _model_metrics(rows, test_variants[name], low_pf_threshold)
        for name, rows in validation_variants.items()
        if name in test_variants
    }
    selected_metrics = {
        "validation": {
            "targets": metrics_by_target_horizon(selected_validation),
            "derived": derived_metrics(selected_validation, low_pf_threshold=low_pf_threshold),
        },
        "test": {
            "targets": metrics_by_target_horizon(selected_test),
            "derived": derived_metrics(selected_test, low_pf_threshold=low_pf_threshold),
        },
    }

    facility_profiles = {
        facility: {
            **dict(values),
            "reactive_sign_preference": int(dict(values).get("reactive_sign_preference", 1)),
        }
        for facility, values in facility_quality.items()
        if dict(values).get("status") == "included"
    }
    model_dir = project_root / "models" / "power_quality"
    routing_payload = {
        "version": 1,
        "generated_utc": utc_now(),
        "eligible": True,
        "deployment_variant": deployment_variant,
        "model_path": deployed_model_path.relative_to(project_root).as_posix(),
        "source_model_path": source_model.relative_to(project_root).as_posix(),
        "available_variants": [name for name in ("official_base_zero_shot", "source", "power_quality_finetuned") if name != "power_quality_finetuned" or finetuned_dir.exists()],
        "variant_selection": variant_selection,
        "selected_by_target_horizon": routing,
        "targets": TARGETS,
        "derived_outputs": [
            "interval_energy_kwh",
            "interval_reactive_energy_kvarh_estimated",
            "power_factor",
            "low_power_factor_risk",
            "tariff_period_energy_cost_proxy",
        ],
        "thresholds": {
            "low_power_factor": low_pf_threshold,
            "critical_power_factor": float(config.get("critical_power_factor_threshold", 0.85)),
        },
        "policy": route_config,
        "claim_boundary": config.get("claim_boundary"),
    }
    atomic_json(model_dir / "routing.json", routing_payload)
    atomic_json(model_dir / "facility_profiles.json", facility_profiles)

    metrics_payload = {
        "status": "pass",
        "generated_utc": utc_now(),
        "dataset": {
            "cleaned_source_rows": int(len(data)),
            "regularised_rows": int(len(frame)),
            "facilities": int(frame["facility_id"].nunique()),
            "archive_files": int(len(aliases)),
            "targets": TARGETS,
            "raw_columns_used": {
                "Power (kW)": "active_power_kw target",
                "Reactive energy (kVAR)": "signed reactive_power_kvar target; label/unit interpreted as reactive power",
                "Demand (kVA)": "past covariate and physical consistency check",
                "Power factor": "quality comparison; operational PF is derived from kW and kVAR",
                "Consumption (kWh)": "validated as approximately 0.5 × kW and derived from forecast kW",
                "Temperature/Humidity": "reported as partial-coverage context only; not required as future covariates",
            },
            "training_period": {"start": str(frame["timestamp"].min()), "end": str(splits["validation_start"] - pd.Timedelta(minutes=30))},
            "validation_period": {"start": str(splits["validation_start"]), "end": str(splits["test_start"] - pd.Timedelta(minutes=30))},
            "test_period": {"start": str(splits["test_start"]), "end": str(splits["test_end"] - pd.Timedelta(minutes=30))},
            "validation_origins": len(validation_origins),
            "test_origins": len(test_origins),
            "facility_quality": facility_quality,
        },
        "training": training_status,
        "deployment_variant": deployment_variant,
        "variant_selection": variant_selection,
        "models": model_metrics,
        "selected_by_target_horizon": routing,
        "selected_route_metrics": selected_metrics,
        "summary": {
            "default": "SIMBA-EMS forecasts active power and signed reactive power, then derives interval energy, reactive-energy burden and power-factor risk from physically consistent relationships.",
            "why_two_targets": "kWh is almost exactly active kW multiplied by the half-hour interval, while power factor is physically derived from active and reactive power. Separate redundant models would add overfitting risk without adding independent information.",
            "deployment_reason": variant_selection["reason"],
            "temperature_humidity_boundary": "Temperature and humidity are present for only part of the archive and are not used as mandatory future inputs until a reliable live forecast source exists.",
        },
        "claim_boundary": config.get("claim_boundary"),
    }
    metrics_path = project_root / "evidence" / "model_validation" / "power_quality_model_comparison.json"
    atomic_json(metrics_path, metrics_payload)

    prediction_rows: list[dict[str, object]] = []
    for split_name, variants in (("validation", validation_variants), ("test", test_variants)):
        for model_name, rows in variants.items():
            for item in rows:
                prediction_rows.append({"split": split_name, "model": model_name, **item})
    for split_name, rows in (("validation", selected_validation), ("test", selected_test)):
        for item in rows:
            prediction_rows.append({"split": split_name, "model": "deployed_route", **item})
    prediction_path = project_root / "evidence" / "model_validation" / "power_quality_predictions.csv"
    pd.DataFrame(prediction_rows).to_csv(prediction_path, index=False)

    manifest = {
        **transaction,
        "status": "success",
        "completed_utc": utc_now(),
        "device": device,
        "source_label": source_label,
        "source_model_extracted_from_zip": extracted_model_zip,
        "training": training_status,
        "deployment_variant": deployment_variant,
        "variant_selection": variant_selection,
        "outputs": {
            "power_quality_model": finetuned_dir.relative_to(project_root).as_posix() if finetuned_dir.exists() else None,
            "deployed_model": deployed_model_path.relative_to(project_root).as_posix(),
            "routing": "models/power_quality/routing.json",
            "metrics": metrics_path.relative_to(project_root).as_posix(),
            "predictions": prediction_path.relative_to(project_root).as_posix(),
        },
        "cleanup_pending": True,
    }
    atomic_json(state_path, manifest)
    atomic_json(model_dir / "setup_manifest.json", manifest)
    shutil.rmtree(processed_dir, ignore_errors=True)
    log_line(log_path, f"Power-quality setup completed. Deployment variant: {deployment_variant}. Input ZIPs remain until verification and tests pass.")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--require-lora", action="store_true")
    args = parser.parse_args()
    state_path = args.project_root.resolve() / "runtime" / "power_quality_setup_state.json"
    log_path = args.project_root.resolve() / "runtime" / "power_quality_setup.log"
    try:
        result = run(args.project_root, force=args.force, require_lora=args.require_lora)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        failure = {
            "status": "failed",
            "failed_utc": utc_now(),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        atomic_json(state_path, failure)
        log_line(log_path, f"FAILED: {exc}")
        print(traceback.format_exc(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
