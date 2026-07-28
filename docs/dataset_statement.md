# Dataset Statement

## Source and authority

The project uses University of Zimbabwe smart-meter workbooks supplied through the Faculty of Engineering and the Built Environment for institutional energy analysis and model development.

## Coverage

- Period: 1 September 2025 to 30 April 2026
- Resolution: 30 minutes
- Facilities: 22
- Raw rows inspected: 255,545
- Clean rows used: 255,350
- Negative-energy rows removed: 195

The data include timestamp, facility, kWh, kVA, and power factor.

## Cleaning

- invalid timestamps removed;
- duplicate facility timestamps resolved by keeping the final record;
- negative kVA rejected;
- negative kWh rejected;
- missing power factor filled from the facility median;
- power-factor sign converted to magnitude;
- records sorted by facility and time.

## Validation split

Records through 31 March 2026 were used for fitting. April 2026 was held out for final testing. This prevents later observations from leaking into the training period.

## Release contents

The repository includes:

- the trained model;
- aggregate dataset counts and model metrics;
- a dataset fingerprint;
- a 49-row authorised historical replay for the local demonstration.

The source archive and workbooks are excluded from the release ZIP. Retraining can be performed locally by placing the archive in `training_data` and running `TRAIN_MODEL.bat`.
