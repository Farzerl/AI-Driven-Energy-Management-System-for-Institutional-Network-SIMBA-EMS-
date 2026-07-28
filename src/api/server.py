from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.cost_store import load_cost_impact
from src.api.evidence_store import (
    DEFAULT_EVIDENCE_DIR,
    DEFAULT_OPERATOR_LOG,
    REPO_ROOT,
    append_operator_decision,
    build_demo_alerts,
    load_control_comparison,
    load_dashboard_evidence,
    read_operator_decisions,
)
from src.api.meter_store import MeterReadingStore, read_edge_status
from src.api.schemas import (
    AdminLoginRequest,
    AdminPasswordChangeRequest,
    ControlledTestRequest,
    HealthResponse,
    NotificationSettingsInput,
    NotificationTestRequest,
    OperatorDecision,
    SystemSettingsInput,
)
from src.edge.schemas import MeterReading, MeterReadingBatch
from src.admin.auth import AdminAuthStore
from src.admin.testing import ControlledTestService
from src.live.adaptation import AdaptiveForecastCalibrator
from src.live.forecast_store import ForecastStore
from src.live.model_manager import LiveModelManager
from src.live.service import LiveInferenceService
from src.live.power_quality_adapter import PowerQualityChronosAdapter
from src.live.power_quality_service import PowerQualityForecastService
from src.notifications.service import NotificationService
from src.config.system_settings import SystemSettingsStore
from src.control.gateway import ControlGateway
from src.simulation.engine import SimulationEngine
from src.simulation.playback import SimulationPlaybackController
from src.simulation.profiles import SCENARIOS, DEFAULT_SCENARIO_ID
from src.simulation.schemas import (
    SimulationActionRequest,
    SimulationApprovalRequest,
    SimulationPlaybackRequest,
    SimulationRecommendationDecisionRequest,
    SimulationResetRequest,
    SimulationStepRequest,
)

DEFAULT_METER_STORE = REPO_ROOT / "runtime" / "meter_readings.jsonl"
DEFAULT_EDGE_STATUS = REPO_ROOT / "runtime" / "edge_status.json"
DEFAULT_COST_IMPACT_DIR = REPO_ROOT / "evidence" / "cost_impact"
DEFAULT_FORECAST_STORE = REPO_ROOT / "runtime" / "live_forecasts.jsonl"
DEFAULT_MODEL = REPO_ROOT / "models" / "institutional_multi_horizon_forecaster.json"
DEFAULT_EDGE_BENCHMARK = REPO_ROOT / "evidence" / "edge_runtime" / "edge_runtime_benchmark.json"
DEFAULT_MODEL_VALIDATION = REPO_ROOT / "evidence" / "model_validation" / "institutional_multi_horizon_metrics.json"
DEFAULT_NOTIFICATION_STORE = REPO_ROOT / "runtime" / "notification_events.jsonl"
DEFAULT_NOTIFICATION_SETTINGS = REPO_ROOT / "runtime" / "notification_settings.json"
DEFAULT_SYSTEM_SETTINGS = REPO_ROOT / "runtime" / "system_settings.json"
DEFAULT_ADAPTATION_STATE = REPO_ROOT / "runtime" / "adaptive_forecast_state.json"
DEFAULT_ADMIN_AUTH = REPO_ROOT / "runtime" / "admin_auth.json"
DEFAULT_ADMIN_TEST_LOG = REPO_ROOT / "runtime" / "admin_test_events.jsonl"
DEFAULT_MODEL_COMPARISON = REPO_ROOT / "evidence" / "model_validation" / "model_family_comparison.json"
DEFAULT_CHRONOS2_METRICS = REPO_ROOT / "evidence" / "model_validation" / "chronos2_model_comparison.json"
DEFAULT_CHRONOS2_ROUTING = REPO_ROOT / "models" / "chronos2" / "routing.json"
DEFAULT_CHRONOS2_SETUP_STATE = REPO_ROOT / "runtime" / "chronos2_setup_state.json"
DEFAULT_POWER_QUALITY_STORE = REPO_ROOT / "runtime" / "power_quality_forecasts.jsonl"
DEFAULT_POWER_QUALITY_METRICS = REPO_ROOT / "evidence" / "model_validation" / "power_quality_model_comparison.json"
DEFAULT_POWER_QUALITY_ROUTING = REPO_ROOT / "models" / "power_quality" / "routing.json"
DEFAULT_POWER_QUALITY_SETUP_STATE = REPO_ROOT / "runtime" / "power_quality_setup_state.json"
DEFAULT_INSTITUTIONAL_CASE_EVIDENCE = REPO_ROOT / "config" / "institutional_case_evidence.json"


def create_app(
    evidence_dir: Path | None = None,
    operator_log: Path | None = None,
    api_key: str | None = None,
    meter_store_path: Path | None = None,
    edge_status_path: Path | None = None,
    cost_impact_dir: Path | None = None,
    forecast_store_path: Path | None = None,
    model_path: Path | None = None,
    notification_store_path: Path | None = None,
    notification_settings_path: Path | None = None,
    system_settings_path: Path | None = None,
    adaptation_state_path: Path | None = None,
    admin_auth_path: Path | None = None,
    admin_test_log_path: Path | None = None,
    autostart_replay: bool | None = None,
) -> FastAPI:
    evidence_path = Path(evidence_dir or os.getenv("AI4I_EVIDENCE_DIR", DEFAULT_EVIDENCE_DIR))
    operator_log_path = Path(operator_log or os.getenv("AI4I_OPERATOR_LOG", DEFAULT_OPERATOR_LOG))
    meter_path = Path(meter_store_path or os.getenv("AI4I_METER_STORE", DEFAULT_METER_STORE))
    status_path = Path(edge_status_path or os.getenv("AI4I_EDGE_STATUS", DEFAULT_EDGE_STATUS))
    cost_path = Path(cost_impact_dir or os.getenv("AI4I_COST_IMPACT_DIR", DEFAULT_COST_IMPACT_DIR))
    forecast_path = Path(forecast_store_path or os.getenv("AI4I_FORECAST_STORE", DEFAULT_FORECAST_STORE))
    selected_model_path = Path(model_path or DEFAULT_MODEL)
    configured_api_key = api_key if api_key is not None else os.getenv("AI4I_API_KEY")

    meter_store = MeterReadingStore(meter_path)
    model_manager = LiveModelManager(selected_model_path)
    forecast_store = ForecastStore(forecast_path)
    system_path = Path(system_settings_path or os.getenv("SIMBA_SYSTEM_SETTINGS", DEFAULT_SYSTEM_SETTINGS))
    system_settings = SystemSettingsStore(system_path, set(SCENARIOS))
    model_manager.set_active_mode(str(dict(system_settings.snapshot().get("model", {})).get("selection_mode", "automatic")))
    resolved_admin_auth_path = Path(
        admin_auth_path
        or os.getenv("SIMBA_ADMIN_AUTH", "")
        or (operator_log_path.with_name("admin_auth.json") if operator_log is not None else DEFAULT_ADMIN_AUTH)
    )
    resolved_admin_test_log = Path(
        admin_test_log_path
        or os.getenv("SIMBA_ADMIN_TEST_LOG", "")
        or (operator_log_path.with_name("admin_test_events.jsonl") if operator_log is not None else DEFAULT_ADMIN_TEST_LOG)
    )
    admin_auth = AdminAuthStore(resolved_admin_auth_path)
    controlled_tests = ControlledTestService(resolved_admin_test_log, model_manager)
    adaptation_path = Path(adaptation_state_path or os.getenv("SIMBA_ADAPTATION_STATE", DEFAULT_ADAPTATION_STATE))
    calibrator = AdaptiveForecastCalibrator(adaptation_path, system_settings.snapshot)
    live_service = LiveInferenceService(model_manager, forecast_store, calibrator)
    power_quality_adapter = PowerQualityChronosAdapter(REPO_ROOT)
    power_quality_store = ForecastStore(DEFAULT_POWER_QUALITY_STORE)
    power_quality_service = PowerQualityForecastService(power_quality_adapter, power_quality_store)
    notification_path = Path(
        notification_store_path
        or os.getenv("SIMBA_NOTIFICATION_STORE", "")
        or (operator_log_path.with_name("notification_events.jsonl") if operator_log is not None else DEFAULT_NOTIFICATION_STORE)
    )
    settings_path = Path(
        notification_settings_path
        or os.getenv("SIMBA_NOTIFICATION_SETTINGS", "")
        or (operator_log_path.with_name("notification_settings.json") if operator_log is not None else DEFAULT_NOTIFICATION_SETTINGS)
    )
    notification_service = NotificationService(notification_path, settings_path=settings_path)
    startup_refresh = live_service.refresh(meter_store.all())
    startup_notifications = notification_service.dispatch(live_service.live_alerts())
    control_gateway = ControlGateway()
    simulation = SimulationEngine(model_manager, control_gateway=control_gateway)
    startup_system = system_settings.snapshot()
    simulation.configure(dict(startup_system.get("operational", {})))
    simulation.reset(
        str(startup_system["simulation"]["scenario_id"]),
        str(startup_system["simulation"]["controller_mode"]),
    )
    playback = SimulationPlaybackController(simulation, system_settings.snapshot)
    explicit_runtime_paths = any(
        value is not None
        for value in (
            evidence_dir, operator_log, meter_store_path, edge_status_path, cost_impact_dir,
            forecast_store_path, model_path, notification_store_path, notification_settings_path,
            system_settings_path, adaptation_state_path, admin_auth_path, admin_test_log_path,
        )
    )
    configured_autostart = bool(dict(startup_system.get("simulation", {})).get("auto_start", True))
    should_autostart = (configured_autostart and not explicit_runtime_paths) if autostart_replay is None else bool(autostart_replay)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            if should_autostart:
                playback.start()
            yield
        finally:
            playback.stop()
            power_quality_service.close()
            model_manager.close()

    app = FastAPI(
        title="SIMBA Institutional Energy Management API",
        version="5.2.0",
        description=(
            "Demand, active-energy and reactive-power forecasting, operator decision support, "
            "software-in-the-loop control testing, cost planning and institutional energy evidence."
        ),
        lifespan=lifespan,
    )

    dashboard_dir = REPO_ROOT / "dashboard"
    static_dir = dashboard_dir / "static"
    diagrams_dir = REPO_ROOT / "docs" / "diagrams"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.mount("/evidence-assets", StaticFiles(directory=evidence_path), name="evidence-assets")
    if diagrams_dir.exists():
        app.mount("/diagrams", StaticFiles(directory=diagrams_dir), name="diagrams")

    @app.middleware("http")
    async def add_security_headers(request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
        if configured_api_key and x_api_key != configured_api_key:
            raise HTTPException(status_code=401, detail="Valid X-API-Key required.")

    def require_admin(x_admin_token: str | None = Header(default=None)) -> str:
        if not admin_auth.validate(x_admin_token):
            raise HTTPException(status_code=401, detail="A valid admin session is required.")
        return str(x_admin_token)


    def load_optional_json(path: Path) -> dict[str, object]:
        if not path.exists():
            return {"status": "not_generated"}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"status": "invalid"}
        except Exception as exc:
            return {"status": "invalid", "error": str(exc)}

    def power_quality_records() -> list[dict[str, object]]:
        records = meter_store.all()
        counts: dict[str, int] = {}
        for row in records:
            facility = str(row.get("facility_id", ""))
            counts[facility] = counts.get(facility, 0) + 1
        if any(count >= 49 for count in counts.values()):
            return records
        return simulation.power_quality_history()

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(dashboard_dir / "index.html")

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="online",
            evidence_ready=(evidence_path / "dashboard_evidence.json").exists(),
            operating_mode="advisory",
            api_key_required=bool(configured_api_key),
            model_ready=model_manager.ready,
        )

    @app.get("/api/health/deep")
    def deep_health() -> dict[str, object]:
        notification = notification_service.status()
        stores = {
            "meter": meter_store.summary(),
            "forecast": forecast_store.summary(),
            "notification_events": len(notification_service.store.latest(500)),
        }
        return {
            "status": "online" if model_manager.ready else "degraded",
            "model_ready": model_manager.ready,
            "notification_mode": notification.get("mode"),
            "email_ready": notification.get("email", {}).get("configured", False),
            "settings_error": notification.get("settings_error"),
            "system_settings_error": system_settings.last_error,
            "adaptive_learning": calibrator.status(),
            "power_quality": power_quality_service.status(),
            "stores": {**stores, "power_quality": power_quality_store.summary()},
            "admin": admin_auth.status(),
            "approval_channel": "dashboard_only",
        }

    @app.get("/api/summary")
    def summary() -> dict[str, object]:
        evidence = load_dashboard_evidence(evidence_path)
        quality = evidence["dataset_quality"]
        forecast = evidence["forecast"]
        peak = evidence["peak_risk"]
        comparison = evidence["controller_comparison"]["comparison"]
        return {
            "dataset": {
                "facilities": quality["facilities"],
                "intervals": quality["rows_after_grid_completion"],
                "usable_percent": quality["forecast_usable_percent"],
                "date_start": quality["date_start"],
                "date_end": quality["date_end"],
            },
            "forecast": {
                "baseline_mae_kva": model_manager.status().get("metrics", {}).get("30_minutes", {}).get("persistence_mae_kva", forecast["baseline"]["mae_kva"]),
                "model_mae_kva": model_manager.status().get("metrics", {}).get("30_minutes", {}).get("mae_kva", forecast["model"]["mae_kva"]),
                "mae_reduction_percent": model_manager.status().get("metrics", {}).get("30_minutes", {}).get("mae_improvement_vs_persistence_percent", forecast["improvement"]["model_vs_persistence"]["mae_reduction_percent"]),
                "rmse_reduction_percent": forecast["improvement"]["model_vs_persistence"]["rmse_reduction_percent"],
            },
            "peak_risk": {
                "balanced_accuracy": peak["balanced_accuracy"],
                "macro_f1": peak["macro_f1"],
                "high_warning_recall": peak["actual_high_predicted_medium_or_high_recall"],
                "critical_miss_rate": peak["actual_high_predicted_low_miss_rate"],
            },
            "controller_comparison": comparison,
            "cost_status": "public_source_planning_estimate_not_actual_bill",
            "operating_mode": "advisory_or_operator_confirmed",
            "live_model": model_manager.status(),
        }

    @app.get("/api/evidence")
    def evidence() -> dict[str, object]:
        return load_dashboard_evidence(evidence_path)

    @app.get("/api/control-comparison")
    def control_comparison() -> dict[str, object]:
        return load_control_comparison(evidence_path)

    @app.get("/api/cost-impact")
    def cost_impact() -> dict[str, object]:
        return load_cost_impact(cost_path)

    @app.get("/api/model-status")
    def model_status() -> dict[str, object]:
        return {
            **model_manager.status(),
            "startup_refresh": startup_refresh,
            "startup_notifications": startup_notifications,
            "adaptive_learning": calibrator.status(),
        }

    @app.get("/api/readiness-evidence")
    def readiness_evidence() -> dict[str, object]:
        def load_optional(path: Path) -> dict[str, object]:
            if not path.exists():
                return {"status": "not_generated", "path": path.relative_to(REPO_ROOT).as_posix()}
            return json.loads(path.read_text(encoding="utf-8"))

        return {
            "edge_runtime": load_optional(DEFAULT_EDGE_BENCHMARK),
            "model_validation": load_optional(DEFAULT_MODEL_VALIDATION),
            "chronos2_validation": load_optional(DEFAULT_CHRONOS2_METRICS),
            "power_quality_validation": load_optional(DEFAULT_POWER_QUALITY_METRICS),
            "institutional_case": load_optional(DEFAULT_INSTITUTIONAL_CASE_EVIDENCE),
            "simulation": {
                "status": "ready",
                "scenario_count": len(simulation.scenarios()),
                "api_endpoints": 8,
                "boundary": "Software-in-the-loop plant response with trained multi-horizon inference.",
            },
        }

    @app.get("/api/live-forecasts")
    def live_forecasts(limit: int = 50) -> dict[str, object]:
        safe_limit = max(1, min(limit, 500))
        return {
            "mode": "multi_horizon_demand_forecast",
            "model": model_manager.status(),
            "summary": forecast_store.summary(),
            "items": live_service.latest_forecasts(safe_limit),
        }

    @app.get("/api/live-alerts")
    def live_alerts(limit: int = 50) -> dict[str, object]:
        return {"mode": "live_edge_forecast", "alerts": live_service.live_alerts(limit)}

    @app.get("/api/power-quality-forecasts")
    def power_quality_forecasts(force: bool = False) -> dict[str, object]:
        return power_quality_service.snapshot(power_quality_records(), force=force)

    @app.get("/api/admin/power-quality/status", dependencies=[Depends(require_admin)])
    def admin_power_quality_status() -> dict[str, object]:
        return {
            **power_quality_service.status(),
            "routing": load_optional_json(DEFAULT_POWER_QUALITY_ROUTING),
            "metrics": load_optional_json(DEFAULT_POWER_QUALITY_METRICS),
            "setup": load_optional_json(DEFAULT_POWER_QUALITY_SETUP_STATE),
            "training_command": "Place one authorised UZ dataset ZIP in training_data and run TRAIN_POWER_QUALITY_FORECASTS.bat as Administrator.",
            "source_model_policy": "The power-quality LoRA starts from models/chronos-2-finetuned, then falls back to models/chronos-2-base or an official model ZIP only when necessary.",
        }

    @app.post("/api/live-inference/rebuild", dependencies=[Depends(require_api_key)])
    def rebuild_live_inference() -> dict[str, object]:
        inference = live_service.refresh(meter_store.all())
        notifications = notification_service.dispatch(live_service.live_alerts())
        power_quality = power_quality_service.request_refresh(power_quality_records(), force=True)
        return {**inference, "notifications": notifications, "power_quality": power_quality}


    @app.get("/api/system-settings", dependencies=[Depends(require_admin)])
    def get_system_settings() -> dict[str, object]:
        return {"settings": system_settings.snapshot(), "scenarios": simulation.scenarios()}

    @app.put("/api/system-settings", dependencies=[Depends(require_api_key), Depends(require_admin)])
    def update_system_settings(payload: SystemSettingsInput) -> dict[str, object]:
        try:
            updated = system_settings.update(payload.model_dump())
            model_manager.set_active_mode(str(dict(updated.get("model", {})).get("selection_mode", "automatic")))
            simulation_config = updated["simulation"]
            playback.stop("settings_updated")
            simulation.configure(dict(updated.get("operational", {})))
            simulation.reset(
                str(simulation_config["scenario_id"]),
                str(simulation_config["controller_mode"]),
            )
            return {"status": "saved", "settings": updated, "simulation": playback.snapshot_with_state()}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/adaptive-learning/status")
    def adaptive_learning_status() -> dict[str, object]:
        return calibrator.status()

    @app.post("/api/adaptive-learning/reset", dependencies=[Depends(require_api_key), Depends(require_admin)])
    def adaptive_learning_reset() -> dict[str, object]:
        return calibrator.reset()

    @app.get("/api/notifications/status")
    def notification_status() -> dict[str, object]:
        return notification_service.status()

    @app.get("/api/notifications/settings")
    def notification_settings() -> dict[str, object]:
        return notification_service.public_settings()

    @app.put("/api/notifications/settings", dependencies=[Depends(require_api_key)])
    def update_notification_settings(payload: NotificationSettingsInput) -> dict[str, object]:
        try:
            updated = notification_service.update_settings(
                payload.model_dump(exclude_none=True)
            )
            return {"status": "saved", "settings": updated, "channels": notification_service.status()}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/notifications/events")
    def notification_events(limit: int = 100) -> dict[str, object]:
        return {"items": notification_service.store.latest(limit)}

    @app.post("/api/notifications/process", dependencies=[Depends(require_api_key)])
    def process_notifications() -> dict[str, object]:
        return notification_service.dispatch(live_service.live_alerts())

    @app.post("/api/notifications/test", dependencies=[Depends(require_api_key)])
    def test_notification(channel: str) -> dict[str, object]:
        try:
            return notification_service.test(channel)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/notifications/test-target", dependencies=[Depends(require_api_key)])
    def test_notification_target(payload: NotificationTestRequest) -> dict[str, object]:
        try:
            return notification_service.test(payload.channel, payload.recipient)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def simulation_action_queue(limit: int = 6) -> list[dict[str, object]]:
        state = playback.snapshot_with_state()
        recommendations = {
            str(item.get("facility_id")): item
            for item in list(state.get("recommendations", []))
            if isinstance(item, dict)
        }
        rows: list[dict[str, object]] = []
        for forecast in list(state.get("forecasts", [])):
            if not isinstance(forecast, dict):
                continue
            facility_id = str(forecast.get("facility_id", ""))
            facility_name = str(forecast.get("facility_name", facility_id or "Facility"))
            limit_kva = max(float(forecast.get("limit_kva", 0.0) or 0.0), 0.001)
            horizons = dict(forecast.get("horizons", {}))
            ranked_horizons: list[tuple[float, int, float, float]] = []
            for horizon in horizons.values():
                if not isinstance(horizon, dict):
                    continue
                point = float(horizon.get("forecast_kva", 0.0) or 0.0)
                upper = float(horizon.get("forecast_upper_kva", point) or point)
                minutes = int(horizon.get("minutes", 30) or 30)
                ranked_horizons.append((upper / limit_kva, minutes, point, upper))
            if ranked_horizons:
                utilization, lead_minutes, point, upper = max(ranked_horizons, key=lambda item: item[0])
            else:
                point = float(forecast.get("forecast_kva", 0.0) or 0.0)
                upper = float(forecast.get("forecast_upper_kva", point) or point)
                utilization = upper / limit_kva
                lead_minutes = 30
            recommendation = recommendations.get(facility_id)
            requires_action = recommendation is not None
            if not requires_action and utilization < 0.80:
                continue
            risk = "high" if utilization >= 0.95 else "medium" if utilization >= 0.85 else "low"
            actions = list(dict(recommendation or {}).get("actions", []))
            action_names = [str(item.get("load_group_name", "non-critical load")) for item in actions if isinstance(item, dict)]
            planned_reduction = float(dict(recommendation or {}).get("planned_reduction_kva", 0.0) or 0.0)
            current = next(
                (item for item in list(state.get("facilities", [])) if str(dict(item).get("facility_id", "")) == facility_id),
                {},
            )
            rows.append({
                "alert_id": str(dict(recommendation or {}).get("recommendation_id") or f"simulation:{state.get('session_id')}:{facility_id}"),
                "recommendation_id": dict(recommendation or {}).get("recommendation_id"),
                "facility_id": facility_id,
                "facility_name": facility_name,
                "timestamp": str(state.get("current_timestamp", "")),
                "tariff_period": str(state.get("tariff_period", "standard")),
                "risk": risk,
                "risk_lead_minutes": lead_minutes,
                "current_kva": float(dict(current).get("controlled_kva", 0.0) or 0.0),
                "forecast_kva": point,
                "forecast_upper_kva": upper,
                "facility_limit_kva": limit_kva,
                "utilization_percent": round(point / limit_kva * 100.0, 2),
                "upper_utilization_percent": round(utilization * 100.0, 2),
                "planning_reduction_kva": round(planned_reduction, 2),
                "recommended_action": (
                    "Prepare " + ", ".join(action_names[:2]) + " for operator review."
                    if action_names else "Continue monitoring; no controllable response is required yet."
                ),
                "requires_action": requires_action,
                "queue_source": "active_simulation_forecast",
                "priority_score": round((1000.0 if requires_action else 0.0) + utilization * 100.0 + planned_reduction, 3),
                "priority_reason": "Ranked by action requirement, conservative limit utilisation and safely controllable reduction. No facility has permanent priority.",
                "approval_channel": "dashboard_only",
            })
        rows.sort(key=lambda item: float(item.get("priority_score", 0.0)), reverse=True)
        for index, row in enumerate(rows[: max(1, min(limit, 20))], start=1):
            row["priority_rank"] = index
        return rows[: max(1, min(limit, 20))]

    @app.get("/api/alerts")
    def alerts() -> dict[str, object]:
        simulation_queue = simulation_action_queue()
        live = live_service.live_alerts()
        if live:
            seen = {str(item.get("facility_name", "")) for item in live}
            combined = list(live) + [item for item in simulation_queue if str(item.get("facility_name", "")) not in seen]
            for index, item in enumerate(combined, start=1):
                item["priority_rank"] = index
                item.setdefault("priority_reason", "Ranked from the latest validated forecast risk and conservative upper bound.")
            return {"mode": "live_edge_and_simulation_queue", "alerts": combined[:8]}
        if simulation_queue:
            return {
                "mode": "active_simulation_action_queue",
                "alerts": simulation_queue,
                "notice": "Priority is recalculated at every replay interval; Central Kitchens is not hard-coded as the permanent first facility.",
            }
        return {
            "mode": "demonstration_scenario",
            "alerts": build_demo_alerts(),
            "notice": "Start the edge replay to replace the scenario alerts with model forecasts from received values.",
        }

    @app.get("/api/operator-decisions")
    def decisions(limit: int = 50) -> dict[str, object]:
        safe_limit = max(1, min(limit, 200))
        return {"items": read_operator_decisions(operator_log_path, safe_limit)}

    @app.post("/api/operator-decisions", dependencies=[Depends(require_api_key)])
    def save_decision(decision: OperatorDecision) -> dict[str, object]:
        if decision.origin != "dashboard":
            raise HTTPException(status_code=422, detail="Operator approval is accepted from the live dashboard only.")
        return append_operator_decision(operator_log_path, decision.model_dump())

    @app.post("/api/meter-readings", dependencies=[Depends(require_api_key)])
    def ingest_meter_readings(payload: MeterReading | MeterReadingBatch) -> dict[str, object]:
        readings = payload.readings if isinstance(payload, MeterReadingBatch) else [payload]
        result = meter_store.append(readings)
        reading_rows = [item.model_dump(mode="json") for item in readings]
        adaptive_update = calibrator.observe_readings(reading_rows, forecast_store.all())
        inference = live_service.refresh(meter_store.all())
        notifications = notification_service.dispatch(live_service.live_alerts())
        power_quality = power_quality_service.request_refresh(meter_store.all())
        return {
            "status": "accepted",
            **result,
            "adaptive_update": adaptive_update,
            "live_inference": inference,
            "power_quality": power_quality,
            "notifications": notifications,
        }

    @app.get("/api/meter-readings")
    def latest_meter_readings(limit: int = 50) -> dict[str, object]:
        safe_limit = max(1, min(limit, 500))
        return {"mode": "edge_demo", "items": meter_store.latest(safe_limit)}

    @app.get("/api/edge-status")
    def edge_status() -> dict[str, object]:
        return {
            "mode": "meter_replay",
            "store": meter_store.summary(),
            "gateway": read_edge_status(status_path),
            "live_forecasts": forecast_store.summary(),
            "power_quality_forecasts": power_quality_store.summary(),
            "model": model_manager.status(),
            "power_quality_model": power_quality_adapter.status(public=True),
            "boundary": "Validated meter readings feed the forecast service. Approved non-critical actions can be passed to the configured control gateway; simulation is the safe default.",
        }

    def simulation_value_error(exc: ValueError) -> HTTPException:
        return HTTPException(status_code=422, detail=str(exc))

    @app.get("/api/simulation/scenarios", dependencies=[Depends(require_admin)])
    def simulation_scenarios() -> dict[str, object]:
        return {
            "mode": "software_in_the_loop",
            "items": simulation.scenarios(),
            "boundary": (
                "The plant response is simulated by default and forecast inference uses the trained demand model. "
                "An authorised pilot may enable the guarded control gateway for configured non-critical smart breakers."
            ),
        }

    @app.post("/api/simulation/reset", dependencies=[Depends(require_api_key), Depends(require_admin)])
    def simulation_reset(payload: SimulationResetRequest) -> dict[str, object]:
        try:
            playback.stop("replay_reset")
            simulation.reset(payload.scenario_id, payload.controller_mode)
            return playback.snapshot_with_state()
        except ValueError as exc:
            raise simulation_value_error(exc) from exc

    @app.get("/api/simulation/state")
    def simulation_state() -> dict[str, object]:
        return playback.snapshot_with_state()

    @app.post("/api/simulation/step", dependencies=[Depends(require_api_key), Depends(require_admin)])
    def simulation_step(payload: SimulationStepRequest) -> dict[str, object]:
        playback.stop("manual_step")
        result = simulation.step(payload.count)
        result["state"] = playback.snapshot_with_state()
        return result

    @app.post("/api/simulation/playback", dependencies=[Depends(require_api_key), Depends(require_admin)])
    def simulation_playback(payload: SimulationPlaybackRequest) -> dict[str, object]:
        try:
            playback_status = playback.start() if payload.action == "start" else playback.stop()
            return {"playback": playback_status, "state": playback.snapshot_with_state()}
        except ValueError as exc:
            raise simulation_value_error(exc) from exc

    @app.post("/api/simulation/action", dependencies=[Depends(require_api_key), Depends(require_admin)])
    def simulation_action(payload: SimulationActionRequest) -> dict[str, object]:
        try:
            return simulation.apply_action(payload)
        except ValueError as exc:
            raise simulation_value_error(exc) from exc

    @app.post("/api/simulation/approve-recommendation", dependencies=[Depends(require_api_key)])
    def simulation_approve_recommendation(payload: SimulationApprovalRequest | None = None) -> dict[str, object]:
        request = payload or SimulationApprovalRequest()
        try:
            result = simulation.apply_recommended_plan(
                operator=request.operator,
                recommendation_ids=request.recommendation_ids,
                request_id=request.request_id,
            )
            if int(result.get("applied", 0)) > 0:
                playback.resume_after_approval()
            result["state"] = playback.snapshot_with_state()
            return result
        except ValueError as exc:
            raise simulation_value_error(exc) from exc

    @app.post("/api/simulation/recommendation-decision", dependencies=[Depends(require_api_key)])
    def simulation_recommendation_decision(payload: SimulationRecommendationDecisionRequest) -> dict[str, object]:
        try:
            result = simulation.decide_recommendation(
                payload.recommendation_id,
                payload.decision,
                operator=payload.operator,
                note=payload.note,
                request_id=payload.request_id,
            )
            if int(result.get("applied", 0)) > 0 or int(result.get("updated", 0)) > 0:
                playback.resume_after_operator_decision()
            result["state"] = playback.snapshot_with_state()
            return result
        except (ValueError, RuntimeError) as exc:
            raise simulation_value_error(exc) from exc

    @app.get("/api/integration/status")
    def integration_status() -> dict[str, object]:
        meter_summary = meter_store.summary()
        forecast_summary = forecast_store.summary()
        model_status = model_manager.status()
        edge = read_edge_status(status_path)
        return {
            "meter_ingestion": {
                "status": "receiving" if int(meter_summary.get("received_count", 0) or 0) > 0 else "api_ready",
                "endpoint": "/api/meter-readings",
                "api_key_required": bool(configured_api_key),
                "stored_readings": int(meter_summary.get("received_count", 0) or 0),
                "latest_timestamp": meter_summary.get("latest_reading_timestamp"),
                "edge_status": edge,
            },
            "cleaning": {
                "status": "active",
                "checks": [
                    "schema and timestamp validation",
                    "finite non-negative electrical values",
                    "facility and interval consistency",
                    "duplicate-safe append and local buffering",
                ],
            },
            "forecasting": {
                "status": "ready" if bool(model_status.get("ready", True)) else "fallback",
                "active_mode": model_status.get("active_mode", "automatic"),
                "forecast_records": int(forecast_summary.get("forecast_count", 0) or 0),
                "model_family": model_status.get("model_family"),
                "outputs": ["kVA demand", "peak risk", "active power kW", "reactive power kVAR", "derived kWh", "estimated kVARh", "power-factor risk"],
            },
            "power_quality": power_quality_service.status(),
            "control_gateway": control_gateway.status(),
            "approval_boundary": "Only dashboard-approved non-critical actions can reach the control gateway.",
        }

    @app.get("/api/simulation/impact")
    def simulation_impact() -> dict[str, object]:
        state = playback.snapshot_with_state()
        metrics = dict(state.get("metrics", {}))
        return {
            "status": state.get("status"),
            "scenario": state.get("scenario"),
            "controller_mode": state.get("controller_mode"),
            "current_timestamp": state.get("current_timestamp"),
            "progress_percent": state.get("progress_percent"),
            "metrics": metrics,
            "campus": state.get("campus"),
            "active_actions": state.get("active_actions", []),
            "actions": simulation.action_history(100),
            "approval_deck": state.get("approval_deck", {}),
            "control_gateway": control_gateway.status(),
            "current_reduction_kva": metrics.get("current_reduction_kva", 0.0),
            "authorised_reduction_kva": metrics.get("authorised_reduction_kva", 0.0),
            "claim_boundary": metrics.get("claim_boundary"),
        }

    @app.get("/api/simulation/metrics", dependencies=[Depends(require_admin)])
    def simulation_metrics() -> dict[str, object]:
        return simulation.metrics()

    @app.get("/api/simulation/events", dependencies=[Depends(require_admin)])
    def simulation_events(limit: int = 100) -> dict[str, object]:
        return {"items": simulation.events(limit)}

    @app.get("/api/simulation/comparison", dependencies=[Depends(require_admin)])
    def simulation_comparison(scenario_id: str = DEFAULT_SCENARIO_ID) -> dict[str, object]:
        try:
            return simulation.compare_controllers(scenario_id)
        except ValueError as exc:
            raise simulation_value_error(exc) from exc

    @app.get("/api/chronos2/status")
    def chronos2_status() -> dict[str, object]:
        return model_manager.status().get("chronos2", {})

    @app.get("/api/admin/chronos2/status", dependencies=[Depends(require_admin)])
    def admin_chronos2_status() -> dict[str, object]:
        return {
            "runtime": model_manager.status().get("chronos2", {}),
            "routing": load_optional_json(DEFAULT_CHRONOS2_ROUTING),
            "metrics": load_optional_json(DEFAULT_CHRONOS2_METRICS),
            "setup": load_optional_json(DEFAULT_CHRONOS2_SETUP_STATE),
            "installation_command": "Run INSTALL_AND_TRAIN_CHRONOS2.bat as Administrator from the repository root.",
            "approval_channel": "dashboard_only",
        }

    @app.get("/api/about")
    def about_product() -> dict[str, object]:
        comparison = load_optional_json(DEFAULT_MODEL_COMPARISON)
        chronos2 = load_optional_json(DEFAULT_CHRONOS2_METRICS)
        selected_metrics: dict[str, object] = {}
        models = dict(comparison.get("models", {})) if isinstance(comparison, dict) else {}
        selected = dict(comparison.get("selected_by_horizon", {})) if isinstance(comparison, dict) else {}
        for horizon, selection in selected.items():
            model_name = str(dict(selection).get("model", "gradient_boosting"))
            metrics = dict(dict(models.get(model_name, {})).get("horizons", {})).get(horizon, {})
            selected_metrics[str(horizon)] = {"model": model_name, **dict(metrics)}
        return {
            "product": {
                "name": "SIMBA-EMS",
                "purpose": "Forecast institutional demand, active energy and reactive-power behaviour, identify peak and power-factor risk, and support operator-confirmed responses.",
                "problem": "Operators can see current and historical use, but cannot reliably coordinate flexible loads before simultaneous demand creates an avoidable peak.",
                "operating_mode": "advisory_and_operator_confirmed",
                "approval_channel": "dashboard_only",
                "track_position": "Development Track working software MVP with a trained model, APIs, safety rules, notifications, simulation and tests.",
            },
            "dataset": {
                "rows": comparison.get("dataset_rows"),
                "facilities": comparison.get("facility_count"),
                "sequence_length_intervals": comparison.get("sequence_length_intervals"),
                "splits": comparison.get("splits", {}),
            },
            "model": model_manager.status(),
            "selected_metrics": selected_metrics,
            "model_comparison": comparison,
            "chronos2_comparison": chronos2,
            "power_quality_comparison": load_optional_json(DEFAULT_POWER_QUALITY_METRICS),
            "power_quality_model": power_quality_adapter.status(),
            "selection_reason": (
                "Automatic mode selects the lowest validation-error model or validation-weighted hybrid separately for each forecast horizon. "
                "The final untouched test period is common to every model, so the comparison is directly comparable."
            ),
            "architecture": [
                "meter ingestion and validation",
                "multi-model demand and multivariate power-quality forecasting",
                "uncertainty-aware peak and power-factor risk classification",
                "engineering safety rules",
                "Gmail attention alerts",
                "dashboard operator approval",
                "auditable outcome verification",
            ],
            "development_evidence": [
                "working FastAPI backend and browser client",
                "trained gradient boosting, LSTM and Transformer model bundles",
                "validation-weighted hybrid routing",
                "locally fine-tuned Chronos-2 demand model plus a separately validated kW and kVAR power-quality adaptation",
                "evidence-grounded recommendation explanations",
                "controlled non-hardcoded proof input",
                "operator-confirmed response workflow",
                "software-in-the-loop controller comparison",
                "automated reliability and security tests",
            ],
            "safety": [
                "critical loads are excluded by deterministic rules",
                "email alerts cannot approve actions",
                "manual and simulated test values cannot update production learning",
                "new model versions require offline chronological validation",
                "Chronos-2 cannot approve actions and falls back to the existing validated router on failure",
            ],
            "claim_boundary": "Forecast and control results are software-MVP evidence until verified through an authorised institutional pilot.",
        }

    @app.post("/api/admin/login")
    def admin_login(payload: AdminLoginRequest) -> dict[str, object]:
        try:
            return admin_auth.login(payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.get("/api/admin/status")
    def admin_status() -> dict[str, object]:
        return admin_auth.status()

    @app.post("/api/admin/logout")
    def admin_logout(x_admin_token: str | None = Header(default=None)) -> dict[str, object]:
        admin_auth.logout(x_admin_token)
        return {"status": "logged_out"}

    @app.post("/api/admin/password", dependencies=[Depends(require_admin)])
    def admin_change_password(payload: AdminPasswordChangeRequest, x_admin_token: str | None = Header(default=None)) -> dict[str, object]:
        try:
            return admin_auth.change_password(x_admin_token, payload.current_password, payload.new_password)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/admin/test-facilities", dependencies=[Depends(require_admin)])
    def admin_test_facilities() -> dict[str, object]:
        return {"items": controlled_tests.facilities()}

    @app.post("/api/admin/test-forecast", dependencies=[Depends(require_admin)])
    def admin_test_forecast(payload: ControlledTestRequest) -> dict[str, object]:
        try:
            return controlled_tests.run(
                request_id=payload.request_id,
                facility_id=payload.facility_id,
                values_kva=payload.values_kva,
                selected_mode=payload.model_mode,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/admin/test-history", dependencies=[Depends(require_admin)])
    def admin_test_history(limit: int = 20) -> dict[str, object]:
        return {"items": controlled_tests.latest(limit)}

    @app.get("/api/admin/diagnostics", dependencies=[Depends(require_admin)])
    def admin_diagnostics() -> dict[str, object]:
        state = simulation.state()
        notification = notification_service.status()
        return {
            "status": "ready" if model_manager.ready else "degraded",
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "model": model_manager.status(),
            "system_settings": system_settings.snapshot(),
            "adaptive_learning": calibrator.status(),
            "power_quality": power_quality_service.status(),
            "email": {
                "mode": notification.get("mode"),
                "configured": notification.get("email", {}).get("configured", False),
                "recipient_count": notification.get("email", {}).get("recipient_count", 0),
                "settings_error": notification.get("settings_error"),
            },
            "stores": {
                "meter": meter_store.summary(),
                "forecast": forecast_store.summary(),
                "power_quality_forecast": power_quality_store.summary(),
                "notification_event_count": len(notification_service.store.latest(500)),
            },
            "simulation": {
                "session_id": state.get("session_id"),
                "status": state.get("status"),
                "scenario": state.get("scenario"),
                "controller_mode": state.get("controller_mode"),
                "cursor": state.get("cursor"),
                "total_steps": state.get("total_steps"),
                "campus": state.get("campus"),
                "recommendation": state.get("recommendation"),
                "model_forecast_count": state.get("model", {}).get("model_forecast_count"),
                "fallback_forecast_count": state.get("model", {}).get("fallback_forecast_count"),
                "latest_batch_inference_latency_ms": state.get("model", {}).get("latest_batch_inference_latency_ms"),
            },
            "controlled_test_history": controlled_tests.latest(5),
            "admin": admin_auth.status(),
            "approval_channel": "dashboard_only",
            "secret_fields_included": False,
        }

    return app

