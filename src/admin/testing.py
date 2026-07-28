from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Mapping, Sequence

from src.live.model_manager import LiveModelManager
from src.simulation.profiles import SCENARIOS


class ControlledTestService:
    """Runs isolated test-value forecasts without mutating production stores or calibration."""

    def __init__(self, path: Path, model: LiveModelManager) -> None:
        self.path = Path(path)
        self.model = model
        self._lock = RLock()
        self._known: dict[str, dict[str, object]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                request_id = str(item.get("request_id", ""))
                if request_id:
                    self._known[request_id] = item
            except Exception:
                continue

    def _append(self, item: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(item, separators=(",", ":"), sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass

    @staticmethod
    def facilities() -> list[str]:
        names = {profile.model_alias for scenario in SCENARIOS.values() for profile in scenario.facilities}
        return sorted(names)

    def _history(self, facility_id: str) -> tuple[list[dict[str, object]], float]:
        for scenario in SCENARIOS.values():
            for profile in scenario.facilities:
                if profile.model_alias != facility_id:
                    continue
                start = scenario.start_time - timedelta(minutes=30 * len(profile.preroll_kva))
                rows = []
                for index, value in enumerate(profile.preroll_kva[-49:]):
                    timestamp = start + timedelta(minutes=30 * index)
                    rows.append(
                        {
                            "timestamp": timestamp.isoformat(),
                            "facility_id": facility_id,
                            "kva": float(value),
                            "kwh": float(value) * 0.5,
                            "kwh_is_measured": True,
                            "power_factor": float(profile.power_factor),
                            "data_origin": "diagnostic_reference",
                        }
                    )
                return rows, float(profile.power_factor)
        raise ValueError(f"No diagnostic history is configured for facility {facility_id!r}.")

    def run(
        self,
        *,
        request_id: str,
        facility_id: str,
        values_kva: Sequence[float],
        selected_mode: str | None = None,
    ) -> dict[str, object]:
        identifier = str(request_id).strip()
        if len(identifier) < 8 or len(identifier) > 100:
            raise ValueError("request_id must contain between 8 and 100 characters.")
        if facility_id not in self.facilities():
            raise ValueError(f"Unknown test facility: {facility_id}")
        if len(values_kva) != 4:
            raise ValueError("Exactly four half-hour test values are required.")
        values = [float(item) for item in values_kva]
        if any(not math.isfinite(item) for item in values):
            raise ValueError("Test values must be finite numbers.")
        if any(item < 0 or item > 100_000 for item in values):
            raise ValueError("Each test value must be between 0 and 100,000 kVA.")
        signature = hashlib.sha256(
            json.dumps(
                {
                    "facility_id": facility_id,
                    "values_kva": values,
                    "selected_mode": selected_mode or self.model.active_mode,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        with self._lock:
            previous = self._known.get(identifier)
            if previous:
                if previous.get("request_signature") != signature:
                    raise ValueError("This request_id was already used with different test values.")
                return {**previous, "idempotent_replay": True}
            rows, power_factor = self._history(facility_id)
            rows = rows[-49:]
            base_time = datetime.fromisoformat(str(rows[-5]["timestamp"]))
            retained = rows[:-4]
            injected: list[dict[str, object]] = []
            for index, value in enumerate(values, start=1):
                timestamp = base_time + timedelta(minutes=30 * index)
                injected.append(
                    {
                        "timestamp": timestamp.isoformat(),
                        "facility_id": facility_id,
                        "kva": value,
                        "kwh": value * 0.5,
                        "kwh_is_measured": False,
                        "power_factor": power_factor,
                        "data_origin": "manual_test",
                    }
                )
            test_rows = retained + injected
            forecasts = self.model.predict_horizons(
                test_rows,
                facility_id,
                mode_override=selected_mode,
            )
            limit = self.model.facility_limit(facility_id, values[-1])
            primary_name = min(forecasts, key=lambda key: float(forecasts[key]["minutes"]))
            primary = forecasts[primary_name]
            upper_ratio = float(primary["forecast_upper_kva"]) / max(limit, 1e-9)
            risk = "high" if upper_ratio >= float(primary["high_alert_threshold_ratio"]) else "medium" if upper_ratio >= float(primary["medium_alert_threshold_ratio"]) else "low"
            result = {
                "request_id": identifier,
                "request_signature": signature,
                "idempotent_replay": False,
                "facility_id": facility_id,
                "values_kva": values,
                "data_origin": "manual_test",
                "production_state_changed": False,
                "adaptive_learning_updated": False,
                "official_metrics_updated": False,
                "facility_limit_kva": round(limit, 3),
                "primary_horizon": primary_name,
                "risk": risk,
                "forecasts": forecasts,
                "generated_utc": datetime.now(timezone.utc).isoformat(),
            }
            self._known[identifier] = result
            self._append(result)
            return result

    def latest(self, limit: int = 20) -> list[dict[str, object]]:
        with self._lock:
            return list(self._known.values())[-max(1, min(limit, 100)) :][::-1]
