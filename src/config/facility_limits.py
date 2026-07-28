from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import pandas as pd


DEFAULT_FACILITY_LIMITS_PATH = Path("config/facility_peak_limits.generated.json")


def normalize_facility_limits(raw: Mapping[object, object]) -> dict[str, float]:
    limits: dict[str, float] = {}
    for key, value in raw.items():
        if key is None or str(key).strip() == "":
            raise ValueError("Facility limit keys must be non-empty facility IDs or facility names.")
        try:
            limit = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid kVA limit for {key!r}: {value!r}") from exc
        if pd.isna(limit) or limit <= 0:
            raise ValueError(f"Facility kVA limit must be positive for {key!r}.")
        limits[str(key)] = limit
    return limits


def load_facility_limits(path: str | Path) -> dict[str, float]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Facility limits file not found: {file_path}")
    raw = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Facility limit file must contain a JSON object.")
    return normalize_facility_limits(raw)


def save_facility_limits(limits: Mapping[object, object], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_facility_limits(limits)
    output_path.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")
    return output_path


def infer_facility_limits_from_data(
    data: pd.DataFrame,
    group_column: str = "facility_id",
    value_column: str = "kva",
    quantile: float = 0.95,
    safety_margin: float = 1.05,
    minimum_limit: float = 1.0,
) -> dict[str, float]:
    if group_column not in data.columns:
        raise ValueError(f"Missing group column: {group_column}")
    if value_column not in data.columns:
        raise ValueError(f"Missing value column: {value_column}")
    if not 0 < quantile <= 1:
        raise ValueError("quantile must be greater than 0 and less than or equal to 1.")
    if safety_margin <= 0:
        raise ValueError("safety_margin must be positive.")
    if minimum_limit <= 0:
        raise ValueError("minimum_limit must be positive.")
    demand = data[[group_column, value_column]].copy()
    demand[value_column] = pd.to_numeric(demand[value_column], errors="coerce")
    demand = demand.dropna(subset=[group_column, value_column])
    if demand.empty:
        raise ValueError("No valid demand rows available for facility limit inference.")
    limits = (
        demand.groupby(group_column)[value_column]
        .quantile(quantile)
        .mul(safety_margin)
        .clip(lower=minimum_limit)
        .round(3)
        .to_dict()
    )
    return normalize_facility_limits(limits)
