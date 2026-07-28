from __future__ import annotations

from pathlib import Path

from scripts.repository_audit import audit
from scripts.security_scan import scan

ROOT = Path(__file__).resolve().parents[1]


def test_repository_alignment_audit_has_no_blocking_findings() -> None:
    result = audit(ROOT)
    assert result["status"] == "pass", result["findings"]


def test_security_scan_has_no_blocking_findings() -> None:
    result = scan(ROOT)
    assert result["status"] == "pass", result["findings"]


def test_core_markdown_links_resolve() -> None:
    result = audit(ROOT)
    broken = [item for item in result["findings"] if item["severity"] == "high" and "Broken local link" in item["message"]]
    assert broken == []


def test_dashboard_has_simple_operator_tabs_and_admin_menu() -> None:
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    for tab_id in ("demo", "overview", "live", "operations", "cost", "evidence", "architecture"):
        assert f'id="{tab_id}"' in html
    for label in ("Home", "Forecasts", "Operations", "Impact", "Evidence"):
        assert label in html
    assert 'id="menu-open"' in html
    assert 'data-menu-action="settings"' in html
    assert 'data-menu-action="admin"' in html
    assert 'data-menu-action="about"' in html
    assert 'data-menu-action="status"' in html
    assert 'data-open-panel="overview"' not in html
    assert 'data-open-panel="architecture"' not in html


def test_transient_directories_are_not_packaged() -> None:
    forbidden_names = {".venv", "venv", "runtime", "logs"}
    present = [path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*") if path.is_dir() and path.name.lower() in forbidden_names]
    assert present == []
