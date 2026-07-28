from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Mapping

DEFAULT_SYSTEM_SETTINGS: dict[str, object] = {
    "simulation": {
        "scenario_id": "campus_peak_replay",
        "controller_mode": "ai_assisted",
        "playback_interval_seconds": 10.0,
        "pause_on_recommendation": False,
        "auto_compare_on_load": False,
        "auto_start": True,
    },
    "model": {
        "selection_mode": "automatic",
    },
    "adaptive_learning": {
        "enabled": True,
        "minimum_observations": 8,
        "correction_gain": 0.55,
        "maximum_correction_percent_of_limit": 5.0,
        "residual_window": 192,
        "retraining_interval_new_readings": 336,
    },
    "operational": {
        "campus_limit_override_kva": None,
        "facility_limit_overrides_kva": {},
        "critical_floor_overrides_kva": {},
        "risk_medium_ratio": 0.85,
        "risk_high_ratio": 0.95,
        "peak_energy_usd_per_kwh": 0.2173,
        "standard_energy_usd_per_kwh": 0.1150,
        "offpeak_energy_usd_per_kwh": 0.0588,
        "demand_charge_usd_per_kva_month": 7.78,
    },
}


def validate_system_settings(value: Mapping[str, object], available_scenarios: set[str]) -> dict[str, object]:
    data = copy.deepcopy(DEFAULT_SYSTEM_SETTINGS)
    for section in ("simulation", "model", "adaptive_learning", "operational"):
        if isinstance(value.get(section), Mapping):
            data[section].update(dict(value[section]))  # type: ignore[index,union-attr]

    simulation = dict(data["simulation"])
    scenario_id = str(simulation.get("scenario_id", "campus_peak_replay"))
    if scenario_id not in available_scenarios:
        scenario_id = "campus_peak_replay" if "campus_peak_replay" in available_scenarios else sorted(available_scenarios)[0]
    controller = str(simulation.get("controller_mode", "ai_assisted"))
    if controller not in {"ai_assisted", "simple_rule", "manual", "no_control"}:
        controller = "ai_assisted"
    simulation.update(
        {
            "scenario_id": scenario_id,
            "controller_mode": controller,
            "playback_interval_seconds": max(0.5, min(float(simulation.get("playback_interval_seconds", 10.0)), 30.0)),
            "pause_on_recommendation": bool(simulation.get("pause_on_recommendation", False)),
            "auto_compare_on_load": bool(simulation.get("auto_compare_on_load", False)),
            "auto_start": bool(simulation.get("auto_start", True)),
        }
    )


    model = dict(data["model"])
    selection_mode = str(model.get("selection_mode", "automatic")).strip().lower()
    allowed_models = {
        "automatic", "gradient_boosting", "lstm", "transformer",
        "hybrid_gb_lstm", "hybrid_gb_transformer",
        "hybrid_lstm_transformer", "hybrid_all",
        "chronos2", "hybrid_chronos_existing",
    }
    if selection_mode not in allowed_models:
        selection_mode = "automatic"
    model["selection_mode"] = selection_mode

    adaptive = dict(data["adaptive_learning"])
    adaptive.update(
        {
            "enabled": bool(adaptive.get("enabled", True)),
            "minimum_observations": max(4, min(int(adaptive.get("minimum_observations", 8)), 96)),
            "correction_gain": max(0.0, min(float(adaptive.get("correction_gain", 0.55)), 1.0)),
            "maximum_correction_percent_of_limit": max(0.0, min(float(adaptive.get("maximum_correction_percent_of_limit", 5.0)), 15.0)),
            "residual_window": max(48, min(int(adaptive.get("residual_window", 192)), 1000)),
            "retraining_interval_new_readings": max(96, min(int(adaptive.get("retraining_interval_new_readings", 336)), 10000)),
        }
    )

    operational = dict(data["operational"])
    facility_limits = {
        str(key): max(0.001, min(float(item), 100000.0))
        for key, item in dict(operational.get("facility_limit_overrides_kva", {})).items()
        if str(key).strip()
    }
    critical_floors = {
        str(key): max(0.0, min(float(item), 100000.0))
        for key, item in dict(operational.get("critical_floor_overrides_kva", {})).items()
        if str(key).strip()
    }
    for key, floor in critical_floors.items():
        if key in facility_limits and floor > facility_limits[key]:
            critical_floors[key] = facility_limits[key]
    medium = max(0.5, min(float(operational.get("risk_medium_ratio", 0.85)), 1.25))
    high = max(medium + 0.01, min(float(operational.get("risk_high_ratio", 0.95)), 1.5))
    campus_override = operational.get("campus_limit_override_kva")
    campus_override = None if campus_override in (None, "") else max(0.001, min(float(campus_override), 100000.0))
    operational = {
        "campus_limit_override_kva": campus_override,
        "facility_limit_overrides_kva": facility_limits,
        "critical_floor_overrides_kva": critical_floors,
        "risk_medium_ratio": medium,
        "risk_high_ratio": high,
        "peak_energy_usd_per_kwh": max(0.0, min(float(operational.get("peak_energy_usd_per_kwh", 0.2173)), 10.0)),
        "standard_energy_usd_per_kwh": max(0.0, min(float(operational.get("standard_energy_usd_per_kwh", 0.1150)), 10.0)),
        "offpeak_energy_usd_per_kwh": max(0.0, min(float(operational.get("offpeak_energy_usd_per_kwh", 0.0588)), 10.0)),
        "demand_charge_usd_per_kva_month": max(0.0, min(float(operational.get("demand_charge_usd_per_kva_month", 7.78)), 10000.0)),
    }
    return {"simulation": simulation, "model": model, "adaptive_learning": adaptive, "operational": operational, "version": 5}


class SystemSettingsStore:
    def __init__(self, path: Path, available_scenarios: set[str]) -> None:
        self.path = Path(path)
        self.available_scenarios = set(available_scenarios)
        self._lock = RLock()
        self._settings = validate_system_settings({}, self.available_scenarios)
        self.last_error: str | None = None
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, Mapping):
                    raise ValueError("System settings must contain a JSON object.")
                migrated = copy.deepcopy(dict(raw))
                if int(migrated.get("version", 0) or 0) < 5:
                    simulation = dict(migrated.get("simulation", {}))
                    if float(simulation.get("playback_interval_seconds", 2.5) or 2.5) == 2.5:
                        simulation["playback_interval_seconds"] = 10.0
                    if str(simulation.get("controller_mode", "ai_assisted")) == "manual":
                        simulation["controller_mode"] = "ai_assisted"
                    simulation["pause_on_recommendation"] = False
                    simulation["auto_start"] = True
                    migrated["simulation"] = simulation
                self._settings = validate_system_settings(migrated, self.available_scenarios)
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return copy.deepcopy({**self._settings, "settings_error": self.last_error})

    def update(self, changes: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            merged = copy.deepcopy(self._settings)
            for section in ("simulation", "model", "adaptive_learning", "operational"):
                if isinstance(changes.get(section), Mapping):
                    merged.setdefault(section, {})
                    merged[section].update(dict(changes[section]))  # type: ignore[index,union-attr]
            validated = validate_system_settings(merged, self.available_scenarios)
            validated["updated_utc"] = datetime.now(timezone.utc).isoformat()
            validated["revision"] = int(self._settings.get("revision", 0)) + 1
            self._write(validated)
            self._settings = validated
            self.last_error = None
            return self.snapshot()

    def _write(self, value: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.path)
