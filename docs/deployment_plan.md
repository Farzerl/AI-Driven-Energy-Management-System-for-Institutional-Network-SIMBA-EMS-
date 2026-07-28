# Deployment Plan

## Deployment model

The recommended pilot is a hybrid institutional deployment:

- approved existing meter interface, export, or optional submeter;
- Raspberry Pi or equivalent edge gateway for validation and local buffering;
- University-controlled server for API, dashboard, configuration, and audit logs;
- optional approved ZCHPC support for retraining, encrypted backup, and recovery;
- browser access for authorised Estates and Facilities personnel.

## Pilot site and users

The first pilot should be limited to Central Kitchens and one selected hostel after:

- meter-interface survey;
- flexible-load assessment;
- critical-load exclusion;
- network and hosting approval.

Initial users:

- one or more Estates and Facilities operators;
- one engineering administrator;
- one project support contact;
- one Finance verifier for account tariff and billing review.

## Minimum host

A practical institutional virtual machine:

- 4 to 8 vCPU;
- 16 GB RAM, with 32 GB preferred for retraining;
- 100 GB or more encrypted storage;
- supported Linux or Windows Server;
- institutional DNS and TLS termination;
- restricted network access;
- scheduled backup and log retention.

## Edge gateway

The gateway should provide:

- approved protocol, API, or file adapter;
- SAST timestamp handling;
- schema and range validation;
- durable local buffering;
- automatic retry after connectivity loss;
- duplicate protection;
- health reporting;
- monitoring-only fallback.

The repository demonstrates these behaviours with safe sample data.

## Onboarding and support

- short operator walkthrough;
- quick-start and escalation guide;
- named engineering administrator;
- named support contact;
- incident log;
- configuration and model-version record;
- periodic alert and performance review.

## Connectivity

The gateway continues collecting and buffering during server or network interruption. It forwards pending data when the service returns. The dashboard is lightweight and uses no external CDN.

## Monitoring

Monitor:

- API health;
- gateway activity;
- buffer depth;
- missing intervals;
- duplicate and rejected readings;
- operator actions;
- model and configuration version;
- service errors;
- backup completion.

## Backup and recovery

- version-controlled source and configuration;
- scheduled encrypted backups for operational data and audit logs;
- documented restore test;
- local buffer preserved across temporary outages;
- rollback to monitoring-only operation after control or communications failure.

## Scale pathway

1. local demonstration;
2. two-facility monitoring pilot;
3. account tariff and response verification;
4. advisory operation with measured outcomes;
5. wider campus integration;
6. supervised control only after engineering and institutional approval.

## Milestones

### 0 to 30 days

- confirm pilot owners and meter interface;
- run Raspberry Pi gateway demonstration;
- complete clean-machine installation test;
- define critical and flexible loads;
- configure institutional hosting.

### 31 to 60 days

- collect baseline live data;
- validate completeness and timestamps;
- tune limits and alert burden;
- train users;
- test backup, recovery, and incident handling.

### 61 to 90 days

- operate advisory pilot;
- compare matched demand periods;
- verify the financial method with authorised Finance personnel;
- document user and operational outcomes;
- decide whether to expand, revise, or stop.
