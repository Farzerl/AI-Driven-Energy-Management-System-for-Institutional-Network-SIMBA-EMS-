from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.facility_limits import DEFAULT_FACILITY_LIMITS_PATH, load_facility_limits
from src.features.time_series import add_lag_features, add_time_features
from src.ingestion.load_data import load_meter_data
from src.models.anomaly_detection import add_simple_anomaly_flags
from src.models.peak_risk import add_peak_risk
from src.recommendations.engine import add_recommendations
from src.tariff.zetdc_tou import add_tariff_features

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE_DIR = REPO_ROOT / "evidence" / "public_dashboard"
DEFAULT_OPERATOR_LOG = REPO_ROOT / "runtime" / "operator_actions.jsonl"
DEFAULT_SAMPLE_DATA = REPO_ROOT / "sample_data" / "sample_meter_readings.csv"
_LOG_LOCK = threading.Lock()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Evidence file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_dashboard_evidence(evidence_dir: Path = DEFAULT_EVIDENCE_DIR) -> dict[str, Any]:
    return load_json(evidence_dir / "dashboard_evidence.json")


def load_control_comparison(evidence_dir: Path = DEFAULT_EVIDENCE_DIR) -> dict[str, Any]:
    separate = evidence_dir / "control_comparison_public.json"
    if separate.exists():
        return load_json(separate)
    return load_dashboard_evidence(evidence_dir)["controller_comparison"]


def build_demo_alerts(
    data_path: Path = DEFAULT_SAMPLE_DATA,
    facility_limits_path: Path = REPO_ROOT / DEFAULT_FACILITY_LIMITS_PATH,
    fallback_limit: float = 700.0,
) -> list[dict[str, Any]]:
    data = load_meter_data(data_path)
    limits: dict[str, float] = {}
    if facility_limits_path.exists():
        limits = load_facility_limits(facility_limits_path)
    data = add_tariff_features(data)
    data = add_time_features(data)
    data = add_lag_features(data, target="kva")
    data = add_simple_anomaly_flags(data)
    data = add_peak_risk(data, peak_kva_limit=fallback_limit, facility_peak_limits=limits)
    data = add_recommendations(data)
    records: list[dict[str, Any]] = []
    for row in data.sort_values("timestamp", ascending=False).itertuples():
        risk = str(row.peak_risk)
        if risk == "low" and not bool(row.is_anomaly):
            continue
        limit = float(row.peak_kva_limit)
        current = float(row.kva)
        reduction = max(current - 0.85 * limit, 0.0)
        identifier = hashlib.sha256(f"{row.facility_id}|{row.timestamp.isoformat()}|{risk}".encode("utf-8")).hexdigest()[:16]
        records.append({
            "alert_id": identifier,
            "timestamp": row.timestamp.isoformat(),
            "facility_id": str(row.facility_id),
            "facility_name": str(row.facility_name),
            "sector": str(row.sector),
            "current_kva": current,
            "facility_limit_kva": limit,
            "utilization_percent": round(float(row.peak_utilization_ratio) * 100, 2),
            "tariff_period": str(row.tariff_period),
            "risk": risk,
            "is_anomaly": bool(row.is_anomaly),
            "recommended_action": str(row.recommended_action),
            "planning_reduction_kva": round(reduction, 2),
            "status": "awaiting_operator",
            "control_mode": "advisory",
        })
    return records


def append_operator_decision(log_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stored = {**record, "recorded_utc": datetime.now(timezone.utc).isoformat()}
    with _LOG_LOCK:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(stored, sort_keys=True) + "\n")
    return stored


def read_operator_decisions(log_path: Path, limit: int = 100) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows[-limit:][::-1]
