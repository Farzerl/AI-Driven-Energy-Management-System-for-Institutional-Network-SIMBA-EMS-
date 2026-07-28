# Edge Gateway Demonstration

The launcher uses `sample_data/edge_demo_readings.csv` as the meter source. The edge collector reads each row, validates it, writes failed deliveries to a local buffer, retries when the API is available, and relies on deterministic reading identifiers for duplicate rejection.

The packaged replay contains 49 authorised Central Kitchens readings. This is the minimum history required by the forecasting model.

The demonstration does not connect to mains wiring and does not send switching commands.

Manual run:

```bash
python -m src.edge.collector --config config/edge.example.json --once
```
