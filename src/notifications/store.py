from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock


class NotificationStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = Lock()
        self._records: list[dict[str, object]] = []
        self._keys: set[str] = set()
        self._status_by_key: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            self._records.append(row)
            key = str(row.get("dedupe_key", ""))
            if key:
                self._keys.add(key)
                self._status_by_key[key] = str(row.get("status", ""))

    def contains(self, dedupe_key: str) -> bool:
        """Return True only when a terminal record already exists.

        Failed deliveries remain retryable on the next dispatch cycle.
        """
        with self._lock:
            return (
                dedupe_key in self._keys
                and self._status_by_key.get(dedupe_key) != "failed"
            )

    def append(self, row: dict[str, object]) -> None:
        key = str(row.get("dedupe_key", ""))
        with self._lock:
            if (
                key
                and key in self._keys
                and self._status_by_key.get(key) != "failed"
            ):
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._records.append(row)
            if key:
                self._keys.add(key)
                self._status_by_key[key] = str(row.get("status", ""))

    def latest(self, limit: int = 100) -> list[dict[str, object]]:
        safe = max(1, min(limit, 500))
        with self._lock:
            return list(reversed(self._records[-safe:]))

    def latest_for(
        self,
        facility: str,
        channel: str,
        recipient_id: str | None = None,
    ) -> dict[str, object] | None:
        with self._lock:
            for row in reversed(self._records):
                if row.get("facility_name") != facility or row.get("channel") != channel:
                    continue
                if recipient_id is not None and row.get("recipient_id") != recipient_id:
                    continue
                return dict(row)
        return None
