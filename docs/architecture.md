# SIMBA-EMS Technical Architecture

SIMBA-EMS is a local-first, layered energy-management platform. The deployed sequence is:

1. **Meter and device layer** - billing meters, submeters, STM32 sensing and equipment-state inputs.
2. **Edge gateway** - Raspberry Pi validation, timestamp alignment, deduplication, local buffering and retry.
3. **Secure ingestion API** - authenticated FastAPI endpoints with schema and range validation.
4. **Operational data platform** - time-series readings, quality flags, facility metadata and engineered features.
5. **Forecast and uncertainty service** - HGB, LSTM, Transformer and Chronos-2 horizon routes for kVA, kW and kVAR.
6. **Risk and engineering rules** - peak/PF risk, tariff logic, critical-load exclusions and safe controllability limits.
7. **Operator decision layer** - Gmail attention alerts, dynamic action queue, approval deck, identity and idempotent decisions.
8. **Control and verification layer** - software plant, dry run or authorised gateway; command acknowledgement, approved-response cost estimate, measured reduction and audit evidence.

## Persistence boundaries

- A **time-series database** stores validated readings, feature values and quality flags.
- An **audit and evidence database** stores forecasts, recommendations, operator decisions, commands, outcomes and model-version references.
- A **model registry** retains validation metrics and deployment routing. A model cannot update production behaviour until it passes offline chronological validation.

## Safety boundary

AI forecasts risk but never overrides local protection. Deterministic engineering rules exclude critical loads, the operator authorises each action, and the electrical gateway remains subordinate to local interlocks and manual override.
