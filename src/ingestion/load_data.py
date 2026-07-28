from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = {"timestamp", "facility_id", "facility_name", "sector", "kwh", "kva"}


def load_meter_data(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Meter data file not found: {file_path}")
    data = pd.read_csv(file_path)
    missing = REQUIRED_COLUMNS.difference(data.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    if data["timestamp"].isna().any():
        raise ValueError("One or more timestamps could not be parsed.")
    numeric_columns = ["kwh", "kva", "kvarh", "power_factor", "occupancy_proxy", "weather_temp_c"]
    for column in numeric_columns:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    return data.sort_values(["facility_id", "timestamp"]).reset_index(drop=True)
