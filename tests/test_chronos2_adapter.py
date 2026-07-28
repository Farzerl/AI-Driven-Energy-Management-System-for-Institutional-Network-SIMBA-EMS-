from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.live.chronos2_adapter import Chronos2Adapter


class FakeChronosPipeline:
    def __init__(self) -> None:
        self.calls = 0

    def predict_df(self, context: pd.DataFrame, **_: object) -> pd.DataFrame:
        self.calls += 1
        latest = pd.Timestamp(context["timestamp"].max())
        base = float(context.sort_values("timestamp").iloc[-1]["target"])
        return pd.DataFrame(
            {
                "item_id": [str(context.iloc[0]["item_id"])] * 48,
                "timestamp": [latest + pd.Timedelta(minutes=30 * step) for step in range(1, 49)],
                "0.1": [base + step - 2.0 for step in range(1, 49)],
                "0.5": [base + step for step in range(1, 49)],
                "0.9": [base + step + 3.0 for step in range(1, 49)],
            }
        )


def _root(tmp_path: Path) -> Path:
    model = tmp_path / "models" / "chronos-2-base"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"test")
    return tmp_path


def _rows(count: int = 60) -> list[dict[str, object]]:
    start = datetime(2026, 4, 1)
    return [
        {
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "kva": 100.0 + index,
            "power_factor": 0.95,
            "kwh_is_measured": True,
        }
        for index in range(count)
    ]


def test_chronos_adapter_extracts_four_quantile_horizons_and_caches(tmp_path: Path) -> None:
    pipeline = FakeChronosPipeline()
    adapter = Chronos2Adapter(_root(tmp_path), pipeline_factory=lambda path, device: pipeline)
    try:
        result = adapter.predict(_rows(), "facility-a")
        assert set(result) == {"30_minutes", "2_hours", "6_hours", "24_hours"}
        assert result["30_minutes"]["forecast_lower_kva"] <= result["30_minutes"]["forecast_kva"] <= result["30_minutes"]["forecast_upper_kva"]
        assert result["24_hours"]["forecast_kva"] > result["30_minutes"]["forecast_kva"]
        replay = adapter.predict(_rows(), "facility-a")
        assert replay == result
        assert pipeline.calls == 1
    finally:
        adapter.close()


def test_chronos_adapter_rejects_short_or_irregular_history(tmp_path: Path) -> None:
    adapter = Chronos2Adapter(_root(tmp_path), pipeline_factory=lambda path, device: FakeChronosPipeline())
    try:
        with pytest.raises(ValueError, match="at least 49"):
            adapter.predict(_rows(20), "facility-a")
        rows = _rows()
        rows[-1]["timestamp"] = (datetime.fromisoformat(str(rows[-2]["timestamp"])) + timedelta(hours=4)).isoformat()
        with pytest.raises(ValueError, match="Irregular interval"):
            adapter.predict(rows, "facility-a")
    finally:
        adapter.close()
