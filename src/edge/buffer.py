from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Iterable

from src.edge.schemas import MeterReading


class DurableReadingBuffer:
    """Small JSONL buffer that survives process and connectivity interruptions."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def append(self, readings: Iterable[MeterReading]) -> int:
        records = list(readings)
        if not records:
            return 0
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                for reading in records:
                    handle.write(reading.model_dump_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return len(records)

    def read_all(self) -> list[MeterReading]:
        if not self.path.exists():
            return []
        items: list[MeterReading] = []
        with self._lock:
            for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    items.append(MeterReading.model_validate_json(line))
                except Exception as exc:
                    raise ValueError(f"Invalid buffer record at line {line_number}: {exc}") from exc
        return items

    def replace(self, readings: Iterable[MeterReading]) -> None:
        records = list(readings)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not records:
                self.path.unlink(missing_ok=True)
                return
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=self.path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                for reading in records:
                    handle.write(reading.model_dump_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(self.path)

    def count(self) -> int:
        return len(self.read_all())
