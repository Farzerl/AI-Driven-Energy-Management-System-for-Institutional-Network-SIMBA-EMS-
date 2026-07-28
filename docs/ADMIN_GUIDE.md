# SIMBA-EMS Admin Guide

## Access

Open the vertical three-dot menu and select **Admin**. The temporary password is `admin`. Change it after first access. The password is stored as a salted PBKDF2-HMAC-SHA256 hash. Admin sessions expire after one hour and are held in memory only.

## Forecast model

**Automatic validated selection** is the normal setting. It selects the lowest validation-error model or hybrid independently for 30 minutes, 2 hours, 6 hours and 24 hours. Manual model selection is for diagnostics and comparison.

## Diagnostic replay

The replay environment is hidden from normal operators. It can:

- select an authorised scenario;
- choose forecast-assisted, current-demand rule, manual or no-control mode;
- step one half-hour interval at a time;
- run automatically at a configurable speed from the backend, independent of the browser dialog;
- pause for one or more operator recommendations and resume after approval;
- compare all controllers on the same baseline;
- expose the measured inference path and control response;
- show current forecasts for all 22 facilities and current-session impact.

Replay values are labelled as simulation data. They cannot update production calibration or official model metrics.

## Controlled proof test

The proof test accepts exactly four new half-hour kVA values. The service preserves 45 prior valid readings, replaces the latest four in an isolated 49-reading context, runs every selected model and shows the resulting forecasts and risk.

The request is idempotent. Reusing the same request ID and payload returns the original result. Reusing the request ID with different values is rejected. Test values do not enter the production meter store, adaptive learning, savings calculations or official validation evidence.

## Operational guardrails

The validated JSON editor supports runtime overrides for:

- campus planning limit;
- facility demand limits;
- critical-load floors;
- medium and high risk ratios;
- peak, standard and off-peak planning rates;
- demand-charge planning rate.

Empty override maps retain the measured scenario defaults. Invalid JSON, inverted risk thresholds, negative values and critical floors above configured facility limits are rejected with an explicit field error.

## Adaptive calibration

Adaptive calibration may correct persistent local residual bias after a minimum number of verified measured observations. It is bounded as a percentage of the facility limit. Manual tests, simulation, replay and diagnostic reference values are excluded. Full model replacement requires offline retraining and chronological validation.

## Diagnostics

**Refresh diagnostics** displays a sanitised runtime package containing model readiness, selected models, settings, stores, simulator state, email readiness and recent controlled tests. Passwords, Gmail app passwords and other secrets are excluded.

## Recovery

1. Stop the API window.
2. Restart `START_SIMBA_EMS.bat`.
3. Check **System status**.
4. Log in to Admin and refresh diagnostics.
5. Restore safe operational defaults when a bad override is suspected.
6. Use a controlled proof test before altering production configuration.
