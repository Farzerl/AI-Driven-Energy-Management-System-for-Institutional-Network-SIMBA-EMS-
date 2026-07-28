from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class OperatorDecision(BaseModel):
    alert_id: str = Field(min_length=8, max_length=64)
    decision: Literal["confirm", "defer", "dismiss", "mute"]
    operator: str = Field(default="demo-operator", min_length=2, max_length=80)
    note: str = Field(default="", max_length=500)
    requested_reduction_kva: float | None = Field(default=None, ge=0)
    origin: Literal["dashboard"] = "dashboard"


class HealthResponse(BaseModel):
    status: str
    evidence_ready: bool
    operating_mode: str
    api_key_required: bool
    model_ready: bool = False


class NotificationRecipientInput(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    label: str = Field(default="", max_length=80)
    address: str = Field(min_length=3, max_length=254)
    enabled: bool = True


class NotificationEmailSettingsInput(BaseModel):
    enabled: bool = True
    recipients: list[NotificationRecipientInput] = Field(default_factory=list, max_length=100)
    gmail_user: str = Field(default="", max_length=254)
    gmail_app_password: str | None = Field(default=None, max_length=128)
    clear_gmail_app_password: bool = False
    gmail_host: str = Field(default="smtp.gmail.com", max_length=255)
    gmail_port: int = Field(default=465, ge=1, le=65535)
    gmail_security: Literal["ssl", "starttls"] = "ssl"


class NotificationSettingsInput(BaseModel):
    mode: Literal["disabled", "dry_run", "live"] = "dry_run"
    minimum_risk: Literal["medium", "high"] = "high"
    cooldown_minutes: int = Field(default=60, ge=0, le=1440)
    dashboard_url: str = Field(
        default="http://127.0.0.1:8000/?tab=operations", max_length=500
    )
    delivery_attempts: int = Field(default=2, ge=1, le=5)
    retry_backoff_seconds: float = Field(default=0.75, ge=0, le=10)
    email: NotificationEmailSettingsInput


class NotificationTestRequest(BaseModel):
    channel: Literal["email"] = "email"
    recipient: str | None = Field(default=None, max_length=254)


class SimulationRuntimeSettingsInput(BaseModel):
    scenario_id: str = Field(min_length=3, max_length=100)
    controller_mode: Literal["ai_assisted", "simple_rule", "manual", "no_control"] = "ai_assisted"
    playback_interval_seconds: float = Field(default=10.0, ge=0.5, le=30.0)
    pause_on_recommendation: bool = False
    auto_compare_on_load: bool = False
    auto_start: bool = True


class AdaptiveLearningSettingsInput(BaseModel):
    enabled: bool = True
    minimum_observations: int = Field(default=8, ge=4, le=96)
    correction_gain: float = Field(default=0.55, ge=0.0, le=1.0)
    maximum_correction_percent_of_limit: float = Field(default=5.0, ge=0.0, le=15.0)
    residual_window: int = Field(default=192, ge=48, le=1000)
    retraining_interval_new_readings: int = Field(default=336, ge=96, le=10000)




class OperationalGuardrailsInput(BaseModel):
    campus_limit_override_kva: float | None = Field(default=None, gt=0, le=100000)
    facility_limit_overrides_kva: dict[str, float] = Field(default_factory=dict)
    critical_floor_overrides_kva: dict[str, float] = Field(default_factory=dict)
    risk_medium_ratio: float = Field(default=0.85, ge=0.5, le=1.25)
    risk_high_ratio: float = Field(default=0.95, ge=0.55, le=1.5)
    peak_energy_usd_per_kwh: float = Field(default=0.2173, ge=0, le=10)
    standard_energy_usd_per_kwh: float = Field(default=0.1150, ge=0, le=10)
    offpeak_energy_usd_per_kwh: float = Field(default=0.0588, ge=0, le=10)
    demand_charge_usd_per_kva_month: float = Field(default=7.78, ge=0, le=10000)

    @model_validator(mode="after")
    def validate_guardrails(self) -> "OperationalGuardrailsInput":
        if self.risk_high_ratio <= self.risk_medium_ratio:
            raise ValueError("risk_high_ratio must be greater than risk_medium_ratio.")
        for name, value in self.facility_limit_overrides_kva.items():
            if not str(name).strip() or not 0 < float(value) <= 100000:
                raise ValueError("Facility limit overrides require a non-empty facility ID and a value between 0 and 100,000 kVA.")
        for name, value in self.critical_floor_overrides_kva.items():
            if not str(name).strip() or not 0 <= float(value) <= 100000:
                raise ValueError("Critical-floor overrides require a non-empty facility ID and a value between 0 and 100,000 kVA.")
            limit = self.facility_limit_overrides_kva.get(name)
            if limit is not None and float(value) > float(limit):
                raise ValueError(f"Critical floor for {name} cannot exceed its facility limit.")
        return self


class ModelSelectionSettingsInput(BaseModel):
    selection_mode: Literal[
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
    ] = "automatic"



class SystemSettingsInput(BaseModel):
    simulation: SimulationRuntimeSettingsInput
    model: ModelSelectionSettingsInput = Field(default_factory=ModelSelectionSettingsInput)
    adaptive_learning: AdaptiveLearningSettingsInput
    operational: OperationalGuardrailsInput = Field(default_factory=OperationalGuardrailsInput)


class AdminLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class AdminPasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class ControlledTestRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=100)
    facility_id: str = Field(min_length=1, max_length=120)
    values_kva: list[float] = Field(min_length=4, max_length=4)
    model_mode: Literal[
        "automatic",
        "gradient_boosting",
        "lstm",
        "transformer",
        "hybrid_gb_lstm",
        "hybrid_gb_transformer",
        "hybrid_lstm_transformer",
        "hybrid_all",
    ] | None = None
