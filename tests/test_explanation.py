from src.live.explanation import build_evidence_explanation


def test_explanation_is_traceable_and_preserves_approval_boundary() -> None:
    result = build_evidence_explanation(
        facility="Central Kitchens",
        recent_kva=[250.0, 270.0, 300.0, 330.0],
        current_kva=330.0,
        forecast_kva=360.0,
        upper_kva=375.0,
        limit_kva=350.0,
        risk="high",
        lead_minutes=30,
        tariff_period="peak",
        recommendation="Review approved water-heating load.",
        model_predictions={"existing": 355.0, "chronos2": 365.0},
    )
    assert "requires operator attention" in result["summary"]
    assert result["approval_required"] is True
    assert result["model_agreement"] in {"high", "moderate", "low"}
    assert len(result["reasons"]) == 4
    assert "cannot approve" in result["boundary"]
