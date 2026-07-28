from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

LoadClass = Literal["critical", "deferrable", "sheddable"]


@dataclass(frozen=True)
class LoadGroup:
    load_group_id: str
    name: str
    classification: LoadClass
    rated_kva: float
    minimum_service_fraction: float
    max_duration_minutes: int
    rebound_intervals: int
    priority: int
    permitted_start_hour: float = 0.0
    permitted_end_hour: float = 24.0


@dataclass(frozen=True)
class FacilityProfile:
    facility_id: str
    name: str
    sector: str
    limit_kva: float
    power_factor: float
    critical_floor_kva: float
    model_alias: str
    baseline_kva: tuple[float, ...]
    preroll_kva: tuple[float, ...]
    load_groups: tuple[LoadGroup, ...]
    profile_source: str = "authorised_half_hour_meter_replay"
    control_partition_basis: str = "engineering assumption"


@dataclass(frozen=True)
class ScenarioProfile:
    scenario_id: str
    name: str
    description: str
    start_time: datetime
    campus_limit_kva: float
    facilities: tuple[FacilityProfile, ...]
    demonstration_goal: str
    observed_peak_kva: float = 0.0
    planning_limit_basis: str = "configured operational planning threshold"
    source: str = "authorised_half_hour_meter_data"
    interval_minutes: int = 30


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_FILE = REPO_ROOT / "data" / "simulation" / "scenarios.json"


def _load_group(payload: dict[str, object]) -> LoadGroup:
    return LoadGroup(
        load_group_id=str(payload["load_group_id"]),
        name=str(payload["name"]),
        classification=str(payload["classification"]),  # type: ignore[arg-type]
        rated_kva=float(payload["rated_kva"]),
        minimum_service_fraction=float(payload["minimum_service_fraction"]),
        max_duration_minutes=int(payload["max_duration_minutes"]),
        rebound_intervals=int(payload["rebound_intervals"]),
        priority=int(payload["priority"]),
        permitted_start_hour=float(payload.get("permitted_start_hour", 0.0)),
        permitted_end_hour=float(payload.get("permitted_end_hour", 24.0)),
    )


def _load_facility(payload: dict[str, object]) -> FacilityProfile:
    return FacilityProfile(
        facility_id=str(payload["facility_id"]),
        name=str(payload["name"]),
        sector=str(payload["sector"]),
        limit_kva=float(payload["limit_kva"]),
        power_factor=float(payload["power_factor"]),
        critical_floor_kva=float(payload["critical_floor_kva"]),
        model_alias=str(payload["model_alias"]),
        baseline_kva=tuple(float(value) for value in payload["baseline_kva"]),  # type: ignore[arg-type]
        preroll_kva=tuple(float(value) for value in payload["preroll_kva"]),  # type: ignore[arg-type]
        load_groups=tuple(_load_group(item) for item in payload["load_groups"]),  # type: ignore[arg-type]
        profile_source=str(payload.get("profile_source", "authorised_half_hour_meter_replay")),
        control_partition_basis=str(payload.get("control_partition_basis", "engineering assumption")),
    )


def load_scenarios(path: Path = DEFAULT_SCENARIO_FILE) -> tuple[dict[str, ScenarioProfile], dict[str, object]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    scenarios: dict[str, ScenarioProfile] = {}
    for item in payload.get("scenarios", []):
        facilities = tuple(_load_facility(facility) for facility in item["facilities"])
        if not facilities:
            raise ValueError(f"Scenario {item.get('scenario_id')} contains no facilities.")
        step_counts = {len(facility.baseline_kva) for facility in facilities}
        history_counts = {len(facility.preroll_kva) for facility in facilities}
        if len(step_counts) != 1:
            raise ValueError(f"Scenario {item.get('scenario_id')} has inconsistent baseline lengths.")
        if min(history_counts) < 49:
            raise ValueError(f"Scenario {item.get('scenario_id')} requires at least 49 pre-roll readings per facility.")
        scenario = ScenarioProfile(
            scenario_id=str(item["scenario_id"]),
            name=str(item["name"]),
            description=str(item["description"]),
            start_time=datetime.fromisoformat(str(item["start_time"])),
            campus_limit_kva=float(item["campus_limit_kva"]),
            facilities=facilities,
            demonstration_goal=str(item["demonstration_goal"]),
            observed_peak_kva=float(item.get("observed_peak_kva", 0.0)),
            planning_limit_basis=str(item.get("planning_limit_basis", "configured operational planning threshold")),
            source=str(item.get("source", "authorised_half_hour_meter_data")),
            interval_minutes=int(item.get("interval_minutes", 30)),
        )
        scenarios[scenario.scenario_id] = scenario
    if not scenarios:
        raise ValueError("No software-in-the-loop scenarios were loaded.")
    metadata = {
        "schema_version": payload.get("schema_version"),
        "facility_count": payload.get("facility_count"),
        "claim_boundary": payload.get("claim_boundary"),
        "source": payload.get("generated_from"),
    }
    return scenarios, metadata


SCENARIOS, SCENARIO_METADATA = load_scenarios()
DEFAULT_SCENARIO_ID = next(iter(SCENARIOS))


def list_scenarios() -> list[dict[str, object]]:
    return [
        {
            "scenario_id": item.scenario_id,
            "name": item.name,
            "description": item.description,
            "start_time": item.start_time.isoformat(),
            "campus_limit_kva": item.campus_limit_kva,
            "observed_peak_kva": item.observed_peak_kva,
            "planning_limit_basis": item.planning_limit_basis,
            "facility_count": len(item.facilities),
            "total_steps": len(item.facilities[0].baseline_kva),
            "demonstration_goal": item.demonstration_goal,
            "source": item.source,
            "claim_boundary": SCENARIO_METADATA.get("claim_boundary"),
        }
        for item in SCENARIOS.values()
    ]
