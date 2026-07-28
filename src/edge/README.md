# Edge Collector

The edge collector converts a meter source into validated API readings.

Functions:

- CSV or adapter input;
- timestamp and numeric validation;
- deterministic reading identifier;
- batch delivery;
- local JSONL buffer;
- retry after connectivity loss;
- status file for the dashboard.

The demonstration uses the authorised historical replay in `sample_data/edge_demo_readings.csv`. The collector does not connect to mains wiring or switch loads.
