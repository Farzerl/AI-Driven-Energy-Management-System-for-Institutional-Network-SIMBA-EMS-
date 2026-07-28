from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

DEFAULT_PEAK_KVA_LIMIT = 700.0
MEDIUM_RISK_RATIO = 0.85
HIGH_RISK_RATIO = 0.95
FacilityLimitMap = Mapping[str, float]


def _as_positive_float(value: object, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if pd.isna(number) or number <= 0:
        return float(fallback)
    return number


def resolve_peak_limit(
    row: pd.Series,
    facility_peak_limits: FacilityLimitMap | None = None,
    default_peak_kva_limit: float = DEFAULT_PEAK_KVA_LIMIT,
    limit_column: str = "peak_kva_limit",
) -> float:
    default_limit = _as_positive_float(default_peak_kva_limit, DEFAULT_PEAK_KVA_LIMIT)
    if limit_column in row and not pd.isna(row.get(limit_column)):
        return _as_positive_float(row.get(limit_column), default_limit)
    if facility_peak_limits:
        for key_column in ("facility_id", "facility_name"):
            key = row.get(key_column)
            if key in facility_peak_limits:
                return _as_positive_float(facility_peak_limits[key], default_limit)
    return default_limit


def score_peak_risk(
    row: pd.Series,
    peak_kva_limit: float = DEFAULT_PEAK_KVA_LIMIT,
    facility_peak_limits: FacilityLimitMap | None = None,
) -> str:
    kva = _as_positive_float(row.get("forecast_kva", row.get("kva", 0)), 0)
    limit = resolve_peak_limit(row, facility_peak_limits, peak_kva_limit)
    ratio = kva / limit if limit > 0 else 0
    if ratio >= HIGH_RISK_RATIO:
        return "high"
    if ratio >= MEDIUM_RISK_RATIO:
        return "medium"
    return "low"


def add_peak_risk(
    data: pd.DataFrame,
    peak_kva_limit: float = DEFAULT_PEAK_KVA_LIMIT,
    facility_peak_limits: FacilityLimitMap | None = None,
) -> pd.DataFrame:
    result = data.copy()
    result["peak_kva_limit"] = result.apply(
        resolve_peak_limit,
        axis=1,
        facility_peak_limits=facility_peak_limits,
        default_peak_kva_limit=peak_kva_limit,
    )
    kva_source = result["forecast_kva"] if "forecast_kva" in result.columns else result["kva"]
    result["peak_utilization_ratio"] = kva_source.astype(float) / result["peak_kva_limit"].replace(0, pd.NA)
    result["peak_risk"] = result.apply(
        score_peak_risk,
        axis=1,
        peak_kva_limit=peak_kva_limit,
        facility_peak_limits=facility_peak_limits,
    )
    return result


def infer_facility_peak_limits(
    data: pd.DataFrame,
    quantile: float = 0.95,
    safety_margin: float = 1.0,
    minimum_limit: float = 1.0,
) -> dict[str, float]:
    if not 0 < quantile <= 1:
        raise ValueError("quantile must be greater than 0 and less than or equal to 1.")
    if safety_margin <= 0:
        raise ValueError("safety_margin must be positive.")
    limits = (
        data.groupby("facility_id")["kva"]
        .quantile(quantile)
        .mul(safety_margin)
        .clip(lower=minimum_limit)
        .round(3)
        .to_dict()
    )
    return {str(key): float(value) for key, value in limits.items()}
