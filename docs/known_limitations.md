# Known Limitations

- The release demonstrates authorised historical replay and controlled input testing. It is not connected to a commissioned live campus feed.
- The plant response is software-in-the-loop. No electrical circuit is switched.
- Model training covers one institutional network and less than a complete multi-year operating cycle.
- HGB is the strongest individual model on the common test. LSTM and Transformer have higher standalone aggregate error and are retained for comparison, research and validated hybrid contribution.
- The automatic hybrid gains are small at some horizons. They must not be exaggerated.
- Longer-horizon alert precision is lower than the urgent 30-minute signal and is intended for planning.
- Weather, occupancy sensors and detailed academic event indicators are not direct current inputs.
- Facility-level meters do not identify the exact appliance causing a peak.
- Planning limits are not electrical protection settings.
- Uncertainty bounds reduce risk but do not guarantee realised demand remains below the upper value.
- Cost outputs depend on planning tariff assumptions and are not realised savings.
- Anomaly alerts indicate unusual behaviour but do not diagnose equipment faults.
- Network voltage, feeder current, transformer loading and losses require a verified OpenDSS model.
- HVAC comfort and thermal response require building-specific modelling, potentially through EnergyPlus.
- Gmail credentials and Admin runtime state are local and are not packaged.
- The temporary default Admin password is `admin` and must be changed before operational deployment.
- Runtime residual calibration is bounded and reversible. It does not guarantee improvement on every future interval.
- Automatic model-weight replacement is intentionally unsupported. New models require offline chronological validation and approval.
