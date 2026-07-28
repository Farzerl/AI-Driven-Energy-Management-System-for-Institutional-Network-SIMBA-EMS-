from __future__ import annotations

import hashlib
import threading
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Iterable, Mapping

from src.live.forecast_store import ForecastStore
from src.live.power_quality_adapter import PowerQualityChronosAdapter


class PowerQualityForecastService:
    """Non-blocking forecast coordinator for browser polling and meter ingestion.

    Chronos inference can be slower than a dashboard poll. This service returns a
    physically derived seasonal guard immediately, starts one background batch,
    and atomically replaces the result after the trained model completes.
    """

    def __init__(
        self,
        adapter: PowerQualityChronosAdapter,
        store: ForecastStore,
    ) -> None:
        self.adapter = adapter
        self.store = store
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="simba-power-quality")
        self._lock = threading.RLock()
        self._future: Future[dict[str, object]] | None = None
        self._future_key: str | None = None
        self._latest: dict[str, object] | None = None
        self._latest_key: str | None = None
        self._last_error = ""

    @staticmethod
    def _group(records: Iterable[Mapping[str, object]]) -> dict[str, list[Mapping[str, object]]]:
        grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for record in records:
            facility = str(record.get("facility_id", "")).strip()
            if facility:
                grouped[facility].append(record)
        return grouped

    @staticmethod
    def _safe_key(grouped: Mapping[str, Iterable[Mapping[str, object]]]) -> str:
        digest = hashlib.sha256()
        for facility, records in sorted(grouped.items()):
            rows = sorted(records, key=lambda item: str(item.get("timestamp", "")))
            digest.update(facility.encode("utf-8"))
            for row in rows[-16:]:
                digest.update(
                    f"|{row.get('timestamp')}|{row.get('kva')}|{row.get('kwh')}|{row.get('active_power_kw')}|{row.get('reactive_power_kvar')}".encode("utf-8")
                )
        return digest.hexdigest()

    def _commit(self, key: str, payload: dict[str, object]) -> None:
        items = list(payload.get("items", []))
        generated = str(payload.get("generated_utc") or datetime.now(timezone.utc).isoformat())
        records: list[dict[str, object]] = []
        for item in items:
            row = dict(item)
            identity = f"{row.get('model_facility_id')}|{row.get('reading_timestamp')}|{payload.get('source')}"
            row["forecast_id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
            row["forecast_timestamp"] = generated
            records.append(row)
        self.store.append(records)
        with self._lock:
            self._latest = payload
            self._latest_key = key
            self._last_error = ""

    def _reap(self) -> None:
        future: Future[dict[str, object]] | None
        key: str | None
        with self._lock:
            future = self._future
            key = self._future_key
        if future is None or key is None or not future.done():
            return
        try:
            payload = future.result()
            self._commit(key, payload)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
        finally:
            with self._lock:
                if self._future is future:
                    self._future = None
                    self._future_key = None

    def request_refresh(self, records: Iterable[Mapping[str, object]], *, force: bool = False) -> dict[str, object]:
        grouped = self._group(records)
        if not grouped:
            return {"scheduled": False, "reason": "No meter or simulation records are available."}
        key = self._safe_key(grouped)
        self._reap()
        with self._lock:
            if self._future is not None and not self._future.done():
                return {"scheduled": False, "reason": "Inference is already running.", "refreshing": True}
            if not force and self._latest_key == key:
                return {"scheduled": False, "reason": "The latest input state is already forecast.", "refreshing": False}
            if not self.adapter.ready:
                return {"scheduled": False, "reason": "Power-quality model is not trained or installed.", "refreshing": False}
            copied = {facility: list(rows) for facility, rows in grouped.items()}
            self._future_key = key
            self._future = self._executor.submit(self.adapter.predict_batch, copied)
            return {"scheduled": True, "refreshing": True, "facility_count": len(copied)}

    def snapshot(self, records: Iterable[Mapping[str, object]], *, force: bool = False) -> dict[str, object]:
        grouped = self._group(records)
        if not grouped:
            return {
                "status": "waiting",
                "source": "none",
                "items": [],
                "summary": self.store.summary(),
                "model": self.adapter.status(public=True),
                "reason": "Waiting for meter history or the software-in-the-loop replay.",
            }
        key = self._safe_key(grouped)
        self._reap()
        scheduled = self.request_refresh([item for rows in grouped.values() for item in rows], force=force)
        with self._lock:
            latest = dict(self._latest) if self._latest is not None else None
            latest_key = self._latest_key
            running = self._future is not None and not self._future.done()
            error = self._last_error
        if latest is not None and latest_key == key:
            return {
                **latest,
                "refreshing": running,
                "refresh": scheduled,
                "summary": self.store.summary(),
            }
        reason = error or str(scheduled.get("reason") or "The trained batch is refreshing for the current interval.")
        fallback = self.adapter.fallback_batch(grouped, reason=reason)
        return {
            **fallback,
            "refreshing": running or bool(scheduled.get("scheduled")),
            "refresh": scheduled,
            "summary": self.store.summary(),
        }

    def status(self) -> dict[str, object]:
        self._reap()
        with self._lock:
            running = self._future is not None and not self._future.done()
            return {
                "runtime": self.adapter.status(),
                "refreshing": running,
                "last_error": self._last_error,
                "latest_source": self._latest.get("source") if self._latest else None,
                "latest_generated_utc": self._latest.get("generated_utc") if self._latest else None,
                "store": self.store.summary(),
            }

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.adapter.close()
