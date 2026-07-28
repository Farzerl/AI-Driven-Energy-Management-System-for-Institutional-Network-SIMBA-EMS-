# SIMBA-EMS Forecast Model Card

## Purpose

The forecast engine estimates facility apparent demand before an expensive demand interval occurs. It supplies four horizons, an uncertainty-aware risk classification, and lead time to the operator. It does not issue electrical switching commands.

## Model families retained in the product

| Model | Purpose | Runtime |
|---|---|---|
| Histogram Gradient Boosting | Facility-aware tabular forecast from lags, rolling statistics, electrical measurements and calendar features | Portable JSON tree engine |
| Compact LSTM | Ordered sequence forecast from 49 half-hour readings | Portable NumPy inference from JSON weights |
| Compact Transformer | Attention-based sequence forecast over the same 49-reading window | Portable NumPy inference from JSON weights |
| Validation-weighted hybrids | Combine model families only when chronological validation supports the blend | Deterministic weighted ensemble |

Automatic mode selects the best validated family separately for each horizon. Manual model selection is available only in Admin for diagnostics and comparison.

## Data and common chronological split

- Clean records: 255,350.
- Facilities: 22.
- Input interval: 30 minutes.
- Required history: 49 consecutive readings.
- Training samples: 103,420.
- Validation samples: 13,708.
- Final common test samples: 30,579.
- Training end: 17 March 2026 at 23:30.
- Validation: 18 to 31 March 2026, with all targets before April.
- Final test: April 2026.

Every model and hybrid is evaluated on the same final test samples. Time-series records are never randomly shuffled across the split.

## Features

The HGB model uses current electrical measurements, measured-energy quality, facility identity, calendar features, demand lags, rolling statistics, trend and variability. The LSTM and Transformer use the same ordered electrical and calendar context across the 49-reading window.

## Automatic horizon selection

| Horizon | Selected family | MAE | RMSE | WAPE | R² | High-risk recall | High-risk F1 |
|---|---|---:|---:|---:|---:|---:|---:|
| 30 minutes | HGB + LSTM | 2.2789 kVA | 5.7625 kVA | 6.697% | 0.9816 | 90.10% | 63.44% |
| 2 hours | HGB | 4.0415 kVA | 9.3617 kVA | 11.877% | 0.9513 | 88.36% | 36.76% |
| 6 hours | HGB + LSTM + Transformer | 4.5885 kVA | 9.8124 kVA | 13.483% | 0.9465 | 89.54% | 29.11% |
| 24 hours | HGB + LSTM | 4.4420 kVA | 9.9919 kVA | 13.056% | 0.9447 | 88.66% | 29.61% |

The 30-minute horizon is the urgent operator signal. Longer horizons support planning and are not treated as equivalent urgent alerts.

## Individual model comparison

Mean MAE across the four horizons:

| Family | Mean MAE |
|---|---:|
| HGB | 3.8572 kVA |
| LSTM | 4.6033 kVA |
| Transformer | 4.8912 kVA |
| HGB + LSTM | 3.8377 kVA |
| HGB + Transformer | 3.8427 kVA |
| LSTM + Transformer | 4.5136 kVA |
| All three | 3.8375 kVA |

The neural models remain part of the product because they provide independent sequence representations, diagnostic comparison, and small validated ensemble gains at selected horizons. They are not claimed to beat HGB in isolation on this dataset.

## Why the selected models fit

HGB is strong on the available structured dataset, learns nonlinear facility and calendar interactions, trains efficiently, and runs locally. The LSTM contributes ordered transition information. The Transformer contributes long-window attention. Validation-weighted hybrids prevent a weaker family from degrading a horizon through equal averaging.

## Uncertainty and peak risk

Each horizon returns an expected forecast, an upper forecast, utilisation against the configured planning limit, and a risk classification. Error-tail evidence includes P90, P95, P99 absolute errors, bias, under-forecast fraction, precision, recall and F1.

Recall is prioritised for the urgent 30-minute warning because missing an imminent peak is more costly than asking an operator to review an additional advisory alert. Operator approval and deterministic safety rules limit the consequence of false positives.

## Input safeguards

- Fewer than 49 readings produces an explicit error.
- Irregular half-hour gaps stop forecasting.
- Duplicate readings use deterministic identifiers.
- Unknown facilities and model-schema mismatches produce explicit errors.
- Manual tests, replay and simulation cannot update production calibration.
- Diagnostic model selection cannot mutate the active model mode.

## Controlled adaptation

Verified realised readings may update a bounded residual-correction and uncertainty layer. They do not rewrite trained model weights. New model promotion requires offline retraining, common chronological validation, final test review, versioning and approval.

## Limitations

- Models were trained on one institutional network and require site-specific validation elsewhere.
- Weather, occupancy and detailed academic events are not direct current inputs.
- Facility meters do not identify the exact appliance causing a peak.
- Forecasts are not protection settings or safety interlocks.
- Field control requires an authorised load map, protection review, interlocks, manual override and commissioning.

Full machine-readable results are stored in `evidence/model_validation/model_family_comparison.json`.
