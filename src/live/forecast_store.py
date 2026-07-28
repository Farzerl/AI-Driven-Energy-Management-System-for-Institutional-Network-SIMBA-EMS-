from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Iterable


class ForecastStore:
    """Write-through JSONL store with an in-memory index for dashboard polling."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = Lock()
        self._records: list[dict[str, object]] = []
        self._known_ids: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        records: list[dict[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        self._records = records
        self._known_ids = {str(item["forecast_id"]) for item in records}

    def append(self, forecasts: Iterable[dict[str, object]]) -> int:
        candidates = list(forecasts)
        if not candidates:
            return 0
        with self._lock:
            accepted = [
                item for item in candidates
                if str(item["forecast_id"]) not in self._known_ids
            ]
            if not accepted:
                return 0
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                for item in accepted:
                    handle.write(json.dumps(item, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._records.extend(accepted)
            self._known_ids.update(str(item["forecast_id"]) for item in accepted)
            return len(accepted)

    def latest(self, limit: int = 50) -> list[dict[str, object]]:
        safe_limit = max(1, min(limit, 500))
        with self._lock:
            return list(reversed(self._records[-safe_limit:]))

    def all(self) -> list[dict[str, object]]:
        with self._lock:
            return list(self._records)

    def summary(self) -> dict[str, object]:
        with self._lock:
            if not self._records:
                return {
                    "forecast_count": 0,
                    "latest_forecast_timestamp": None,
                    "latest_facility_id": None,
                }
            latest = self._records[-1]
            return {
                "forecast_count": len(self._records),
                "latest_forecast_timestamp": latest.get("forecast_timestamp"),
                "latest_facility_id": latest.get("facility_id"),
            }
