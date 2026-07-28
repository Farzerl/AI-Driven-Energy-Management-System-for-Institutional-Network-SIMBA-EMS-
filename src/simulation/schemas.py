from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ControllerMode = Literal["ai_assisted", "simple_rule", "manual", "no_control"]


class SimulationResetRequest(BaseModel):
    scenario_id: str = "campus_peak_replay"
    controller_mode: ControllerMode = "ai_assisted"


class SimulationStepRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=48)


class SimulationActionRequest(BaseModel):
    facility_id: str = Field(min_length=2, max_length=80)
    action: Literal["defer_load", "shed_load"]
    load_group: str = Field(min_length=2, max_length=80)
    reduction_kva: float = Field(gt=0, le=10_000)
    duration_minutes: int = Field(default=30, ge=30, le=240, multiple_of=30)
    approved_by_operator: bool = True
    operator: str = Field(default="demo-operator", min_length=2, max_length=80)
    note: str = Field(default="", max_length=500)


class SimulationApprovalRequest(BaseModel):
    request_id: str | None = Field(default=None, min_length=8, max_length=120)
    recommendation_ids: list[str] = Field(default_factory=list, max_length=20)
    operator: str = Field(default="dashboard-operator", min_length=2, max_length=80)


class SimulationRecommendationDecisionRequest(BaseModel):
    request_id: str | None = Field(default=None, min_length=8, max_length=120)
    recommendation_id: str = Field(min_length=8, max_length=160)
    decision: Literal["approve", "acknowledge", "disapprove"]
    operator: str = Field(default="dashboard-operator", min_length=2, max_length=80)
    note: str = Field(default="", max_length=500)


class SimulationPlaybackRequest(BaseModel):
    action: Literal["start", "stop"]
