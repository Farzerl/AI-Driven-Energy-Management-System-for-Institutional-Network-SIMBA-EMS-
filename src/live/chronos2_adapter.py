from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

HORIZON_STEPS = {
    "30_minutes": 1,
    "2_hours": 4,
    "6_hours": 12,
    "24_hours": 48,
}


class Chronos2Unavailable(RuntimeError):
    """Raised when the optional Chronos-2 runtime cannot provide a safe forecast."""


@dataclass(frozen=True)
class Chronos2Paths:
    base_model: Path
    finetuned_model: Path
    routing: Path
    metrics: Path


class Chronos2Adapter:
    """Lazy, defensive adapter around the optional local Chronos-2 pipeline.

    Chronos remains a challenger. The existing SIMBA-EMS models continue operating when
    the optional package, model files, or fine-tuned route are unavailable.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        pipeline_factory: Callable[[Path, str], Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.paths = Chronos2Paths(
            base_model=self.project_root / "models" / "chronos-2-base",
            finetuned_model=self.project_root / "models" / "chronos-2-finetuned",
            routing=self.project_root / "models" / "chronos2" / "routing.json",
            metrics=self.project_root / "evidence" / "model_validation" / "chronos2_model_comparison.json",
        )
        self._pipeline_factory = pipeline_factory
        self._pipeline: Any | None = None
        self._pipeline_path: Path | None = None
        self._lock = RLock()
        self._executor: ThreadPoolExecutor | None = None
        self._timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("SIMBA_CHRONOS2_TIMEOUT_SECONDS", "20")
        )
        self._timeout_seconds = max(1.0, min(self._timeout_seconds, 300.0))
        self._cache: OrderedDict[tuple[object, ...], dict[str, dict[str, float]]] = OrderedDict()
        self._cache_limit = 512
        self._error = ""
        self._last_latency_ms = 0.0
        self._inference_count = 0
        self._failure_count = 0
        self._fallback_count = 0
        self._circuit_open_until = 0.0
        self._routing = self._load_json(self.paths.routing)
        self._metrics = self._load_json(self.paths.metrics)

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    @property
    def model_path(self) -> Path | None:
        variant = str(self._routing.get("deployment_variant", "finetuned")).strip().lower()
        if variant == "base" and self.paths.base_model.exists():
            return self.paths.base_model
        if variant == "finetuned" and self.paths.finetuned_model.exists():
            return self.paths.finetuned_model
        if self.paths.finetuned_model.exists():
            return self.paths.finetuned_model
        if self.paths.base_model.exists():
            return self.paths.base_model
        return None

    @property
    def installed(self) -> bool:
        path = self.model_path
        return bool(path and (path / "config.json").exists() and any(path.rglob("*.safetensors")))

    @property
    def package_available(self) -> bool:
        if self._pipeline_factory is not None:
            return True
        return importlib.util.find_spec("chronos") is not None

    @property
    def ready(self) -> bool:
        return self.installed and self.package_available

    @property
    def selected_by_horizon(self) -> dict[str, dict[str, Any]]:
        value = self._routing.get("selected_by_horizon", {})
        return dict(value) if isinstance(value, Mapping) else {}

    def reload_metadata(self) -> None:
        with self._lock:
            self._routing = self._load_json(self.paths.routing)
            self._metrics = self._load_json(self.paths.metrics)
            self._cache.clear()

    def unload(self) -> None:
        executor: ThreadPoolExecutor | None
        with self._lock:
            self._pipeline = None
            self._pipeline_path = None
            self._cache.clear()
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def close(self) -> None:
        """Release optional inference resources without affecting persisted model files."""
        self.unload()

    def _executor_for_use(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="simba-chronos2")
            return self._executor

    def mark_fallback(self) -> None:
        with self._lock:
            self._fallback_count += 1

    def status(self, *, public: bool = False) -> dict[str, object]:
        model_path = self.model_path
        route = self.selected_by_horizon
        base = {
            "installed": self.installed,
            "package_available": self.package_available,
            "ready": self.ready,
            "fine_tuned": self.paths.finetuned_model.exists(),
            "eligible_for_automatic_routing": bool(self._routing.get("eligible", False)),
            "selected_horizons": {
                key: str(dict(value).get("model", "existing"))
                for key, value in route.items()
            },
            "last_inference_latency_ms": round(self._last_latency_ms, 3),
            "inference_count": self._inference_count,
            "failure_count": self._failure_count,
            "fallback_count": self._fallback_count,
            "error": self._error,
        }
        if public:
            return base
        return {
            **base,
            "model_path": str(model_path.relative_to(self.project_root)) if model_path else None,
            "routing_path": str(self.paths.routing.relative_to(self.project_root)),
            "metrics_path": str(self.paths.metrics.relative_to(self.project_root)),
            "timeout_seconds": self._timeout_seconds,
            "circuit_open": time.monotonic() < self._circuit_open_until,
            "routing": deepcopy(self._routing),
            "metrics_summary": deepcopy(self._metrics.get("summary", {})),
        }

    def _default_factory(self, model_path: Path, device: str) -> Any:
        from chronos import Chronos2Pipeline  # type: ignore[import-not-found]

        return Chronos2Pipeline.from_pretrained(str(model_path), device_map=device)

    def _device(self) -> str:
        configured = os.getenv("SIMBA_CHRONOS2_DEVICE", "auto").strip().lower()
        if configured in {"cpu", "cuda"}:
            return configured
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def _load_pipeline(self) -> Any:
        with self._lock:
            path = self.model_path
            if path is None or not self.installed:
                raise Chronos2Unavailable("Chronos-2 model files are not installed.")
            if not self.package_available:
                raise Chronos2Unavailable("The chronos-forecasting package is not installed in the active .venv.")
            if self._pipeline is not None and self._pipeline_path == path:
                return self._pipeline
            factory = self._pipeline_factory or self._default_factory
            try:
                self._pipeline = factory(path, self._device())
                self._pipeline_path = path
                self._error = ""
                return self._pipeline
            except Exception as exc:
                self._pipeline = None
                self._pipeline_path = None
                self._error = f"Chronos-2 load failed: {exc}"
                raise Chronos2Unavailable(self._error) from exc

    @staticmethod
    def _normalise_timestamp(value: object) -> pd.Timestamp:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("Africa/Harare").tz_localize(None)
        return timestamp

    @classmethod
    def _validate_rows(cls, records: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for raw in records:
            if "timestamp" not in raw or "kva" not in raw:
                raise ValueError("Each Chronos-2 reading requires timestamp and kva.")
            timestamp = cls._normalise_timestamp(raw["timestamp"])
            kva = float(raw["kva"])
            if not math.isfinite(kva) or kva < 0:
                raise ValueError("Chronos-2 readings must contain finite non-negative kVA values.")
            power_factor = float(raw.get("power_factor", 0.95))
            if not math.isfinite(power_factor):
                power_factor = 0.95
            rows.append(
                {
                    "timestamp": timestamp,
                    "kva": kva,
                    "power_factor": min(max(abs(power_factor), 0.0), 1.0),
                    "kwh_is_measured": float(bool(raw.get("kwh_is_measured", True))),
                }
            )
        rows.sort(key=lambda item: item["timestamp"])
        if len(rows) < 49:
            raise ValueError(f"Chronos-2 requires at least 49 readings; received {len(rows)}.")
        selected = rows[-336:]
        for previous, current in zip(selected[:-1], selected[1:]):
            minutes = (current["timestamp"] - previous["timestamp"]).total_seconds() / 60.0
            if abs(minutes - 30.0) > 7.5:
                raise ValueError(f"Irregular interval detected: {minutes:.1f} minutes; expected approximately 30.")
        return rows[-336:]

    @staticmethod
    def _calendar(timestamp: pd.Timestamp) -> dict[str, object]:
        slot = int(timestamp.hour * 2 + timestamp.minute // 30)
        weekend = int(timestamp.dayofweek >= 5)
        if timestamp.hour in {7, 8, 17, 18}:
            tariff = "peak"
        elif 22 <= timestamp.hour or timestamp.hour < 5:
            tariff = "offpeak"
        else:
            tariff = "standard"
        return {
            "half_hour_slot": slot,
            "day_of_week": int(timestamp.dayofweek),
            "is_weekend": weekend,
            "month": int(timestamp.month),
            "tariff_period": tariff,
        }

    @classmethod
    def _frames(
        cls,
        records: Iterable[Mapping[str, object]],
        facility_id: str,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        rows = cls._validate_rows(records)
        context_rows: list[dict[str, object]] = []
        for row in rows:
            context_rows.append(
                {
                    "item_id": str(facility_id),
                    "timestamp": row["timestamp"],
                    "target": row["kva"],
                    "power_factor": row["power_factor"],
                    "kwh_is_measured": row["kwh_is_measured"],
                    **cls._calendar(row["timestamp"]),
                }
            )
        latest = rows[-1]["timestamp"]
        future_rows: list[dict[str, object]] = []
        for step in range(1, 49):
            timestamp = latest + timedelta(minutes=30 * step)
            future_rows.append(
                {
                    "item_id": str(facility_id),
                    "timestamp": timestamp,
                    **cls._calendar(timestamp),
                }
            )
        return pd.DataFrame(context_rows), pd.DataFrame(future_rows)

    @classmethod
    def _cache_key(cls, records: Iterable[Mapping[str, object]], facility_id: str) -> tuple[object, ...]:
        rows = cls._validate_rows(records)
        digest = hashlib.sha256()
        for row in rows:
            digest.update(str(row["timestamp"]).encode("utf-8"))
            digest.update(f"|{float(row['kva']):.6f}|{float(row['power_factor']):.6f}".encode("utf-8"))
        return str(facility_id), digest.hexdigest()

    @staticmethod
    def _extract(predictions: pd.DataFrame) -> dict[str, dict[str, float]]:
        if predictions.empty:
            raise Chronos2Unavailable("Chronos-2 returned an empty forecast.")
        ordered = predictions.sort_values("timestamp").reset_index(drop=True)
        output: dict[str, dict[str, float]] = {}
        for horizon, step in HORIZON_STEPS.items():
            index = step - 1
            if index >= len(ordered):
                raise Chronos2Unavailable(f"Chronos-2 returned only {len(ordered)} steps; {step} required.")
            row = ordered.iloc[index]
            p50 = float(row.get("0.5", row.get("predictions")))
            p10 = float(row.get("0.1", p50))
            p90 = float(row.get("0.9", p50))
            values = [p10, p50, p90]
            if not all(math.isfinite(item) for item in values):
                raise Chronos2Unavailable("Chronos-2 returned NaN or infinite forecast values.")
            p10, p50, p90 = sorted(max(item, 0.0) for item in values)
            output[horizon] = {
                "forecast_kva": p50,
                "forecast_lower_kva": p10,
                "forecast_upper_kva": p90,
            }
        return output

    def _predict_sync(self, context: pd.DataFrame, future: pd.DataFrame) -> dict[str, dict[str, float]]:
        pipeline = self._load_pipeline()
        result = pipeline.predict_df(
            context,
            future_df=future,
            prediction_length=48,
            quantile_levels=[0.1, 0.5, 0.9],
            id_column="item_id",
            timestamp_column="timestamp",
            target="target",
        )
        return self._extract(result)

    def predict(
        self,
        records: Iterable[Mapping[str, object]],
        facility_id: str,
    ) -> dict[str, dict[str, float]]:
        if time.monotonic() < self._circuit_open_until:
            raise Chronos2Unavailable("Chronos-2 circuit breaker is temporarily open after a recent failure.")
        rows = list(records)
        key = self._cache_key(rows, facility_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return deepcopy(cached)
        context, future = self._frames(rows, facility_id)
        started = time.perf_counter()
        future_result = self._executor_for_use().submit(self._predict_sync, context, future)
        try:
            result = future_result.result(timeout=self._timeout_seconds)
        except FutureTimeoutError as exc:
            with self._lock:
                self._failure_count += 1
                self._circuit_open_until = time.monotonic() + 60.0
                self._error = f"Chronos-2 inference exceeded {self._timeout_seconds:.1f} seconds."
            raise Chronos2Unavailable(self._error) from exc
        except Exception as exc:
            with self._lock:
                self._failure_count += 1
                self._circuit_open_until = time.monotonic() + 30.0
                self._error = f"Chronos-2 inference failed: {exc}"
            raise Chronos2Unavailable(self._error) from exc
        elapsed = (time.perf_counter() - started) * 1000.0
        with self._lock:
            self._last_latency_ms = elapsed
            self._inference_count += 1
            self._error = ""
            self._cache[key] = deepcopy(result)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_limit:
                self._cache.popitem(last=False)
        return result
