from __future__ import annotations

import os
import smtplib
import ssl
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from threading import Lock
from typing import Iterable, Mapping

from src.notifications.settings import (
    NotificationSettingsStore,
    normalize_recipients,
    validate_email,
)
from src.notifications.store import NotificationStore


def _load_local_env() -> None:
    path = Path(__file__).resolve().parents[2] / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _recipient_env(value: str) -> list[dict[str, object]]:
    entries = [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    return normalize_recipients(entries)


@dataclass(frozen=True)
class NotificationConfig:
    mode: str
    minimum_risk: str
    cooldown_minutes: int
    dashboard_url: str
    email_enabled: bool
    email_recipients: tuple[dict[str, object], ...]
    gmail_user: str
    gmail_app_password: str
    gmail_host: str
    gmail_port: int
    gmail_security: str
    delivery_attempts: int = 2
    retry_backoff_seconds: float = 0.75

    @classmethod
    def from_env(cls) -> "NotificationConfig":
        _load_local_env()
        port = int(os.getenv("SIMBA_GMAIL_PORT", "465"))
        return cls(
            mode=os.getenv("SIMBA_NOTIFICATION_MODE", "dry_run").strip().lower(),
            minimum_risk=os.getenv("SIMBA_ALERT_MIN_RISK", "high").strip().lower(),
            cooldown_minutes=min(
                max(int(os.getenv("SIMBA_ALERT_COOLDOWN_MINUTES", "60")), 0), 1440
            ),
            dashboard_url=os.getenv(
                "SIMBA_DASHBOARD_URL", "http://127.0.0.1:8000/?tab=operations"
            ).strip(),
            email_enabled=os.getenv("SIMBA_EMAIL_ENABLED", "true").strip().lower()
            not in {"0", "false", "no"},
            email_recipients=tuple(
                _recipient_env(os.getenv("SIMBA_ALERT_EMAIL_TO", ""))
            ),
            gmail_user=os.getenv("SIMBA_GMAIL_USER", "").strip(),
            gmail_app_password=os.getenv("SIMBA_GMAIL_APP_PASSWORD", "")
            .replace(" ", "")
            .strip(),
            gmail_host=os.getenv("SIMBA_GMAIL_HOST", "smtp.gmail.com").strip(),
            gmail_port=port,
            gmail_security=os.getenv(
                "SIMBA_GMAIL_SECURITY", "ssl" if port == 465 else "starttls"
            )
            .strip()
            .lower(),
            delivery_attempts=max(
                int(os.getenv("SIMBA_NOTIFICATION_DELIVERY_ATTEMPTS", "2")), 1
            ),
            retry_backoff_seconds=max(
                float(os.getenv("SIMBA_NOTIFICATION_RETRY_BACKOFF_SECONDS", "0.75")),
                0.0,
            ),
        )

    @classmethod
    def from_settings(cls, data: Mapping[str, object]) -> "NotificationConfig":
        email = dict(data.get("email", {}))
        port = int(email.get("gmail_port", 465))
        return cls(
            mode=str(data.get("mode", "dry_run")),
            minimum_risk=str(data.get("minimum_risk", "high")),
            cooldown_minutes=int(data.get("cooldown_minutes", 60)),
            dashboard_url=str(data.get("dashboard_url", "")),
            email_enabled=bool(email.get("enabled", True)),
            email_recipients=tuple(dict(item) for item in email.get("recipients", [])),
            gmail_user=str(email.get("gmail_user", "")),
            gmail_app_password=str(email.get("gmail_app_password", "")),
            gmail_host=str(email.get("gmail_host", "smtp.gmail.com")),
            gmail_port=port,
            gmail_security=str(
                email.get("gmail_security", "ssl" if port == 465 else "starttls")
            ),
            delivery_attempts=int(data.get("delivery_attempts", 2)),
            retry_backoff_seconds=float(data.get("retry_backoff_seconds", 0.75)),
        )

    def to_settings(self) -> dict[str, object]:
        return {
            "version": 3,
            "revision": 0,
            "mode": self.mode,
            "minimum_risk": self.minimum_risk,
            "cooldown_minutes": self.cooldown_minutes,
            "dashboard_url": self.dashboard_url,
            "delivery_attempts": self.delivery_attempts,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "email": {
                "enabled": self.email_enabled,
                "recipients": list(self.email_recipients),
                "gmail_user": self.gmail_user,
                "gmail_app_password": self.gmail_app_password,
                "gmail_host": self.gmail_host,
                "gmail_port": self.gmail_port,
                "gmail_security": self.gmail_security,
            },
        }


class NotificationService:
    RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

    def __init__(
        self,
        store_path: Path,
        config: NotificationConfig | None = None,
        settings_path: Path | None = None,
    ) -> None:
        base = config or NotificationConfig.from_env()
        self.store = NotificationStore(store_path)
        self.settings = NotificationSettingsStore(
            settings_path or Path(store_path).with_name("notification_settings.json"),
            base.to_settings(),
        )
        self._dispatch_lock = Lock()

    @property
    def config(self) -> NotificationConfig:
        return NotificationConfig.from_settings(self.settings.snapshot())

    @staticmethod
    def _mask_email(value: str) -> str:
        if not value or "@" not in value:
            return "Not configured"
        local, domain = value.split("@", 1)
        shown = local[:2] + "***" if len(local) > 2 else "***"
        return f"{shown}@{domain}"

    @staticmethod
    def _enabled(recipients: tuple[dict[str, object], ...]) -> list[dict[str, object]]:
        return [dict(item) for item in recipients if bool(item.get("enabled", True))]

    def public_settings(self) -> dict[str, object]:
        return self.settings.public_snapshot()

    def update_settings(self, changes: Mapping[str, object]) -> dict[str, object]:
        return self.settings.update(changes)

    def _email_configuration_issues(self, config: NotificationConfig) -> list[str]:
        issues: list[str] = []
        if not config.gmail_user:
            issues.append("Gmail sender address is missing")
        if not config.gmail_app_password:
            issues.append("Gmail app password is not stored")
        if not config.gmail_host:
            issues.append("SMTP host is missing")
        if (config.gmail_port, config.gmail_security) not in {
            (465, "ssl"),
            (587, "starttls"),
        }:
            issues.append(
                "SMTP transport mismatch: use port 465 with SSL or port 587 with STARTTLS"
            )
        return issues

    @staticmethod
    def _email_provider_name(config: NotificationConfig) -> str:
        transport = "SSL" if config.gmail_security == "ssl" else "STARTTLS"
        return f"Gmail SMTP over {transport}"

    def _configuration_error(self, config: NotificationConfig) -> str:
        issues = self._email_configuration_issues(config)
        return "; ".join(issues) if issues else "Gmail SMTP configuration is incomplete."

    def status(self) -> dict[str, object]:
        config = self.config
        emails = self._enabled(config.email_recipients)
        issues = self._email_configuration_issues(config)
        ready = bool(config.email_enabled and emails and not issues)
        public = self.public_settings()
        return {
            "mode": config.mode,
            "minimum_risk": config.minimum_risk,
            "cooldown_minutes": config.cooldown_minutes,
            "delivery_attempts": config.delivery_attempts,
            "approval_channel": "dashboard_only",
            "dashboard_url": config.dashboard_url,
            "settings_revision": public.get("revision", 0),
            "settings_error": public.get("settings_error"),
            "email": {
                "enabled": config.email_enabled,
                "configured": ready,
                "recipient_count": len(emails),
                "recipient": self._mask_email(str(emails[0]["address"]))
                if emails
                else "Not configured",
                "recipients": [
                    {
                        "id": item.get("id"),
                        "label": item.get("label", ""),
                        "address": self._mask_email(str(item.get("address", ""))),
                    }
                    for item in emails
                ],
                "provider": self._email_provider_name(config),
                "smtp_host": config.gmail_host,
                "smtp_port": config.gmail_port,
                "security": config.gmail_security,
                "app_password_set": bool(config.gmail_app_password),
                "configuration_issues": issues,
            },
            "future_channels": {
                "gsm": "Reserved for a later pilot after a suitable modem or provider is selected."
            },
            "last_events": self.store.latest(8),
        }

    def _qualifies(self, alert: Mapping[str, object], config: NotificationConfig) -> bool:
        risk = str(alert.get("risk", "low"))
        return self.RISK_ORDER.get(risk, 0) >= self.RISK_ORDER.get(
            config.minimum_risk, 2
        )

    def _cooldown_active(
        self,
        alert: Mapping[str, object],
        recipient_id: str,
        config: NotificationConfig,
    ) -> bool:
        latest = self.store.latest_for(
            str(alert.get("facility_name", "")), "email", recipient_id
        )
        if not latest or config.cooldown_minutes <= 0:
            return False
        if str(latest.get("status", "")) in {"failed", "disabled"}:
            return False
        timestamp = latest.get("recorded_utc")
        if not timestamp:
            return False
        previous = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - previous < timedelta(
            minutes=config.cooldown_minutes
        )

    @staticmethod
    def _subject(alert: Mapping[str, object]) -> str:
        return (
            f"SIMBA EMS {str(alert.get('risk', '')).upper()} alert: "
            f"{alert.get('facility_name', '')}"
        )

    @staticmethod
    def _message(alert: Mapping[str, object], dashboard_url: str) -> str:
        return (
            "SIMBA EMS demand warning\n\n"
            f"Facility: {alert.get('facility_name')}\n"
            f"Risk: {str(alert.get('risk', '')).upper()}\n"
            f"Lead time: {alert.get('risk_lead_minutes')} minutes\n"
            f"Current demand: {float(alert.get('current_kva', 0)):.1f} kVA\n"
            f"Forecast: {float(alert.get('forecast_kva', 0)):.1f} kVA\n"
            "Conservative upper forecast: "
            f"{float(alert.get('forecast_upper_kva', alert.get('forecast_kva', 0))):.1f} kVA\n"
            f"Facility limit: {float(alert.get('facility_limit_kva', 0)):.1f} kVA\n"
            f"Recommended response: {alert.get('recommended_action')}\n\n"
            "For safety, this email cannot approve or execute an electrical action. "
            f"Review and approve only in the SIMBA EMS dashboard: {dashboard_url}"
        )

    def _record(
        self,
        *,
        alert: Mapping[str, object],
        status: str,
        detail: str,
        dedupe_key: str,
        recipient: Mapping[str, object],
        provider: str,
        attempt_count: int = 0,
    ) -> dict[str, object]:
        raw_address = str(recipient.get("address", ""))
        row = {
            "notification_id": uuid.uuid4().hex[:24],
            "dedupe_key": dedupe_key,
            "alert_id": str(alert.get("alert_id", "")),
            "facility_name": str(alert.get("facility_name", "")),
            "risk": str(alert.get("risk", "")),
            "channel": "email",
            "provider": provider,
            "recipient_id": str(recipient.get("id", "")),
            "recipient_label": str(recipient.get("label", "")),
            "recipient_masked": self._mask_email(raw_address),
            "status": status,
            "attempt_count": attempt_count,
            "detail": detail[:500],
            "approval_channel": "dashboard_only",
            "recorded_utc": datetime.now(timezone.utc).isoformat(),
        }
        self.store.append(row)
        return row

    def _send_email(
        self,
        alert: Mapping[str, object],
        recipient: str,
        config: NotificationConfig,
    ) -> str:
        message = EmailMessage()
        message["From"] = config.gmail_user
        message["To"] = recipient
        message["Subject"] = self._subject(alert)
        message.set_content(self._message(alert, config.dashboard_url))
        context = ssl.create_default_context()

        if (config.gmail_port, config.gmail_security) not in {
            (465, "ssl"),
            (587, "starttls"),
        }:
            raise ValueError(
                "Gmail transport mismatch. Use port 465 with SSL, or port 587 with STARTTLS."
            )

        if config.gmail_security == "ssl":
            with smtplib.SMTP_SSL(
                config.gmail_host,
                config.gmail_port,
                context=context,
                timeout=20,
            ) as smtp:
                smtp.login(config.gmail_user, config.gmail_app_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(config.gmail_host, config.gmail_port, timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(config.gmail_user, config.gmail_app_password)
                smtp.send_message(message)
        return "Gmail accepted the alert message."

    def _deliver(
        self,
        alert: Mapping[str, object],
        recipient: Mapping[str, object],
        config: NotificationConfig,
    ) -> tuple[str, int]:
        address = str(recipient.get("address", ""))
        final_error: Exception | None = None
        for attempt in range(1, config.delivery_attempts + 1):
            try:
                return self._send_email(alert, address, config), attempt
            except Exception as exc:
                final_error = exc
                if attempt < config.delivery_attempts and config.retry_backoff_seconds > 0:
                    time.sleep(config.retry_backoff_seconds * attempt)
        raise RuntimeError(str(final_error or "Email delivery failed."))

    def dispatch(self, alerts: Iterable[Mapping[str, object]]) -> dict[str, object]:
        events: list[dict[str, object]] = []
        config = self.config
        with self._dispatch_lock:
            for alert in alerts:
                if not self._qualifies(alert, config) or not config.email_enabled:
                    continue
                recipients = self._enabled(config.email_recipients)
                provider = self._email_provider_name(config)
                provider_ready = not self._email_configuration_issues(config)
                for recipient in recipients:
                    recipient_key = str(recipient.get("id", ""))
                    dedupe_key = f"{alert.get('alert_id')}|email|{recipient_key}"
                    if self.store.contains(dedupe_key) or self._cooldown_active(
                        alert, recipient_key, config
                    ):
                        continue
                    if config.mode == "disabled":
                        events.append(
                            self._record(
                                alert=alert,
                                status="disabled",
                                detail="Notification mode is disabled.",
                                dedupe_key=dedupe_key,
                                recipient=recipient,
                                provider=provider,
                            )
                        )
                        continue
                    if config.mode != "live":
                        events.append(
                            self._record(
                                alert=alert,
                                status="dry_run",
                                detail=(
                                    "Email composed and logged. No external message was sent. "
                                    "Change delivery mode to live after verifying Gmail settings."
                                ),
                                dedupe_key=dedupe_key,
                                recipient=recipient,
                                provider=provider,
                            )
                        )
                        continue
                    if not provider_ready:
                        events.append(
                            self._record(
                                alert=alert,
                                status="failed",
                                detail=self._configuration_error(config),
                                dedupe_key=dedupe_key,
                                recipient=recipient,
                                provider=provider,
                            )
                        )
                        continue
                    try:
                        detail, attempts = self._deliver(alert, recipient, config)
                        events.append(
                            self._record(
                                alert=alert,
                                status="sent",
                                detail=detail,
                                dedupe_key=dedupe_key,
                                recipient=recipient,
                                provider=provider,
                                attempt_count=attempts,
                            )
                        )
                    except Exception as exc:
                        events.append(
                            self._record(
                                alert=alert,
                                status="failed",
                                detail=str(exc),
                                dedupe_key=dedupe_key,
                                recipient=recipient,
                                provider=provider,
                                attempt_count=config.delivery_attempts,
                            )
                        )
        return {"processed": len(events), "events": events}

    def test(self, channel: str = "email", recipient: str | None = None) -> dict[str, object]:
        if channel != "email":
            raise ValueError("Only the email notification channel is enabled in this release.")

        config = self.config
        configured = self._enabled(config.email_recipients)
        if recipient:
            address = validate_email(recipient)
            target = {
                "id": f"test-{uuid.uuid4().hex[:12]}",
                "label": "Manual test",
                "address": address,
                "enabled": True,
            }
        elif configured:
            target = configured[0]
        else:
            raise ValueError("No email recipient is configured.")

        alert = {
            "alert_id": f"test-{uuid.uuid4().hex[:16]}",
            "facility_name": "Notification test",
            "risk": "high",
            "risk_lead_minutes": 30,
            "current_kva": 900.0,
            "forecast_kva": 980.0,
            "forecast_upper_kva": 1010.0,
            "facility_limit_kva": 950.0,
            "recommended_action": "Open the dashboard and review the test alert.",
        }
        provider = self._email_provider_name(config)
        provider_ready = not self._email_configuration_issues(config)
        dedupe_key = f"{alert['alert_id']}|email|{target['id']}"

        if config.mode != "live":
            row = self._record(
                alert=alert,
                status="dry_run",
                detail="Test email composed successfully. External sending is disabled.",
                dedupe_key=dedupe_key,
                recipient=target,
                provider=provider,
            )
            return {"status": "dry_run", "event": row}
        if not provider_ready:
            row = self._record(
                alert=alert,
                status="failed",
                detail=self._configuration_error(config),
                dedupe_key=dedupe_key,
                recipient=target,
                provider=provider,
            )
            return {"status": "failed", "event": row}
        try:
            detail, attempts = self._deliver(alert, target, config)
            row = self._record(
                alert=alert,
                status="sent",
                detail=detail,
                dedupe_key=dedupe_key,
                recipient=target,
                provider=provider,
                attempt_count=attempts,
            )
            return {"status": "sent", "event": row}
        except Exception as exc:
            row = self._record(
                alert=alert,
                status="failed",
                detail=str(exc),
                dedupe_key=dedupe_key,
                recipient=target,
                provider=provider,
                attempt_count=config.delivery_attempts,
            )
            return {"status": "failed", "event": row}
