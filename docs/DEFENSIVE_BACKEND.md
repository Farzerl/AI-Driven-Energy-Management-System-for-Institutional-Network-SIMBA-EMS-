# Defensive Backend Design

## Validation

Pydantic validates API payload types, ranges, lengths and allowed modes. The forecasting path rejects incomplete histories and irregular half-hour intervals. Runtime guardrails are validated before they are written atomically.

## Idempotency

- Meter readings are deduplicated by their stable reading identity.
- Forecast records use stable identifiers and duplicate protection.
- Controlled proof tests use caller-supplied request IDs and payload signatures.
- Notification cooldown and event identities prevent repeated delivery.
- Settings writes replace one validated revision atomically.

## Explicit errors

Expected operational failures return descriptive 401, 422 or validation responses. Examples include invalid admin sessions, unsafe critical-load requests, reused proof-test IDs with different values, malformed guardrail JSON, missing forecast history and SMTP transport mismatch.

## Isolation

Manual tests and simulation data use explicit origins. They are excluded from adaptive learning, formal metrics and realised-savings claims. Secrets are stored only in local runtime files and are never returned through public settings or diagnostic APIs.

## Fail-safe behaviour

The model forecasts risk. Deterministic rules protect critical loads. Email cannot approve control. The trained model has a bounded forecast guard and an explicit fallback. New model versions require offline chronological validation before promotion.
