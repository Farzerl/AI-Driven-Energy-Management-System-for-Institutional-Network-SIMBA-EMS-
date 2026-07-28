from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import time
import traceback
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.live.dataset_loader import load_dataset_archive
from src.live.model_manager import LiveModelManager

HORIZONS = {"30_minutes": 1, "2_hours": 4, "6_hours": 12, "24_hours": 48}
KNOWN_COVARIATES = ["half_hour_slot", "day_of_week", "is_weekend", "month", "tariff_period"]
OFFICIAL_MODEL_SHA256 = "ddcda3c7508bf2528087723e98a20707cc04b7f370ae275a9fd88078ddba4f42"
SETUP_TRANSACTION_VERSION = 7


@dataclass(frozen=True)
class Sample:
    facility: str
    origin: pd.Timestamp
    context: pd.DataFrame
    actual: dict[str, float]
    limit_kva: float


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def log_line(log_path: Path, message: str) -> None:
    line = f"[{utc_now()}] {message}"
    print(line, flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def one_zip(folder: Path, label: str) -> Path:
    items = sorted(folder.glob("*.zip"))
    if len(items) != 1:
        raise ValueError(f"{label} requires exactly one ZIP in {folder}; found {len(items)}.")
    return items[0]


def safe_extract_model(archive: Path, destination: Path) -> None:
    temporary = destination.with_name(destination.name + ".extracting")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(archive) as handle:
        members = [item for item in handle.infolist() if not item.is_dir()]
        if not members:
            raise ValueError("The Chronos-2 ZIP is empty.")
        for item in members:
            posix = PurePosixPath(item.filename)
            if posix.is_absolute() or ".." in posix.parts:
                raise ValueError(f"Unsafe model ZIP path: {item.filename}")
            total += max(int(item.file_size), 0)
            if total > 4 * 1024 * 1024 * 1024:
                raise ValueError("The Chronos-2 ZIP expands beyond the 4 GiB safety limit.")
            target = temporary / posix
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(item) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    candidates = [path.parent for path in temporary.rglob("config.json") if any(path.parent.rglob("*.safetensors"))]
    if len(candidates) != 1:
        raise ValueError("The model ZIP must contain one Chronos-2 folder with config.json and .safetensors weights.")
    source = candidates[0]
    verified = destination.with_name(destination.name + ".verified")
    shutil.rmtree(verified, ignore_errors=True)
    shutil.copytree(source, verified)
    weights = sorted(verified.rglob("*.safetensors"))
    if not (verified / "config.json").exists() or not weights:
        raise ValueError("Extracted Chronos-2 files failed verification.")
    if len(weights) != 1 or weights[0].name != "model.safetensors":
        raise ValueError("The official Chronos-2 package must contain one model.safetensors file.")
    actual_hash = sha256(weights[0])
    if actual_hash != OFFICIAL_MODEL_SHA256:
        raise ValueError(
            "Chronos-2 model checksum mismatch. Expected the official amazon/chronos-2 weights; "
            f"received {actual_hash}."
        )
    shutil.rmtree(destination, ignore_errors=True)
    os.replace(verified, destination)
    shutil.rmtree(temporary, ignore_errors=True)


def calendar_features(timestamp: pd.Timestamp) -> dict[str, object]:
    local = pd.Timestamp(timestamp)
    if local.tzinfo is not None:
        local = local.tz_convert("Africa/Harare").tz_localize(None)
    if local.hour in {7, 8, 17, 18}:
        tariff = "peak"
    elif local.hour >= 22 or local.hour < 5:
        tariff = "offpeak"
    else:
        tariff = "standard"
    return {
        "half_hour_slot": int(local.hour * 2 + local.minute // 30),
        "day_of_week": int(local.dayofweek),
        "is_weekend": int(local.dayofweek >= 5),
        "month": int(local.month),
        "tariff_period": tariff,
    }


def prepare_frame(data: pd.DataFrame) -> pd.DataFrame:
    frame = data.rename(columns={"facility_id": "item_id", "kva": "target"}).copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    if frame["timestamp"].dt.tz is not None:
        frame["timestamp"] = frame["timestamp"].dt.tz_convert("Africa/Harare").dt.tz_localize(None)
    features = frame["timestamp"].map(calendar_features).apply(pd.Series)
    for column in features.columns:
        frame[column] = features[column]
    frame = frame.sort_values(["item_id", "timestamp"]).drop_duplicates(["item_id", "timestamp"], keep="last")
    return frame.reset_index(drop=True)


def split_months(frame: pd.DataFrame) -> dict[str, pd.Timestamp]:
    months = sorted(frame["timestamp"].dt.to_period("M").unique())
    if len(months) < 3:
        raise ValueError("Chronos-2 setup requires at least three chronological months.")
    test_month = months[-1]
    validation_month = months[-2]
    return {
        "training_start": frame["timestamp"].min(),
        "validation_start": validation_month.start_time,
        "test_start": test_month.start_time,
        "test_end": (test_month + 1).start_time,
    }


def complete_context(group: pd.DataFrame, origin: pd.Timestamp, context_length: int) -> pd.DataFrame | None:
    context = group[group["timestamp"] <= origin].tail(context_length)
    if len(context) < 49:
        return None
    gaps = context["timestamp"].diff().dropna().dt.total_seconds().div(60)
    if not gaps.empty and bool((gaps.sub(30).abs() > 7.5).any()):
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
    limits: Mapping[str, float],
) -> tuple[list[pd.Timestamp], list[Sample]]:
    timestamps = sorted(frame.loc[(frame["timestamp"] >= start) & (frame["timestamp"] < end), "timestamp"].unique())
    candidates = [pd.Timestamp(value) for value in timestamps[:: max(stride, 1)]]
    if len(candidates) > maximum_origins:
        indices = np.linspace(0, len(candidates) - 1, maximum_origins, dtype=int)
        candidates = [candidates[int(index)] for index in sorted(set(indices.tolist()))]
    grouped = {str(name): group.reset_index(drop=True) for name, group in frame.groupby("item_id", sort=True)}
    samples: list[Sample] = []
    accepted_origins: list[pd.Timestamp] = []
    for origin in candidates:
        origin_samples = 0
        for facility, group in grouped.items():
            context = complete_context(group, origin, context_length)
            if context is None:
                continue
            future = group[group["timestamp"] > origin].head(48)
            if len(future) < 48:
                continue
            expected = [origin + pd.Timedelta(minutes=30 * step) for step in range(1, 49)]
            if any(abs((pd.Timestamp(actual) - expected_value).total_seconds()) > 450 for actual, expected_value in zip(future["timestamp"], expected)):
                continue
            samples.append(
                Sample(
                    facility=facility,
                    origin=origin,
                    context=context,
                    actual={name: float(future.iloc[step - 1]["target"]) for name, step in HORIZONS.items()},
                    limit_kva=float(limits.get(facility, max(float(context.iloc[-1]["target"]) * 1.1, 1.0))),
                )
            )
            origin_samples += 1
        if origin_samples:
            accepted_origins.append(origin)
    if not samples:
        raise ValueError(f"No valid evaluation samples were found between {start} and {end}.")
    return accepted_origins, samples


def future_frame(samples: list[Sample]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Sample]]:
    contexts: list[pd.DataFrame] = []
    futures: list[dict[str, object]] = []
    mapping: dict[str, Sample] = {}
    for sample in samples:
        sample_id = f"{sample.facility}::{sample.origin.isoformat()}"
        mapping[sample_id] = sample
        context = sample.context.copy()
        context["item_id"] = sample_id
        contexts.append(context[["item_id", "timestamp", "target", "power_factor", "kwh_is_measured", *KNOWN_COVARIATES]])
        for step in range(1, 49):
            timestamp = sample.origin + pd.Timedelta(minutes=30 * step)
            futures.append({"item_id": sample_id, "timestamp": timestamp, **calendar_features(timestamp)})
    return pd.concat(contexts, ignore_index=True), pd.DataFrame(futures), mapping


def chronos_predictions(pipeline: Any, samples: list[Sample], batch_origins: int = 4) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    origins = sorted({sample.origin for sample in samples})
    for index in range(0, len(origins), max(batch_origins, 1)):
        selected_origins = set(origins[index : index + max(batch_origins, 1)])
        batch = [sample for sample in samples if sample.origin in selected_origins]
        context, future, mapping = future_frame(batch)
        started = time.perf_counter()
        predicted = pipeline.predict_df(
            context,
            future_df=future,
            prediction_length=48,
            quantile_levels=[0.1, 0.5, 0.9],
            id_column="item_id",
            timestamp_column="timestamp",
            target="target",
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        for sample_id, group in predicted.groupby("item_id", sort=False):
            sample = mapping[str(sample_id)]
            ordered = group.sort_values("timestamp").reset_index(drop=True)
            for horizon, step in HORIZONS.items():
                row = ordered.iloc[step - 1]
                p50 = max(float(row.get("0.5", row.get("predictions"))), 0.0)
                values = sorted([max(float(row.get("0.1", p50)), 0.0), p50, max(float(row.get("0.9", p50)), 0.0)])
                rows.append(
                    {
                        "facility": sample.facility,
                        "origin": sample.origin.isoformat(),
                        "horizon": horizon,
                        "actual": sample.actual[horizon],
                        "forecast": values[1],
                        "upper": values[2],
                        "limit": sample.limit_kva,
                        "latency_ms_per_series": elapsed_ms / max(len(batch), 1),
                    }
                )
    return rows


def existing_predictions(manager: LiveModelManager, samples: list[Sample]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sample in samples:
        records = sample.context.rename(columns={"target": "kva"}).to_dict("records")
        predicted = manager.predict_horizons(
            records,
            sample.facility,
            mode_override="automatic",
            include_optional_models=False,
        )
        for horizon in HORIZONS:
            item = predicted[horizon]
            rows.append(
                {
                    "facility": sample.facility,
                    "origin": sample.origin.isoformat(),
                    "horizon": horizon,
                    "actual": sample.actual[horizon],
                    "forecast": float(item["forecast_kva"]),
                    "upper": float(item.get("forecast_upper_kva", item["forecast_kva"])),
                    "limit": sample.limit_kva,
                    "latency_ms_per_series": float(item.get("inference_latency_ms", 0.0)),
                }
            )
    return rows


def metric_rows(rows: Iterable[Mapping[str, object]]) -> dict[str, object]:
    items = list(rows)
    actual = np.asarray([float(item["actual"]) for item in items], dtype=float)
    forecast = np.asarray([float(item["forecast"]) for item in items], dtype=float)
    upper = np.asarray([float(item["upper"]) for item in items], dtype=float)
    limits = np.asarray([max(float(item["limit"]), 1e-9) for item in items], dtype=float)
    errors = actual - forecast
    abs_errors = np.abs(errors)
    actual_high = actual / limits >= 0.95
    predicted_high = upper / limits >= 0.95
    tp = int(np.sum(actual_high & predicted_high))
    fp = int(np.sum(~actual_high & predicted_high))
    fn = int(np.sum(actual_high & ~predicted_high))
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-12)
    latency = np.asarray([float(item.get("latency_ms_per_series", 0.0)) for item in items], dtype=float)
    return {
        "samples": len(items),
        "mae_kva": round(float(abs_errors.mean()), 4),
        "rmse_kva": round(float(np.sqrt(np.mean(errors**2))), 4),
        "wape_percent": round(float(abs_errors.sum() / max(np.abs(actual).sum(), 1e-9) * 100), 4),
        "mean_bias_kva": round(float(errors.mean()), 4),
        "p90_abs_error_kva": round(float(np.percentile(abs_errors, 90)), 4),
        "p99_abs_error_kva": round(float(np.percentile(abs_errors, 99)), 4),
        "under_forecast_fraction": round(float(np.mean(errors > 0)), 4),
        "high_risk_precision": round(float(precision), 4),
        "high_risk_recall": round(float(recall), 4),
        "high_risk_f1": round(float(f1), 4),
        "high_risk_events": int(np.sum(actual_high)),
        "median_latency_ms_per_series": round(float(np.median(latency)), 4),
        "p95_latency_ms_per_series": round(float(np.percentile(latency, 95)), 4),
    }


def metrics_by_horizon(rows: list[dict[str, object]]) -> dict[str, object]:
    return {horizon: metric_rows([item for item in rows if item["horizon"] == horizon]) for horizon in HORIZONS}


def choose_deployment_variant(
    validation_results: Mapping[str, list[dict[str, object]]],
    route_config: Mapping[str, object],
) -> tuple[str, dict[str, object]]:
    """Choose base or LoRA from validation evidence only.

    Both variants remain installed and reported. The chosen variant is the one used by
    the production Chronos adapter; final-test data is never used for this choice.
    """
    zero_rows = list(validation_results["chronos_zero_shot"])
    zero_by_horizon = metrics_by_horizon(zero_rows)
    zero_mean = float(np.mean([float(zero_by_horizon[name]["mae_kva"]) for name in HORIZONS]))
    zero_risk = metric_rows(zero_rows)
    if "chronos_lora" not in validation_results:
        return "base", {
            "selected": "base",
            "reason": "LoRA metrics were unavailable, so the validated zero-shot model was retained.",
            "zero_shot_mean_validation_mae_kva": round(zero_mean, 4),
            "finetuned_mean_validation_mae_kva": None,
            "validation_mae_improvement_percent": None,
            "validation_recall_drop": None,
            "horizon_wins": 0,
        }

    lora_rows = list(validation_results["chronos_lora"])
    lora_by_horizon = metrics_by_horizon(lora_rows)
    lora_mean = float(np.mean([float(lora_by_horizon[name]["mae_kva"]) for name in HORIZONS]))
    lora_risk = metric_rows(lora_rows)
    improvement = (zero_mean - lora_mean) / max(zero_mean, 1e-9) * 100.0
    recall_drop = float(zero_risk["high_risk_recall"]) - float(lora_risk["high_risk_recall"])
    minimum_improvement = float(route_config.get("minimum_finetuned_validation_mae_improvement_percent", 0.0))
    maximum_recall_drop = float(route_config.get("maximum_recall_drop", 0.02))
    horizon_wins = sum(
        float(lora_by_horizon[name]["mae_kva"]) < float(zero_by_horizon[name]["mae_kva"])
        for name in HORIZONS
    )
    eligible = improvement >= minimum_improvement and recall_drop <= maximum_recall_drop
    selected = "finetuned" if eligible else "base"
    if selected == "finetuned":
        reason = (
            "LoRA was selected because it improved mean chronological validation MAE "
            "without exceeding the peak-risk recall guardrail."
        )
    else:
        reason = (
            "Zero-shot was retained because LoRA did not clear both the validation-MAE "
            "and peak-risk recall guardrails. The fine-tuned checkpoint and its metrics remain available."
        )
    return selected, {
        "selected": selected,
        "reason": reason,
        "zero_shot_mean_validation_mae_kva": round(zero_mean, 4),
        "finetuned_mean_validation_mae_kva": round(lora_mean, 4),
        "validation_mae_improvement_percent": round(improvement, 4),
        "validation_recall_drop": round(recall_drop, 4),
        "zero_shot_validation_high_risk_recall": zero_risk["high_risk_recall"],
        "finetuned_validation_high_risk_recall": lora_risk["high_risk_recall"],
        "horizon_wins": int(horizon_wins),
        "minimum_improvement_percent": minimum_improvement,
        "maximum_recall_drop": maximum_recall_drop,
    }


def hybrid_rows(existing: list[dict[str, object]], chronos: list[dict[str, object]], weight: float) -> list[dict[str, object]]:
    keys = lambda item: (item["facility"], item["origin"], item["horizon"])
    current = {keys(item): item for item in existing}
    output: list[dict[str, object]] = []
    for item in chronos:
        base = current.get(keys(item))
        if base is None:
            continue
        output.append(
            {
                **item,
                "forecast": (1.0 - weight) * float(base["forecast"]) + weight * float(item["forecast"]),
                "upper": (1.0 - weight) * float(base["upper"]) + weight * float(item["upper"]),
                "latency_ms_per_series": float(base.get("latency_ms_per_series", 0.0)) + float(item.get("latency_ms_per_series", 0.0)),
            }
        )
    return output


def select_routes(
    validation: dict[str, list[dict[str, object]]],
    test: dict[str, list[dict[str, object]]],
    weights: list[float],
    min_improvement: float,
    max_recall_drop: float,
) -> tuple[dict[str, object], dict[str, object]]:
    routing: dict[str, object] = {}
    selection_evidence: dict[str, object] = {}
    for horizon in HORIZONS:
        existing_validation = metric_rows([item for item in validation["existing"] if item["horizon"] == horizon])
        candidates: list[tuple[str, float | None, dict[str, object], list[dict[str, object]]]] = []
        for name in ("chronos_zero_shot", "chronos_lora"):
            if name not in validation:
                continue
            rows = [item for item in validation[name] if item["horizon"] == horizon]
            candidates.append((name, None, metric_rows(rows), rows))
            for weight in weights:
                combined = hybrid_rows(
                    [item for item in validation["existing"] if item["horizon"] == horizon],
                    rows,
                    float(weight),
                )
                candidates.append((f"hybrid_{name}", float(weight), metric_rows(combined), combined))
        eligible: list[tuple[str, float | None, dict[str, object], list[dict[str, object]]]] = []
        baseline_mae = float(existing_validation["mae_kva"])
        baseline_recall = float(existing_validation["high_risk_recall"])
        for candidate in candidates:
            metrics = candidate[2]
            improvement = (baseline_mae - float(metrics["mae_kva"])) / max(baseline_mae, 1e-9) * 100
            recall_drop = baseline_recall - float(metrics["high_risk_recall"])
            if improvement >= min_improvement and recall_drop <= max_recall_drop:
                eligible.append(candidate)
        if eligible:
            winner = min(eligible, key=lambda row: (float(row[2]["mae_kva"]), -float(row[2]["high_risk_recall"]), -float(row[2]["high_risk_f1"])))
            model, weight, winner_metrics, _ = winner
        else:
            model, weight, winner_metrics = "existing", None, existing_validation
        if model == "existing":
            deployed_model = "existing"
            test_rows = [item for item in test["existing"] if item["horizon"] == horizon]
        elif model.startswith("hybrid_"):
            source = model.removeprefix("hybrid_")
            deployed_model = "hybrid_chronos_existing"
            test_rows = hybrid_rows(
                [item for item in test["existing"] if item["horizon"] == horizon],
                [item for item in test[source] if item["horizon"] == horizon],
                float(weight),
            )
        else:
            deployed_model = "chronos2"
            test_rows = [item for item in test[model] if item["horizon"] == horizon]
        routing[horizon] = {
            "model": deployed_model,
            "source_candidate": model,
            "chronos_weight": weight,
            "validation_metrics": winner_metrics,
            "test_metrics": metric_rows(test_rows),
            "reason": (
                "Chronos route met the validation improvement and recall guardrails."
                if model != "existing"
                else "The existing validated route remained default because no Chronos candidate cleared both validation improvement and recall guardrails."
            ),
        }
        selection_evidence[horizon] = {
            "baseline_validation": existing_validation,
            "selected_candidate": model,
            "selected_weight": weight,
            "selected_test": metric_rows(test_rows),
        }
    return routing, selection_evidence


def _is_complete_model_checkpoint(path: Path) -> bool:
    """Return True for a self-contained Hugging Face model checkpoint."""
    if not (path / "config.json").is_file():
        return False
    weight_patterns = ("*.safetensors", "pytorch_model*.bin")
    return any(any(path.rglob(pattern)) for pattern in weight_patterns)


def _is_lora_adapter_checkpoint(path: Path) -> bool:
    """Return True for a PEFT LoRA adapter checkpoint."""
    if not (path / "adapter_config.json").is_file():
        return False
    return any(
        (path / filename).is_file()
        for filename in ("adapter_model.safetensors", "adapter_model.bin")
    )


def save_pipeline(pipeline: Any, destination: Path) -> str:
    """Persist the fine-tuned pipeline as a portable, self-contained model.

    Chronos-2 LoRA training returns a PEFT model. Calling ``save_pretrained``
    directly on that object writes an adapter-only checkpoint, which correctly
    contains ``adapter_config.json`` rather than ``config.json``. SIMBA-EMS
    packages a complete offline model, so the LoRA weights are merged into the
    base Chronos-2 model before saving.
    """
    temporary = destination.with_name(destination.name + ".saving")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True, exist_ok=True)

    model = getattr(pipeline, "model", None)
    checkpoint_format = "full_model"
    if model is not None and hasattr(model, "merge_and_unload"):
        # PEFT's merge produces a normal Chronos2Model containing the learned
        # LoRA update. The result can be loaded without a separate adapter path.
        merged_model = model.merge_and_unload()
        from chronos import Chronos2Pipeline

        Chronos2Pipeline(model=merged_model).save_pretrained(str(temporary))
        checkpoint_format = "merged_lora_full_model"
    elif hasattr(pipeline, "save_pretrained"):
        pipeline.save_pretrained(str(temporary))
    elif model is not None and hasattr(model, "save_pretrained"):
        model.save_pretrained(str(temporary))
    else:
        raise RuntimeError("The fine-tuned Chronos-2 pipeline does not expose a supported save method.")

    if _is_lora_adapter_checkpoint(temporary) and not _is_complete_model_checkpoint(temporary):
        raise RuntimeError(
            "Chronos-2 produced an adapter-only checkpoint. The LoRA adapter could not be merged into the base model."
        )
    if not _is_complete_model_checkpoint(temporary):
        files = sorted(item.name for item in temporary.iterdir())
        raise RuntimeError(
            "The fine-tuned Chronos-2 checkpoint is incomplete. "
            f"Files written: {files}"
        )

    shutil.rmtree(destination, ignore_errors=True)
    os.replace(temporary, destination)
    return checkpoint_format


def run(project_root: Path, *, force: bool = False, require_lora: bool = False) -> dict[str, object]:
    project_root = Path(project_root).resolve()
    runtime = project_root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    log_path = runtime / "chronos2_setup.log"
    state_path = runtime / "chronos2_setup_state.json"
    config = json.loads((project_root / "config" / "chronos2_training.json").read_text(encoding="utf-8"))
    model_zip = one_zip(project_root / "chronos_input", "Chronos-2 model input")
    dataset_zip = one_zip(project_root / "training_data", "Training dataset input")
    transaction = {
        "status": "running",
        "started_utc": utc_now(),
        "model_zip_sha256": sha256(model_zip),
        "dataset_zip_sha256": sha256(dataset_zip),
        "config_sha256": sha256(project_root / "config" / "chronos2_training.json"),
        "setup_transaction_version": SETUP_TRANSACTION_VERSION,
        "setup_code_sha256": sha256(Path(__file__).resolve()),
        "force_requested": bool(force),
        "require_lora": bool(require_lora),
    }
    previous = {}
    if state_path.exists():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}
    idempotency_keys = (
        "model_zip_sha256",
        "dataset_zip_sha256",
        "config_sha256",
        "setup_transaction_version",
        "setup_code_sha256",
    )
    same_transaction = previous.get("status") == "success" and all(
        previous.get(key) == transaction.get(key) for key in idempotency_keys
    )
    lora_required = bool(dict(config.get("lora", {})).get("enabled", False))
    previous_training_succeeded = bool(dict(previous.get("training", {})).get("succeeded", False))
    if same_transaction and not force and (not lora_required or previous_training_succeeded):
        log_line(log_path, "Idempotent replay: matching completed setup transaction already exists.")
        return previous
    if force:
        log_line(log_path, "Forced local fine-tuning requested; prior setup state will not be reused.")
    elif same_transaction and lora_required and not previous_training_succeeded:
        log_line(log_path, "Retrying setup because required LoRA fine-tuning did not complete previously.")
    atomic_json(state_path, transaction)
    log_line(log_path, "Starting Chronos-2 local setup transaction.")

    base_model = project_root / "models" / "chronos-2-base"
    safe_extract_model(model_zip, base_model)
    base_weights_hash = sha256(base_model / "model.safetensors")
    log_line(log_path, f"Verified official Chronos-2 model archive: {model_zip.name}")

    processed_dir = runtime / "chronos2_processed"
    shutil.rmtree(processed_dir, ignore_errors=True)
    data, aliases = load_dataset_archive(dataset_zip, processed_dir)
    frame = prepare_frame(data)
    splits = split_months(frame)
    log_line(log_path, f"Loaded {len(frame):,} cleaned readings across {frame['item_id'].nunique()} facilities.")

    existing_bundle = json.loads((project_root / "models" / "institutional_multi_horizon_forecaster.json").read_text(encoding="utf-8"))
    limits = {str(key): float(value) for key, value in dict(existing_bundle.get("facility_limits_kva", {})).items()}
    context_length = int(config.get("context_length_intervals", 336))
    validation_origins, validation_samples = build_samples(
        frame,
        start=splits["validation_start"],
        end=splits["test_start"],
        stride=int(config.get("validation_stride_intervals", 48)),
        maximum_origins=int(config.get("maximum_validation_origins", 40)),
        context_length=context_length,
        limits=limits,
    )
    test_origins, test_samples = build_samples(
        frame,
        start=splits["test_start"],
        end=splits["test_end"],
        stride=int(config.get("test_stride_intervals", 48)),
        maximum_origins=int(config.get("maximum_test_origins", 40)),
        context_length=context_length,
        limits=limits,
    )
    log_line(log_path, f"Prepared {len(validation_samples):,} validation and {len(test_samples):,} final-test facility origins.")

    from chronos import Chronos2Pipeline
    from chronos.chronos2 import preprocess
    import torch

    device_config = str(config.get("device", "auto")).lower()
    device = device_config if device_config in {"cpu", "cuda"} else ("cuda" if torch.cuda.is_available() else "cpu")
    log_line(log_path, f"Loading Chronos-2 on {device}.")
    base_pipeline = Chronos2Pipeline.from_pretrained(str(base_model), device_map=device)

    manager = LiveModelManager(project_root / "models" / "institutional_multi_horizon_forecaster.json")
    validation_results: dict[str, list[dict[str, object]]] = {
        "existing": existing_predictions(manager, validation_samples),
        "chronos_zero_shot": chronos_predictions(base_pipeline, validation_samples),
    }
    test_results: dict[str, list[dict[str, object]]] = {
        "existing": existing_predictions(manager, test_samples),
        "chronos_zero_shot": chronos_predictions(base_pipeline, test_samples),
    }
    log_line(log_path, "Completed existing-router and zero-shot Chronos-2 benchmarks.")

    lora_config = dict(config.get("lora", {}))
    finetuned_dir = project_root / "models" / "chronos-2-finetuned"
    training_status: dict[str, object] = {"attempted": bool(lora_config.get("enabled", True)), "succeeded": False}
    if lora_config.get("enabled", True):
        try:
            train_frame = frame[frame["timestamp"] < splits["validation_start"]].copy()
            validation_frame = frame[(frame["timestamp"] >= splits["validation_start"]) & (frame["timestamp"] < splits["test_start"])].copy()
            train_inputs = preprocess.from_data_frame(
                train_frame,
                target_columns=["target"],
                prediction_length=48,
                id_column="item_id",
                timestamp_column="timestamp",
                known_covariates_names=KNOWN_COVARIATES,
            )
            validation_inputs = preprocess.from_data_frame(
                validation_frame,
                target_columns=["target"],
                prediction_length=48,
                id_column="item_id",
                timestamp_column="timestamp",
                known_covariates_names=KNOWN_COVARIATES,
            )
            steps = int(lora_config.get("gpu_num_steps", 600) if device == "cuda" else lora_config.get("cpu_num_steps", 100))
            batch_size = int(lora_config.get("gpu_batch_size", 32) if device == "cuda" else lora_config.get("cpu_batch_size", 4))
            output_dir = runtime / "chronos2_training_output"
            shutil.rmtree(output_dir, ignore_errors=True)
            log_line(log_path, f"Starting LoRA fine-tuning: {steps} steps, batch size {batch_size}.")
            started = time.perf_counter()
            finetuned = base_pipeline.fit(
                inputs=train_inputs,
                validation_inputs=validation_inputs,
                prediction_length=48,
                context_length=int(lora_config.get("context_length_intervals", context_length)),
                min_past=int(lora_config.get("minimum_past_intervals", 96)),
                num_steps=steps,
                learning_rate=float(lora_config.get("learning_rate", 1e-5)),
                batch_size=batch_size,
                logging_steps=int(lora_config.get("logging_steps", 25)),
                finetune_mode="lora",
                output_dir=output_dir,
                # When validation_inputs are supplied, Chronos-2 configures matching
                # step-based evaluation and checkpoint saving internally so that the
                # best validation checkpoint can be restored safely.
                report_to="none",
            )
            training_seconds = time.perf_counter() - started
            # Evaluate the in-memory LoRA model before merging its adapter into
            # the self-contained checkpoint used by the local runtime.
            validation_results["chronos_lora"] = chronos_predictions(finetuned, validation_samples)
            test_results["chronos_lora"] = chronos_predictions(finetuned, test_samples)
            checkpoint_format = save_pipeline(finetuned, finetuned_dir)
            shutil.rmtree(output_dir, ignore_errors=True)
            training_status = {
                "attempted": True,
                "succeeded": True,
                "device": device,
                "steps": steps,
                "batch_size": batch_size,
                "duration_seconds": round(training_seconds, 2),
                "checkpoint_format": checkpoint_format,
                "checkpoint_path": "models/chronos-2-finetuned",
            }
            log_line(log_path, f"LoRA fine-tuning completed in {training_seconds / 60:.1f} minutes.")
        except Exception as exc:
            shutil.rmtree(finetuned_dir, ignore_errors=True)
            training_status = {
                "attempted": True,
                "succeeded": False,
                "device": device,
                "error": str(exc),
                "fallback": "chronos_zero_shot",
            }
            log_line(log_path, f"LoRA fine-tuning failed safely; zero-shot outputs remain untouched: {exc}")
            if require_lora:
                manager.close()
                shutil.rmtree(processed_dir, ignore_errors=True)
                shutil.rmtree(runtime / "chronos2_training_output", ignore_errors=True)
                raise RuntimeError(f"Required LoRA fine-tuning failed: {exc}") from exc

    route_config = dict(config.get("routing", {}))
    deployment_variant, variant_selection = choose_deployment_variant(validation_results, route_config)
    deployed_key = "chronos_lora" if deployment_variant == "finetuned" else "chronos_zero_shot"
    log_line(log_path, f"Deployment variant selected from validation evidence: {deployment_variant}. {variant_selection['reason']}")

    routing, selection = select_routes(
        {"existing": validation_results["existing"], deployed_key: validation_results[deployed_key]},
        {"existing": test_results["existing"], deployed_key: test_results[deployed_key]},
        [float(item) for item in route_config.get("hybrid_weight_grid", [0.25, 0.5, 0.75])],
        float(route_config.get("minimum_validation_mae_improvement_percent", 0.5)),
        float(route_config.get("maximum_recall_drop", 0.02)),
    )
    # Preserve whether the selected route uses zero-shot or locally fine-tuned Chronos.
    route_variant_label = "finetuned" if deployment_variant == "finetuned" else "zero_shot"
    for row in routing.values():
        candidate = str(dict(row).get("source_candidate", "existing"))
        if candidate == deployed_key:
            row["source_candidate"] = f"chronos2_{route_variant_label}"
        elif candidate == f"hybrid_{deployed_key}":
            row["source_candidate"] = f"hybrid_chronos2_{route_variant_label}"

    eligible = any(dict(item).get("model") != "existing" for item in routing.values())
    routing_payload = {
        "version": 1,
        "generated_utc": utc_now(),
        "eligible": eligible,
        "deployment_variant": deployment_variant,
        "available_variants": ["base", "finetuned"] if finetuned_dir.exists() else ["base"],
        "variant_selection": variant_selection,
        "selected_by_horizon": routing,
        "policy": route_config,
    }
    atomic_json(project_root / "models" / "chronos2" / "routing.json", routing_payload)

    metrics_models: dict[str, object] = {}
    for name, rows in {**validation_results, **{}}.items():
        metrics_models.setdefault(name, {})
        metrics_models[name] = {
            "validation": metrics_by_horizon(rows),
            "test": metrics_by_horizon(test_results[name]) if name in test_results else {},
        }
    metrics_payload = {
        "status": "pass",
        "generated_utc": utc_now(),
        "dataset": {
            "cleaned_rows": len(frame),
            "facilities": int(frame["item_id"].nunique()),
            "aliases": len(aliases),
            "training_period": {"start": str(frame["timestamp"].min()), "end": str(splits["validation_start"] - pd.Timedelta(minutes=30))},
            "validation_period": {"start": str(splits["validation_start"]), "end": str(splits["test_start"] - pd.Timedelta(minutes=30))},
            "test_period": {"start": str(splits["test_start"]), "end": str(splits["test_end"] - pd.Timedelta(minutes=30))},
            "validation_origins": len(validation_origins),
            "test_origins": len(test_origins),
        },
        "training": training_status,
        "deployment_variant": deployment_variant,
        "available_variants": ["base", "finetuned"] if finetuned_dir.exists() else ["base"],
        "variant_selection": variant_selection,
        "models": metrics_models,
        "selected_by_horizon": routing,
        "selection_evidence": selection,
        "summary": {
            "default": f"Validated {deployment_variant} Chronos-2 is combined with the existing router only where accuracy and recall guardrails are cleared.",
            "variant_reason": variant_selection["reason"],
            "eligible_horizons": [name for name, item in routing.items() if dict(item).get("model") != "existing"],
            "retained_existing_horizons": [name for name, item in routing.items() if dict(item).get("model") == "existing"],
            "why": {name: dict(item).get("reason") for name, item in routing.items()},
        },
        "claim_boundary": "Zero-shot and LoRA metrics use the same sampled chronological origins. Variant and production-route selection use validation results only; the latest month remains an untouched final test.",
    }
    metrics_path = project_root / "evidence" / "model_validation" / "chronos2_model_comparison.json"
    atomic_json(metrics_path, metrics_payload)

    prediction_rows: list[dict[str, object]] = []
    for split_name, models in (("validation", validation_results), ("test", test_results)):
        for model_name, rows in models.items():
            for item in rows:
                prediction_rows.append({"split": split_name, "model": model_name, **item})
    pd.DataFrame(prediction_rows).to_csv(project_root / "evidence" / "model_validation" / "chronos2_predictions.csv", index=False)

    setup_manifest = {
        **transaction,
        "status": "success",
        "completed_utc": utc_now(),
        "device": device,
        "official_base_model_sha256": base_weights_hash,
        "training": training_status,
        "deployment_variant": deployment_variant,
        "available_variants": metrics_payload["available_variants"],
        "variant_selection": variant_selection,
        "eligible_horizons": metrics_payload["summary"]["eligible_horizons"],
        "outputs": {
            "base_model": "models/chronos-2-base",
            "finetuned_model": "models/chronos-2-finetuned" if finetuned_dir.exists() else None,
            "routing": "models/chronos2/routing.json",
            "metrics": "evidence/model_validation/chronos2_model_comparison.json",
        },
    }
    atomic_json(state_path, setup_manifest)
    atomic_json(project_root / "models" / "chronos2" / "setup_manifest.json", setup_manifest)

    # Processed extraction and trainer scratch files are not needed after evidence is committed.
    manager.close()
    shutil.rmtree(processed_dir, ignore_errors=True)
    shutil.rmtree(runtime / "chronos2_training_output", ignore_errors=True)

    setup_manifest["cleanup_pending"] = bool(
        dict(config.get("cleanup", {})).get("delete_input_zips_after_success", True)
    )
    atomic_json(state_path, setup_manifest)
    atomic_json(project_root / "models" / "chronos2" / "setup_manifest.json", setup_manifest)
    log_line(log_path, "Chronos-2 setup transaction completed. Input ZIPs remain until verification and regression tests pass.")
    return setup_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--force", action="store_true", help="Ignore a matching prior setup state and rerun training and benchmarks.")
    parser.add_argument("--require-lora", action="store_true", help="Fail safely unless a fine-tuned LoRA checkpoint is produced and benchmarked.")
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    state_path = project_root / "runtime" / "chronos2_setup_state.json"
    log_path = project_root / "runtime" / "chronos2_setup.log"
    try:
        result = run(project_root, force=args.force, require_lora=args.require_lora)
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
