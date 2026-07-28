from __future__ import annotations

import json
import os
import time
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Mapping

from src.live.multihorizon import MINIMUM_HISTORY, feature_vector, predict_portable
from src.live.chronos2_adapter import Chronos2Adapter, Chronos2Unavailable
from src.live.neural_models import PortableLSTM, PortableTransformer

MODEL_OPTIONS = {
    "automatic",
    "gradient_boosting",
    "lstm",
    "transformer",
    "hybrid_gb_lstm",
    "hybrid_gb_transformer",
    "hybrid_lstm_transformer",
    "hybrid_all",
    "chronos2",
    "hybrid_chronos_existing",
}


class LiveModelManager:
    def __init__(self, model_path: Path) -> None:
        configured = os.getenv("AI4I_MODEL_PATH", "").strip()
        self._path = Path(configured) if configured else Path(model_path)
        self._bundle: dict[str, Any] | None = None
        self._ensemble: dict[str, Any] | None = None
        self._lstm: PortableLSTM | None = None
        self._transformer: PortableTransformer | None = None
        self._error = ""
        self._warnings: list[str] = []
        self._lock = RLock()
        self._active_mode = "automatic"
        self._prediction_cache: OrderedDict[tuple[object, ...], dict[str, dict[str, object]]] = OrderedDict()
        self._prediction_cache_limit = 4096
        self._chronos = Chronos2Adapter(self._path.parents[1])
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._bundle = None
            self._ensemble = None
            self._lstm = None
            self._transformer = None
            self._error = ""
            self._warnings = []
            self._prediction_cache.clear()
            self._chronos.reload_metadata()
            self._chronos.unload()
            try:
                bundle = json.loads(self._path.read_text(encoding="utf-8"))
                required = {"model_name", "model_family", "horizons", "facilities", "facility_limits_kva"}
                if not isinstance(bundle, dict) or not required.issubset(bundle):
                    raise ValueError("The gradient-boosting model bundle is incomplete.")
                for name, item in bundle["horizons"].items():
                    if "minutes" not in item or "model" not in item:
                        raise ValueError(f"Horizon {name!r} is incomplete.")
                self._bundle = bundle
                neural_dir = self._path.parent / "neural"
                ensemble_path = neural_dir / "ensemble_config.json"
                if ensemble_path.exists():
                    self._ensemble = json.loads(ensemble_path.read_text(encoding="utf-8"))
                else:
                    self._warnings.append("Neural ensemble configuration is unavailable; gradient boosting remains active.")
                try:
                    self._lstm = PortableLSTM(neural_dir / "lstm_forecaster.json")
                except Exception as exc:
                    self._warnings.append(f"LSTM unavailable: {exc}")
                try:
                    self._transformer = PortableTransformer(neural_dir / "transformer_forecaster.json")
                except Exception as exc:
                    self._warnings.append(f"Transformer unavailable: {exc}")
            except Exception as exc:
                self._error = str(exc)

    @property
    def ready(self) -> bool:
        return self._bundle is not None

    @property
    def minimum_history(self) -> int:
        if not self._bundle:
            return MINIMUM_HISTORY
        return int(self._bundle.get("minimum_history_intervals", MINIMUM_HISTORY))

    @property
    def context_history(self) -> int:
        return 336 if self._chronos.ready else self.minimum_history

    def _validate_mode(self, mode: str) -> str:
        selected = str(mode).strip().lower()
        if selected not in MODEL_OPTIONS:
            raise ValueError(f"Unknown model mode: {mode}")
        if selected in {"lstm", "hybrid_gb_lstm", "hybrid_lstm_transformer", "hybrid_all"} and self._lstm is None:
            raise ValueError("The selected mode requires the LSTM bundle, which is not available.")
        if selected in {"transformer", "hybrid_gb_transformer", "hybrid_lstm_transformer", "hybrid_all"} and self._transformer is None:
            raise ValueError("The selected mode requires the Transformer bundle, which is not available.")
        if selected in {"chronos2", "hybrid_chronos_existing"} and not self._chronos.ready:
            raise ValueError("The selected mode requires a verified local Chronos-2 installation.")
        return selected

    def set_active_mode(self, mode: str) -> str:
        with self._lock:
            selected = self._validate_mode(mode)
            self._active_mode = selected
            return selected

    @property
    def active_mode(self) -> str:
        with self._lock:
            return self._active_mode

    def clear_prediction_cache(self) -> None:
        with self._lock:
            self._prediction_cache.clear()

    def close(self) -> None:
        """Release optional model runtime resources during API shutdown."""
        self._chronos.close()

    def status(self) -> dict[str, object]:
        if not self._bundle:
            return {
                "ready": False,
                "model_name": None,
                "model_family": None,
                "source": "unavailable",
                "error": self._error,
                "warnings": self._warnings,
                "operating_mode": "advisory",
            }
        horizon_metrics = {
            name: {**dict(item.get("metrics", {})), "classification": dict(item.get("classification", {}))}
            for name, item in self._bundle["horizons"].items()
        }
        selected = dict((self._ensemble or {}).get("selected_by_horizon", {}))
        return {
            "ready": True,
            "model_name": "SIMBA Multi-Model Demand Forecaster",
            "model_family": "Gradient boosting, LSTM, compact Transformer and validation-weighted hybrids",
            "source": "validated_institutional_models",
            "active_mode": self._active_mode,
            "available_models": sorted(MODEL_OPTIONS),
            "automatic_selection": selected,
            "neural_models_ready": {"lstm": self._lstm is not None, "transformer": self._transformer is not None},
            "chronos2": self._chronos.status(public=True),
            "training_period": self._bundle.get("training_period", {}),
            "validation_period": self._bundle.get("chronological_validation_period", {}),
            "test_period": self._bundle.get("chronological_test_period", {}),
            "prediction_horizons": {name: int(item["minutes"]) for name, item in self._bundle["horizons"].items()},
            "minimum_history_intervals": self.minimum_history,
            "facility_count": len(self._bundle["facilities"]),
            "metrics": horizon_metrics,
            "data_quality": self._bundle.get("data_quality", {}),
            "warnings": self._warnings,
            "error": self._error,
            "operating_mode": "advisory",
        }

    def _gradient_predictions(
        self,
        rows: list[Mapping[str, object]],
        facility_id: str,
    ) -> dict[str, float]:
        assert self._bundle is not None
        facilities = list(self._bundle["facilities"])
        current_kva = max(float(rows[-1]["kva"]), 0.0)
        output: dict[str, float] = {}
        for name, item in self._bundle["horizons"].items():
            vector = feature_vector(rows, facility_id=facility_id, horizon_steps=int(item["steps"]), facilities=facilities)
            raw = float(predict_portable(item["model"], vector))
            alpha = float(item.get("facility_blend_alpha", {}).get(facility_id, 1.0))
            output[name] = max(alpha * raw + (1.0 - alpha) * current_kva, 0.0)
        return output

    def _all_predictions(
        self,
        rows: list[Mapping[str, object]],
        facility_id: str,
        *,
        include_optional_models: bool = True,
    ) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, dict[str, float]]]]:
        assert self._bundle is not None
        predictions: dict[str, dict[str, float]] = {
            "gradient_boosting": self._gradient_predictions(rows, facility_id)
        }
        metadata: dict[str, dict[str, dict[str, float]]] = {}
        if self._lstm is not None:
            predictions["lstm"] = self._lstm.predict(rows, facility_id)
        if self._transformer is not None:
            predictions["transformer"] = self._transformer.predict(rows, facility_id)
        if self._ensemble:
            for name, definition in dict(self._ensemble.get("ensemble_definitions", {})).items():
                members = [str(item) for item in definition.get("members", [])]
                if not members or any(member not in predictions for member in members):
                    continue
                rows_by_horizon: dict[str, float] = {}
                for horizon in self._bundle["horizons"]:
                    weights = [max(float(item), 0.0) for item in definition.get("weights_by_horizon", {}).get(horizon, [])]
                    if len(weights) != len(members):
                        continue
                    total = sum(weights)
                    if total <= 0:
                        continue
                    rows_by_horizon[horizon] = max(
                        sum((weight / total) * predictions[member][horizon] for weight, member in zip(weights, members)),
                        0.0,
                    )
                if len(rows_by_horizon) == len(self._bundle["horizons"]):
                    predictions[name] = rows_by_horizon

        if include_optional_models and self._chronos.ready:
            try:
                quantiles = self._chronos.predict(rows, facility_id)
                predictions["chronos2"] = {name: float(item["forecast_kva"]) for name, item in quantiles.items()}
                metadata["chronos2"] = quantiles
                existing_selected = dict((self._ensemble or {}).get("selected_by_horizon", {}))
                routing = self._chronos.selected_by_horizon
                hybrid_values: dict[str, float] = {}
                hybrid_meta: dict[str, dict[str, float]] = {}
                for horizon in self._bundle["horizons"]:
                    base_model = str(dict(existing_selected.get(horizon, {})).get("model", "gradient_boosting"))
                    if base_model not in predictions:
                        base_model = "gradient_boosting"
                    route = dict(routing.get(horizon, {}))
                    weight = route.get("chronos_weight")
                    weight = 0.5 if weight is None else max(0.0, min(float(weight), 1.0))
                    base_value = float(predictions[base_model][horizon])
                    chronos_row = quantiles[horizon]
                    hybrid_values[horizon] = max((1.0 - weight) * base_value + weight * float(chronos_row["forecast_kva"]), 0.0)
                    base_upper = base_value + float(dict(self._bundle["horizons"][horizon]).get("uncertainty", {}).get("global_underforecast_margin_kva", 0.0))
                    hybrid_meta[horizon] = {
                        "forecast_kva": hybrid_values[horizon],
                        "forecast_lower_kva": max((1.0 - weight) * base_value + weight * float(chronos_row.get("forecast_lower_kva", chronos_row["forecast_kva"])), 0.0),
                        "forecast_upper_kva": max((1.0 - weight) * base_upper + weight * float(chronos_row["forecast_upper_kva"]), 0.0),
                    }
                predictions["hybrid_chronos_existing"] = hybrid_values
                metadata["hybrid_chronos_existing"] = hybrid_meta
            except Chronos2Unavailable:
                self._chronos.mark_fallback()
        return predictions, metadata

    def _cache_key(
        self,
        rows: list[Mapping[str, object]],
        facility_id: str,
        selected_mode: str,
        include_optional_models: bool,
    ) -> tuple[object, ...]:
        tail = rows[-self.context_history :]
        signature = tuple(
            (
                str(row.get("timestamp", "")),
                round(float(row.get("kva", 0.0)), 6),
                round(float(row.get("kwh", 0.0)), 6),
                round(float(row.get("power_factor", 1.0)), 6),
            )
            for row in tail
        )
        return (str(facility_id), selected_mode, bool(include_optional_models), signature)

    def predict_horizons(
        self,
        records: Iterable[Mapping[str, object]],
        facility_id: str,
        *,
        mode_override: str | None = None,
        include_optional_models: bool = True,
    ) -> dict[str, dict[str, object]]:
        if not self._bundle:
            raise RuntimeError(self._error or "The forecasting model is not ready.")
        rows = list(records)
        if not rows:
            raise ValueError("No readings were supplied for inference.")
        with self._lock:
            selected_mode = self._validate_mode(mode_override) if mode_override is not None else self._active_mode
            cache_key = self._cache_key(rows, str(facility_id), selected_mode, include_optional_models)
            cached = self._prediction_cache.get(cache_key)
            if cached is not None:
                self._prediction_cache.move_to_end(cache_key)
                return deepcopy(cached)
        started = time.perf_counter()
        predictions, model_metadata = self._all_predictions(
            rows,
            str(facility_id),
            include_optional_models=include_optional_models,
        )
        total_ms = (time.perf_counter() - started) * 1000.0
        output: dict[str, dict[str, object]] = {}
        selected_by_horizon = dict((self._ensemble or {}).get("selected_by_horizon", {}))
        chronos_routes = self._chronos.selected_by_horizon if include_optional_models and self._chronos.ready else {}
        risk_configs = dict((self._ensemble or {}).get("risk_configs", {}))
        for name, item in self._bundle["horizons"].items():
            if selected_mode == "automatic":
                chronos_route = dict(chronos_routes.get(name, {}))
                routed = str(chronos_route.get("model", "existing"))
                if routed in {"chronos2", "hybrid_chronos_existing"}:
                    selected_model = routed
                else:
                    selected_model = str(dict(selected_by_horizon.get(name, {})).get("model", "gradient_boosting"))
            else:
                selected_model = selected_mode
            if selected_model not in predictions:
                selected_model = str(dict(selected_by_horizon.get(name, {})).get("model", "gradient_boosting"))
                if selected_model not in predictions:
                    selected_model = "gradient_boosting"
                self._chronos.mark_fallback()
            forecast = float(predictions[selected_model][name])
            direct_metadata = dict(model_metadata.get(selected_model, {}).get(name, {}))
            selected_risk = dict(risk_configs.get(selected_model, {}).get(name, {}))
            if direct_metadata:
                upper = max(float(direct_metadata.get("forecast_upper_kva", forecast)), forecast)
                lower = min(float(direct_metadata.get("forecast_lower_kva", forecast)), forecast)
                margin = max(upper - forecast, 0.0)
                high_threshold = 0.95
                medium_threshold = 0.85
            elif selected_risk:
                margin = max(float(selected_risk.get("uncertainty_margin_kva", 0.0)), 0.0)
                upper = forecast + margin
                lower = max(forecast - margin, 0.0)
                high_threshold = float(selected_risk.get("alert_threshold_ratio", 0.95))
                medium_threshold = max(high_threshold - 0.10, 0.75)
            else:
                uncertainty = item.get("uncertainty", {})
                global_margin = float(uncertainty.get("global_underforecast_margin_kva", 0.0))
                margin = float(uncertainty.get("facility_underforecast_margin_kva", {}).get(str(facility_id), global_margin))
                upper = forecast + margin
                lower = max(forecast - margin, 0.0)
                policy = item.get("risk_policy", {})
                high_threshold = float(policy.get("high_alert_threshold_ratio", 0.95))
                medium_threshold = float(policy.get("medium_alert_threshold_ratio", 0.85))
            output[name] = {
                "minutes": float(item["minutes"]),
                "forecast_kva": max(forecast, 0.0),
                "forecast_lower_kva": max(lower, 0.0),
                "forecast_upper_kva": max(upper, 0.0),
                "uncertainty_margin_kva": margin,
                "blend_alpha": float(item.get("facility_blend_alpha", {}).get(str(facility_id), 1.0)),
                "high_alert_threshold_ratio": high_threshold,
                "medium_alert_threshold_ratio": medium_threshold,
                "selected_model": selected_model,
                "model_predictions": {model: round(float(values[name]), 4) for model, values in predictions.items()},
                "inference_latency_ms": round(total_ms, 4),
            }
        with self._lock:
            self._prediction_cache[cache_key] = deepcopy(output)
            self._prediction_cache.move_to_end(cache_key)
            while len(self._prediction_cache) > self._prediction_cache_limit:
                self._prediction_cache.popitem(last=False)
        return output

    def facility_limit(self, facility_id: str, current_kva: float) -> float:
        if not self._bundle:
            return max(current_kva * 1.1, 1.0)
        configured = self._bundle.get("facility_limits_kva", {}).get(str(facility_id))
        return float(configured) if configured is not None else max(current_kva * 1.1, 1.0)
