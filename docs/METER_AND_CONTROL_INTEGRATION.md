# Meter and approved-control integration

## Meter path

External smart meters or an edge gateway submit validated readings to:

```text
POST /api/meter-readings
```

When `AI4I_API_KEY` is configured, the sender includes it as `X-API-Key`. The existing ingestion layer validates the schema, timestamps and electrical values, prevents deterministic duplicates, stores readings locally, refreshes the multi-horizon forecast and processes eligible notifications.

The Home page reads `/api/integration/status` and shows whether the ingestion API is ready or readings have been received.

## Approved control path

Every recommended response is limited to configured deferrable or sheddable load groups. Critical groups cannot be dispatched. The sequence is:

```text
AI forecast → engineering safety rules → approval deck → operator decision
→ control gateway → smart breaker or software plant → measured reduction
```

The default gateway mode is `simulation`, which produces an auditable command acknowledgement without contacting electrical equipment.

### Pilot HTTP gateway

Set these environment variables only for an authorised installation:

```text
SIMBA_CONTROL_MODE=http
SIMBA_CONTROL_ENDPOINT=https://approved-gateway.example/control
SIMBA_CONTROL_TOKEN=<secret token>
SIMBA_CONTROL_ALLOW_LIVE=true
SIMBA_CONTROL_TIMEOUT_SECONDS=5
```

The gateway receives a JSON command containing the command ID, facility, load group, action, reduction, duration, start time, end time and approving operator. The external gateway must still enforce local electrical interlocks, protection settings, breaker feedback, manual override and fail-safe behaviour.

`SIMBA_CONTROL_MODE=dry_run` validates and records commands without sending them. Secrets remain in environment variables and are not placed in the repository.
