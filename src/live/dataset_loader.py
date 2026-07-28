from __future__ import annotations

import io
import math
import re
import zipfile
from pathlib import Path, PurePosixPath

import pandas as pd


INTERVAL_HOURS = 0.5


def _normalise(name: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()


def _find_header_row(workbook: pd.ExcelFile, sheet_name: str) -> int:
    preview = workbook.parse(sheet_name=sheet_name, header=None, nrows=20)
    for index, row in preview.iterrows():
        labels = [_normalise(value) for value in row.tolist()]
        if any("date time" in label for label in labels) and any(
            "demand kva" in label for label in labels
        ):
            return int(index)
    raise ValueError(f"Could not locate the meter header row in sheet {sheet_name!r}")


def _column(columns: list[object], *terms: str) -> object | None:
    normalised = {_normalise(column): column for column in columns}
    for label, original in normalised.items():
        if all(term in label for term in terms):
            return original
    return None


def _numeric(data: pd.DataFrame, column: object | None) -> pd.Series:
    if column is None:
        return pd.Series(float("nan"), index=data.index, dtype="float64")
    return pd.to_numeric(data[column], errors="coerce")


def _derive_power_quality(
    *,
    kw: pd.Series,
    reactive_kvar: pd.Series,
    kva: pd.Series,
    measured_pf: pd.Series,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    kva_safe = kva.where(kva.abs() > 1e-9)
    derived_pf = (kw.abs() / kva_safe).clip(0, 1)
    measured_pf_magnitude = measured_pf.abs().clip(0, 1)
    # The archive contains signed PF values and several missing PF runs. The
    # component-derived magnitude is internally consistent with kW and kVA and
    # is therefore the operational value. The measured value is retained for
    # provenance and quality comparison.
    operational_pf = derived_pf.fillna(measured_pf_magnitude).fillna(0.95)
    q_magnitude = reactive_kvar.abs()
    reactive_kvarh = q_magnitude * INTERVAL_HOURS
    return operational_pf, measured_pf_magnitude, reactive_kvarh


def _frame_from_columns(data: pd.DataFrame, filename: str, alias: str) -> pd.DataFrame:
    columns = data.columns.tolist()
    timestamp_col = _column(columns, "date", "time") or _column(columns, "timestamp")
    kva_col = _column(columns, "demand", "kva") or _column(columns, "kva")
    kwh_col = _column(columns, "consumption", "kwh") or _column(columns, "kwh")
    kw_col = _column(columns, "power", "kw") or _column(columns, "active", "power")
    reactive_col = (
        _column(columns, "reactive", "kvar")
        or _column(columns, "reactive", "power")
        or _column(columns, "kvar")
    )
    pf_col = _column(columns, "power", "factor")
    data_points_col = _column(columns, "data", "points")
    temperature_col = _column(columns, "temperature")
    humidity_col = _column(columns, "humidity")
    if timestamp_col is None or kva_col is None:
        raise ValueError(f"Required timestamp or Demand (kVA) column missing in {filename}")

    timestamp = pd.to_datetime(
        data[timestamp_col].astype(str).str.strip(),
        format="%m/%d/%Y, %H:%M:%S",
        errors="coerce",
    )
    if timestamp.dt.tz is None:
        timestamp = timestamp.dt.tz_localize(
            "Africa/Harare",
            nonexistent="shift_forward",
            ambiguous="NaT",
        )

    kva = _numeric(data, kva_col)
    kwh_is_measured = kwh_col is not None
    kwh = _numeric(data, kwh_col) if kwh_is_measured else pd.Series(float("nan"), index=data.index)
    active_kw_is_measured = kw_col is not None
    active_kw = _numeric(data, kw_col)
    if not active_kw_is_measured:
        active_kw = kwh * 2.0
    if not kwh_is_measured:
        kwh = active_kw * INTERVAL_HOURS

    reactive_is_measured = reactive_col is not None
    reactive_kvar = _numeric(data, reactive_col)
    measured_pf = _numeric(data, pf_col)
    if not reactive_is_measured:
        magnitude = (kva.pow(2) - active_kw.pow(2)).clip(lower=0).pow(0.5)
        reactive_kvar = magnitude

    power_factor, measured_pf_magnitude, reactive_kvarh = _derive_power_quality(
        kw=active_kw,
        reactive_kvar=reactive_kvar,
        kva=kva,
        measured_pf=measured_pf,
    )

    return pd.DataFrame(
        {
            "timestamp": timestamp,
            "facility_id": alias,
            "kva": kva,
            "kwh": kwh,
            "kwh_is_measured": bool(kwh_is_measured),
            "active_power_kw": active_kw,
            "active_power_kw_is_measured": bool(active_kw_is_measured),
            "reactive_power_kvar": reactive_kvar,
            "reactive_power_kvar_is_measured": bool(reactive_is_measured),
            "reactive_energy_kvarh_estimated": reactive_kvarh,
            "power_factor": power_factor,
            "power_factor_measured_magnitude": measured_pf_magnitude,
            "power_factor_signed": measured_pf,
            "data_points": _numeric(data, data_points_col),
            "temperature_c": _numeric(data, temperature_col),
            "humidity_percent": _numeric(data, humidity_col),
            "interval_minutes": 30,
        }
    )


def _read_excel(payload: bytes, filename: str, alias: str) -> pd.DataFrame:
    with io.BytesIO(payload) as stream:
        with pd.ExcelFile(stream, engine="openpyxl") as workbook:
            sheet = "Summed" if "Summed" in workbook.sheet_names else workbook.sheet_names[0]
            header = _find_header_row(workbook, sheet)
            data = workbook.parse(sheet_name=sheet, header=header)
    return _frame_from_columns(data, filename, alias)


def _read_csv(payload: bytes, filename: str, alias: str) -> pd.DataFrame:
    with io.BytesIO(payload) as stream:
        data = pd.read_csv(stream)
    return _frame_from_columns(data, filename, alias)


def load_dataset_archive(
    archive: Path,
    output_dir: Path,
) -> tuple[pd.DataFrame, dict[str, str]]:
    archive = Path(archive)
    if not archive.exists() or archive.suffix.lower() != ".zip":
        raise FileNotFoundError(f"Dataset ZIP not found: {archive}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive) as handle:
        members = sorted(
            [
                member
                for member in handle.infolist()
                if not member.is_dir()
                and PurePosixPath(member.filename).suffix.lower() in {".xlsx", ".csv"}
                and not PurePosixPath(member.filename).name.startswith("~$")
            ],
            key=lambda member: member.filename.lower(),
        )
        if not members:
            raise ValueError("No XLSX or CSV meter files were found in the authorised dataset ZIP.")

        frames: list[pd.DataFrame] = []
        alias_map: dict[str, str] = {}
        for member in members:
            filename = PurePosixPath(member.filename).name
            stem = PurePosixPath(member.filename).stem
            name = re.sub(r"^University-of-Zimbabwe-", "", stem)
            name = re.sub(r"-PM\d+.*$", "", name).replace("-", " ")
            alias = name
            alias_map[alias] = filename
            with handle.open(member, "r") as source:
                payload = source.read()

            suffix = PurePosixPath(member.filename).suffix.lower()
            frame = _read_excel(payload, filename, alias) if suffix == ".xlsx" else _read_csv(payload, filename, alias)
            frames.append(frame)

    data = pd.concat(frames, ignore_index=True)
    data = data.dropna(subset=["timestamp", "kva", "kwh", "active_power_kw", "reactive_power_kvar"])
    data = data[(data["kva"] >= 0) & (data["kwh"] >= 0) & (data["active_power_kw"] >= 0)]
    data["kwh_is_measured"] = data["kwh_is_measured"].fillna(False).astype(float)
    data["active_power_kw_is_measured"] = data["active_power_kw_is_measured"].fillna(False).astype(float)
    data["reactive_power_kvar_is_measured"] = data["reactive_power_kvar_is_measured"].fillna(False).astype(float)
    data["power_factor"] = (
        data["power_factor"]
        .fillna(data.groupby("facility_id")["power_factor"].transform("median"))
        .fillna(0.95)
        .clip(0, 1)
    )
    data["temperature_c"] = data["temperature_c"].where(data["temperature_c"].between(-20, 60))
    data["humidity_percent"] = data["humidity_percent"].where(data["humidity_percent"].between(0, 100))
    data = data.sort_values(["facility_id", "timestamp"]).drop_duplicates(
        subset=["facility_id", "timestamp"],
        keep="last",
    )

    pd.Series(alias_map).to_json(output_dir / "facility_alias_map.json", indent=2)
    return data.reset_index(drop=True), alias_map
