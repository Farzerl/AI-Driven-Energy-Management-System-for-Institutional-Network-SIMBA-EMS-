from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from src.live.features import tariff_period

TARGETS = ["active_power_kw", "reactive_power_kvar"]
HORIZON_STEPS = {"30_minutes": 1, "2_hours": 4, "6_hours": 12, "24_hours": 48}
INTERVAL_HOURS = 0.5


class PowerQualityUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PowerQualityPaths:
    routing: Path
    metrics: Path
    profiles: Path
    setup: Path


class PowerQualityChronosAdapter:
    """Local multivariate Chronos-2 adapter for active and reactive power.

    The adapter never changes the existing demand forecast. It forecasts two
    independent electrical quantities (kW and signed kVAR) and derives kWh,
    estimated kVARh and power factor from their physical relationship.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        pipeline_factory: Any | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.paths = PowerQualityPaths(
            routing=self.project_root / "models" / "power_quality" / "routing.json",
            metrics=self.project_root / "evidence" / "model_validation" / "power_quality_model_comparison.json",
            profiles=self.project_root / "models" / "power_quality" / "facility_profiles.json",
            setup=self.project_root / "runtime" / "power_quality_setup_state.json",
        )
        self._pipeline_factory = pipeline_factory
        self._timeout_seconds = float(timeout_seconds or os.getenv("SIMBA_POWER_QUALITY_TIMEOUT_SECONDS", "30"))
        self._lock = threading.RLock()
        self._pipeline: Any | None = None
        self._pipeline_path: Path | None = None
        self._routing = self._load_json(self.paths.routing)
        self._metrics = self._load_json(self.paths.metrics)
        self._profiles = self._load_json(self.paths.profiles)
        self._last_latency_ms = 0.0
        self._inference_count = 0
        self._failure_count = 0
        self._fallback_count = 0
        self._error = ""
        self._tariff = self._load_tariff()

    @staticmethod
    def _load_json(path: Path) -> dict[str, object]:
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _load_tariff(self) -> dict[str, float]:
        path = self.project_root / "config" / "tariff.public-estimate.json"
        payload = self._load_json(path)
        values = dict(payload.get("study_calibrated_planning_parameters", {}))
        return {
            "peak": float(values.get("peak_energy_usd_per_kwh", 0.2173)),
            "standard": float(values.get("standard_energy_usd_per_kwh", 0.115)),
            "offpeak": float(values.get("off_peak_energy_usd_per_kwh", 0.0588)),
        }

    def reload_metadata(self) -> None:
        with self._lock:
            self._routing = self._load_json(self.paths.routing)
            self._metrics = self._load_json(self.paths.metrics)
            self._profiles = self._load_json(self.paths.profiles)
            expected = self.model_path
            if self._pipeline_path is not None and expected != self._pipeline_path:
                self._pipeline = None
                self._pipeline_path = None

    @property
    def model_path(self) -> Path | None:
        raw = self._routing.get("model_path")
        if not raw:
            return None
        path = (self.project_root / str(raw)).resolve()
        try:
            path.relative_to(self.project_root)
        except ValueError:
            return None
        return path

    @property
    def package_available(self) -> bool:
        return self._pipeline_factory is not None or importlib.util.find_spec("chronos") is not None

    @property
    def installed(self) -> bool:
        path = self.model_path
        return bool(path and (path / "config.json").is_file() and any(path.rglob("*.safetensors")))

    @property
    def ready(self) -> bool:
        return bool(self._routing.get("eligible", False)) and self.installed and self.package_available

    def close(self) -> None:
        with self._lock:
            self._pipeline = None
            self._pipeline_path = None

    def mark_fallback(self, reason: str = "") -> None:
        with self._lock:
            self._fallback_count += 1
            if reason:
                self._error = reason

    def status(self, *, public: bool = False) -> dict[str, object]:
        selected = dict(self._routing.get("selected_by_target_horizon", {}))
        base = {
            "installed": self.installed,
            "package_available": self.package_available,
            "ready": self.ready,
            "fine_tuned": (self.project_root / "models" / "chronos-2-power-quality-finetuned").exists(),
            "deployment_variant": self._routing.get("deployment_variant"),
            "targets": list(self._routing.get("targets", TARGETS)),
            "derived_outputs": list(self._routing.get("derived_outputs", [])),
            "last_inference_latency_ms": round(self._last_latency_ms, 3),
            "inference_count": self._inference_count,
            "failure_count": self._failure_count,
            "fallback_count": self._fallback_count,
            "error": self._error,
        }
        if public:
            return base
        path = self.model_path
        return {
            **base,
            "model_path": path.relative_to(self.project_root).as_posix() if path else None,
            "routing_path": self.paths.routing.relative_to(self.project_root).as_posix(),
            "metrics_path": self.paths.metrics.relative_to(self.project_root).as_posix(),
            "timeout_seconds": self._timeout_seconds,
            "routing": deepcopy(self._routing),
            "metrics_summary": deepcopy(self._metrics.get("summary", {})),
            "selected_by_target_horizon": deepcopy(selected),
        }

    def _default_factory(self, model_path: Path, device: str) -> Any:
        from chronos import Chronos2Pipeline  # type: ignore[import-not-found]

        return Chronos2Pipeline.from_pretrained(str(model_path), device_map=device)

    @staticmethod
    def _device() -> str:
        configured = os.getenv("SIMBA_POWER_QUALITY_DEVICE", "auto").strip().lower()
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
                raise PowerQualityUnavailable("The trained power-quality model is not installed.")
            if not self.package_available and self._pipeline_factory is None:
                raise PowerQualityUnavailable("The chronos-forecasting package is unavailable in the active .venv.")
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
                self._error = f"Power-quality model load failed: {exc}"
                self._failure_count += 1
                raise PowerQualityUnavailable(self._error) from exc

    def _profile(self, facility_id: str) -> dict[str, object]:
        value = self._profiles.get(str(facility_id), {})
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _timestamp(value: object) -> pd.Timestamp:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("Africa/Harare").tz_localize(None)
        return timestamp

    def _row(self, raw: Mapping[str, object], facility_id: str) -> dict[str, object]:
        timestamp = self._timestamp(raw["timestamp"])
        kva = float(raw.get("kva", 0.0))
        if not math.isfinite(kva) or kva < 0:
            raise ValueError("Power-quality history requires finite non-negative kVA.")
        pf_raw = float(raw.get("power_factor", 0.95))
        pf = min(max(abs(pf_raw) if math.isfinite(pf_raw) else 0.95, 0.0), 1.0)

        active_raw = raw.get("active_power_kw")
        if active_raw is None:
            kwh = raw.get("kwh")
            active_kw = float(kwh) / INTERVAL_HOURS if kwh is not None else kva * pf
        else:
            active_kw = float(active_raw)
        if not math.isfinite(active_kw):
            raise ValueError("Power-quality history contains invalid active power.")
        active_kw = max(active_kw, 0.0)

        reactive_raw = raw.get("reactive_power_kvar")
        if reactive_raw is None:
            sign = int(self._profile(facility_id).get("reactive_sign_preference", 1) or 1)
            reactive_kvar = sign * math.sqrt(max(kva * kva - active_kw * active_kw, 0.0))
        else:
            reactive_kvar = float(reactive_raw)
        if not math.isfinite(reactive_kvar):
            raise ValueError("Power-quality history contains invalid reactive power.")

        physical_kva = math.hypot(active_kw, reactive_kvar)
        operational_pf = active_kw / physical_kva if physical_kva > 1e-9 else 1.0
        return {
            "timestamp": timestamp,
            "active_power_kw": active_kw,
            "reactive_power_kvar": reactive_kvar,
            "demand_kva": kva if kva > 0 else physical_kva,
            "power_factor": min(max(operational_pf, 0.0), 1.0),
            "measurement_quality": float(raw.get("measurement_quality", 1.0)),
            "gap_imputed": float(bool(raw.get("gap_imputed", False))),
            "facility_name": str(raw.get("facility_name") or raw.get("display_name") or facility_id),
            "display_facility_id": str(raw.get("display_facility_id") or facility_id),
        }

    def _validated_rows(self, records: Iterable[Mapping[str, object]], facility_id: str) -> list[dict[str, object]]:
        rows = [self._row(raw, facility_id) for raw in records if "timestamp" in raw]
        rows.sort(key=lambda item: item["timestamp"])
        deduplicated: dict[pd.Timestamp, dict[str, object]] = {pd.Timestamp(item["timestamp"]): item for item in rows}
        rows = list(deduplicated.values())
        if len(rows) < 49:
            raise ValueError(f"{facility_id} requires at least 49 completed readings; received {len(rows)}.")
        selected = rows[-336:]
        for previous, current in zip(selected[:-1], selected[1:]):
            minutes = (pd.Timestamp(current["timestamp"]) - pd.Timestamp(previous["timestamp"])).total_seconds() / 60
            if abs(minutes - 30.0) > 7.5:
                raise ValueError(f"{facility_id} contains an interval gap of {minutes:.1f} minutes.")
        return selected

    @staticmethod
    def _calendar(timestamp: pd.Timestamp) -> dict[str, object]:
        period = tariff_period(timestamp)
        return {
            "half_hour_slot": int(timestamp.hour * 2 + timestamp.minute // 30),
            "day_of_week": int(timestamp.dayofweek),
            "is_weekend": int(timestamp.dayofweek >= 5),
            "month": int(timestamp.month),
            "tariff_period": period,
        }

    def _frames(
        self,
        grouped: Mapping[str, Iterable[Mapping[str, object]]],
    ) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, list[dict[str, object]]]]:
        contexts: list[dict[str, object]] = []
        futures: list[dict[str, object]] = []
        validated: dict[str, list[dict[str, object]]] = {}
        for facility_id, raw_records in sorted(grouped.items()):
            rows = self._validated_rows(raw_records, facility_id)
            validated[facility_id] = rows
            for row in rows:
                contexts.append(
                    {
                        "item_id": facility_id,
                        "timestamp": row["timestamp"],
                        "active_power_kw": row["active_power_kw"],
                        "reactive_power_kvar": row["reactive_power_kvar"],
                        "demand_kva": row["demand_kva"],
                        "power_factor": row["power_factor"],
                        "measurement_quality": row["measurement_quality"],
                        "gap_imputed": row["gap_imputed"],
                        **self._calendar(pd.Timestamp(row["timestamp"])),
                    }
                )
            latest = pd.Timestamp(rows[-1]["timestamp"])
            for step in range(1, 49):
                timestamp = latest + timedelta(minutes=30 * step)
                futures.append({"item_id": facility_id, "timestamp": timestamp, **self._calendar(timestamp)})
        if not validated:
            raise ValueError("No facility has enough valid history for power-quality forecasting.")
        return pd.DataFrame(contexts), pd.DataFrame(futures), validated

    def cache_key(self, grouped: Mapping[str, Iterable[Mapping[str, object]]]) -> str:
        digest = hashlib.sha256()
        for facility_id, records in sorted(grouped.items()):
            rows = self._validated_rows(records, facility_id)
            digest.update(facility_id.encode("utf-8"))
            for row in rows[-16:]:
                digest.update(
                    f"|{row['timestamp']}|{float(row['active_power_kw']):.5f}|{float(row['reactive_power_kvar']):.5f}".encode("utf-8")
                )
        return digest.hexdigest()

    @staticmethod
    def _quantiles(row: pd.Series, target: str) -> tuple[float, float, float]:
        point = float(row.get("0.5", row.get("predictions")))
        lower = float(row.get("0.1", point))
        upper = float(row.get("0.9", point))
        values = sorted([lower, point, upper])
        if target == "active_power_kw":
            values = [max(value, 0.0) for value in values]
        if not all(math.isfinite(value) for value in values):
            raise PowerQualityUnavailable(f"The model returned invalid {target} values.")
        return values[1], values[0], values[2]

    @staticmethod
    def _seasonal(rows: list[dict[str, object]], target: str, step: int) -> float:
        target_time = pd.Timestamp(rows[-1]["timestamp"]) + timedelta(minutes=30 * step)
        seasonal_time = target_time - timedelta(hours=24)
        lookup = {pd.Timestamp(item["timestamp"]): float(item[target]) for item in rows}
        value = lookup.get(seasonal_time, float(rows[-1][target]))
        return max(value, 0.0) if target == "active_power_kw" else value

    def _route(self, target: str, horizon: str) -> tuple[str, float]:
        targets = dict(self._routing.get("selected_by_target_horizon", {}))
        route = dict(dict(targets.get(target, {})).get(horizon, {}))
        return str(route.get("model", "seasonal_persistence")), float(route.get("chronos_weight", 0.0))

    def _recommendation(self, pf: float, reactive_kvar: float, period: str) -> tuple[str, str]:
        thresholds = dict(self._routing.get("thresholds", {}))
        low = float(thresholds.get("low_power_factor", 0.95))
        critical = float(thresholds.get("critical_power_factor", 0.85))
        if pf < critical:
            return (
                "critical",
                "Inspect inductive equipment and the authorised power-factor-correction system. Escalate to electrical maintenance; no capacitor switching is issued by AI.",
            )
        if pf < low:
            return (
                "attention",
                "Review motors, pumps, refrigeration and capacitor-bank status before the forecast interval. Coordinate high-reactive loads where operationally safe.",
            )
        if abs(reactive_kvar) > 0 and period == "peak":
            return ("normal", "Power factor remains acceptable. Continue monitoring reactive loading during the peak tariff window.")
        return ("normal", "Power factor is forecast within the configured acceptable range.")

    def _extract(
        self,
        predictions: pd.DataFrame,
        validated: Mapping[str, list[dict[str, object]]],
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for facility_id, group in predictions.groupby("item_id", sort=False):
            facility = str(facility_id)
            rows = validated[facility]
            target_frames = {
                target: group[group["target_name"] == target].sort_values("timestamp").reset_index(drop=True)
                for target in TARGETS
            }
            if any(len(frame) < 48 for frame in target_frames.values()):
                raise PowerQualityUnavailable(f"The model returned fewer than 48 steps for {facility}.")
            horizon_rows: dict[str, dict[str, object]] = {}
            for horizon, step in HORIZON_STEPS.items():
                target_values: dict[str, tuple[float, float, float]] = {}
                route_labels: dict[str, str] = {}
                for target in TARGETS:
                    chronos = self._quantiles(target_frames[target].iloc[step - 1], target)
                    seasonal = self._seasonal(rows, target, step)
                    model, weight = self._route(target, horizon)
                    if model == "seasonal_persistence":
                        values = (seasonal, seasonal, seasonal)
                    elif model == "chronos":
                        values = chronos
                    else:
                        blended = [
                            (1.0 - weight) * seasonal + weight * value
                            for value in chronos
                        ]
                        values = (blended[0], blended[1], blended[2])
                    if target == "active_power_kw":
                        values = tuple(max(float(value), 0.0) for value in values)
                    target_values[target] = values
                    route_labels[target] = model

                p, p_low, p_high = target_values["active_power_kw"]
                q, q_low, q_high = target_values["reactive_power_kvar"]
                apparent = math.hypot(p, q)
                power_factor = p / apparent if apparent > 1e-9 else 1.0
                q_bound = max(abs(q_low), abs(q_high))
                conservative_apparent = math.hypot(max(p_low, 0.0), q_bound)
                conservative_pf = max(p_low, 0.0) / conservative_apparent if conservative_apparent > 1e-9 else 1.0
                timestamp = pd.Timestamp(rows[-1]["timestamp"]) + timedelta(minutes=30 * step)
                period = tariff_period(timestamp)
                energy = p * INTERVAL_HOURS
                reactive_energy = abs(q) * INTERVAL_HOURS
                risk, action = self._recommendation(conservative_pf, q, period)
                horizon_rows[horizon] = {
                    "minutes": step * 30,
                    "target_timestamp": timestamp.isoformat(),
                    "forecast_active_power_kw": round(p, 4),
                    "forecast_active_power_lower_kw": round(p_low, 4),
                    "forecast_active_power_upper_kw": round(p_high, 4),
                    "forecast_reactive_power_kvar": round(q, 4),
                    "forecast_reactive_power_lower_kvar": round(q_low, 4),
                    "forecast_reactive_power_upper_kvar": round(q_high, 4),
                    "forecast_apparent_power_kva_crosscheck": round(apparent, 4),
                    "forecast_power_factor": round(min(max(power_factor, 0.0), 1.0), 4),
                    "conservative_power_factor": round(min(max(conservative_pf, 0.0), 1.0), 4),
                    "forecast_interval_energy_kwh": round(energy, 4),
                    "forecast_interval_reactive_energy_kvarh_estimated": round(reactive_energy, 4),
                    "tariff_period": period,
                    "forecast_energy_cost_proxy_usd": round(energy * self._tariff.get(period, 0.0), 4),
                    "power_factor_risk": risk,
                    "recommended_action": action,
                    "routes": route_labels,
                }
            latest = rows[-1]
            current_p = float(latest["active_power_kw"])
            current_q = float(latest["reactive_power_kvar"])
            current_kva = math.hypot(current_p, current_q)
            current_pf = current_p / current_kva if current_kva > 1e-9 else 1.0
            display_name = str(latest.get("facility_name") or facility)
            display_id = str(latest.get("display_facility_id") or facility)
            primary = horizon_rows["30_minutes"]
            items.append(
                {
                    "facility_id": display_id,
                    "model_facility_id": facility,
                    "facility_name": display_name,
                    "reading_timestamp": pd.Timestamp(latest["timestamp"]).isoformat(),
                    "current_active_power_kw": round(current_p, 4),
                    "current_reactive_power_kvar": round(current_q, 4),
                    "current_apparent_power_kva_crosscheck": round(current_kva, 4),
                    "current_power_factor": round(current_pf, 4),
                    "forecast_power_factor": primary["forecast_power_factor"],
                    "conservative_power_factor": primary["conservative_power_factor"],
                    "power_factor_risk": primary["power_factor_risk"],
                    "recommended_action": primary["recommended_action"],
                    "forecasts": horizon_rows,
                    "model_source": "validated_multivariate_power_quality_model",
                }
            )
        items.sort(key=lambda item: ({"critical": 2, "attention": 1, "normal": 0}.get(str(item["power_factor_risk"]), 0), abs(float(item["current_reactive_power_kvar"]))), reverse=True)
        return items

    def predict_batch(self, grouped: Mapping[str, Iterable[Mapping[str, object]]]) -> dict[str, object]:
        if not self.ready:
            raise PowerQualityUnavailable("Power-quality forecasting is not ready. Run TRAIN_POWER_QUALITY_FORECASTS.bat.")
        context, future, validated = self._frames(grouped)
        pipeline = self._load_pipeline()
        started = time.perf_counter()
        try:
            result = pipeline.predict_df(
                context,
                future_df=future,
                prediction_length=48,
                quantile_levels=[0.1, 0.5, 0.9],
                id_column="item_id",
                timestamp_column="timestamp",
                target=TARGETS,
                batch_size=max(96, len(validated) * len(TARGETS)),
                context_length=336,
                cross_learning=True,
            )
            items = self._extract(result, validated)
        except Exception as exc:
            with self._lock:
                self._failure_count += 1
                self._error = str(exc)
            raise PowerQualityUnavailable(f"Power-quality inference failed: {exc}") from exc
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        with self._lock:
            self._last_latency_ms = elapsed_ms
            self._inference_count += 1
            self._error = ""
        return {
            "status": "success",
            "generated_utc": pd.Timestamp.utcnow().isoformat(),
            "source": "trained_multivariate_chronos2",
            "latency_ms": round(elapsed_ms, 3),
            "facility_count": len(items),
            "items": items,
            "model": self.status(public=True),
            "claim_boundary": self._routing.get("claim_boundary"),
        }

    def fallback_batch(self, grouped: Mapping[str, Iterable[Mapping[str, object]]], *, reason: str) -> dict[str, object]:
        items: list[dict[str, object]] = []
        for facility_id, raw_records in sorted(grouped.items()):
            try:
                rows = self._validated_rows(raw_records, facility_id)
            except Exception:
                continue
            latest = rows[-1]
            horizons: dict[str, dict[str, object]] = {}
            for horizon, step in HORIZON_STEPS.items():
                p = self._seasonal(rows, "active_power_kw", step)
                q = self._seasonal(rows, "reactive_power_kvar", step)
                kva = math.hypot(p, q)
                pf = p / kva if kva > 1e-9 else 1.0
                timestamp = pd.Timestamp(latest["timestamp"]) + timedelta(minutes=30 * step)
                period = tariff_period(timestamp)
                risk, action = self._recommendation(pf, q, period)
                horizons[horizon] = {
                    "minutes": step * 30,
                    "target_timestamp": timestamp.isoformat(),
                    "forecast_active_power_kw": round(p, 4),
                    "forecast_reactive_power_kvar": round(q, 4),
                    "forecast_apparent_power_kva_crosscheck": round(kva, 4),
                    "forecast_power_factor": round(pf, 4),
                    "conservative_power_factor": round(pf, 4),
                    "forecast_interval_energy_kwh": round(p * INTERVAL_HOURS, 4),
                    "forecast_interval_reactive_energy_kvarh_estimated": round(abs(q) * INTERVAL_HOURS, 4),
                    "tariff_period": period,
                    "forecast_energy_cost_proxy_usd": round(p * INTERVAL_HOURS * self._tariff.get(period, 0.0), 4),
                    "power_factor_risk": risk,
                    "recommended_action": action,
                    "routes": {target: "seasonal_persistence_guard" for target in TARGETS},
                }
            current_p = float(latest["active_power_kw"])
            current_q = float(latest["reactive_power_kvar"])
            current_kva = math.hypot(current_p, current_q)
            primary = horizons["30_minutes"]
            items.append(
                {
                    "facility_id": str(latest.get("display_facility_id") or facility_id),
                    "model_facility_id": facility_id,
                    "facility_name": str(latest.get("facility_name") or facility_id),
                    "reading_timestamp": pd.Timestamp(latest["timestamp"]).isoformat(),
                    "current_active_power_kw": round(current_p, 4),
                    "current_reactive_power_kvar": round(current_q, 4),
                    "current_apparent_power_kva_crosscheck": round(current_kva, 4),
                    "current_power_factor": round(current_p / current_kva if current_kva > 1e-9 else 1.0, 4),
                    "forecast_power_factor": primary["forecast_power_factor"],
                    "conservative_power_factor": primary["conservative_power_factor"],
                    "power_factor_risk": primary["power_factor_risk"],
                    "recommended_action": primary["recommended_action"],
                    "forecasts": horizons,
                    "model_source": "seasonal_persistence_guard",
                }
            )
        self.mark_fallback(reason)
        return {
            "status": "fallback",
            "generated_utc": pd.Timestamp.utcnow().isoformat(),
            "source": "seasonal_persistence_guard",
            "reason": reason,
            "facility_count": len(items),
            "items": items,
            "model": self.status(public=True),
            "claim_boundary": self._routing.get("claim_boundary") or (
                "Power-quality outputs are operational forecasts. Billing-grade reactive energy and savings require meter-register reconciliation."
            ),
        }
