# Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| Data gaps or meter interruption | Weak forecasts and incomplete evidence | Quality flags, local buffering, retry, completeness monitoring |
| Incorrect timestamp or timezone | Misaligned tariff and forecast features | SAST validation and schema tests |
| Public tariff proxy differs from account tariff | Incorrect planning value | Label public estimates and verify against the account during the pilot |
| Model regime change | Forecast degradation | Rolling validation, baseline monitoring, advisory mode |
| False or excessive alerts | Operator fatigue | Compare with rule baseline, tune limits, allow defer/dismiss/mute |
| Unsafe load action | Service disruption | Human approval, critical-load exclusion, monitoring-only fallback |
| Credential exposure | System compromise | Environment variables, scan, no committed secrets |
| Raw data disclosure | Institutional privacy breach | Aggregate-only evidence and repository audit |
| Edge gateway outage | Missing data | Durable local buffer, restart, retry, health status |
| Duplicate readings | Distorted totals | Deterministic reading identifiers and duplicate rejection |
| File-store corruption | Lost local audit evidence | Backups, controlled storage, production database before scale |
| Unclear operational owner | Pilot abandonment | Named operator, administrator, support contact, and escalation route |
| Overstated submission claim | Loss of trust | Claims register, limitations, and evidence index |
| Automatic control attempted too early | Electrical and safety risk | No actuator in MVP; engineering gate before future supervised control |

## Safety principle

The current product is a monitoring and decision-support MVP. Direct electrical control remains a later, supervised stage requiring protection review, institutional approval, interlocks, manual override, and rollback.
