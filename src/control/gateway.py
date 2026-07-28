from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Mapping
from urllib import error, request


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class ControlGateway:
    """Dispatch operator-approved commands through a constrained adapter.

    The default ``simulation`` mode records a realistic command acknowledgement but
    never contacts electrical equipment. ``http`` mode is available for an authorised
    pilot gateway and is disabled unless SIMBA_CONTROL_ALLOW_LIVE is explicitly true.
    """

    ALLOWED_MODES = {"simulation", "dry_run", "http"}

    def __init__(
        self,
        *,
        mode: str | None = None,
        endpoint: str | None = None,
        token: str | None = None,
        allow_live: bool | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        configured_mode = str(mode or os.getenv("SIMBA_CONTROL_MODE", "simulation")).strip().lower()
        self.mode = configured_mode if configured_mode in self.ALLOWED_MODES else "simulation"
        self.endpoint = str(endpoint or os.getenv("SIMBA_CONTROL_ENDPOINT", "")).strip()
        self.token = str(token or os.getenv("SIMBA_CONTROL_TOKEN", "")).strip()
        self.allow_live = _truthy(os.getenv("SIMBA_CONTROL_ALLOW_LIVE")) if allow_live is None else bool(allow_live)
        self.timeout_seconds = max(
            1.0,
            min(float(timeout_seconds or os.getenv("SIMBA_CONTROL_TIMEOUT_SECONDS", "5")), 30.0),
        )
        self._lock = RLock()
        self._dispatch_count = 0
        self._failure_count = 0
        self._last_command: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            live_ready = self.mode == "http" and self.allow_live and bool(self.endpoint)
            return {
                "mode": self.mode,
                "ready": self.mode in {"simulation", "dry_run"} or live_ready,
                "live_enabled": live_ready,
                "endpoint_configured": bool(self.endpoint),
                "dispatch_count": self._dispatch_count,
                "failure_count": self._failure_count,
                "last_command": dict(self._last_command or {}),
                "safety_boundary": (
                    "Only operator-approved, configured non-critical load commands are dispatched. "
                    "Electrical protection, local interlocks and manual override remain authoritative."
                ),
            }

    @staticmethod
    def _command_payload(action: Mapping[str, Any]) -> dict[str, Any]:
        classification = str(action.get("classification", ""))
        if classification not in {"deferrable", "sheddable"}:
            raise ValueError("The control gateway accepts only configured non-critical load groups.")
        command = str(action.get("action", ""))
        if command not in {"defer_load", "shed_load"}:
            raise ValueError("Unsupported control command.")
        if not bool(action.get("approved_by_operator")):
            raise ValueError("A dashboard operator must approve the command before dispatch.")
        return {
            "command_id": uuid.uuid4().hex[:20],
            "facility_id": str(action.get("facility_id", "")),
            "facility_name": str(action.get("facility_name", "")),
            "load_group": str(action.get("load_group", "")),
            "load_group_name": str(action.get("load_group_name", "")),
            "command": command,
            "reduction_kva": float(action.get("reduction_kva", 0.0)),
            "duration_minutes": int(action.get("duration_minutes", 0)),
            "starts_at": str(action.get("starts_at", "")),
            "ends_at": str(action.get("ends_at", "")),
            "operator": str(action.get("operator", "")),
            "issued_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }

    def dispatch(self, action: Mapping[str, Any]) -> dict[str, Any]:
        payload = self._command_payload(action)
        try:
            if self.mode == "simulation":
                result = {
                    **payload,
                    "status": "simulated_acknowledged",
                    "transport": "software_in_the_loop",
                    "detail": "The approved command was applied to the software plant model.",
                }
            elif self.mode == "dry_run":
                result = {
                    **payload,
                    "status": "validated_not_sent",
                    "transport": "dry_run",
                    "detail": "The command passed safety validation but external dispatch is disabled.",
                }
            else:
                if not self.allow_live:
                    raise RuntimeError("Live control is locked. Set SIMBA_CONTROL_ALLOW_LIVE=true only for an authorised pilot.")
                if not self.endpoint:
                    raise RuntimeError("SIMBA_CONTROL_ENDPOINT is required for HTTP control mode.")
                body = json.dumps(payload).encode("utf-8")
                headers = {"Content-Type": "application/json", "Accept": "application/json"}
                if self.token:
                    headers["Authorization"] = f"Bearer {self.token}"
                http_request = request.Request(self.endpoint, data=body, headers=headers, method="POST")
                try:
                    with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                        response_body = response.read().decode("utf-8", errors="replace")
                        if not 200 <= int(response.status) < 300:
                            raise RuntimeError(f"Control gateway returned HTTP {response.status}.")
                except error.URLError as exc:
                    raise RuntimeError(f"Control gateway connection failed: {exc.reason}") from exc
                result = {
                    **payload,
                    "status": "gateway_accepted",
                    "transport": "http",
                    "detail": response_body[:500] or "The authorised gateway accepted the command.",
                }
            with self._lock:
                self._dispatch_count += 1
                self._last_command = dict(result)
            return result
        except Exception:
            with self._lock:
                self._failure_count += 1
            raise
