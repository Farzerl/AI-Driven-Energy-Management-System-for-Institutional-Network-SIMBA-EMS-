# Implementation Status and Next Milestones

## Completed for the demonstration release

- authorised UZ dataset ingestion and cleaning;
- trained 30-minute, 2-hour, 6-hour, and 24-hour forecasts;
- chronological April 2026 hold-out testing;
- persistence and current-demand-rule comparisons;
- FastAPI service and browser dashboard;
- operator decision and audit workflow;
- edge replay with durable offline buffering;
- software-in-the-loop response simulator;
- tariff and cost-planning evidence;
- automated tests, repository audit, security scan, and documentation.

## Before demonstration day

- extract the final ZIP to a short Windows path;
- run `START_SIMBA_EMS.bat` once on the presentation laptop;
- verify the Demonstration, Forecasts, Operations, Evidence, and System tabs;
- keep the screenshots and pitch notes available offline;
- rehearse the primary scenario and the recovery sequence.

## First 30 days after challenge support

- confirm Estates and ICT pilot owners;
- inspect approved meter interfaces and network access;
- deploy the edge gateway on a Raspberry Pi or institutional server;
- classify critical, deferrable, and sheddable loads with Estates;
- confirm hosting, backup, access control, and retention requirements;
- verify the applicable account tariff and billing method.

## Days 31 to 60

- collect live baseline data;
- measure completeness, timestamp alignment, and gateway health;
- compare live readings with the trained facility ranges;
- tune alert thresholds with operators;
- train users and test backup and recovery procedures.

## Days 61 to 90

- run an advisory pilot;
- compare forecast alerts with actual demand events;
- record operator responses and missed opportunities;
- calculate matched-period peak and tariff effects;
- decide whether to expand, retrain, revise, or stop.

## Control gate

Physical actuation remains outside the current release. It requires approved circuit classification, protection review, interlocks, manual override, communication-loss behaviour, fail-safe states, commissioning tests, and a documented rollback procedure.
