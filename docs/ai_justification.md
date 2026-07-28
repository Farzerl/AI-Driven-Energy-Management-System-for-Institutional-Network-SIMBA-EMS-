# AI Justification and Model Selection

## Why forecasting is required

A current-demand rule asks whether the limit has already been crossed. SIMBA-EMS asks whether the next interval is likely to cross it, how uncertain that forecast is, and how much approved load should be prepared for coordination.

The AI predicts demand. Deterministic rules continue to govern critical loads, action types, duration, minimum service, operator approval and auditability.

## Why three model families were tested

- **HGB** tests the strongest practical tabular approach for engineered lag and calendar features.
- **LSTM** tests whether recurrent sequence memory improves changing demand transitions.
- **Transformer** tests whether attention across the complete history window improves long-range relationships.
- **Hybrids** test whether their independent errors can be combined without assuming equal quality.

All use the same common final test samples. Separate test portions were not assigned to different models because that would make the comparison invalid.

## Selection result

HGB was the strongest individual family. LSTM and Transformer did not beat it independently on aggregate. Small gains appeared when later validation data assigned conservative weights to neural outputs at selected horizons.

Automatic routing therefore uses:

- 30 minutes: HGB + LSTM.
- 2 hours: HGB.
- 6 hours: HGB + LSTM + Transformer.
- 24 hours: HGB + LSTM.

The model family is selected by evidence, not by complexity.

## Why not deploy only an LSTM or Transformer

The current dataset is structured, covers less than a full annual cycle, and has strong facility and schedule effects. HGB uses this efficiently. Neural sequence models add training complexity, can overfit, and produced higher individual test error. They remain available as product components and comparison evidence rather than being forced into production where validation does not support them.

## Baselines

Every forecast is compared with persistence, where future demand equals the latest reading. The software-in-the-loop controller comparison also includes no control and a current-demand threshold rule.

A learned model is not treated as useful merely because it is labelled AI. It must improve forecast or operational performance on later data.

## Alert evaluation

The report includes precision, recall, F1, false positives, missed events, bias and error tails. The 30-minute alert balances 90.10% recall with 63.44% F1 in the selected hybrid. Longer horizons support preparation and are not used as repeated urgent email triggers.

## Governance

- Common chronological split.
- No final-test tuning.
- Horizon-specific selection.
- Explicit persistence comparison.
- Input-quality failure rather than silent padding.
- Bounded residual adaptation only for verified measured data.
- Manual test and simulation isolation.
- Dashboard-only operator approval.
- Versioned promotion after offline validation.
