from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cost_dashboard_uses_current_chart_api() -> None:
    javascript = (ROOT / "dashboard" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    assert 'renderBarChart(\n    "cost-monthly-chart"' in javascript
    assert 'renderBarChart(\n    "cost-scenario-chart"' in javascript


def test_dashboard_asset_has_cache_version() -> None:
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "/static/app.js?v=" in html
    assert "/static/app.css?v=" in html


def test_dashboard_renders_protected_anomaly_escalation() -> None:
    javascript = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    assert "recommendation.escalation" in javascript
    assert "Investigation required" in javascript
    assert "Control blocked" in javascript


def test_dashboard_has_email_only_notification_settings_dialog() -> None:
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="menu-open"' in html
    assert 'data-menu-action="settings"' in html
    assert 'data-menu-action="admin"' in html
    assert 'id="settings-dialog"' in html
    assert 'id="email-recipient-list"' in html
    assert 'id="phone-recipient-list"' not in html
    assert 'Phone recipients' not in html
    assert 'Send phone test' not in html
    assert 'saveNotificationSettings' in javascript
    assert '/api/notifications/test-target' in javascript
    assert 'test-phone' not in javascript


def test_notification_settings_clamp_stale_numeric_values_and_format_api_errors() -> None:
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'class="settings-shell" novalidate' in html
    assert 'simba-power-quality-1' in html
    assert 'function validateNotificationSettingsForm()' in javascript
    assert 'setting-phone-lead' not in javascript
    assert 'function apiErrorMessage(payload, fallback)' in javascript
    assert 'item.loc.filter' in javascript


def test_dashboard_hides_diagnostic_configuration_in_admin_and_keeps_logo() -> None:
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'simba-emblem.png' in html
    assert 'id="admin-dialog"' in html
    assert 'id="admin-login-dialog"' in html
    assert 'id="setting-simulation-scenario"' in html
    assert 'id="setting-playback-seconds"' in html
    assert 'id="setting-adaptive-enabled"' in html
    assert 'id="admin-test-1"' in html
    assert 'id="about-dialog"' in html
    assert 'loadSystemSettings' in javascript
    assert 'X-Admin-Token' in javascript
    assert 'latest_batch_inference_latency_ms' in javascript
    assert 'data-tooltip=' in html


def test_admin_menu_recovers_from_expired_or_restarted_backend_session() -> None:
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    assert "simba-power-quality-1" in html
    assert "class APIRequestError extends Error" in javascript
    assert "function isAdminSessionError(error)" in javascript
    assert "function showAdminLogin(message" in javascript
    assert "Your previous Admin session is no longer valid" in javascript
    assert "state.adminToken = null" in javascript
    assert "handleAdminError(error" in javascript


def test_admin_settings_loader_uses_loaded_system_settings_state() -> None:
    javascript = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'const operationalSettings = state.systemSettings.operational;' in javascript
    assert 'const safeOperationalSettings = operationalSettings && typeof operationalSettings === "object"' in javascript
    assert 'JSON.stringify(safeOperationalSettings, null, 2)' in javascript
    assert 'JSON.stringify(settings.operational || SAFE_OPERATIONAL_DEFAULTS' not in javascript


def test_dashboard_exposes_live_facility_forecasts_action_queue_and_session_impact() -> None:
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    assert "Current facility forecast outlook" in html
    assert "VERIFIED SESSION IMPACT" in html
    assert "payload.recommendations" in javascript
    assert "Approve this action" in javascript
    assert "/api/simulation/impact" in javascript
    assert "Validated multi-model forecast" in javascript
    assert "chart_timeline" in javascript
    assert "setInterval(() =>" in javascript


def test_dashboard_exposes_chronos_local_setup_evidence_and_explanations() -> None:
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    assert "simba-power-quality-1" in html
    assert 'value="chronos2"' in html
    assert 'value="hybrid_chronos_existing"' in html
    assert 'id="admin-chronos-status"' in html
    assert 'id="admin-chronos-refresh"' in html
    assert 'id="chronos-evidence-table"' in html
    assert "/api/admin/chronos2/status" in javascript
    assert "renderChronosEvidence" in javascript
    assert "Why this recommendation" in javascript
    assert "cannot approve or execute" in javascript


def test_dashboard_exposes_power_quality_forecasts_evidence_and_admin_status() -> None:
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    for element_id in (
        "home-power-quality-strip",
        "power-quality-kpis",
        "power-quality-horizon-grid",
        "power-quality-chart",
        "power-quality-table",
        "impact-power-quality-opportunity",
        "power-quality-evidence-table",
        "admin-power-quality-status",
    ):
        assert f'id="{element_id}"' in html
    assert "/api/power-quality-forecasts" in javascript
    assert "/api/admin/power-quality/status" in javascript
    assert "renderPowerQualityEvidence" in javascript
    assert "N/A — no events" in javascript
    assert "signed reactive kVAR" in html
