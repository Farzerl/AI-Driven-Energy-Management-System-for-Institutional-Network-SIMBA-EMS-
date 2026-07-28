import pandas as pd

from src.tariff.zetdc_tou import classify_tariff_period


def test_peak_morning_window():
    assert classify_tariff_period(pd.Timestamp("2026-04-01 07:30:00")) == "peak"


def test_offpeak_night_window():
    assert classify_tariff_period(pd.Timestamp("2026-04-01 23:30:00")) == "offpeak"


def test_standard_midday_window():
    assert classify_tariff_period(pd.Timestamp("2026-04-01 12:00:00")) == "standard"
