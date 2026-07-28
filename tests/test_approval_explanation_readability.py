from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_approval_explanation_state_survives_poll_rerender():
    script = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    assert "approvalDeckExpandedIds: new Set()" in script
    assert "const detailsOpen = state.approvalDeckExpandedIds.has(item.recommendation_id)" in script
    assert "detailsElement?.addEventListener(\"toggle\"" in script
    assert 'details class="deck-details" data-recommendation-id=' in script
    assert '${detailsOpen ? "open" : ""}' in script


def test_dashboard_uses_balanced_readability_rules():
    css = (ROOT / "dashboard" / "static" / "app.css").read_text(encoding="utf-8")
    assert "SIMBA 11.1 - balanced readability" in css
    assert "body{font-size:17.5px" in css
    assert ".deck-details summary{font-size:15px" in css
    assert ".deck-details .action-plan span" in css
