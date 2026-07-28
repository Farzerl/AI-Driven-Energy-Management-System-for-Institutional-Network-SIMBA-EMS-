# Software-in-the-Loop Controller Comparison

Generated: 2026-07-25T21:45:52.876507+00:00

## Scenario

- **Name:** Campus morning peak replay
- **Facilities:** 22
- **Baseline source:** measured half-hourly full-campus replay
- **Planning limit:** 1203.961 kVA

| Controller | Controlled peak | Peak reduction | Limit exceedances | Approved actions | Critical violations |
|---|---:|---:|---:|---:|---:|
| No control | 1280.810 kVA | 0.000 kVA | 7 | 0 | 0 |
| Current-demand rule | 1264.124 kVA | 16.686 kVA | 5 | 19 | 0 |
| Forecast-assisted | 1262.047 kVA | 18.763 kVA | 3 | 23 | 0 |

The forecast-assisted controller reduced the replay peak by **18.763 kVA** relative to no control and by an additional **2.077 kVA** relative to the current-demand rule. It also reduced controlled planning-limit exceedances from **5** under the rule to **3**.

## Interpretation boundary

The baseline trajectories are measured data. Controllable load partitions and response behaviour are engineering assumptions used to test decision logic. These results are planning evidence and are not realised bill savings or commissioned switching results.
