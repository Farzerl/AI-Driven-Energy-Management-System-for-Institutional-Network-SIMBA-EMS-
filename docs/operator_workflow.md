# Operator Workflow

## Alert content

An operator receives:

- facility;
- current kVA;
- facility planning limit;
- 30-minute, 2-hour, 6-hour, and 24-hour forecasts;
- highest risk;
- risk lead time;
- recommended action;
- protected-load statement.

## Decisions

- **Confirm**: approve the listed action.
- **Defer**: postpone the decision for a stated period.
- **Dismiss**: reject the recommendation and record the reason.
- **Mute**: suppress repeated alerts for an approved interval.

## Control constraints

A recommendation can use only eligible deferrable or sheddable loads. It must respect permitted hours, maximum reduction, maximum duration, minimum service, critical-load floor, active actions, and recovery state.

## Audit record

The log records alert, facility, decision, operator, reason, timestamp, model version, and scenario or meter context.
