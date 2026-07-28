# SIMBA-EMS Model Family Comparison

## Experimental control

All model families use the same cleaned institutional meter dataset, the same 49-interval input window, the same facility list and the same forecast targets. Time order is preserved.

- Clean rows: 255,350
- Facilities: 22
- Training samples: 103,420
- Validation samples: 13,708
- Final test samples: 30,579
- Validation: 2026-03-18 to 2026-03-31 with all targets before April
- Final test: April 2026, common samples for every model

No final-test result is used to choose model weights or select a production model. Validation data determine ensemble weights and automatic routing. The common April test period measures final performance.

## Thirty-minute operational horizon

| Model | MAE kVA | RMSE kVA | WAPE | R² | Precision | Recall | F1 | Mean MAE across horizons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Histogram Gradient Boosting | 2.2787 | 5.7526 | 6.697% | 0.9816 | 0.4888 | 0.9021 | 0.6340 | 3.8572 |
| LSTM | 2.8206 | 6.8979 | 8.289% | 0.9736 | 0.4453 | 0.8842 | 0.5923 | 4.6033 |
| Compact Transformer | 2.8755 | 7.4134 | 8.451% | 0.9695 | 0.4523 | 0.8842 | 0.5985 | 4.8912 |
| HGB + LSTM | 2.2789 | 5.7625 | 6.697% | 0.9816 | 0.4895 | 0.9010 | 0.6344 | 3.8377 |
| HGB + Transformer | 2.2787 | 5.7526 | 6.697% | 0.9816 | 0.4888 | 0.9021 | 0.6340 | 3.8427 |
| LSTM + Transformer | 2.7165 | 6.9442 | 7.983% | 0.9732 | 0.4674 | 0.8920 | 0.6134 | 4.5136 |
| HGB + LSTM + Transformer | 2.2787 | 5.7526 | 6.697% | 0.9816 | 0.4888 | 0.9021 | 0.6340 | 3.8375 |

The 30-minute HGB and HGB-LSTM hybrid are effectively tied. The hybrid improves F1 slightly, while HGB has marginally lower MAE. The automatic selector uses validation performance, not the final test result.

## Automatic selection by horizon

| Horizon | Selected model | Validation MAE kVA | Final-test MAE kVA | High-risk recall | High-risk F1 |
|---|---|---:|---:|---:|---:|
| 30 minutes | HGB + LSTM | 2.2286 | 2.2789 | 0.9010 | 0.6344 |
| 2 hours | Histogram Gradient Boosting | 3.7895 | 4.0415 | 0.8836 | 0.3676 |
| 6 hours | HGB + LSTM + Transformer | 4.3667 | 4.5885 | 0.8954 | 0.2911 |
| 24 hours | HGB + LSTM | 4.1485 | 4.4420 | 0.8866 | 0.2961 |

## Why every model remains in the product

- **Histogram Gradient Boosting** is strongest on the tabular lag, rolling-statistic and facility-context representation. It is the most consistent individual model.
- **LSTM** provides a sequence-specific view of the previous 49 half-hour readings. It contributes measurable value to the 6-hour and 24-hour hybrids.
- **Compact Transformer** provides attention-based sequence modelling. It does not win the current dataset, but remains available for comparison and future retraining as longer histories, weather, occupancy and solar features are added.
- **Validation-weighted hybrids** retain only the contribution justified by validation data. A model may receive zero weight at a horizon when it does not improve validation error.

## Operational interpretation

The system does not claim that a neural network is automatically better. The product routes each horizon to the lowest validation-error option and retains persistence and engineering-rule fallbacks. For the urgent 30-minute alert horizon, recall is prioritised because missing a true peak is more costly than asking an operator to review an additional advisory warning. Approval remains on the dashboard.

## Deployment boundary

PyTorch is required only for optional retraining. Deployed LSTM and Transformer weights are exported to JSON and executed by the NumPy runtime. This keeps the operating dependency set small and supports local CPU deployment.
