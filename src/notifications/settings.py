from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Mapping

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
VALID_MODES = {"disabled", "dry_run", "live"}
VALID_RISKS = {"medium", "high"}
VALID_GMAIL_SECURITY = {"ssl", "starttls"}
MAX_EMAIL_RECIPIENTS = 100


def recipient_id(address: str) -> str:
    normalized = address.strip().lower()
    return hashlib.sha256(f"email|{normalized}".encode("utf-8")).hexdigest()[:16]


def validate_email(value: str) -> str:
    address = value.strip()
    if not EMAIL_PATTERN.fullmatch(address):
        raise ValueError(f"Invalid email address: {value!r}")
    return address


def normalize_recipients(values: object) -> list[dict[str, object]]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("Email recipients must be a list.")
    if len(values) > MAX_EMAIL_RECIPIENTS:
        raise ValueError(f"A maximum of {MAX_EMAIL_RECIPIENTS} email recipients is supported.")

    output: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, str):
            raw_address = item
            label = ""
            enabled = True
            supplied_id = ""
        elif isinstance(item, Mapping):
            raw_address = str(item.get("address", ""))
            label = str(item.get("label", "")).strip()[:80]
            enabled = bool(item.get("enabled", True))
            supplied_id = str(item.get("id", "")).strip()
        else:
            raise ValueError("Invalid email recipient entry.")

        address = validate_email(raw_address)
        key = address.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "id": supplied_id or recipient_id(address),
                "label": label,
                "address": address,
                "enabled": enabled,
            }
        )
    return output


def _deep_merge(current: dict[str, object], update: Mapping[str, object]) -> dict[str, object]:
    merged = copy.deepcopy(current)
    for key, value in update.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(dict(merged[key]), value)
        elif value is not None:
            merged[key] = copy.deepcopy(value)
    return merged


def validate_settings(value: Mapping[str, object]) -> dict[str, object]:
    raw = copy.deepcopy(dict(value))
    # Persist only the active email-channel schema. Unsupported legacy keys are discarded.
    data = {
        key: raw[key]
        for key in (
            "mode", "minimum_risk", "cooldown_minutes", "delivery_attempts",
            "retry_backoff_seconds", "dashboard_url", "email", "version",
            "revision", "updated_utc",
        )
        if key in raw
    }

    mode = str(data.get("mode", "dry_run")).strip().lower()
    if mode not in VALID_MODES:
        raise ValueError("Notification mode must be disabled, dry_run, or live.")
    minimum_risk = str(data.get("minimum_risk", "high")).strip().lower()
    if minimum_risk not in VALID_RISKS:
        raise ValueError("Minimum risk must be medium or high.")

    data["mode"] = mode
    data["minimum_risk"] = minimum_risk
    data["cooldown_minutes"] = max(0, min(int(data.get("cooldown_minutes", 60)), 1440))
    data["delivery_attempts"] = max(1, min(int(data.get("delivery_attempts", 2)), 5))
    data["retry_backoff_seconds"] = max(
        0.0, min(float(data.get("retry_backoff_seconds", 0.75)), 10.0)
    )
    data["dashboard_url"] = str(data.get("dashboard_url", "")).strip()[:500]

    email = dict(data.get("email", {}))
    email["enabled"] = bool(email.get("enabled", True))
    email["recipients"] = normalize_recipients(email.get("recipients", []))
    email["gmail_user"] = str(email.get("gmail_user", "")).strip()
    if email["gmail_user"]:
        validate_email(email["gmail_user"])
    email["gmail_app_password"] = (
        str(email.get("gmail_app_password", "")).replace(" ", "").strip()
    )
    email["gmail_host"] = (
        str(email.get("gmail_host", "smtp.gmail.com")).strip() or "smtp.gmail.com"
    )
    email["gmail_port"] = int(email.get("gmail_port", 465))
    if email["gmail_port"] not in {465, 587}:
        raise ValueError("Gmail port must be 465 or 587.")
    inferred_security = "ssl" if email["gmail_port"] == 465 else "starttls"
    email["gmail_security"] = str(
        email.get("gmail_security", inferred_security)
    ).strip().lower() or inferred_security
    if email["gmail_security"] not in VALID_GMAIL_SECURITY:
        raise ValueError("Gmail security must be ssl or starttls.")
    if (email["gmail_port"], email["gmail_security"]) not in {
        (465, "ssl"),
        (587, "starttls"),
    }:
        raise ValueError(
            "Gmail transport mismatch. Use port 465 with SSL, or port 587 with STARTTLS."
        )

    data["email"] = email
    data["version"] = 3
    return data


class NotificationSettingsStore:
    """Atomic local email-alert settings store with secret redaction."""

    def __init__(self, path: Path, defaults: Mapping[str, object]) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self._defaults = validate_settings(defaults)
        self._settings = copy.deepcopy(self._defaults)
        self.last_error: str | None = None
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(loaded, Mapping):
                    raise ValueError("Settings file must contain a JSON object.")
                self._settings = validate_settings(_deep_merge(self._defaults, loaded))
                self.last_error = None
            except Exception as exc:
                self.last_error = f"Settings file could not be loaded: {exc}"
                self._settings = copy.deepcopy(self._defaults)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return copy.deepcopy(self._settings)

    def public_snapshot(self) -> dict[str, object]:
        with self._lock:
            data = copy.deepcopy(self._settings)
        email = dict(data.get("email", {}))
        email["gmail_app_password_set"] = bool(email.pop("gmail_app_password", ""))
        data["email"] = email
        data["settings_error"] = self.last_error
        return data

    def update(self, changes: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            safe_changes = dict(changes)
            candidate = _deep_merge(self._settings, safe_changes)
            candidate = self._apply_secret_controls(candidate, safe_changes)
            validated = validate_settings(candidate)
            validated["updated_utc"] = datetime.now(timezone.utc).isoformat()
            validated["revision"] = int(self._settings.get("revision", 0)) + 1
            self._write_atomic(validated)
            self._settings = validated
            self.last_error = None
            return self.public_snapshot()

    def _apply_secret_controls(
        self,
        candidate: dict[str, object],
        changes: Mapping[str, object],
    ) -> dict[str, object]:
        current = self._settings
        email_change = (
            dict(changes.get("email", {}))
            if isinstance(changes.get("email"), Mapping)
            else {}
        )
        email_candidate = dict(candidate.get("email", {}))
        current_email = dict(current.get("email", {}))
        supplied_secret = str(email_change.get("gmail_app_password", "")).strip()
        if not supplied_secret:
            email_candidate["gmail_app_password"] = current_email.get(
                "gmail_app_password", ""
            )
        if bool(email_change.get("clear_gmail_app_password", False)):
            email_candidate["gmail_app_password"] = ""
        candidate["email"] = email_candidate
        return candidate

    def _write_atomic(self, data: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
