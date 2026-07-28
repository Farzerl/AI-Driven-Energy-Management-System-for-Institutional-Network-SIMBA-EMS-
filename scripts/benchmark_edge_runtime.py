from __future__ import annotations

import argparse
import csv
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.live.model_manager import LiveModelManager
MODEL_PATH = ROOT / "models" / "institutional_multi_horizon_forecaster.json"
SAMPLE_PATH = ROOT / "sample_data" / "edge_demo_readings.csv"


def benchmark(iterations: int = 400) -> dict[str, object]:
    manager = LiveModelManager(MODEL_PATH)
    if not manager.ready:
        raise RuntimeError(str(manager.status().get("error") or "Model unavailable"))
    with SAMPLE_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle))
    facility = str(records[-1]["facility_id"])
    for _ in range(3):
        manager.clear_prediction_cache()
        manager.predict_horizons(records, facility)
    latencies_ms: list[float] = []
    for _ in range(iterations):
        manager.clear_prediction_cache()
        started = time.perf_counter_ns()
        manager.predict_horizons(records, facility)
        latencies_ms.append((time.perf_counter_ns() - started) / 1_000_000.0)
    ordered = sorted(latencies_ms)
    p95_index = min(len(ordered) - 1, int(0.95 * len(ordered)))
    model_files = [MODEL_PATH, *sorted((MODEL_PATH.parent / "neural").glob("*.json"))]
    model_bytes = sum(path.stat().st_size for path in model_files if path.is_file())
    status = manager.status()
    result = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark_type": "four-horizon local inference",
        "iterations": iterations,
        "model": {
            "path": MODEL_PATH.relative_to(ROOT).as_posix(),
            "included_files": [path.relative_to(ROOT).as_posix() for path in model_files if path.is_file()],
            "bundle_size_bytes": model_bytes,
            "bundle_size_megabytes": round(model_bytes / 1024 / 1024, 4),
            "model_name": status.get("model_name"),
            "model_family": status.get("model_family"),
            "source": status.get("source"),
        },
        "latency_ms": {
            "median": round(statistics.median(latencies_ms), 4),
            "mean": round(statistics.fmean(latencies_ms), 4),
            "p95": round(ordered[p95_index], 4),
            "maximum": round(max(latencies_ms), 4),
        },
        "platform": {
            "python": sys.version.split()[0],
            "system": platform.system(),
            "machine": platform.machine(),
            "processor": platform.processor() or "not reported",
        },
        "edge_checks": {
            "model_under_256_mb": model_bytes < 256 * 1024 * 1024,
            "p95_four_horizon_inference_under_250_ms": ordered[p95_index] < 250.0,
        },
        "scope_boundary": "The benchmark clears the prediction cache before every run and covers HGB, LSTM, Transformer and selected hybrid outputs for four horizons from one 49-interval facility history. It excludes API transport, browser rendering and full-campus plant response.",
    }
    result["status"] = "pass" if all(result["edge_checks"].values()) else "fail"
    return result


def write_report(result: dict[str, object], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "edge_runtime_benchmark.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    model = result["model"]
    latency = result["latency_ms"]
    lines = [
        "# Edge Runtime Benchmark",
        "",
        f"- Status: **{str(result['status']).upper()}**",
        f"- Model bundle: **{model['bundle_size_megabytes']} MB**",
        f"- Median four-horizon inference: **{latency['median']} ms**",
        f"- P95 four-horizon inference: **{latency['p95']} ms**",
        "",
        str(result["scope_boundary"]),
    ]
    (output_dir / "edge_runtime_benchmark.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark the trained institutional demand model for edge deployment.")
    parser.add_argument("--iterations", type=int, default=400)
    parser.add_argument("--output-dir", default="evidence/edge_runtime")
    args = parser.parse_args()
    result = benchmark(max(100, args.iterations))
    write_report(result, ROOT / args.output_dir)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
