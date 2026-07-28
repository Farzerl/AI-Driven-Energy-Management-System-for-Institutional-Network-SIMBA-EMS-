# Reproduction

## Runtime

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dashboard.lock.txt
.venv\Scripts\python -m uvicorn src.api.server:create_app --factory --host 127.0.0.1 --port 8000
```

Run the edge replay in another terminal:

```bash
.venv\Scripts\python -m src.edge.collector --config config/edge.example.json --once
```

## Tests

```bash
.venv\Scripts\python -m pytest -q
node --check dashboard/static/app.js
```

## Optional retraining

Copy one authorised dataset ZIP into `training_data`, then run:

```bash
TRAIN_MODEL.bat
```

The training process writes the model and metrics only after successful parsing, cleaning, fitting, and chronological testing.
