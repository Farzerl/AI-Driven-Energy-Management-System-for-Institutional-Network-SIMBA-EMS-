from src.ingestion.load_data import load_meter_data


def test_load_sample_data():
    data = load_meter_data("sample_data/sample_meter_readings.csv")
    assert not data.empty
    assert "timestamp" in data.columns
    assert "kva" in data.columns
