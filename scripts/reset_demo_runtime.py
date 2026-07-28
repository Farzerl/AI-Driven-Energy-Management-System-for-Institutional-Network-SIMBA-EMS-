from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
for name in ["meter_readings.jsonl", "live_forecasts.jsonl", "edge_status.json", "edge_buffer.jsonl", "notification_events.jsonl", "operator_actions.jsonl"]:
    path = ROOT / "runtime" / name
    if path.exists():
        path.unlink()
print("Demonstration runtime cleared.")
