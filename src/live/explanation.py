from __future__ import annotations

from typing import Mapping, Sequence


def _trend(values: Sequence[float]) -> tuple[str, float]:
    if len(values) < 2:
        return "stable", 0.0
    change = float(values[-1]) - float(values[0])
    scale = max(abs(float(values[0])), 1.0)
    ratio = change / scale
    if ratio >= 0.08:
        return "rising", change
    if ratio <= -0.08:
        return "falling", change
    return "stable", change


def build_evidence_explanation(
    *,
    facility: str,
    recent_kva: Sequence[float],
    current_kva: float,
    forecast_kva: float,
    upper_kva: float,
    limit_kva: float,
    risk: str,
    lead_minutes: int,
    tariff_period: str,
    recommendation: str,
    model_predictions: Mapping[str, float] | None = None,
) -> dict[str, object]:
    """Build a traceable explanation from validated numerical evidence.

    This is deliberately not open-ended text generation. Every statement is derived
    from a supplied measurement, forecast, rule, or model-comparison value.
    """

    direction, change = _trend([float(item) for item in recent_kva[-4:]])
    utilisation = upper_kva / max(limit_kva, 1e-9)
    predictions = [float(value) for value in dict(model_predictions or {}).values()]
    spread = max(predictions) - min(predictions) if len(predictions) >= 2 else 0.0
    spread_ratio = spread / max(abs(forecast_kva), 1.0)
    agreement = "high" if spread_ratio <= 0.05 else "moderate" if spread_ratio <= 0.12 else "low"
    confidence = "high" if agreement == "high" and upper_kva - forecast_kva <= 0.08 * max(limit_kva, 1.0) else "moderate"
    if agreement == "low":
        confidence = "review"

    reasons = [
        f"The conservative forecast is {upper_kva:.1f} kVA, equal to {utilisation * 100:.1f}% of the {limit_kva:.1f} kVA facility limit.",
        f"The most recent four readings are {direction}; the net change is {change:+.1f} kVA.",
        f"The earliest relevant forecast horizon is {lead_minutes} minutes and the tariff period is {tariff_period}.",
        f"Forecast model agreement is {agreement}; the spread across available model estimates is {spread:.1f} kVA.",
    ]
    if risk == "high":
        summary = f"{facility} requires operator attention before the expected demand event."
    elif risk == "medium":
        summary = f"{facility} should be monitored and prepared for a possible demand constraint."
    else:
        summary = f"{facility} remains within its configured operating limit."

    return {
        "summary": summary,
        "confidence": confidence,
        "model_agreement": agreement,
        "reasons": reasons,
        "recommended_review": recommendation,
        "approval_required": risk in {"medium", "high"},
        "evidence_fields": [
            "recent_meter_readings",
            "expected_forecast",
            "conservative_upper_forecast",
            "facility_limit",
            "tariff_period",
            "model_comparison",
        ],
        "boundary": "Explanation is evidence-grounded decision support. It cannot approve or execute an operational action.",
    }
