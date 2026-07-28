from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping

from src.live.adaptation import AdaptiveForecastCalibrator
from src.live.features import tariff_period
from src.live.forecast_store import ForecastStore
from src.live.model_manager import LiveModelManager
from src.live.explanation import build_evidence_explanation


def risk_from_bounds(
    point_utilization: float,
    upper_utilization: float,
    *,
    medium_threshold: float,
    high_threshold: float,
) -> str:
    del point_utilization
    if upper_utilization >= high_threshold:
        return "high"
    if upper_utilization >= medium_threshold:
        return "medium"
    return "low"


def recommendation(risk: str, tariff: str, lead_minutes: int) -> str:
    if risk == "high" and tariff == "peak":
        return (
            f"Review approved non-critical loads within {lead_minutes} minutes. "
            "Confirm any action only in the SIMBA-EMS dashboard."
        )
    if risk == "high":
        return (
            f"Prepare an operator-reviewed load shift within {lead_minutes} minutes and "
            "verify the facility schedule in the dashboard."
        )
    if risk == "medium" and tariff == "peak":
        return "Prepare a load-coordination action if the conservative forecast continues to rise."
    if tariff == "offpeak":
        return "Use available off-peak capacity for approved deferrable loads."
    return "Continue monitoring. No immediate action is required."


def inference_quality(blend_alpha: float) -> str:
    if blend_alpha >= 0.75:
        return "trained_model"
    if blend_alpha >= 0.25:
        return "model_persistence_blend"
    return "persistence_guard"


class LiveInferenceService:
    def __init__(
        self,
        model: LiveModelManager,
        store: ForecastStore,
        calibrator: AdaptiveForecastCalibrator | None = None,
    ) -> None:
        self.model = model
        self.store = store
        self.calibrator = calibrator

    def refresh(self, meter_records: Iterable[Mapping[str, object]]) -> dict[str, object]:
        grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for record in meter_records:
            grouped[str(record["facility_id"])].append(record)
        produced: list[dict[str, object]] = []
        failures: list[str] = []
        waiting: list[str] = []
        if not self.model.ready:
            return {"generated": 0, "failures": [str(self.model.status().get("error"))]}

        model_status = self.model.status()
        model_metrics = dict(model_status.get("metrics", {}))
        model_name = str(model_status.get("model_name") or "institutional_forecaster")

        for facility_id, records in grouped.items():
            ordered = sorted(records, key=lambda item: str(item["timestamp"]))
            if len(ordered) < self.model.minimum_history:
                waiting.append(
                    f"{facility_id}: {len(ordered)}/{self.model.minimum_history} readings"
                )
                continue
            window = ordered[-self.model.context_history :]
            try:
                latest = window[-1]
                reading_id = str(latest.get("reading_id") or f"{facility_id}-{latest['timestamp']}")
                forecast_id = hashlib.sha256(
                    f"{reading_id}|{model_name}".encode("utf-8")
                ).hexdigest()[:24]
                horizons = self.model.predict_horizons(window, facility_id)
                current_kva = float(latest["kva"])
                limit = self.model.facility_limit(facility_id, current_kva)
                latest_time = datetime.fromisoformat(str(latest["timestamp"]).replace("Z", "+00:00"))
                horizon_rows: dict[str, dict[str, object]] = {}
                risk_order = {"low": 0, "medium": 1, "high": 2}
                highest_risk = "low"
                earliest_high: int | None = None
                earliest_medium: int | None = None
                highest_upper_utilization = 0.0

                for name, item in horizons.items():
                    minutes = int(item["minutes"])
                    base_forecast = float(item["forecast_kva"])
                    base_upper = float(item.get("forecast_upper_kva", base_forecast))
                    adaptive = (
                        self.calibrator.apply(
                            facility=facility_id,
                            horizon=name,
                            forecast_kva=base_forecast,
                            upper_kva=base_upper,
                            limit_kva=limit,
                        )
                        if self.calibrator is not None
                        else {
                            "forecast_kva": base_forecast,
                            "forecast_upper_kva": base_upper,
                            "uncertainty_margin_kva": max(base_upper - base_forecast, 0.0),
                            "base_forecast_kva": base_forecast,
                            "adaptive_correction_kva": 0.0,
                            "adaptive_observations": 0,
                            "adaptive_status": "unavailable",
                        }
                    )
                    forecast_kva = float(adaptive["forecast_kva"])
                    forecast_upper_kva = float(adaptive["forecast_upper_kva"])
                    point_utilization = forecast_kva / max(limit, 1e-9)
                    upper_utilization = forecast_upper_kva / max(limit, 1e-9)
                    risk = risk_from_bounds(
                        point_utilization,
                        upper_utilization,
                        medium_threshold=float(item.get("medium_alert_threshold_ratio", 0.85)),
                        high_threshold=float(item.get("high_alert_threshold_ratio", 0.95)),
                    )
                    if risk_order[risk] > risk_order[highest_risk]:
                        highest_risk = risk
                    if risk == "high" and (earliest_high is None or minutes < earliest_high):
                        earliest_high = minutes
                    if risk == "medium" and (earliest_medium is None or minutes < earliest_medium):
                        earliest_medium = minutes
                    highest_upper_utilization = max(highest_upper_utilization, upper_utilization)
                    blend_alpha = float(item.get("blend_alpha", 1.0))
                    target_time = latest_time + timedelta(minutes=minutes)
                    validation_p95 = float(dict(model_metrics.get(name, {})).get("p95_abs_error_kva", 0.0))
                    horizon_rows[name] = {
                        "minutes": minutes,
                        "target_timestamp": target_time.isoformat(),
                        "forecast_kva": round(forecast_kva, 3),
                        "forecast_upper_kva": round(forecast_upper_kva, 3),
                        "uncertainty_margin_kva": round(float(adaptive["uncertainty_margin_kva"]), 3),
                        "base_forecast_kva": round(float(adaptive["base_forecast_kva"]), 3),
                        "adaptive_correction_kva": round(float(adaptive["adaptive_correction_kva"]), 3),
                        "adaptive_observations": int(adaptive["adaptive_observations"]),
                        "adaptive_status": str(adaptive["adaptive_status"]),
                        "validation_p95_abs_error_kva": validation_p95,
                        "utilization_percent": round(point_utilization * 100, 2),
                        "upper_utilization_percent": round(upper_utilization * 100, 2),
                        "risk": risk,
                        "inference_quality": inference_quality(blend_alpha),
                        "blend_alpha": round(blend_alpha, 2),
                    }

                primary_name = min(horizon_rows, key=lambda key: int(horizon_rows[key]["minutes"]))
                primary = horizon_rows[primary_name]
                primary_minutes = int(primary["minutes"])
                forecast_time = latest_time + timedelta(minutes=primary_minutes)
                period = tariff_period(forecast_time)
                lead_minutes = earliest_high or earliest_medium or primary_minutes
                explanation = build_evidence_explanation(
                    facility=facility_id,
                    recent_kva=[float(row["kva"]) for row in window[-4:]],
                    current_kva=current_kva,
                    forecast_kva=float(primary["forecast_kva"]),
                    upper_kva=float(primary["forecast_upper_kva"]),
                    limit_kva=limit,
                    risk=highest_risk,
                    lead_minutes=lead_minutes,
                    tariff_period=period,
                    recommendation=recommendation(highest_risk, period, lead_minutes),
                    model_predictions=dict(horizons[primary_name].get("model_predictions", {})),
                )
                produced.append(
                    {
                        "forecast_id": forecast_id,
                        "reading_id": reading_id,
                        "facility_id": facility_id,
                        "reading_timestamp": str(latest["timestamp"]),
                        "forecast_timestamp": forecast_time.isoformat(),
                        "primary_horizon": primary_name,
                        "current_kva": round(current_kva, 3),
                        "forecast_kva": primary["forecast_kva"],
                        "forecast_upper_kva": primary["forecast_upper_kva"],
                        "uncertainty_margin_kva": primary["uncertainty_margin_kva"],
                        "facility_limit_kva": round(limit, 3),
                        "utilization_percent": primary["utilization_percent"],
                        "upper_utilization_percent": primary["upper_utilization_percent"],
                        "peak_risk": highest_risk,
                        "risk_lead_minutes": lead_minutes,
                        "highest_horizon_upper_utilization_percent": round(highest_upper_utilization * 100, 2),
                        "tariff_period": period,
                        "recommended_action": recommendation(highest_risk, period, lead_minutes),
                        "explanation": explanation,
                        "forecasts": horizon_rows,
                        "model_source": "validated_institutional_model",
                        "adaptive_calibration": primary.get("adaptive_status"),
                        "operating_mode": "advisory",
                        "approval_channel": "dashboard_only",
                        "generated_utc": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception as exc:
                failures.append(f"{facility_id}: {exc}")
        generated = self.store.append(produced)
        return {
            "generated": generated,
            "candidates": len(produced),
            "waiting": waiting[:20],
            "failures": failures[:20],
            "adaptive_learning": self.calibrator.status() if self.calibrator else {"enabled": False},
        }

    def latest_forecasts(self, limit: int = 50) -> list[dict[str, object]]:
        return self.store.latest(limit)

    def live_alerts(self, limit: int = 50) -> list[dict[str, object]]:
        latest_by_facility: dict[str, dict[str, object]] = {}
        for item in self.store.all():
            latest_by_facility[str(item["facility_id"])] = item
        alerts: list[dict[str, object]] = []
        for item in latest_by_facility.values():
            if item.get("peak_risk") not in {"medium", "high"}:
                continue
            alerts.append(
                {
                    "alert_id": str(item["forecast_id"]),
                    "facility_name": str(item["facility_id"]),
                    "timestamp": str(item["forecast_timestamp"]),
                    "tariff_period": str(item["tariff_period"]),
                    "risk": str(item["peak_risk"]),
                    "risk_lead_minutes": int(item.get("risk_lead_minutes", 30)),
                    "current_kva": float(item["current_kva"]),
                    "forecast_kva": float(item["forecast_kva"]),
                    "forecast_upper_kva": float(item.get("forecast_upper_kva", item["forecast_kva"])),
                    "uncertainty_margin_kva": float(item.get("uncertainty_margin_kva", 0.0)),
                    "facility_limit_kva": float(item["facility_limit_kva"]),
                    "utilization_percent": float(item["utilization_percent"]),
                    "upper_utilization_percent": float(item.get("upper_utilization_percent", item["utilization_percent"])),
                    "planning_reduction_kva": round(
                        max(
                            float(item.get("forecast_upper_kva", item.get("forecast_kva", 0)))
                            - 0.9 * float(item["facility_limit_kva"]),
                            0,
                        ),
                        2,
                    ),
                    "recommended_action": str(item["recommended_action"]),
                    "explanation": item.get("explanation", {}),
                    "source": "validated_demand_forecast",
                    "adaptive_calibration": item.get("adaptive_calibration"),
                    "approval_channel": "dashboard_only",
                }
            )
        alerts.sort(
            key=lambda item: (
                item["risk"] != "high",
                int(item["risk_lead_minutes"]),
                -float(item["upper_utilization_percent"]),
            )
        )
        return alerts[: max(1, min(limit, 200))]
