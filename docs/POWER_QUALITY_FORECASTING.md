# SIMBA-EMS Energy and Power-Quality Forecasting

## Purpose

The existing SIMBA-EMS demand engine remains responsible for forecasting apparent demand in kVA and supporting peak-shaving decisions. This extension adds a separate, locally trained forecasting path for electrical behaviour that affects energy cost, reactive-power performance and equipment diagnostics.

The new model forecasts two independent measurements from the University of Zimbabwe archive:

- active power in kW;
- signed reactive power in kVAR.

It then derives, for every 30-minute forecast interval:

- active energy in kWh;
- estimated reactive energy in kVARh;
- apparent power as a kVA physical cross-check;
- power factor magnitude;
- low-power-factor risk;
- a Time-of-Use energy-cost planning proxy.

## Why only kW and kVAR are trained directly

Inspection of the authorised UZ archive found that interval consumption is approximately half of active power because each record represents 30 minutes:

`kWh ≈ kW × 0.5 hours`

The archive's power factor is also physically reproducible from active and reactive power:

`PF = |kW| / sqrt(kW² + kVAR²)`

Training separate independent models for kWh and power factor would duplicate information and increase overfitting and inconsistency risk. Forecasting kW and signed kVAR preserves the independent information and lets SIMBA-EMS derive the other values consistently.

The source column is labelled `Reactive energy (kVAR)`. Because kVAR is a power unit and the half-hour values behave as signed reactive power, SIMBA-EMS treats it as kVAR and labels the derived interval quantity as **estimated kVARh**. Billing-grade reactive energy must be reconciled with the utility or meter register.

## Dataset findings used by the patch

The uploaded archive contains 22 facility workbooks and approximately 255,350 cleaned half-hourly readings. The relevant fields are:

- Power (kW)
- Reactive energy (kVAR)
- Demand (kVA)
- Power factor
- Consumption (kWh)
- Data points
- Temperature
- Humidity

Temperature and humidity are only available for part of the archive, so they are not mandatory future inputs. They should be added later only when a dependable live weather or sensor source is available.

## Training strategy

The power-quality adaptation starts from the already installed local model in this order:

1. `models/chronos-2-finetuned` — the UZ demand-adapted model;
2. `models/chronos-2-base`;
3. one official Chronos-2 ZIP from `chronos_input`, only when neither local model exists.

LoRA training learns kW and signed kVAR jointly across the 22 related facilities. It saves a separate model at:

`models/chronos-2-power-quality-finetuned`

It does not overwrite or retrain the existing kVA demand model.

Chronological validation and final testing are performed before route selection. For each target and horizon, SIMBA-EMS compares:

- same-time previous-day persistence;
- the installed source Chronos model before the new adaptation;
- the new power-quality LoRA model;
- validation-weighted Chronos/persistence hybrids.

Routes are selected separately for active kW and reactive kVAR at 30 minutes, 2 hours, 6 hours and 24 hours.

## Runtime behaviour

The browser never waits synchronously for the larger model. SIMBA-EMS immediately shows a previous-day safety forecast while one background trained-model batch refreshes all eligible facilities. The completed batch atomically replaces the temporary guard forecast.

Power-factor recommendations remain advisory. The AI may recommend inspection of motors, pumps, refrigeration or an authorised capacitor bank, but it cannot switch a capacitor bank, bypass a protection device or approve its own action.

## Dashboard changes

### Home

Shows next-interval energy, estimated reactive-energy burden, facilities with forecast power-factor attention and the active model source.

### Forecasts

Shows:

- current active kW and signed reactive kVAR;
- current and conservative forecast power factor;
- kW, kVAR, kWh and estimated kVARh at all four horizons;
- facility-level low-power-factor risk;
- grounded maintenance and scheduling guidance.

### Impact

Adds projected active energy, estimated reactive-energy exposure, facilities needing attention and a Time-of-Use energy-cost proxy. These values are forecasts, not realised savings.

### Evidence

Shows target-specific validation and final-test metrics, selected routes, derived power-factor metrics and `N/A — no events` when a classification slice contains no low-power-factor events.

### Admin

Shows model readiness, deployment variant, kW and kVAR routes per horizon, inference failures/fallbacks and the local training command.

## Local training

1. Keep the existing `models/chronos-2-finetuned` folder.
2. Put exactly one authorised dataset ZIP in `training_data`.
3. A model ZIP is not required when the installed fine-tuned or base model exists.
4. Run `TRAIN_POWER_QUALITY_FORECASTS.bat` as Administrator.
5. The input ZIP is deleted only after training, verification and product tests pass.
6. Start normally with `START_SIMBA_EMS.bat`.

## Claim boundary

Use this external description:

> SIMBA-EMS forecasts apparent demand in kVA and separately forecasts active kW and signed reactive kVAR. It derives interval kWh, estimated kVARh and power-factor risk, then provides operator-reviewed demand and maintenance recommendations.

Do not claim billing-grade kVARh, realised savings or automatic capacitor switching until verified in an authorised institutional pilot.
