from pathlib import Path

from fastapi.testclient import TestClient

from src.api.server import create_app

ROOT = Path(__file__).resolve().parents[1]


def payload(timestamp: str = "2026-07-14T09:00:00+02:00") -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "facility_id": "F17",
        "kva": 482.6,
        "kwh": 228.7,
        "power_factor": 0.91,
        "source": "edge-test",
    }


def test_meter_ingestion_duplicate_protection_and_status(tmp_path: Path) -> None:
    app = create_app(
        evidence_dir=ROOT / "evidence" / "public_dashboard",
        operator_log=tmp_path / "operator.jsonl",
        meter_store_path=tmp_path / "meter.jsonl",
        edge_status_path=tmp_path / "edge_status.json",
        api_key=None,
    )
    client = TestClient(app)

    first = client.post("/api/meter-readings", json=payload())
    assert first.status_code == 200
    assert first.json()["accepted"] == 1

    duplicate = client.post("/api/meter-readings", json=payload())
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicates"] == 1

    latest = client.get("/api/meter-readings?limit=10")
    assert latest.status_code == 200
    assert len(latest.json()["items"]) == 1

    status = client.get("/api/edge-status")
    assert status.status_code == 200
    assert status.json()["store"]["received_count"] == 1


def test_invalid_meter_reading_is_rejected(tmp_path: Path) -> None:
    app = create_app(
        evidence_dir=ROOT / "evidence" / "public_dashboard",
        operator_log=tmp_path / "operator.jsonl",
        meter_store_path=tmp_path / "meter.jsonl",
        edge_status_path=tmp_path / "edge_status.json",
        api_key=None,
    )
    client = TestClient(app)
    invalid = payload()
    invalid["kva"] = -1
    assert client.post("/api/meter-readings", json=invalid).status_code == 422
