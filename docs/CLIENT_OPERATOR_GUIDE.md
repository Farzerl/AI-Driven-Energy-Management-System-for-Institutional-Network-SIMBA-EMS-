# SIMBA-EMS Operator Guide

## What the main screen answers

The client view is designed around one question: **Does the institution need to act now?**

It shows:

- current institutional demand;
- forecast demand and time horizon;
- Normal, Attention or Action required status;
- the facility contributing most to the risk;
- the recommended approved load response;
- protected-load information;
- Gmail delivery status;
- operator confirm, defer or dismiss controls;
- the measured or simulated result after action.

## Normal workflow

1. Meter readings enter the backend and pass validation.
2. The forecast engine evaluates 30-minute, 2-hour, 6-hour and 24-hour demand.
3. Risk is calculated against configured limits and forecast uncertainty.
4. Deterministic rules exclude critical loads.
5. A Gmail alert informs the operations manager when attention is required.
6. The manager opens the dashboard and reviews the context.
7. The manager confirms, defers or dismisses the recommendation.
8. The decision and outcome are recorded.

Email is an attention channel only. It cannot approve or execute an action.

## What operators do not need to configure

Model selection, simulation, replay speed, raw guardrails, adaptive calibration and controlled test inputs are restricted to Admin. This keeps the operational view focused on decisions and value.

## Status interpretation

- **Normal:** Forecast demand is within the configured operating envelope.
- **Attention:** Demand is approaching a limit or uncertainty is material. Review the forecast.
- **Action required:** A high-risk event is forecast and an approved response is available.
- **Investigation required:** An abnormal condition is detected, but automatic control is blocked.
