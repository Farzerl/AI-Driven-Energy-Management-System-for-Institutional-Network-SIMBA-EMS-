# Chronos-2 local integration

## Purpose

Chronos-2 is an optional time-series foundation model challenger. It is evaluated against the existing validated SIMBA-EMS router on the same chronological validation and test origins. It becomes the default only for horizons where validation evidence shows a measurable improvement without an unacceptable loss in high-risk recall.

Chronos-2 does not replace electrical safety rules or approve actions. The explanation layer converts validated measurements, quantiles, model agreement, tariff state, limits, and controllable-load rules into traceable operator language.

## Local process

1. Keep the existing `.venv` in the repository root.
2. Put one official Chronos-2 model ZIP in `chronos_input`. The installer verifies the official `model.safetensors` SHA-256 before loading it.
3. Put one authorised dataset ZIP in `training_data`.
4. Run `INSTALL_AND_TRAIN_CHRONOS2.bat`.
5. Allow Administrator access.
6. Wait for installation, zero-shot benchmarking, LoRA fine-tuning, comparison, routing, verification, and regression tests.
7. Start the product with `START_SIMBA_EMS.bat`.

The source ZIP files are removed only after fine-tuning or safe zero-shot fallback, benchmarking, verification and all repository tests succeed. A failed run leaves them in place. Temporary extracted dataset and trainer scratch files are also removed after evidence is committed.

## Outputs

- `models/chronos-2-base/`: verified extracted base model.
- `models/chronos-2-finetuned/`: local LoRA-adapted model when fine-tuning succeeds.
- `models/chronos2/routing.json`: per-horizon production routing.
- `evidence/model_validation/chronos2_model_comparison.json`: zero-shot, adapted, existing and hybrid metrics.
- `evidence/model_validation/chronos2_predictions.csv`: sampled held-out predictions for audit.
- `runtime/chronos2_setup_state.json`: idempotent setup state.
- `runtime/chronos2_setup.log`: detailed setup log.

## Safety and claims

A larger pretrained model is not assumed to be better. The existing router remains default unless Chronos-2 or a validation-weighted hybrid wins under the configured routing policy. Final test metrics are reported separately and are never used to choose the route.
