from __future__ import annotations

import argparse
import csv
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from src.api.meter_store import write_edge_status
from src.edge.buffer import DurableReadingBuffer
from src.edge.schemas import EdgeStatusReport, MeterReading

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class EdgeConfig:
    gateway_id: str
    facility_id: str
    server_url: str
    poll_interval_seconds: float
    batch_size: int
    buffer_path: Path
    status_path: Path
    source_csv: Path
    api_key: str | None
    accelerated_demo: bool = False

    @classmethod
    def load(cls, path: Path) -> "EdgeConfig":
        config_path = Path(path)
        raw = json.loads(config_path.read_text(encoding="utf-8"))

        def resolve(value: str) -> Path:
            candidate = Path(value)
            return candidate if candidate.is_absolute() else REPO_ROOT / candidate

        return cls(
            gateway_id=str(raw.get("gateway_id", "pi-edge-demo-01")),
            facility_id=str(raw.get("facility_id", "Central Kitchens NC1 4")),
            server_url=str(raw.get("server_url", "http://127.0.0.1:8000")).rstrip("/"),
            poll_interval_seconds=max(0.2, float(raw.get("poll_interval_seconds", 5))),
            batch_size=max(1, min(int(raw.get("batch_size", 4)), 100)),
            buffer_path=resolve(str(raw.get("buffer_path", "runtime/edge_buffer.jsonl"))),
            status_path=resolve(str(raw.get("status_path", "runtime/edge_status.json"))),
            source_csv=resolve(str(raw.get("source_csv", "sample_data/edge_demo_readings.csv"))),
            api_key=(os.getenv("AI4I_API_KEY") or raw.get("api_key") or None),
            accelerated_demo=bool(raw.get("accelerated_demo", False)),
        )


class HttpReadingSender:
    def __init__(self, server_url: str, api_key: str | None = None, timeout: float = 10.0) -> None:
        self.url = server_url.rstrip("/") + "/api/meter-readings"
        self.api_key = api_key
        self.timeout = timeout

    def __call__(self, readings: list[MeterReading]) -> dict[str, object]:
        payload = json.dumps({"readings": [item.model_dump(mode="json") for item in readings]}).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "AI4I-EMS-Edge-Demo/1.0"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        request = urllib.request.Request(self.url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8")
            if response.status not in {200, 201}:
                raise RuntimeError(f"Server returned HTTP {response.status}: {body}")
            return json.loads(body)


class CsvReadingSource:
    def __init__(self, path: Path, facility_id: str) -> None:
        self.path = Path(path)
        self.facility_id = facility_id

    def read(self) -> list[MeterReading]:
        readings: list[MeterReading] = []
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                readings.append(
                    MeterReading(
                        timestamp=row["timestamp"],
                        facility_id=row.get("facility_id") or self.facility_id,
                        kva=float(row["kva"]),
                        kwh=float(row["kwh"]),
                        power_factor=float(row["power_factor"]),
                        source=row.get("source") or "edge-demo",
                    )
                )
        if not readings:
            raise ValueError(f"No readings found in {self.path}")
        return readings


class EdgeCollector:
    def __init__(
        self,
        config: EdgeConfig,
        sender: Callable[[list[MeterReading]], dict[str, object]],
        buffer: DurableReadingBuffer | None = None,
    ) -> None:
        self.config = config
        self.sender = sender
        self.buffer = buffer or DurableReadingBuffer(config.buffer_path)
        self.sent_readings = 0
        self.failed_batches = 0
        self.last_success: datetime | None = None

    def _report(self, state: str, error: str = "") -> None:
        report = EdgeStatusReport(
            gateway_id=self.config.gateway_id,
            facility_id=self.config.facility_id,
            mode="csv_simulator",
            state=state,  # type: ignore[arg-type]
            buffered_readings=self.buffer.count(),
            sent_readings=self.sent_readings,
            failed_batches=self.failed_batches,
            last_attempt_utc=datetime.now(timezone.utc),
            last_success_utc=self.last_success,
            last_error=error[:500],
        )
        write_edge_status(self.config.status_path, report)

    def transmit(self, readings: Iterable[MeterReading]) -> bool:
        pending = self.buffer.read_all()
        new_readings = list(readings)
        combined = pending + new_readings
        if not combined:
            self._report("online")
            return True
        try:
            response = self.sender(combined)
            accepted = int(response.get("accepted", 0))
            duplicates = int(response.get("duplicates", 0))
            if accepted + duplicates != len(combined):
                raise RuntimeError("Server acknowledgement did not cover the complete batch.")
            self.buffer.replace([])
            self.sent_readings += accepted
            self.last_success = datetime.now(timezone.utc)
            self._report("online")
            return True
        except Exception as exc:
            self.failed_batches += 1
            # Existing pending readings are already on disk. Only append new records.
            self.buffer.append(new_readings)
            self._report("buffering", str(exc))
            print(f"Transmission failed; readings buffered locally: {exc}")
            return False


def chunked(items: list[MeterReading], size: int) -> Iterable[list[MeterReading]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def run(config: EdgeConfig, once: bool = False) -> int:
    source = CsvReadingSource(config.source_csv, config.facility_id)
    sender = HttpReadingSender(config.server_url, config.api_key)
    collector = EdgeCollector(config, sender)
    readings = source.read()
    collector._report("starting")
    print(f"Edge demo gateway: {config.gateway_id}")
    print(f"Server: {config.server_url}")
    print(f"Source: {config.source_csv}")
    print(f"Accelerated half-hour demo: {'ON' if config.accelerated_demo else 'OFF'}")
    print("Press Ctrl+C to stop.")

    harare = ZoneInfo("Africa/Harare")
    now = datetime.now(harare)
    virtual_start = now.replace(minute=30 if now.minute >= 30 else 0, second=0, microsecond=0)
    sequence = 0

    try:
        while True:
            for batch in chunked(readings, config.batch_size):
                outgoing = batch
                if config.accelerated_demo:
                    outgoing = []
                    for item in batch:
                        outgoing.append(
                            MeterReading(
                                timestamp=virtual_start + timedelta(minutes=30 * sequence),
                                facility_id=item.facility_id,
                                kva=item.kva,
                                kwh=item.kwh,
                                power_factor=item.power_factor,
                                source="edge-demo-accelerated",
                            )
                        )
                        sequence += 1
                collector.transmit(outgoing)
                if once:
                    collector._report("stopped")
                    return 0
                time.sleep(config.poll_interval_seconds)
    except KeyboardInterrupt:
        collector._report("stopped")
        print("Edge demo stopped.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AI4I EMS edge-gateway simulator.")
    parser.add_argument("--config", default="config/edge.example.json")
    parser.add_argument("--once", action="store_true", help="Transmit one batch and exit.")
    args = parser.parse_args()
    try:
        return run(EdgeConfig.load(Path(args.config)), once=args.once)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError) as exc:
        print(f"EDGE DEMO FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
