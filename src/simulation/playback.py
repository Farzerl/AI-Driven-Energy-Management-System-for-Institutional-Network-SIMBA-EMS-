from __future__ import annotations

import copy
import time
from threading import Event, RLock, Thread
from typing import Callable, Mapping

from src.simulation.engine import SimulationEngine


class SimulationPlaybackController:
    """Server-side replay clock.

    The clock is intentionally separate from the browser. Once an authenticated
    administrator starts replay, the simulation continues even if the Admin dialog
    is closed. It pauses before an operator-confirmed action and can resume after a
    successful dashboard approval.
    """

    def __init__(self, engine: SimulationEngine, settings_supplier: Callable[[], Mapping[str, object]]) -> None:
        self.engine = engine
        self.settings_supplier = settings_supplier
        self._lock = RLock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._status = "stopped"
        self._reason = "not_started"
        self._resume_after_approval = False
        self._last_error: str | None = None
        self._last_step_monotonic: float | None = None
        self._ignore_recommendation_cursor: int | None = None

    def _simulation_settings(self) -> dict[str, object]:
        value = self.settings_supplier()
        section = value.get("simulation", {}) if isinstance(value, Mapping) else {}
        return dict(section) if isinstance(section, Mapping) else {}

    def status(self) -> dict[str, object]:
        with self._lock:
            alive = bool(self._thread and self._thread.is_alive())
            return {
                "status": self._status,
                "running": alive and self._status == "running",
                "reason": self._reason,
                "resume_after_approval": self._resume_after_approval,
                "last_error": self._last_error,
                "last_step_monotonic": self._last_step_monotonic,
                "playback_interval_seconds": float(
                    self._simulation_settings().get("playback_interval_seconds", 10.0)
                ),
            }

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            current = self.engine.state()
            if current.get("status") == "completed":
                raise ValueError("Replay is complete. Reset the replay before starting it again.")
            self._stop_event = Event()
            self._status = "running"
            self._reason = "administrator_started"
            self._last_error = None
            self._thread = Thread(target=self._run, name="simba-replay-clock", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self, reason: str = "administrator_stopped") -> dict[str, object]:
        with self._lock:
            self._stop_event.set()
            self._status = "stopped"
            self._reason = reason
            self._resume_after_approval = False
            return self.status()

    def resume_after_approval(self) -> dict[str, object]:
        with self._lock:
            should_resume = self._resume_after_approval
            self._resume_after_approval = False
            if should_resume:
                self._ignore_recommendation_cursor = int(self.engine.state().get("cursor", 0))
        if should_resume:
            return self.start()
        return self.status()

    def resume_after_operator_decision(self) -> dict[str, object]:
        """Resume a replay paused for any dashboard decision, not only approval."""
        return self.resume_after_approval()

    def _pause_for_approval(self) -> None:
        with self._lock:
            self._status = "paused"
            self._reason = "operator_approval_required"
            self._resume_after_approval = True
            self._stop_event.set()

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                snapshot = self.engine.state()
                if snapshot.get("status") == "completed":
                    with self._lock:
                        self._status = "stopped"
                        self._reason = "replay_completed"
                        self._resume_after_approval = False
                    return

                settings = self._simulation_settings()
                pause_on_recommendation = bool(settings.get("pause_on_recommendation", False))
                recommendations = snapshot.get("recommendations", [])
                has_recommendation = bool(recommendations) or bool(
                    dict(snapshot.get("recommendation", {})).get("available")
                )
                current_cursor = int(snapshot.get("cursor", 0))
                ignore_current = self._ignore_recommendation_cursor == current_cursor
                if pause_on_recommendation and has_recommendation and not ignore_current:
                    self._pause_for_approval()
                    return

                interval = max(0.5, min(float(settings.get("playback_interval_seconds", 10.0)), 30.0))
                if self._stop_event.wait(interval):
                    return
                self.engine.step(1)
                with self._lock:
                    self._last_step_monotonic = time.monotonic()
                    self._ignore_recommendation_cursor = None
        except Exception as exc:  # defensive boundary for the daemon thread
            with self._lock:
                self._status = "error"
                self._reason = "playback_failure"
                self._last_error = str(exc)
                self._resume_after_approval = False
                self._stop_event.set()

    def snapshot_with_state(self) -> dict[str, object]:
        payload = self.engine.state()
        payload["playback"] = copy.deepcopy(self.status())
        return payload
