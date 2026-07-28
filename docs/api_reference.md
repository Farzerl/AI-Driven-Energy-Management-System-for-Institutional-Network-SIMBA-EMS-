# API Reference

Base URL for the local demonstration: `http://127.0.0.1:8000`

Write endpoints require `X-API-Key` only when `AI4I_API_KEY` is configured.

## Health and evidence

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | API, model, evidence, and authentication status |
| GET | `/api/summary` | Dataset, forecast, peak-risk, and controller summary |
| GET | `/api/evidence` | Dashboard evidence bundle |
| GET | `/api/control-comparison` | Historical controller comparison evidence |
| GET | `/api/cost-impact` | Tariff and planning-cost outputs |
| GET | `/api/readiness-evidence` | Model, edge-runtime, and simulation readiness |

## Model and live forecasting

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/model-status` | Model name, family, periods, horizons, and metrics |
| POST | `/api/meter-readings` | Validate and append one reading or a batch, then refresh forecasts |
| GET | `/api/meter-readings` | Read recent stored meter values |
| GET | `/api/live-forecasts` | Read stored four-horizon forecast records |
| GET | `/api/live-alerts` | Read current medium and high risk alerts |
| POST | `/api/live-inference/rebuild` | Rebuild forecasts from stored readings |
| GET | `/api/edge-status` | Gateway and buffer status |

### Meter batch

```json
{
  "readings": [
    {
      "timestamp": "2026-04-21T06:00:00+02:00",
      "facility_id": "Central Kitchens NC1 4",
      "kva": 288.06,
      "kwh": 144.01,
      "power_factor": 1.0,
      "source": "authorised-historical-replay"
    }
  ]
}
```

A facility needs 49 completed 30-minute readings before the live service creates a forecast.

### Forecast record

```json
{
  "facility_id": "Central Kitchens NC1 4",
  "current_kva": 288.06,
  "facility_limit_kva": 289.22,
  "peak_risk": "high",
  "risk_lead_minutes": 30,
  "forecasts": {
    "30_minutes": {"minutes": 30, "forecast_kva": 303.37, "risk": "high"},
    "2_hours": {"minutes": 120, "forecast_kva": 267.94, "risk": "medium"},
    "6_hours": {"minutes": 360, "forecast_kva": 228.57, "risk": "low"},
    "24_hours": {"minutes": 1440, "forecast_kva": 283.28, "risk": "high"}
  }
}
```

Values above are an example from the packaged replay and may change after retraining.

## Operator decisions

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/alerts` | Read alerts from the evidence and live forecast services |
| GET | `/api/operator-decisions` | Read decision history |
| POST | `/api/operator-decisions` | Record confirm, defer, dismiss, or mute |

## Software-in-the-loop simulation

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/simulation/scenarios` | List scenarios and boundaries |
| POST | `/api/simulation/reset` | Select scenario and controller mode |
| GET | `/api/simulation/state` | Read current scenario state |
| POST | `/api/simulation/step` | Advance one or more intervals |
| POST | `/api/simulation/action` | Submit an operator action |
| POST | `/api/simulation/approve-recommendation` | Apply the current recommendation |
| GET | `/api/simulation/metrics` | Read peak, energy, action, and safety metrics |
| GET | `/api/simulation/events` | Read scenario event history |
| GET | `/api/simulation/comparison` | Compare no control, rule, and forecast-assisted control |

## Responses

- `200`: completed;
- `401`: missing or incorrect API key;
- `422`: validation or operating-rule error;
- `500`: storage or server failure.

## Notification endpoints

### `GET /api/notifications/status`

Returns notification mode, minimum risk, cooldown, recipient counts, provider readiness, and `approval_channel: dashboard_only`.

### `GET /api/notifications/settings`

Returns editable local settings and recipient lists. Passwords and tokens are replaced by boolean `*_set` indicators.

### `PUT /api/notifications/settings`

Atomically saves notification settings and activates them without restarting the API. The endpoint may require `X-API-Key`.

### `GET /api/notifications/events?limit=100`

Returns the delivery audit log. States include `dry_run`, `sent`, `failed`, and `disabled`.

### `POST /api/notifications/process`

Processes currently qualifying live alerts. The endpoint may require `X-API-Key` when a key is configured.

### `POST /api/notifications/test?channel=email`

Creates a channel test using the first enabled recipient.

### `POST /api/notifications/test-target`

Accepts `{ "channel": "email", "recipient": "..." }` and tests one selected recipient. In `dry_run`, the message is composed and logged without external delivery.

## Operator decision boundary

`POST /api/operator-decisions` accepts only:

```json
{
  "alert_id": "...",
  "decision": "confirm",
  "operator": "estates-operator",
  "note": "...",
  "requested_reduction_kva": 35.0,
  "origin": "dashboard"
}
```

Any non-dashboard origin is rejected. Email notifications do not expose this endpoint as an approval action.

## Energy and power-quality forecasts

### `GET /api/power-quality-forecasts`

Returns non-blocking facility forecasts for active kW and signed reactive kVAR, with physically derived kWh, estimated kVARh, apparent-kVA cross-check and power-factor risk at 30 minutes, 2 hours, 6 hours and 24 hours.

Query parameter:

- `force=true` requests a new trained-model batch for the current meter state.

The endpoint may temporarily return `source: seasonal_persistence_guard` while the trained local model refreshes in the background.

### `GET /api/admin/power-quality/status`

Requires an authenticated Admin session. Returns model readiness, selected target-by-horizon routes, setup evidence, runtime latency/fallback counts and the training command.
