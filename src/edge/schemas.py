from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class MeterReading(BaseModel):
    """One validated interval reading from a meter or edge gateway."""

    timestamp: datetime
    facility_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.()& -]+$")
    kva: float = Field(ge=0, le=100_000)
    kwh: float = Field(ge=0, le=1_000_000)
    kwh_is_measured: bool = True
    active_power_kw: float | None = Field(default=None, ge=0, le=100_000)
    reactive_power_kvar: float | None = Field(default=None, ge=-100_000, le=100_000)
    reactive_energy_kvarh: float | None = Field(default=None, ge=0, le=1_000_000)
    power_factor: float = Field(ge=-1, le=1)
    source: str = Field(default="edge-demo", min_length=2, max_length=64)

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone offset")
        return value


class MeterReadingBatch(BaseModel):
    readings: list[MeterReading] = Field(min_length=1, max_length=500)


class EdgeStatusReport(BaseModel):
    gateway_id: str = Field(min_length=2, max_length=64)
    facility_id: str = Field(min_length=1, max_length=64)
    mode: Literal["csv_simulator", "meter", "manual"] = "csv_simulator"
    state: Literal["starting", "online", "buffering", "stopped", "error"]
    buffered_readings: int = Field(ge=0)
    sent_readings: int = Field(ge=0)
    failed_batches: int = Field(ge=0)
    last_attempt_utc: datetime | None = None
    last_success_utc: datetime | None = None
    last_error: str = Field(default="", max_length=500)
