from datetime import datetime, timezone
from pathlib import Path

from src.edge.buffer import DurableReadingBuffer
from src.edge.schemas import MeterReading


def reading(timestamp: str = "2026-07-14T09:00:00+02:00") -> MeterReading:
    return MeterReading(
        timestamp=timestamp,
        facility_id="F17",
        kva=480.0,
        kwh=225.0,
        power_factor=0.91,
        source="test-edge",
    )


def test_buffer_persists_and_clears(tmp_path: Path) -> None:
    path = tmp_path / "buffer.jsonl"
    buffer = DurableReadingBuffer(path)
    assert buffer.append([reading()]) == 1
    assert buffer.count() == 1
    assert buffer.read_all()[0].facility_id == "F17"
    buffer.replace([])
    assert buffer.count() == 0
