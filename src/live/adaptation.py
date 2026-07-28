from __future__ import annotations

import copy
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Iterable, Mapping


class AdaptiveForecastCalibrator:
    """Conservative online residual calibration for a fixed, validated forecast model.

    The deployed gradient-boosted trees are never modified from live traffic. Instead,
    realised forecast residuals update a bounded per-facility bias correction and an
    adaptive upper uncertainty margin. This is reversible, auditable and cannot promote
    a new model without an offline chronological validation run.
    """

    def __init__(self, path: Path, settings_provider) -> None:  # type: ignore[no-untyped-def]
        self.path = Path(path)
        self._settings_provider = settings_provider
        self._lock = RLock()
        self._state: dict[str, object] = {"version": 1, "series": {}, "observed_ids": [], "total_updates": 0}
        self.last_error: str | None = None
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._state.update(raw)
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)

    @staticmethod
    def _key(facility: str, horizon: str) -> str:
        return f"{facility}|{horizon}"

    def settings(self) -> dict[str, object]:
        root = self._settings_provider()
        return dict(root.get("adaptive_learning", {})) if isinstance(root, Mapping) else {}

    def apply(
        self,
        *,
        facility: str,
        horizon: str,
        forecast_kva: float,
        upper_kva: float,
        limit_kva: float,
    ) -> dict[str, object]:
        settings = self.settings()
        enabled = bool(settings.get("enabled", True))
        minimum = int(settings.get("minimum_observations", 8))
        gain = float(settings.get("correction_gain", 0.55))
        cap = max(float(limit_kva) * float(settings.get("maximum_correction_percent_of_limit", 5.0)) / 100.0, 0.0)
        with self._lock:
            item = copy.deepcopy(dict(self._state.get("series", {})).get(self._key(facility, horizon), {}))
        count = int(item.get("count", 0))
        correction = 0.0
        if enabled and count >= minimum:
            correction = max(-cap, min(float(item.get("ewma_bias_kva", 0.0)) * gain, cap))
        corrected = max(float(forecast_kva) + correction, 0.0)
        base_margin = max(float(upper_kva) - float(forecast_kva), 0.0)
        adaptive_margin = max(base_margin, float(item.get("positive_residual_p85_kva", 0.0))) if enabled else base_margin
        drift = str(item.get("drift_status", "learning" if count < minimum else "stable"))
        return {
            "forecast_kva": corrected,
            "forecast_upper_kva": corrected + adaptive_margin,
            "uncertainty_margin_kva": adaptive_margin,
            "base_forecast_kva": float(forecast_kva),
            "adaptive_correction_kva": correction,
            "adaptive_observations": count,
            "adaptive_status": drift if enabled else "disabled",
        }

    def observe_readings(
        self,
        readings: Iterable[Mapping[str, object]],
        forecasts: Iterable[Mapping[str, object]],
    ) -> dict[str, object]:
        settings = self.settings()
        if not bool(settings.get("enabled", True)):
            return {"updated": 0, "status": "disabled"}
        window = int(settings.get("residual_window", 192))
        forecast_rows = list(forecasts)
        updates = 0
        with self._lock:
            series = dict(self._state.get("series", {}))
            observed = list(self._state.get("observed_ids", []))
            observed_set = set(str(value) for value in observed)
            for reading in readings:
                source = str(reading.get("data_origin") or reading.get("source") or "").strip().lower()
                if source in {
                    "manual_test",
                    "simulation",
                    "simulation_preroll",
                    "simulation_observation",
                    "simulation_current",
                    "historical_replay",
                    "diagnostic_reference",
                }:
                    continue
                facility = str(reading.get("facility_id", ""))
                timestamp = str(reading.get("timestamp", ""))
                actual = float(reading.get("kva", 0.0))
                for forecast in forecast_rows:
                    if str(forecast.get("facility_id")) != facility:
                        continue
                    for horizon, row in dict(forecast.get("forecasts", {})).items():
                        if str(row.get("target_timestamp", "")) != timestamp:
                            continue
                        observation_id = f"{forecast.get('forecast_id')}|{horizon}|{timestamp}"
                        if observation_id in observed_set:
                            continue
                        predicted = float(row.get("forecast_kva", 0.0))
                        residual = actual - predicted
                        key = self._key(facility, str(horizon))
                        item = dict(series.get(key, {}))
                        count = int(item.get("count", 0)) + 1
                        alpha = 0.08 if count > 1 else 1.0
                        bias = (1 - alpha) * float(item.get("ewma_bias_kva", 0.0)) + alpha * residual
                        abs_error = abs(residual)
                        ewma_abs = (1 - alpha) * float(item.get("ewma_abs_error_kva", abs_error)) + alpha * abs_error
                        residuals = list(item.get("recent_residuals_kva", []))[-(window - 1):] + [residual]
                        positive = sorted(max(value, 0.0) for value in residuals)
                        p85 = positive[min(len(positive) - 1, max(0, math.ceil(0.85 * len(positive)) - 1))]
                        baseline_p95 = max(float(row.get("validation_p95_abs_error_kva", 0.0)), 0.5)
                        if count < int(settings.get("minimum_observations", 8)):
                            drift_status = "learning"
                        elif ewma_abs > 1.5 * baseline_p95 or abs(bias) > max(0.03 * float(forecast.get("facility_limit_kva", 0.0)), 1.0):
                            drift_status = "watch"
                        else:
                            drift_status = "stable"
                        item.update(
                            {
                                "facility_id": facility,
                                "horizon": str(horizon),
                                "count": count,
                                "ewma_bias_kva": round(bias, 6),
                                "ewma_abs_error_kva": round(ewma_abs, 6),
                                "positive_residual_p85_kva": round(float(p85), 6),
                                "recent_residuals_kva": [round(float(value), 6) for value in residuals],
                                "drift_status": drift_status,
                                "last_observed_timestamp": timestamp,
                                "updated_utc": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        series[key] = item
                        observed_set.add(observation_id)
                        observed.append(observation_id)
                        updates += 1
            self._state["series"] = series
            self._state["observed_ids"] = observed[-5000:]
            self._state["total_updates"] = int(self._state.get("total_updates", 0)) + updates
            if updates:
                self._write()
        return {"updated": updates, "status": "active", "total_updates": self._state.get("total_updates", 0)}

    def status(self) -> dict[str, object]:
        settings = self.settings()
        with self._lock:
            series = copy.deepcopy(dict(self._state.get("series", {})))
            total = int(self._state.get("total_updates", 0))
        counts = [int(item.get("count", 0)) for item in series.values()]
        watch = sum(1 for item in series.values() if item.get("drift_status") == "watch")
        retrain_interval = int(settings.get("retraining_interval_new_readings", 336))
        return {
            "enabled": bool(settings.get("enabled", True)),
            "series_count": len(series),
            "total_residual_updates": total,
            "calibrated_series": sum(1 for count in counts if count >= int(settings.get("minimum_observations", 8))),
            "watch_series": watch,
            "retraining_due": total >= retrain_interval or watch >= 3,
            "retraining_policy": "Offline retraining and chronological validation are required before model promotion.",
            "last_error": self.last_error,
            "settings": settings,
        }

    def reset(self) -> dict[str, object]:
        with self._lock:
            self._state = {"version": 1, "series": {}, "observed_ids": [], "total_updates": 0}
            self._write()
        return self.status()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(self._state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.path)
