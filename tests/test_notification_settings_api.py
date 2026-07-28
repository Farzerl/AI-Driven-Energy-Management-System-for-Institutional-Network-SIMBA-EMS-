from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.server import create_app

ROOT = Path(__file__).resolve().parents[1]


def client(tmp_path: Path) -> TestClient:
    app = create_app(
        evidence_dir=ROOT / "evidence" / "public_dashboard",
        operator_log=tmp_path / "operator.jsonl",
        notification_store_path=tmp_path / "notifications.jsonl",
        notification_settings_path=tmp_path / "notification_settings.json",
        api_key=None,
    )
    return TestClient(app)


def settings_payload() -> dict[str, object]:
    return {
        "mode": "dry_run",
        "minimum_risk": "high",
        "cooldown_minutes": 45,
        "delivery_attempts": 2,
        "retry_backoff_seconds": 0,
        "dashboard_url": "http://127.0.0.1:8000/?tab=operations",
        "email": {
            "enabled": True,
            "recipients": [
                {"label": "Operations manager", "address": "manager@example.com", "enabled": True},
                {"label": "Deputy", "address": "deputy@example.com", "enabled": True},
            ],
            "gmail_user": "alerts@example.com",
            "gmail_app_password": "test-app-secret",
            "gmail_host": "smtp.gmail.com",
            "gmail_port": 465,
            "gmail_security": "ssl",
        },
    }


def test_settings_save_reload_and_secret_redaction(tmp_path: Path) -> None:
    api = client(tmp_path)
    response = api.put("/api/notifications/settings", json=settings_payload())
    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["revision"] == 1
    assert settings["email"]["gmail_app_password_set"] is True
    assert "gmail_app_password" not in settings["email"]
    assert "phone" not in settings
    assert len(settings["email"]["recipients"]) == 2


def test_explicit_email_target_is_logged_without_external_delivery(tmp_path: Path) -> None:
    api = client(tmp_path)
    assert api.put("/api/notifications/settings", json=settings_payload()).status_code == 200
    response = api.post(
        "/api/notifications/test-target",
        json={"channel": "email", "recipient": "manager@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "dry_run"
    events = api.get("/api/notifications/events").json()["items"]
    assert events[0]["channel"] == "email"
    assert events[0]["approval_channel"] == "dashboard_only"


def test_phone_test_request_is_rejected_by_schema(tmp_path: Path) -> None:
    api = client(tmp_path)
    response = api.post(
        "/api/notifications/test-target",
        json={"channel": "phone", "recipient": "+263772123456"},
    )
    assert response.status_code == 422


def test_deep_health_reports_email_only_readiness(tmp_path: Path) -> None:
    api = client(tmp_path)
    payload = api.get("/api/health/deep").json()
    assert payload["status"] in {"online", "degraded"}
    assert payload["approval_channel"] == "dashboard_only"
    assert "phone_ready" not in payload


def test_gmail_transport_mismatch_is_rejected(tmp_path: Path) -> None:
    api = client(tmp_path)
    payload = settings_payload()
    payload["email"]["gmail_port"] = 465
    payload["email"]["gmail_security"] = "starttls"
    response = api.put("/api/notifications/settings", json=payload)
    assert response.status_code == 422
    assert "port 465 with SSL" in response.json()["detail"]


def test_notification_status_exposes_gmail_readiness_without_secret(tmp_path: Path) -> None:
    api = client(tmp_path)
    assert api.put("/api/notifications/settings", json=settings_payload()).status_code == 200
    status = api.get("/api/notifications/status").json()
    assert status["email"]["configured"] is True
    assert status["email"]["security"] == "ssl"
    assert status["email"]["smtp_port"] == 465
    assert status["email"]["configuration_issues"] == []
    assert "phone" not in status
    assert "gsm" in status["future_channels"]
