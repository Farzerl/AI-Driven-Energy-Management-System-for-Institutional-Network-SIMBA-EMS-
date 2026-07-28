from pathlib import Path

from src.edge.buffer import DurableReadingBuffer
from src.edge.collector import EdgeCollector, EdgeConfig
from src.edge.schemas import MeterReading


def config(tmp_path: Path) -> EdgeConfig:
    return EdgeConfig(
        gateway_id="test-gateway",
        facility_id="F17",
        server_url="http://127.0.0.1:8000",
        poll_interval_seconds=1,
        batch_size=2,
        buffer_path=tmp_path / "buffer.jsonl",
        status_path=tmp_path / "status.json",
        source_csv=tmp_path / "source.csv",
        api_key=None,
    )


def reading(timestamp: str) -> MeterReading:
    return MeterReading(
        timestamp=timestamp,
        facility_id="F17",
        kva=480.0,
        kwh=225.0,
        power_factor=0.91,
        source="test-edge",
    )


def test_offline_readings_are_buffered_then_flushed(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    buffer = DurableReadingBuffer(cfg.buffer_path)

    def offline(_items: list[MeterReading]) -> dict[str, object]:
        raise ConnectionError("offline")

    collector = EdgeCollector(cfg, offline, buffer)
    assert collector.transmit([reading("2026-07-14T09:00:00+02:00")]) is False
    assert buffer.count() == 1

    sent: list[MeterReading] = []

    def online(items: list[MeterReading]) -> dict[str, object]:
        sent.extend(items)
        return {"accepted": len(items), "duplicates": 0}

    collector.sender = online
    assert collector.transmit([reading("2026-07-14T09:30:00+02:00")]) is True
    assert len(sent) == 2
    assert buffer.count() == 0
