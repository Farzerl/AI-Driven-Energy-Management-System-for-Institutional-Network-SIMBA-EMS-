from __future__ import annotations

from pathlib import Path

from src.notifications.service import NotificationConfig, NotificationService


def recipient(address: str, label: str) -> dict[str, object]:
    return {
        "id": f"email-{label.lower().replace(' ', '-')}",
        "label": label,
        "address": address,
        "enabled": True,
    }


def config(mode: str = "dry_run") -> NotificationConfig:
    return NotificationConfig(
        mode=mode,
        minimum_risk="high",
        cooldown_minutes=60,
        dashboard_url="http://127.0.0.1:8000/?tab=operations",
        email_enabled=True,
        email_recipients=(
            recipient("manager@example.com", "Manager"),
            recipient("deputy@example.com", "Deputy"),
        ),
        gmail_user="alerts@example.com",
        gmail_app_password="test-password",
        gmail_host="smtp.gmail.com",
        gmail_port=465,
        gmail_security="ssl",
        delivery_attempts=2,
        retry_backoff_seconds=0,
    )


def alert() -> dict[str, object]:
    return {
        "alert_id": "alert-30-12345678",
        "facility_name": "Central Kitchens",
        "risk": "high",
        "risk_lead_minutes": 30,
        "current_kva": 900.0,
        "forecast_kva": 970.0,
        "forecast_upper_kva": 1010.0,
        "facility_limit_kva": 950.0,
        "recommended_action": "Review approved non-critical loads in the dashboard.",
    }


def test_dry_run_logs_every_enabled_email_recipient(tmp_path: Path) -> None:
    service = NotificationService(tmp_path / "events.jsonl", config())
    result = service.dispatch([alert()])
    assert result["processed"] == 2
    rows = service.store.latest(10)
    assert {row["channel"] for row in rows} == {"email"}
    assert len({row["recipient_id"] for row in rows}) == 2
    assert all(row["status"] == "dry_run" for row in rows)
    assert all(row["approval_channel"] == "dashboard_only" for row in rows)


def test_settings_update_supports_multiple_recipients_and_hides_secret(tmp_path: Path) -> None:
    service = NotificationService(
        tmp_path / "events.jsonl",
        config(),
        settings_path=tmp_path / "settings.json",
    )
    public = service.update_settings(
        {
            "mode": "live",
            "email": {
                "recipients": [
                    {"label": "Ops", "address": "ops@example.com", "enabled": True},
                    {"label": "Bursar", "address": "bursar@example.com", "enabled": True},
                ],
                "gmail_app_password": "new-secret",
            },
        }
    )
    assert public["mode"] == "live"
    assert len(public["email"]["recipients"]) == 2
    assert public["email"]["gmail_app_password_set"] is True
    assert "gmail_app_password" not in public["email"]
    assert "phone" not in public
    assert (tmp_path / "settings.json").exists()


def test_unsupported_legacy_fields_are_discarded_during_migration(tmp_path: Path) -> None:
    service = NotificationService(
        tmp_path / "events.jsonl",
        config(),
        settings_path=tmp_path / "settings.json",
    )
    public = service.update_settings(
        {
            "legacy_channel": {"enabled": True},
            "email": {"recipients": [{"address": "ops@example.com", "enabled": True}]},
        }
    )
    assert "legacy_channel" not in public


def test_non_email_test_channel_is_rejected(tmp_path: Path) -> None:
    service = NotificationService(tmp_path / "events.jsonl", config())
    try:
        service.test("unsupported", "ops@example.com")
    except ValueError as exc:
        assert "Only the email notification channel" in str(exc)
    else:
        raise AssertionError("unsupported channel should be rejected")

def test_failed_delivery_remains_retryable(tmp_path: Path) -> None:
    service = NotificationService(tmp_path / "events.jsonl", config(mode="live"))
    attempts = {"count": 0}

    def fail_then_succeed(event, target, active_config):  # type: ignore[no-untyped-def]
        del event, target, active_config
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise RuntimeError("temporary provider failure")
        return "accepted", 1

    service._deliver = fail_then_succeed  # type: ignore[method-assign]
    first = service.dispatch([alert()])
    assert first["processed"] == 2
    assert all(row["status"] == "failed" for row in first["events"])
    second = service.dispatch([alert()])
    assert second["processed"] == 2
    assert all(row["status"] == "sent" for row in second["events"])


def test_live_email_reports_specific_missing_credentials(tmp_path: Path) -> None:
    incomplete = config(mode="live")
    incomplete = NotificationConfig(
        **{**incomplete.__dict__, "gmail_user": "", "gmail_app_password": ""}
    )
    service = NotificationService(tmp_path / "events.jsonl", incomplete)
    result = service.test("email", "manager@example.com")
    assert result["status"] == "failed"
    detail = result["event"]["detail"]
    assert "Gmail sender address is missing" in detail
    assert "Gmail app password is not stored" in detail


def test_dry_run_email_does_not_require_smtp_credentials(tmp_path: Path) -> None:
    incomplete = config(mode="dry_run")
    incomplete = NotificationConfig(
        **{**incomplete.__dict__, "gmail_user": "", "gmail_app_password": ""}
    )
    service = NotificationService(tmp_path / "events.jsonl", incomplete)
    result = service.test("email", "manager@example.com")
    assert result["status"] == "dry_run"
