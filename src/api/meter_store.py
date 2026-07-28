from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Iterable

from src.edge.schemas import EdgeStatusReport, MeterReading


def reading_id(reading: MeterReading) -> str:
    identity = "|".join(
        [
            reading.facility_id,
            reading.timestamp.isoformat(),
            reading.source,
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


class MeterReadingStore:
    """Append-only demo store with deterministic duplicate protection."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = Lock()

    def _read_records_unlocked(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        records: list[dict[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records

    def append(self, readings: Iterable[MeterReading]) -> dict[str, object]:
        candidates = list(readings)
        accepted: list[str] = []
        duplicates: list[str] = []
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            records = self._read_records_unlocked()
            known = {str(record["reading_id"]) for record in records if "reading_id" in record}
            with self.path.open("a", encoding="utf-8") as handle:
                for reading in candidates:
                    identifier = reading_id(reading)
                    if identifier in known:
                        duplicates.append(identifier)
                        continue
                    record = reading.model_dump(mode="json")
                    record["reading_id"] = identifier
                    record["received_utc"] = datetime.now(timezone.utc).isoformat()
                    handle.write(json.dumps(record, separators=(",", ":")) + "\n")
                    known.add(identifier)
                    accepted.append(identifier)
                handle.flush()
                os.fsync(handle.fileno())
        return {
            "submitted": len(candidates),
            "accepted": len(accepted),
            "duplicates": len(duplicates),
            "accepted_ids": accepted,
            "duplicate_ids": duplicates,
        }

    def all(self) -> list[dict[str, object]]:
        with self._lock:
            return self._read_records_unlocked()

    def latest(self, limit: int = 50) -> list[dict[str, object]]:
        safe_limit = max(1, min(limit, 500))
        with self._lock:
            records = self._read_records_unlocked()
        return list(reversed(records[-safe_limit:]))

    def summary(self) -> dict[str, object]:
        with self._lock:
            records = self._read_records_unlocked()
        if not records:
            return {
                "received_count": 0,
                "latest_received_utc": None,
                "latest_reading_timestamp": None,
                "latest_facility_id": None,
            }
        latest = records[-1]
        return {
            "received_count": len(records),
            "latest_received_utc": latest.get("received_utc"),
            "latest_reading_timestamp": latest.get("timestamp"),
            "latest_facility_id": latest.get("facility_id"),
        }


def write_edge_status(path: Path, report: EdgeStatusReport) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def read_edge_status(path: Path) -> dict[str, object] | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "error", "last_error": "Edge status file could not be read."}
