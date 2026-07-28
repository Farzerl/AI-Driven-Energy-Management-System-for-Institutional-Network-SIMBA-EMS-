from __future__ import annotations

import copy
import math
import statistics
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Iterable, Mapping

from src.live.features import tariff_period
from src.live.model_manager import LiveModelManager
from src.control.gateway import ControlGateway
from src.simulation.profiles import DEFAULT_SCENARIO_ID, FacilityProfile, LoadGroup, SCENARIOS, ScenarioProfile, list_scenarios
from src.simulation.schemas import ControllerMode, SimulationActionRequest

INTERVAL_MINUTES = 30
INTERVAL_HOURS = INTERVAL_MINUTES / 60.0
ENERGY_RATES_USD_PER_KWH = {
    "peak": 0.2173,
    "standard": 0.1150,
    "offpeak": 0.0588,
}
DEMAND_CHARGE_USD_PER_KVA_MONTH = 7.78


def _round(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def _risk(utilization: float) -> str:
    if utilization >= 0.95:
        return "high"
    if utilization >= 0.85:
        return "medium"
    return "low"


def _hour_value(timestamp: datetime) -> float:
    return timestamp.hour + timestamp.minute / 60.0


def _permitted(group: LoadGroup, timestamp: datetime) -> bool:
    hour = _hour_value(timestamp)
    if group.permitted_start_hour <= group.permitted_end_hour:
        return group.permitted_start_hour <= hour < group.permitted_end_hour
    return hour >= group.permitted_start_hour or hour < group.permitted_end_hour


class SimulationEngine:
    """Deterministic software-in-the-loop simulator for the demonstration workflow.

    The simulator separates the forecasting path from the plant model. Forecasts use the
    trained multi-horizon model plus a bounded trend guard. Facility response,
    load constraints, action delay and rebound are then calculated by the simulation.
    No future baseline value is passed into the forecast function.
    """

    def __init__(self, model_manager: LiveModelManager, control_gateway: ControlGateway | None = None) -> None:
        self.model = model_manager
        self.control_gateway = control_gateway or ControlGateway()
        self._lock = RLock()
        self._scenario: ScenarioProfile = SCENARIOS[DEFAULT_SCENARIO_ID]
        self._controller_mode: ControllerMode = "ai_assisted"
        self._session_id = ""
        self._cursor = 0
        self._status = "ready"
        self._actions: list[dict[str, Any]] = []
        self._timeline: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self._forecast_latency_ms: list[float] = []
        self._approval_results: dict[str, dict[str, Any]] = {}
        self._recommendation_records: dict[str, dict[str, Any]] = {}
        self._recommendation_order: list[str] = []
        self._recommendation_revision = 0
        self._state_cache_key: tuple[int, int, int, str, int] | None = None
        self._state_cache: dict[str, Any] | None = None
        self._operational: dict[str, object] = {
            "campus_limit_override_kva": None,
            "facility_limit_overrides_kva": {},
            "critical_floor_overrides_kva": {},
            "risk_medium_ratio": 0.85,
            "risk_high_ratio": 0.95,
            "peak_energy_usd_per_kwh": ENERGY_RATES_USD_PER_KWH["peak"],
            "standard_energy_usd_per_kwh": ENERGY_RATES_USD_PER_KWH["standard"],
            "offpeak_energy_usd_per_kwh": ENERGY_RATES_USD_PER_KWH["offpeak"],
            "demand_charge_usd_per_kva_month": DEMAND_CHARGE_USD_PER_KVA_MONTH,
        }
        self.reset(self._scenario.scenario_id, self._controller_mode)

    @staticmethod
    def scenarios() -> list[dict[str, object]]:
        return list_scenarios()

    def configure(self, operational: Mapping[str, object] | None) -> dict[str, object]:
        """Apply validated runtime guardrails without mutating source scenario data."""
        with self._lock:
            if operational is not None:
                self._operational = {**self._operational, **dict(operational)}
            self._state_cache_key = None
            self._state_cache = None
            return copy.deepcopy(self._operational)

    def _facility_limit(self, profile: FacilityProfile) -> float:
        overrides = dict(self._operational.get("facility_limit_overrides_kva", {}))
        return max(float(overrides.get(profile.facility_id, profile.limit_kva)), 0.001)

    def _critical_floor(self, profile: FacilityProfile) -> float:
        overrides = dict(self._operational.get("critical_floor_overrides_kva", {}))
        return max(float(overrides.get(profile.facility_id, profile.critical_floor_kva)), 0.0)

    def _campus_limit(self) -> float:
        configured = self._operational.get("campus_limit_override_kva")
        return max(float(configured if configured not in (None, "") else self._scenario.campus_limit_kva), 0.001)

    def _risk_level(self, utilization: float) -> str:
        high = float(self._operational.get("risk_high_ratio", 0.95))
        medium = float(self._operational.get("risk_medium_ratio", 0.85))
        if utilization >= high:
            return "high"
        if utilization >= medium:
            return "medium"
        return "low"

    def _energy_rate(self, period: str) -> float:
        key = "offpeak_energy_usd_per_kwh" if period == "offpeak" else f"{period}_energy_usd_per_kwh"
        return max(float(self._operational.get(key, ENERGY_RATES_USD_PER_KWH[period])), 0.0)

    def _demand_charge_rate(self) -> float:
        return max(float(self._operational.get("demand_charge_usd_per_kva_month", DEMAND_CHARGE_USD_PER_KVA_MONTH)), 0.0)

    def reset(self, scenario_id: str, controller_mode: ControllerMode) -> dict[str, Any]:
        with self._lock:
            if scenario_id not in SCENARIOS:
                available = ", ".join(sorted(SCENARIOS))
                raise ValueError(f"Unknown scenario '{scenario_id}'. Available scenarios: {available}.")
            self._scenario = SCENARIOS[scenario_id]
            self._controller_mode = controller_mode
            self._session_id = uuid.uuid4().hex[:16]
            self._cursor = 0
            self._status = "ready"
            self._actions = []
            self._timeline = []
            self._events = []
            self._forecast_latency_ms = []
            self._approval_results = {}
            self._recommendation_records = {}
            self._recommendation_order = []
            self._recommendation_revision = 0
            self._state_cache_key = None
            self._state_cache = None
            self._event(
                "simulation_reset",
                f"Scenario reset to {self._scenario.name} using { {'ai_assisted': 'forecast-assisted', 'simple_rule': 'current-demand rule', 'manual': 'manual operator', 'no_control': 'no control'}[controller_mode] } control.",
                severity="info",
            )
            return self.state()

    def _event(
        self,
        event_type: str,
        message: str,
        *,
        severity: str = "info",
        facility_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._events.append(
            {
                "event_id": uuid.uuid4().hex[:16],
                "event_type": event_type,
                "severity": severity,
                "message": message,
                "facility_id": facility_id,
                "simulation_index": self._cursor,
                "simulation_timestamp": self._timestamp_for(self._cursor).isoformat(),
                "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "details": details or {},
            }
        )


    def _register_recommendations(self, recommendations: list[dict[str, Any]]) -> None:
        current_ids = {str(item.get("recommendation_id", "")) for item in recommendations if item.get("recommendation_id")}
        changed = False
        for record in self._recommendation_records.values():
            is_current = str(record.get("recommendation_id", "")) in current_ids
            execution_available = (
                str(record.get("decision_status", "not_approved")) == "not_approved"
                and self._cursor <= int(record.get("expires_index", self._cursor))
            )
            if record.get("current") != is_current or record.get("execution_available") != execution_available:
                record["current"] = is_current
                record["execution_available"] = execution_available
                changed = True

        for recommendation in recommendations:
            recommendation_id = str(recommendation.get("recommendation_id", ""))
            if not recommendation_id:
                continue
            existing = self._recommendation_records.get(recommendation_id)
            if existing is None:
                record = {
                    **copy.deepcopy(recommendation),
                    "decision_status": "not_approved",
                    "decision_label": "Not approved",
                    "generated_index": self._cursor,
                    "generated_timestamp": self._timestamp_for(self._cursor).isoformat(),
                    "expires_index": min(self._cursor + 3, self.total_steps - 1),
                    "last_seen_index": self._cursor,
                    "current": True,
                    "execution_available": True,
                    "decision": None,
                    "action_ids": [],
                }
                self._recommendation_records[recommendation_id] = record
                self._recommendation_order.append(recommendation_id)
                changed = True
            else:
                before = (
                    int(existing.get("last_seen_index", -1)),
                    bool(existing.get("current", False)),
                    bool(existing.get("execution_available", False)),
                )
                existing.update(copy.deepcopy(recommendation))
                existing["last_seen_index"] = self._cursor
                existing["current"] = True
                existing["execution_available"] = (
                    str(existing.get("decision_status", "not_approved")) == "not_approved"
                    and self._cursor <= int(existing.get("expires_index", self._cursor))
                )
                after = (
                    int(existing.get("last_seen_index", -1)),
                    bool(existing.get("current", False)),
                    bool(existing.get("execution_available", False)),
                )
                changed = changed or before != after

        if len(self._recommendation_order) > 80:
            removable = self._recommendation_order[:-80]
            self._recommendation_order = self._recommendation_order[-80:]
            for recommendation_id in removable:
                self._recommendation_records.pop(recommendation_id, None)
            changed = True
        if changed:
            self._recommendation_revision += 1
            self._state_cache_key = None
            self._state_cache = None

    def recommendation_deck(self, limit: int = 80) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 200))
        items = [
            copy.deepcopy(self._recommendation_records[item_id])
            for item_id in self._recommendation_order[-safe_limit:]
            if item_id in self._recommendation_records
        ]
        counts = {"not_approved": 0, "approved": 0, "disapproved": 0, "acknowledged": 0}
        for item in items:
            status = str(item.get("decision_status", "not_approved"))
            counts[status] = counts.get(status, 0) + 1
        return {"items": items, "counts": counts, "total": len(items)}

    def decide_recommendation(
        self,
        recommendation_id: str,
        decision: str,
        *,
        operator: str,
        note: str = "",
        request_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if decision == "approve":
                return self.apply_recommended_plan(
                    operator=operator,
                    recommendation_ids=[recommendation_id],
                    request_id=request_id,
                    approve_all=False,
                )
            if decision not in {"acknowledge", "disapprove"}:
                raise ValueError("Decision must be approve, acknowledge or disapprove.")
            record = self._recommendation_records.get(str(recommendation_id))
            if record is None:
                raise ValueError("Recommendation was not found in the current approval deck.")
            if str(record.get("decision_status")) != "not_approved":
                return {"updated": 0, "reason": "This recommendation already has an operator decision.", "state": self.state()}
            status = "acknowledged" if decision == "acknowledge" else "disapproved"
            label = "Acknowledged" if status == "acknowledged" else "Disapproved"
            record["decision_status"] = status
            record["decision_label"] = label
            record["execution_available"] = False
            record["decision"] = {
                "operator": operator,
                "note": note,
                "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
            self._recommendation_revision += 1
            self._state_cache_key = None
            self._state_cache = None
            self._event(
                "recommendation_acknowledged" if status == "acknowledged" else "recommendation_disapproved",
                f"{operator} {label.lower()} the recommendation for {record.get('facility_name', 'the facility')}.",
                severity="info" if status == "acknowledged" else "warning",
                facility_id=str(record.get("facility_id", "")) or None,
                details={"recommendation_id": recommendation_id, "decision": status, "note": note},
            )
            return {"updated": 1, "decision": status, "recommendation": copy.deepcopy(record), "state": self.state()}

    def _timestamp_for(self, index: int) -> datetime:
        return self._scenario.start_time + timedelta(minutes=INTERVAL_MINUTES * index)

    def _facility(self, facility_id: str) -> FacilityProfile:
        match = next((item for item in self._scenario.facilities if item.facility_id == facility_id), None)
        if match is None:
            raise ValueError(f"Facility '{facility_id}' is not part of the active scenario.")
        return match

    @staticmethod
    def _load_group(profile: FacilityProfile, load_group_id: str) -> LoadGroup:
        match = next((item for item in profile.load_groups if item.load_group_id == load_group_id), None)
        if match is None:
            raise ValueError(f"Load group '{load_group_id}' does not exist for {profile.name}.")
        return match

    def _scheduled_group_reduction(self, facility_id: str, group_id: str, index: int) -> float:
        return sum(
            float(action["reduction_kva"])
            for action in self._actions
            if action["facility_id"] == facility_id
            and action["load_group"] == group_id
            and int(action["start_index"]) <= index < int(action["end_index"])
            and action["status"] == "approved"
        )

    def _active_reduction(self, facility_id: str, index: int) -> float:
        return sum(
            float(action["reduction_kva"])
            for action in self._actions
            if action["facility_id"] == facility_id
            and int(action["start_index"]) <= index < int(action["end_index"])
            and action["status"] == "approved"
        )

    def _recovery_start_index(self, action: dict[str, Any]) -> int:
        recovery_start = int(action["end_index"])
        while recovery_start < self.total_steps and tariff_period(self._timestamp_for(recovery_start)) == "peak":
            recovery_start += 1
        return recovery_start

    def _desired_rebound_kva(self, facility_id: str, index: int) -> float:
        desired = 0.0
        for action in self._actions:
            if action["facility_id"] != facility_id or action["status"] != "approved":
                continue
            recovery_intervals = int(action.get("rebound_intervals", 0))
            if recovery_intervals <= 0:
                continue
            recovery_start = self._recovery_start_index(action)
            if recovery_start <= index < recovery_start + recovery_intervals:
                action_intervals = int(action["end_index"]) - int(action["start_index"])
                desired += float(action["reduction_kva"]) * action_intervals / recovery_intervals
        return desired

    def _rebound_kva(self, facility_id: str, index: int) -> float:
        desired_rebound = self._desired_rebound_kva(facility_id, index)
        if desired_rebound <= 0:
            return 0.0
        profile = self._facility(facility_id)
        safe_index = max(0, min(index, len(profile.baseline_kva) - 1))
        baseline = float(profile.baseline_kva[safe_index])
        facility_headroom = max(0.90 * self._facility_limit(profile) - baseline, 0.0)

        # Recovery is also constrained by campus headroom. This prevents several
        # independently deferred loads from rebounding into a new campus peak.
        campus_baseline = sum(
            float(item.baseline_kva[max(0, min(index, len(item.baseline_kva) - 1))])
            for item in self._scenario.facilities
        )
        campus_headroom = max(0.90 * self._campus_limit() - campus_baseline, 0.0)
        all_desired = sum(self._desired_rebound_kva(item.facility_id, index) for item in self._scenario.facilities)
        allocated_campus_headroom = campus_headroom * desired_rebound / max(all_desired, 1e-9)
        return min(desired_rebound, facility_headroom, allocated_campus_headroom)

    def _facility_snapshot(self, profile: FacilityProfile, index: int) -> dict[str, Any]:
        safe_index = max(0, min(index, len(profile.baseline_kva) - 1))
        baseline = float(profile.baseline_kva[safe_index])
        requested_reduction = self._active_reduction(profile.facility_id, safe_index)
        rebound = self._rebound_kva(profile.facility_id, safe_index)
        natural_load = baseline + rebound
        critical_floor = self._critical_floor(profile)
        effective_floor = min(critical_floor, natural_load)
        available_reduction = max(natural_load - effective_floor, 0.0)
        actual_reduction = min(requested_reduction, available_reduction)
        controlled = max(effective_floor, natural_load - actual_reduction)
        timestamp = self._timestamp_for(safe_index)
        period = tariff_period(timestamp)
        baseline_kw = baseline * profile.power_factor
        controlled_kw = controlled * profile.power_factor
        return {
            "facility_id": profile.facility_id,
            "facility_name": profile.name,
            "sector": profile.sector,
            "timestamp": timestamp.isoformat(),
            "tariff_period": period,
            "baseline_kva": _round(baseline),
            "controlled_kva": _round(controlled),
            "requested_reduction_kva": _round(requested_reduction),
            "actual_reduction_kva": _round(actual_reduction),
            "rebound_kva": _round(rebound),
            "limit_kva": _round(self._facility_limit(profile)),
            "utilization_percent": _round(controlled / self._facility_limit(profile) * 100, 2),
            "risk": self._risk_level(controlled / self._facility_limit(profile)),
            "power_factor": profile.power_factor,
            "baseline_energy_kwh": _round(baseline_kw * INTERVAL_HOURS),
            "controlled_energy_kwh": _round(controlled_kw * INTERVAL_HOURS),
            "baseline_energy_cost_usd": _round(baseline_kw * INTERVAL_HOURS * self._energy_rate(period), 4),
            "controlled_energy_cost_usd": _round(controlled_kw * INTERVAL_HOURS * self._energy_rate(period), 4),
            "critical_floor_kva": _round(critical_floor),
            "effective_critical_floor_kva": _round(effective_floor),
        }

    def _history_records(self, profile: FacilityProfile, current_snapshot: dict[str, Any]) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        start = self._scenario.start_time - timedelta(minutes=INTERVAL_MINUTES * len(profile.preroll_kva))
        for offset, kva in enumerate(profile.preroll_kva):
            timestamp = start + timedelta(minutes=INTERVAL_MINUTES * offset)
            records.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "facility_id": profile.facility_id,
                    "kva": float(kva),
                    "kwh": float(kva) * profile.power_factor * INTERVAL_HOURS,
                    "active_power_kw": float(kva) * profile.power_factor,
                    "reactive_power_kvar": math.sqrt(max(float(kva) ** 2 - (float(kva) * profile.power_factor) ** 2, 0.0)),
                    "power_factor": profile.power_factor,
                    "facility_name": profile.name,
                    "display_facility_id": profile.facility_id,
                    "source": "simulation_preroll",
                }
            )
        for row in self._timeline:
            facility_row = next(
                item for item in row["facilities"] if item["facility_id"] == profile.facility_id
            )
            records.append(
                {
                    "timestamp": facility_row["timestamp"],
                    "facility_id": profile.facility_id,
                    "kva": float(facility_row["controlled_kva"]) + float(facility_row["actual_reduction_kva"]) - float(facility_row["rebound_kva"]),
                    "kwh": (float(facility_row["controlled_kva"]) + float(facility_row["actual_reduction_kva"]) - float(facility_row["rebound_kva"])) * profile.power_factor * INTERVAL_HOURS,
                    "active_power_kw": (float(facility_row["controlled_kva"]) + float(facility_row["actual_reduction_kva"]) - float(facility_row["rebound_kva"])) * profile.power_factor,
                    "reactive_power_kvar": math.sqrt(max((float(facility_row["controlled_kva"]) + float(facility_row["actual_reduction_kva"]) - float(facility_row["rebound_kva"])) ** 2 - ((float(facility_row["controlled_kva"]) + float(facility_row["actual_reduction_kva"]) - float(facility_row["rebound_kva"])) * profile.power_factor) ** 2, 0.0)),
                    "power_factor": profile.power_factor,
                    "facility_name": profile.name,
                    "display_facility_id": profile.facility_id,
                    "source": "simulation_observation",
                }
            )
        if not self._timeline or self._timeline[-1]["index"] != self._cursor:
            records.append(
                {
                    "timestamp": current_snapshot["timestamp"],
                    "facility_id": profile.facility_id,
                    "kva": float(current_snapshot["controlled_kva"]) + float(current_snapshot["actual_reduction_kva"]) - float(current_snapshot["rebound_kva"]),
                    "kwh": (float(current_snapshot["controlled_kva"]) + float(current_snapshot["actual_reduction_kva"]) - float(current_snapshot["rebound_kva"])) * profile.power_factor * INTERVAL_HOURS,
                    "active_power_kw": (float(current_snapshot["controlled_kva"]) + float(current_snapshot["actual_reduction_kva"]) - float(current_snapshot["rebound_kva"])) * profile.power_factor,
                    "reactive_power_kvar": math.sqrt(max((float(current_snapshot["controlled_kva"]) + float(current_snapshot["actual_reduction_kva"]) - float(current_snapshot["rebound_kva"])) ** 2 - ((float(current_snapshot["controlled_kva"]) + float(current_snapshot["actual_reduction_kva"]) - float(current_snapshot["rebound_kva"])) * profile.power_factor) ** 2, 0.0)),
                    "power_factor": profile.power_factor,
                    "facility_name": profile.name,
                    "display_facility_id": profile.facility_id,
                    "source": "simulation_current",
                }
            )
        return records[-max(self.model.minimum_history, 336) :]

    def power_quality_history(self) -> list[dict[str, object]]:
        """Return isolated meter-like history for the power-quality forecaster.

        Replay values are marked as simulation sources and are never written into
        production training or adaptive-learning evidence. Model aliases preserve
        compatibility with the facility names used during local training.
        """
        with self._lock:
            output: list[dict[str, object]] = []
            for profile in self._scenario.facilities:
                current = self._facility_snapshot(profile, self._cursor)
                for row in self._history_records(profile, current):
                    output.append({**row, "facility_id": profile.model_alias})
            return output

    def _forecast_facility(self, profile: FacilityProfile, current_snapshot: dict[str, Any]) -> dict[str, Any]:
        records = self._history_records(profile, current_snapshot)
        kva_values = [float(row["kva"]) for row in records]
        current = kva_values[-1]
        lag_1 = kva_values[-2] if len(kva_values) >= 2 else current
        lag_2 = kva_values[-3] if len(kva_values) >= 3 else lag_1
        trend_forecast = max(current + 0.72 * (current - lag_1) + 0.12 * (lag_1 - lag_2), 0.0)
        method = "bounded_persistence_trend"
        source = "operational_fallback"
        latency_ms = 0.0
        horizon_rows: dict[str, dict[str, Any]] = {
            "30_minutes": {
                "minutes": 30,
                "forecast_kva": trend_forecast,
                "forecast_upper_kva": trend_forecast,
                "uncertainty_margin_kva": 0.0,
            }
        }

        if self.model.ready:
            try:
                model_limit = self.model.facility_limit(profile.model_alias, max(current, 1.0))
                facility_limit = self._facility_limit(profile)
                scale = facility_limit / max(model_limit, 1e-9)
                model_records: list[dict[str, object]] = []
                for row in records:
                    model_records.append(
                        {
                            **row,
                            "facility_id": profile.model_alias,
                            "kva": float(row["kva"]) / scale,
                            "kwh": float(row["kwh"]) / scale,
                            "kwh_is_measured": True,
                        }
                    )
                started = time.perf_counter()
                predicted = self.model.predict_horizons(model_records, profile.model_alias)
                latency_ms = (time.perf_counter() - started) * 1000.0
                self._forecast_latency_ms.append(latency_ms)
                converted: dict[str, dict[str, Any]] = {}
                guard_ceiling = max(facility_limit * 1.8, current * 2.0, 5.0)
                for name, row in predicted.items():
                    point = float(row["forecast_kva"]) * scale
                    upper = float(row.get("forecast_upper_kva", row["forecast_kva"])) * scale
                    if not math.isfinite(point) or point < 0 or point > guard_ceiling:
                        raise ValueError(f"{name} forecast {point:.2f} kVA is outside the operational guard.")
                    converted[name] = {
                        "minutes": int(row["minutes"]),
                        "forecast_kva": point,
                        "forecast_upper_kva": max(upper, point),
                        "uncertainty_margin_kva": max(upper - point, 0.0),
                        "blend_alpha": float(row.get("blend_alpha", 1.0)),
                        "selected_model": str(row.get("selected_model", "gradient_boosting")),
                        "model_predictions": {
                            key: float(value) * scale
                            for key, value in dict(row.get("model_predictions", {})).items()
                        },
                        "inference_latency_ms": float(row.get("inference_latency_ms", latency_ms)),
                    }
                horizon_rows = converted
                method = "validated_multi_horizon_model"
                source = "validated_institutional_model"
            except Exception as exc:  # pragma: no cover - operational fallback is explicitly tested separately
                self._event(
                    "forecast_fallback",
                    f"Model forecast failed for {profile.name}; bounded trend fallback used.",
                    severity="warning",
                    facility_id=profile.facility_id,
                    details={"error": str(exc)},
                )

        primary_name = min(horizon_rows, key=lambda name: int(horizon_rows[name]["minutes"]))
        primary = horizon_rows[primary_name]
        raw_forecast = float(primary["forecast_kva"])
        next_timestamp = self._timestamp_for(min(self._cursor + 1, len(profile.baseline_kva) - 1))
        facility_limit = self._facility_limit(profile)
        utilization = raw_forecast / facility_limit
        return {
            "facility_id": profile.facility_id,
            "facility_name": profile.name,
            "model_alias": profile.model_alias,
            "forecast_timestamp": next_timestamp.isoformat(),
            "forecast_kva": _round(raw_forecast),
            "forecast_upper_kva": _round(float(primary.get("forecast_upper_kva", raw_forecast))),
            "limit_kva": _round(facility_limit),
            "utilization_percent": _round(utilization * 100, 2),
            "risk": self._risk_level(max(raw_forecast, float(primary.get("forecast_upper_kva", raw_forecast))) / facility_limit),
            "method": method,
            "model_source": source,
            "inference_latency_ms": _round(latency_ms, 4),
            "horizons": {
                name: {
                    "minutes": int(row["minutes"]),
                    "forecast_kva": _round(float(row["forecast_kva"])),
                    "forecast_upper_kva": _round(float(row.get("forecast_upper_kva", row["forecast_kva"]))),
                    "uncertainty_margin_kva": _round(float(row.get("uncertainty_margin_kva", 0.0))),
                    "selected_model": str(row.get("selected_model", "gradient_boosting")),
                    "model_predictions": {
                        key: _round(float(value))
                        for key, value in dict(row.get("model_predictions", {})).items()
                    },
                    "inference_latency_ms": _round(float(row.get("inference_latency_ms", latency_ms)), 4),
                }
                for name, row in horizon_rows.items()
            },
        }

    def _available_groups(self, profile: FacilityProfile, next_index: int) -> list[LoadGroup]:
        timestamp = self._timestamp_for(next_index)
        output: list[LoadGroup] = []
        for group in profile.load_groups:
            if group.classification == "critical" or not _permitted(group, timestamp):
                continue
            already_scheduled = self._scheduled_group_reduction(
                profile.facility_id, group.load_group_id, next_index
            )
            in_cooldown = any(
                action["facility_id"] == profile.facility_id
                and action["load_group"] == group.load_group_id
                and action["status"] == "approved"
                and next_index < self._recovery_start_index(action) + int(action.get("rebound_intervals", 0))
                for action in self._actions
            )
            if in_cooldown or already_scheduled >= group.rated_kva * (1.0 - group.minimum_service_fraction) - 1e-6:
                continue
            output.append(group)
        return sorted(output, key=lambda item: (item.priority, item.name))

    def _anomaly_findings(self, facilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return conservative after-hours deviation findings for operator escalation.

        This detector uses only preceding reference values and the current observation.
        It does not auto-control a load and is kept separate from peak-demand control.
        """
        findings: list[dict[str, Any]] = []
        current_timestamp = self._timestamp_for(self._cursor)
        hour = _hour_value(current_timestamp)
        after_hours = hour >= 20.0 or hour < 5.0
        if not after_hours:
            return findings
        by_id = {item["facility_id"]: item for item in facilities}
        for profile in self._scenario.facilities:
            current = by_id[profile.facility_id]
            reference = max(statistics.median(profile.preroll_kva), 1.0)
            observed = float(current["controlled_kva"])
            ratio = observed / reference
            minimum_deviation_kva = max(3.0, 0.08 * self._facility_limit(profile))
            if ratio < 1.8 or observed - reference < minimum_deviation_kva:
                continue
            findings.append(
                {
                    "facility_id": profile.facility_id,
                    "facility_name": profile.name,
                    "timestamp": current_timestamp.isoformat(),
                    "reference_kva": _round(reference),
                    "observed_kva": _round(observed),
                    "deviation_percent": _round((ratio - 1.0) * 100.0, 1),
                    "severity": "high" if ratio >= 2.2 else "medium",
                    "detector": "after_hours_reference_deviation",
                    "recommended_action": (
                        "Investigate equipment state and meter validity. Do not automatically interrupt protected research or security loads."
                    ),
                    "control_blocked": True,
                }
            )
        return findings

    def _recommended_plan(
        self,
        facility: FacilityProfile,
        required_reduction_kva: float,
        next_index: int,
    ) -> list[dict[str, Any]]:
        remaining = max(required_reduction_kva, 0.0)
        plan: list[dict[str, Any]] = []
        for group in self._available_groups(facility, next_index):
            maximum = group.rated_kva * (1.0 - group.minimum_service_fraction)
            already = self._scheduled_group_reduction(
                facility.facility_id, group.load_group_id, next_index
            )
            available = max(maximum - already, 0.0)
            if available <= 0:
                continue
            reduction = min(available, remaining)
            if reduction <= 0:
                break
            plan.append(
                {
                    "facility_id": facility.facility_id,
                    "facility_name": facility.name,
                    "load_group": group.load_group_id,
                    "load_group_name": group.name,
                    "classification": group.classification,
                    "action": "defer_load" if group.classification == "deferrable" else "shed_load",
                    "reduction_kva": _round(reduction),
                    "duration_minutes": min(group.max_duration_minutes, 150 if group.classification == "deferrable" else 60),
                    "starts_at": self._timestamp_for(next_index).isoformat(),
                    "maximum_available_kva": _round(available),
                }
            )
            remaining -= reduction
        return plan

    def _recommendation_set(
        self,
        facilities: list[dict[str, Any]],
        forecasts: list[dict[str, Any]],
        campus_forecast_kva: float,
    ) -> dict[str, Any]:
        if self._status == "completed" or self._cursor >= self.total_steps - 1:
            primary = {
                "available": False,
                "controller_mode": self._controller_mode,
                "reason": "The scenario is complete.",
                "actions": [],
            }
            return {"primary": primary, "items": []}
        if self._controller_mode in {"manual", "no_control"}:
            primary = {
                "available": False,
                "controller_mode": self._controller_mode,
                "reason": "Automatic recommendations are disabled for this controller mode.",
                "actions": [],
            }
            return {"primary": primary, "items": []}

        anomalies = self._anomaly_findings(facilities) if self._controller_mode == "ai_assisted" else []
        if anomalies:
            finding = max(anomalies, key=lambda item: float(item["deviation_percent"]))
            primary = {
                "available": False,
                "escalation": True,
                "recommendation_type": "investigation",
                "controller_mode": self._controller_mode,
                "facility_id": finding["facility_id"],
                "facility_name": finding["facility_name"],
                "reason": (
                    f"{finding['facility_name']} is {finding['deviation_percent']:.1f}% above its preceding after-hours reference."
                ),
                "recommended_action": finding["recommended_action"],
                "actions": [],
                "safety_boundary": "Protected research and security loads are escalated for investigation, not automatically shed.",
            }
            return {"primary": primary, "items": []}

        current_by_id = {item["facility_id"]: item for item in facilities}
        forecast_by_id = {item["facility_id"]: item for item in forecasts}
        candidates: list[dict[str, Any]] = []
        campus_current_kva = sum(float(item["controlled_kva"]) for item in facilities)

        if self._controller_mode == "ai_assisted":
            campus_horizon_totals: dict[int, float] = {}
            for minutes, key in ((30, "30_minutes"), (120, "2_hours")):
                campus_horizon_totals[minutes] = sum(
                    float(item["horizons"].get(key, {}).get("forecast_upper_kva", item["forecast_upper_kva"]))
                    for item in forecasts
                )
            campus_basis_kva = max(campus_horizon_totals.values())
        else:
            campus_basis_kva = campus_current_kva

        campus_target_ratio = 0.90 if self._controller_mode == "ai_assisted" else 0.95
        campus_excess = max(campus_basis_kva - campus_target_ratio * self._campus_limit(), 0.0)
        minimum_meaningful_reduction = max(1.0, 0.001 * self._campus_limit())

        for profile in self._scenario.facilities:
            current = current_by_id[profile.facility_id]
            forecast = forecast_by_id[profile.facility_id]
            if self._controller_mode == "ai_assisted":
                horizon_candidates: list[tuple[int, float]] = []
                for name in ("30_minutes", "2_hours"):
                    row = forecast["horizons"].get(name)
                    if row:
                        horizon_candidates.append(
                            (int(row["minutes"]), float(row.get("forecast_upper_kva", row["forecast_kva"])))
                        )
                lead_minutes, risk_basis = (
                    max(horizon_candidates, key=lambda item: item[1])
                    if horizon_candidates
                    else (30, float(forecast["forecast_upper_kva"]))
                )
                source = "multi_horizon_forecast"
            else:
                lead_minutes, risk_basis = 0, float(current["controlled_kva"])
                source = "current_demand_threshold"
            facility_target_ratio = 0.88 if self._controller_mode == "ai_assisted" else 0.92
            trigger_ratio = 0.95 if self._controller_mode == "ai_assisted" else 0.98
            facility_required = max(risk_basis - facility_target_ratio * self._facility_limit(profile), 0.0)
            campus_share = campus_excess * max(risk_basis, 0.0) / max(campus_basis_kva, 1e-9)
            required = max(facility_required, campus_share)
            trigger = risk_basis / self._facility_limit(profile) >= trigger_ratio or campus_excess > 0
            if not trigger or required < minimum_meaningful_reduction:
                continue
            candidates.append(
                {
                    "profile": profile,
                    "required": required,
                    "risk_basis": risk_basis,
                    "utilization": risk_basis / self._facility_limit(profile),
                    "source": source,
                    "lead_minutes": lead_minutes,
                }
            )

        next_index = min(self._cursor + 1, self.total_steps - 1)
        recommendations: list[dict[str, Any]] = []
        for ranked in sorted(
            candidates,
            key=lambda item: (item["required"], item["risk_basis"], item["utilization"]),
            reverse=True,
        ):
            profile = ranked["profile"]
            plan = self._recommended_plan(profile, float(ranked["required"]), next_index)
            if not plan:
                continue
            planned = sum(float(item["reduction_kva"]) for item in plan)
            current = current_by_id[profile.facility_id]
            forecast = forecast_by_id[profile.facility_id]
            lead_time = int(ranked.get("lead_minutes", 30)) if self._controller_mode == "ai_assisted" else 0
            recommendation_id = f"{self._session_id}:{self._cursor}:{profile.facility_id}"
            recommendations.append(
                {
                    "recommendation_id": recommendation_id,
                    "available": True,
                    "controller_mode": self._controller_mode,
                    "source": ranked["source"],
                    "facility_id": profile.facility_id,
                    "facility_name": profile.name,
                    "current_kva": current["controlled_kva"],
                    "forecast_kva": forecast["forecast_kva"],
                    "forecast_upper_kva": forecast["forecast_upper_kva"],
                    "facility_limit_kva": _round(self._facility_limit(profile)),
                    "required_reduction_kva": _round(float(ranked["required"])),
                    "planned_reduction_kva": _round(planned),
                    "lead_time_minutes": lead_time,
                    "reason": (
                        f"{profile.name} is projected at {ranked['utilization'] * 100:.1f}% of its limit within {lead_time} minutes."
                        if self._controller_mode == "ai_assisted"
                        else f"{profile.name} has reached {current['utilization_percent']:.1f}% of its current-demand limit."
                    ),
                    "actions": plan,
                    "safety_boundary": "Only configured deferrable or sheddable groups are proposed. Critical groups are never selectable.",
                }
            )
            if len(recommendations) >= 6:
                break

        if recommendations:
            primary = copy.deepcopy(recommendations[0])
            primary["queue_count"] = len(recommendations)
            primary["total_planned_reduction_kva"] = _round(
                sum(float(item["planned_reduction_kva"]) for item in recommendations)
            )
            return {"primary": primary, "items": recommendations}

        reason = (
            "Risk is elevated, but no approved non-critical load remains available in the permitted operating window."
            if candidates
            else "No facility or campus threshold requires an operator action at this interval."
        )
        primary = {
            "available": False,
            "controller_mode": self._controller_mode,
            "reason": reason,
            "actions": [],
        }
        return {"primary": primary, "items": []}

    def _recommendation(
        self,
        facilities: list[dict[str, Any]],
        forecasts: list[dict[str, Any]],
        campus_forecast_kva: float,
    ) -> dict[str, Any]:
        return self._recommendation_set(facilities, forecasts, campus_forecast_kva)["primary"]

    @property
    def total_steps(self) -> int:
        return len(self._scenario.facilities[0].baseline_kva)

    def state(self) -> dict[str, Any]:
        with self._lock:
            cache_key = (self._cursor, len(self._actions), len(self._timeline), self._status, self._recommendation_revision)
            if self._state_cache_key == cache_key and self._state_cache is not None:
                return copy.deepcopy(self._state_cache)
            facilities = [self._facility_snapshot(profile, self._cursor) for profile in self._scenario.facilities]
            batch_started = time.perf_counter()
            forecasts = [self._forecast_facility(profile, current) for profile, current in zip(self._scenario.facilities, facilities)]
            batch_latency_ms = (time.perf_counter() - batch_started) * 1000.0
            model_forecast_count = sum(1 for item in forecasts if item.get("model_source") == "validated_institutional_model")
            campus_baseline = sum(float(item["baseline_kva"]) for item in facilities)
            campus_controlled = sum(float(item["controlled_kva"]) for item in facilities)
            campus_forecast = sum(float(item["forecast_kva"]) for item in forecasts)
            anomalies = self._anomaly_findings(facilities)
            recommendation_set = self._recommendation_set(facilities, forecasts, campus_forecast)
            recommendation = recommendation_set["primary"]
            recommendations = recommendation_set["items"]
            self._register_recommendations(recommendations)
            current_timestamp = self._timestamp_for(self._cursor)
            period = tariff_period(current_timestamp)
            chart_start = max(0, self._cursor - 15)
            timeline_by_index = {int(row["index"]): row for row in self._timeline}
            chart_timeline: list[dict[str, Any]] = []
            for chart_index in range(chart_start, self._cursor + 1):
                history_facilities = [
                    self._facility_snapshot(profile, chart_index)
                    for profile in self._scenario.facilities
                ]
                recorded = timeline_by_index.get(chart_index, {})
                recorded_campus = dict(recorded.get("campus", {})) if isinstance(recorded, dict) else {}
                chart_timeline.append(
                    {
                        "index": chart_index,
                        "timestamp": self._timestamp_for(chart_index).isoformat(),
                        "campus": {
                            "baseline_kva": _round(sum(float(item["baseline_kva"]) for item in history_facilities)),
                            "controlled_kva": _round(sum(float(item["controlled_kva"]) for item in history_facilities)),
                            "forecast_kva": recorded_campus.get("forecast_kva"),
                            "limit_kva": _round(self._campus_limit()),
                        },
                    }
                )
            if chart_timeline:
                chart_timeline[-1]["campus"]["forecast_kva"] = _round(campus_forecast)
            result = {
                "session_id": self._session_id,
                "status": self._status,
                "scenario": {
                    "scenario_id": self._scenario.scenario_id,
                    "name": self._scenario.name,
                    "description": self._scenario.description,
                    "demonstration_goal": self._scenario.demonstration_goal,
                    "facility_count": len(self._scenario.facilities),
                    "observed_peak_kva": self._scenario.observed_peak_kva,
                    "planning_limit_basis": self._scenario.planning_limit_basis,
                    "source": self._scenario.source,
                    "configured_campus_limit_kva": _round(self._campus_limit()),
                },
                "controller_mode": self._controller_mode,
                "cursor": self._cursor,
                "total_steps": self.total_steps,
                "progress_percent": _round((self._cursor / max(self.total_steps - 1, 1)) * 100, 1),
                "current_timestamp": current_timestamp.isoformat(),
                "tariff_period": period,
                "campus": {
                    "baseline_kva": _round(campus_baseline),
                    "controlled_kva": _round(campus_controlled),
                    "forecast_kva": _round(campus_forecast),
                    "limit_kva": _round(self._campus_limit()),
                    "current_utilization_percent": _round(campus_controlled / self._campus_limit() * 100, 2),
                    "forecast_utilization_percent": _round(campus_forecast / self._campus_limit() * 100, 2),
                    "risk": self._risk_level(max(campus_controlled, campus_forecast) / self._campus_limit()),
                },
                "facilities": facilities,
                "forecasts": forecasts,
                "recommendation": recommendation,
                "recommendations": recommendations,
                "anomalies": anomalies,
                "metrics": self.metrics(),
                "active_actions": self._active_actions_for_state(),
                "action_history": self.action_history(100),
                "approval_deck": self.recommendation_deck(80),
                "control_gateway": self.control_gateway.status(),
                "model": {
                    **self.model.status(),
                    "simulation_forecast_guard": "range validation with explicit fallback",
                    "latest_batch_inference_latency_ms": _round(batch_latency_ms, 4),
                    "mean_facility_inference_latency_ms": _round(
                        sum(float(item["inference_latency_ms"]) for item in forecasts) / max(len(forecasts), 1), 4
                    ),
                    "model_forecast_count": model_forecast_count,
                    "fallback_forecast_count": len(forecasts) - model_forecast_count,
                    "operational_guardrails": copy.deepcopy(self._operational),
                },
                "timeline": copy.deepcopy(self._timeline),
                "chart_timeline": chart_timeline,
            }
            self._state_cache_key = (self._cursor, len(self._actions), len(self._timeline), self._status, self._recommendation_revision)
            self._state_cache = result
            return copy.deepcopy(result)

    def _active_actions_for_state(self) -> list[dict[str, Any]]:
        output = []
        for action in self._actions:
            active = int(action["start_index"]) <= self._cursor < int(action["end_index"])
            pending = self._cursor < int(action["start_index"])
            if active or pending:
                output.append({**copy.deepcopy(action), "phase": "active" if active else "scheduled"})
        return output

    def apply_action(self, request: SimulationActionRequest, *, include_state: bool = True) -> dict[str, Any]:
        with self._lock:
            profile = self._facility(request.facility_id)
            group = self._load_group(profile, request.load_group)
            if group.classification == "critical":
                raise ValueError("Critical loads cannot be deferred or shed by the simulator.")
            if request.action == "defer_load" and group.classification != "deferrable":
                raise ValueError("defer_load is valid only for a deferrable load group.")
            if request.action == "shed_load" and group.classification != "sheddable":
                raise ValueError("shed_load is valid only for a sheddable load group.")
            if request.duration_minutes > group.max_duration_minutes:
                raise ValueError(
                    f"Requested duration exceeds the {group.max_duration_minutes}-minute limit for {group.name}."
                )
            next_index = min(self._cursor + 1, self.total_steps - 1)
            next_timestamp = self._timestamp_for(next_index)
            if not _permitted(group, next_timestamp):
                raise ValueError(f"{group.name} is outside its permitted operating window at {next_timestamp.isoformat()}.")
            maximum = group.rated_kva * (1.0 - group.minimum_service_fraction)
            already = self._scheduled_group_reduction(profile.facility_id, group.load_group_id, next_index)
            available = max(maximum - already, 0.0)
            if request.reduction_kva > available + 1e-6:
                raise ValueError(
                    f"Requested reduction exceeds the {available:.1f} kVA currently available from {group.name}."
                )
            intervals = request.duration_minutes // INTERVAL_MINUTES
            stored = {
                "action_id": uuid.uuid4().hex[:16],
                "facility_id": profile.facility_id,
                "facility_name": profile.name,
                "action": request.action,
                "load_group": group.load_group_id,
                "load_group_name": group.name,
                "classification": group.classification,
                "reduction_kva": _round(request.reduction_kva),
                "duration_minutes": request.duration_minutes,
                "start_index": next_index,
                "end_index": min(next_index + intervals, self.total_steps),
                "starts_at": next_timestamp.isoformat(),
                "ends_at": self._timestamp_for(min(next_index + intervals, self.total_steps - 1)).isoformat(),
                "rebound_intervals": group.rebound_intervals if request.action == "defer_load" else 0,
                "approved_by_operator": request.approved_by_operator,
                "operator": request.operator,
                "note": request.note,
                "status": "approved" if request.approved_by_operator else "rejected",
                "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
            if request.approved_by_operator:
                stored["control_command"] = self.control_gateway.dispatch(stored)
            else:
                stored["control_command"] = {
                    "status": "not_dispatched",
                    "transport": self.control_gateway.status().get("mode", "simulation"),
                    "detail": "The proposed action was not approved by the operator.",
                }
            self._actions.append(stored)
            if request.approved_by_operator:
                self._event(
                    "operator_action_approved",
                    f"{request.operator} approved {request.reduction_kva:.1f} kVA from {group.name} for {request.duration_minutes} minutes.",
                    severity="success",
                    facility_id=profile.facility_id,
                    details=stored,
                )
            else:
                self._event(
                    "operator_action_rejected",
                    f"{request.operator} rejected the proposed action for {group.name}.",
                    severity="warning",
                    facility_id=profile.facility_id,
                    details=stored,
                )
            result: dict[str, Any] = {"action": copy.deepcopy(stored)}
            if include_state:
                result["state"] = self.state()
            return result

    def apply_recommended_plan(
        self,
        *,
        operator: str = "demo-operator",
        recommendation_ids: Iterable[str] | None = None,
        request_id: str | None = None,
        approve_all: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            requested_ids = tuple(sorted(str(item) for item in (recommendation_ids or []) if str(item)))
            fingerprint = f"{self._session_id}|{self._cursor}|{operator}|{'|'.join(requested_ids)}"
            if request_id:
                existing = self._approval_results.get(request_id)
                if existing is not None:
                    if existing.get("fingerprint") != fingerprint:
                        raise ValueError("The approval request ID was already used with a different payload.")
                    return copy.deepcopy(existing["result"])

            current = self.state()
            current_recommendations = list(current.get("recommendations", []))
            if not current_recommendations and current["recommendation"].get("available"):
                current_recommendations = [current["recommendation"]]

            if requested_ids:
                selected: list[dict[str, Any]] = []
                missing: list[str] = []
                for recommendation_id in requested_ids:
                    record = self._recommendation_records.get(recommendation_id)
                    if record is None:
                        missing.append(recommendation_id)
                        continue
                    if str(record.get("decision_status", "not_approved")) != "not_approved":
                        raise ValueError("One or more selected recommendations already have an operator decision.")
                    if self._cursor > int(record.get("expires_index", self._cursor)):
                        raise ValueError("This recommendation is retained for audit but has expired. Acknowledge or disapprove it and review the latest card.")
                    selected.append(copy.deepcopy(record))
                if missing:
                    raise ValueError("One or more recommendations were not found in the approval deck.")
            else:
                selected = current_recommendations if approve_all else current_recommendations[:1]

            if not selected:
                result = {
                    "applied": 0,
                    "reason": current["recommendation"].get("reason", "No recommendation is available."),
                    "actions": [],
                    "skipped": [],
                    "state": current,
                }
                if request_id:
                    self._approval_results[request_id] = {"fingerprint": fingerprint, "result": copy.deepcopy(result)}
                return result

            applied: list[dict[str, Any]] = []
            skipped: list[dict[str, Any]] = []
            next_index = min(self._cursor + 1, self.total_steps - 1)
            approved_ids: set[str] = set()
            for recommendation in selected:
                recommendation_id = str(recommendation.get("recommendation_id", ""))
                recommendation_actions: list[dict[str, Any]] = []
                for action in recommendation.get("actions", []):
                    profile = self._facility(str(action["facility_id"]))
                    group = self._load_group(profile, str(action["load_group"]))
                    maximum = group.rated_kva * (1.0 - group.minimum_service_fraction)
                    already = self._scheduled_group_reduction(profile.facility_id, group.load_group_id, next_index)
                    available = max(maximum - already, 0.0)
                    requested = min(float(action["reduction_kva"]), available)
                    safe_reduction = math.floor((requested + 1e-9) * 1000.0) / 1000.0
                    if safe_reduction <= 0.0:
                        skipped.append({
                            "recommendation_id": recommendation_id,
                            "facility_id": profile.facility_id,
                            "load_group": group.load_group_id,
                            "reason": "No approved flexibility remains for the next interval.",
                        })
                        continue
                    try:
                        action_result = self.apply_action(
                            SimulationActionRequest(
                                facility_id=profile.facility_id,
                                action=action["action"],
                                load_group=group.load_group_id,
                                reduction_kva=safe_reduction,
                                duration_minutes=int(action["duration_minutes"]),
                                approved_by_operator=True,
                                operator=operator,
                                note=(
                                    "Approved from the dashboard approval deck. "
                                    f"Recommendation {recommendation_id or 'current'}."
                                ),
                            ),
                            include_state=False,
                        )
                        applied.append(action_result["action"])
                        recommendation_actions.append(action_result["action"])
                    except (ValueError, RuntimeError) as exc:
                        skipped.append({
                            "recommendation_id": recommendation_id,
                            "facility_id": profile.facility_id,
                            "load_group": group.load_group_id,
                            "reason": str(exc),
                        })
                if recommendation_actions and recommendation_id:
                    approved_ids.add(recommendation_id)
                    record = self._recommendation_records.get(recommendation_id)
                    if record is not None:
                        record["decision_status"] = "approved"
                        record["decision_label"] = "Approved"
                        record["execution_available"] = False
                        record["action_ids"] = [str(item.get("action_id", "")) for item in recommendation_actions]
                        record["decision"] = {
                            "operator": operator,
                            "note": "Approved from the dashboard approval deck.",
                            "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                        }

            if approved_ids:
                self._recommendation_revision += 1
                self._state_cache_key = None
                self._state_cache = None
            result = {
                "applied": len(applied),
                "approved_recommendations": len(approved_ids),
                "actions": applied,
                "skipped": skipped,
                "reason": None if applied else "No selected action remained valid at approval time.",
                "state": self.state(),
            }
            if request_id:
                self._approval_results[request_id] = {"fingerprint": fingerprint, "result": copy.deepcopy(result)}
            return result

    def step(self, count: int = 1) -> dict[str, Any]:
        with self._lock:
            advanced = 0
            for _ in range(count):
                if self._status == "completed":
                    break
                snapshot = self.state()
                row = {
                    "index": self._cursor,
                    "timestamp": snapshot["current_timestamp"],
                    "tariff_period": snapshot["tariff_period"],
                    "campus": copy.deepcopy(snapshot["campus"]),
                    "facilities": copy.deepcopy(snapshot["facilities"]),
                    "forecast": copy.deepcopy(snapshot["forecasts"]),
                    "recommendation": copy.deepcopy(snapshot["recommendation"]),
                    "anomalies": copy.deepcopy(snapshot.get("anomalies", [])),
                    "actions_active": copy.deepcopy(snapshot["active_actions"]),
                }
                if not self._timeline or self._timeline[-1]["index"] != self._cursor:
                    self._timeline.append(row)
                for finding in row.get("anomalies", []):
                    already_recorded = any(
                        event["event_type"] == "anomaly_escalation"
                        and event["facility_id"] == finding["facility_id"]
                        and int(event["simulation_index"]) == self._cursor
                        for event in self._events
                    )
                    if not already_recorded:
                        self._event(
                            "anomaly_escalation",
                            f"After-hours abnormal demand detected at {finding['facility_name']}; operator investigation required.",
                            severity="warning",
                            facility_id=str(finding["facility_id"]),
                            details=finding,
                        )
                if any(
                    float(item["controlled_kva"]) < float(item["effective_critical_floor_kva"]) - 1e-6
                    for item in row["facilities"]
                ):
                    self._event(
                        "critical_constraint_violation",
                        "A critical-load floor was violated.",
                        severity="critical",
                    )
                if self._cursor >= self.total_steps - 1:
                    self._status = "completed"
                    self._event(
                        "simulation_completed",
                        f"Scenario completed with {len(self._actions)} operator action records.",
                        severity="success",
                    )
                    advanced += 1
                    break
                self._cursor += 1
                self._status = "running"
                advanced += 1
            return {"advanced": advanced, "state": self.state()}

    def run_to_end(self, *, auto_approve: bool = False) -> dict[str, Any]:
        with self._lock:
            while self._status != "completed":
                if auto_approve and self._controller_mode not in {"manual", "no_control"}:
                    self.apply_recommended_plan(operator="comparison-runner", approve_all=False)
                self.step(1)
            return self.state()

    def metrics(self) -> dict[str, Any]:
        rows = self._timeline
        if not rows:
            current = [self._facility_snapshot(profile, self._cursor) for profile in self._scenario.facilities]
            baseline_peak = sum(float(item["baseline_kva"]) for item in current)
            controlled_peak = sum(float(item["controlled_kva"]) for item in current)
            baseline_energy_cost = sum(float(item["baseline_energy_cost_usd"]) for item in current)
            controlled_energy_cost = sum(float(item["controlled_energy_cost_usd"]) for item in current)
        else:
            baseline_peak = max(float(row["campus"]["baseline_kva"]) for row in rows)
            controlled_peak = max(float(row["campus"]["controlled_kva"]) for row in rows)
            baseline_energy_cost = sum(
                float(item["baseline_energy_cost_usd"])
                for row in rows
                for item in row["facilities"]
            )
            controlled_energy_cost = sum(
                float(item["controlled_energy_cost_usd"])
                for row in rows
                for item in row["facilities"]
            )
        baseline_demand_proxy = baseline_peak * self._demand_charge_rate()
        controlled_demand_proxy = controlled_peak * self._demand_charge_rate()
        approved_actions = [item for item in self._actions if item["status"] == "approved"]
        shifted_energy = 0.0
        curtailed_energy = 0.0
        approved_energy_cost_saving = 0.0
        for action in approved_actions:
            profile = self._facility(str(action["facility_id"]))
            energy = (
                float(action["reduction_kva"])
                * profile.power_factor
                * (float(action["duration_minutes"]) / 60.0)
            )
            try:
                action_period = tariff_period(datetime.fromisoformat(str(action["starts_at"]).replace("Z", "+00:00")))
            except (TypeError, ValueError):
                action_period = "standard"
            source_rate = self._energy_rate(action_period)
            if action["classification"] == "deferrable":
                shifted_energy += energy
                approved_energy_cost_saving += energy * max(source_rate - self._energy_rate("offpeak"), 0.0)
            else:
                curtailed_energy += energy
                approved_energy_cost_saving += energy * source_rate

        approved_peak_reduction_plan = 0.0
        if approved_actions:
            for index in range(self.total_steps):
                simultaneous = sum(
                    float(action["reduction_kva"])
                    for action in approved_actions
                    if int(action["start_index"]) <= index < int(action["end_index"])
                )
                approved_peak_reduction_plan = max(approved_peak_reduction_plan, simultaneous)
        approved_projected_peak = max(baseline_peak - approved_peak_reduction_plan, 0.0)
        approved_demand_charge_saving = approved_peak_reduction_plan * self._demand_charge_rate()
        approved_total_cost_saving = approved_energy_cost_saving + approved_demand_charge_saving
        critical_violations = sum(1 for event in self._events if event["event_type"] == "critical_constraint_violation")
        baseline_exceedances = sum(
            1
            for row in rows
            if float(row["campus"]["baseline_kva"]) > self._campus_limit()
        )
        controlled_exceedances = sum(
            1
            for row in rows
            if float(row["campus"]["controlled_kva"]) > self._campus_limit()
        )
        latency = self._forecast_latency_ms
        current_facilities = [self._facility_snapshot(profile, self._cursor) for profile in self._scenario.facilities]
        current_reduction = sum(max(float(item["baseline_kva"]) - float(item["controlled_kva"]), 0.0) for item in current_facilities)
        active_or_scheduled = self._active_actions_for_state()
        authorised_reduction = sum(float(item.get("reduction_kva", 0.0)) for item in active_or_scheduled)
        cumulative_reduction = sum(
            max(float(row["campus"]["baseline_kva"]) - float(row["campus"]["controlled_kva"]), 0.0)
            for row in rows
        )
        return {
            "completed_intervals": len(rows),
            "baseline_peak_kva": _round(baseline_peak),
            "controlled_peak_kva": _round(controlled_peak),
            "peak_reduction_kva": _round(max(baseline_peak - controlled_peak, 0.0)),
            "peak_reduction_percent": _round(max(baseline_peak - controlled_peak, 0.0) / max(baseline_peak, 1e-9) * 100, 2),
            "baseline_energy_cost_usd": _round(baseline_energy_cost, 4),
            "controlled_energy_cost_usd": _round(controlled_energy_cost, 4),
            "energy_cost_difference_usd": _round(baseline_energy_cost - controlled_energy_cost, 4),
            "baseline_demand_charge_proxy_usd": _round(baseline_demand_proxy, 2),
            "controlled_demand_charge_proxy_usd": _round(controlled_demand_proxy, 2),
            "demand_charge_proxy_difference_usd": _round(baseline_demand_proxy - controlled_demand_proxy, 2),
            "approved_actions": len(approved_actions),
            "current_reduction_kva": _round(current_reduction),
            "authorised_reduction_kva": _round(authorised_reduction),
            "cumulative_reduction_kva_intervals": _round(cumulative_reduction),
            "energy_shifted_kwh": _round(shifted_energy),
            "energy_curtailed_kwh": _round(curtailed_energy),
            "approved_peak_reduction_plan_kva": _round(approved_peak_reduction_plan),
            "approved_projected_peak_kva": _round(approved_projected_peak),
            "approved_energy_cost_saving_estimate_usd": _round(approved_energy_cost_saving, 2),
            "approved_demand_charge_saving_estimate_usd": _round(approved_demand_charge_saving, 2),
            "approved_total_cost_saving_estimate_usd": _round(approved_total_cost_saving, 2),
            "approved_saving_estimate_basis": (
                "Operator-approved load duration and reduction, configured time-of-use energy rates, "
                "off-peak recovery for deferrable loads, and the configured monthly demand rate."
            ),
            "critical_load_violations": critical_violations,
            "anomaly_escalations": sum(1 for event in self._events if event["event_type"] == "anomaly_escalation"),
            "baseline_campus_limit_exceedances": baseline_exceedances,
            "controlled_campus_limit_exceedances": controlled_exceedances,
            "mean_inference_latency_ms": _round(sum(latency) / len(latency), 4) if latency else 0.0,
            "max_inference_latency_ms": _round(max(latency), 4) if latency else 0.0,
            "claim_boundary": (
                "Scenario cost and demand-charge values are software-in-the-loop planning proxies. "
                "They are not realised savings or a reproduced institutional invoice."
            ),
        }

    def action_history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            safe_limit = max(1, min(limit, 500))
            output: list[dict[str, Any]] = []
            for action in reversed(self._actions[-safe_limit:]):
                if self._cursor < int(action["start_index"]):
                    phase = "scheduled"
                elif self._cursor < int(action["end_index"]):
                    phase = "active"
                else:
                    phase = "completed"
                output.append({**copy.deepcopy(action), "phase": phase})
            return output

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            safe_limit = max(1, min(limit, 500))
            return copy.deepcopy(list(reversed(self._events[-safe_limit:])))

    def compare_controllers(self, scenario_id: str) -> dict[str, Any]:
        if scenario_id not in SCENARIOS:
            raise ValueError(f"Unknown scenario '{scenario_id}'.")
        results: dict[str, Any] = {}
        for mode in ("no_control", "simple_rule", "ai_assisted"):
            runner = SimulationEngine(self.model)
            runner.configure(self._operational)
            runner.reset(scenario_id, mode)  # type: ignore[arg-type]
            runner.run_to_end(auto_approve=mode not in {"no_control", "manual"})
            state = runner.state()
            results[mode] = {
                "controller_mode": mode,
                "metrics": state["metrics"],
                "timeline": [
                    {
                        "timestamp": row["timestamp"],
                        "baseline_kva": row["campus"]["baseline_kva"],
                        "controlled_kva": row["campus"]["controlled_kva"],
                        "forecast_kva": row["campus"]["forecast_kva"],
                        "limit_kva": row["campus"]["limit_kva"],
                    }
                    for row in state["timeline"]
                ],
                "actions": [copy.deepcopy(item) for item in runner._actions if item["status"] == "approved"],
            }
        no_control_peak = float(results["no_control"]["metrics"]["controlled_peak_kva"])
        simple_peak = float(results["simple_rule"]["metrics"]["controlled_peak_kva"])
        ai_peak = float(results["ai_assisted"]["metrics"]["controlled_peak_kva"])
        return {
            "scenario": {
                **asdict(SCENARIOS[scenario_id]),
                "start_time": SCENARIOS[scenario_id].start_time.isoformat(),
            },
            "controllers": results,
            "comparison": {
                "ai_peak_reduction_vs_no_control_kva": _round(no_control_peak - ai_peak),
                "simple_peak_reduction_vs_no_control_kva": _round(no_control_peak - simple_peak),
                "ai_additional_peak_reduction_vs_simple_rule_kva": _round(simple_peak - ai_peak),
                "all_critical_load_violations": sum(
                    int(results[mode]["metrics"]["critical_load_violations"])
                    for mode in results
                ),
                "interpretation": (
                    "The forecast-assisted controller can schedule an approved action one interval before the current-demand rule. "
                    "Results depend on the disclosed scenario response model and are planning evidence, not field savings."
                ),
            },
        }
