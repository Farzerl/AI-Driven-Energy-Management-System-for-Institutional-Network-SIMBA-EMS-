# Software-in-the-Loop Simulator Specification

## Purpose

The simulator provides a controllable institutional plant for demonstration, testing and controller comparison without connecting to mains equipment.

## Data basis

Each packaged scenario contains a compact measured replay for all 22 monitored facilities plus at least 49 preceding half-hour readings for model features. The baseline trajectories are measured. Facility load partitions, flexible-load ratings, operating windows, minimum service fractions, response delay, and rebound behaviour are conservative engineering assumptions pending appliance-level submetering and commissioning.

## Scenarios

- `campus_peak_replay`: measured morning peak, Central Kitchens focus, all facilities active.
- `evening_residential_replay`: measured evening demand with residential emphasis.
- `after_hours_review`: measured night sequence used to demonstrate investigation rather than automatic fault diagnosis.

## Controllers

### No control

Replays the measured baseline without a recommendation.

### Current-demand rule

Uses current demand and acts only when the present facility or campus threshold is reached.

### Forecast-assisted

Uses uncertainty-aware 30-minute and 2-hour forecasts for near-term coordination. The 6-hour and 24-hour horizons remain visible for shift and next-day planning. When one high-risk facility has no available approved flexibility, the controller continues through the ranked facility list.

### Manual

Disables automatic recommendations while retaining direct operator actions and all constraints.

## Playback

Playback speed, scenario, controller, pause-on-recommendation and optional comparison-on-load are stored in system settings. Automatic playback never approves an action.

## After-hours guard

The guard compares a current observation with preceding after-hours reference values. A material relative and absolute deviation creates an investigation escalation. It does not diagnose an equipment fault and does not automatically interrupt protected loads.

## Action constraints

- Critical groups are not selectable.
- Deferral is limited to deferrable groups.
- Shedding is limited to sheddable groups.
- Reduction cannot exceed available non-critical capacity.
- Duration cannot exceed the configured limit.
- Actions begin on a future interval.
- Recovery is delayed outside peak periods.
- Recovery is bounded by both facility and campus headroom.

## Metrics

- baseline and controlled peak kVA;
- peak reduction;
- energy and demand-charge planning proxies;
- shifted and curtailed energy;
- action count;
- campus-limit exceedances;
- critical-load violations;
- anomaly escalations;
- measured model inference latency.

## API

```text
GET  /api/simulation/scenarios
POST /api/simulation/reset
GET  /api/simulation/state
POST /api/simulation/step
POST /api/simulation/action
POST /api/simulation/approve-recommendation
GET  /api/simulation/metrics
GET  /api/simulation/events
GET  /api/simulation/comparison
GET  /api/system-settings
PUT  /api/system-settings
```

## Claim boundary

The simulator is not a commissioned electrical digital twin. The response model is suitable for workflow and controller evaluation. Field savings and network feasibility require commissioned equipment data and engineering validation.
