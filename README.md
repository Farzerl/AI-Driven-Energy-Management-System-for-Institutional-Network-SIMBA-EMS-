# SIMBA-EMS

**AI4I 2026 Track 3 — Development | Source and technical-evidence repository**

SIMBA-EMS is a locally hosted, AI-assisted institutional energy-management MVP for Estates and facilities teams. It receives electrical meter readings, forecasts demand and power-quality conditions, applies deterministic engineering safety rules, ranks controllable responses, requires operator approval, and records the resulting decision and impact evidence.

## Current product status

- Working browser dashboard and FastAPI backend.
- Multi-horizon demand forecasting at 30 minutes, 2 hours, 6 hours and 24 hours.
- HGB, LSTM, Transformer and Chronos-2 model routing with chronological validation.
- Separate active-power and reactive-power forecasting for power-factor and tariff-risk analysis.
- Ranked multi-facility action queue.
- Dashboard-only approval, rejection, acknowledgement and audit trail.
- Gmail notification support with local credentials and dry-run mode.
- Server-side software-in-the-loop replay.
- External or physical control disabled by default.
- Planning and simulated impact are not presented as realised electricity-bill savings.

## Core workflow

```text
smart meter or safe simulation
    → edge validation and buffering
    → FastAPI ingestion
    → operational storage
    → multi-model forecasting
    → uncertainty and risk assessment
    → deterministic safety rules
    → operator approval
    → simulation or authorised gateway
    → verification and audit evidence
```

See [`docs/architecture.md`](docs/architecture.md) and the architecture diagrams under [`docs/diagrams`](docs/diagrams).

## Repository scope

This public-review repository contains:

- application and API source code;
- dashboard source and assets;
- Windows launcher source and build script;
- dependency manifests and lock files;
- anonymised sample and simulation data;
- database/data-contract documentation;
- AI method, model-card and validation summaries;
- automated tests and security/repository-audit evidence;
- edge, deployment, pilot, business and safety documentation.

It deliberately excludes:

- `.venv` and installed third-party packages;
- runtime state, logs, credentials and local operator records;
- the raw institutional smart-meter dataset;
- Chronos-2 `model.safetensors` weight files;
- private model or dataset ZIP archives;
- compiled `SIMBA-EMS.exe` and Windows shortcut files;
- cache files, temporary test folders and historical patch notes;
- large row-level prediction exports that are not required to inspect the method.

The adjudication build and the public source repository are therefore separate artefacts. The build demonstrates the full working product. This repository makes the implementation, tests, architecture, evidence and limitations inspectable.

## AI and data credibility

SIMBA-EMS uses forecasting because a fixed threshold reacts to present demand, while the operator needs advance warning of a likely peak and enough lead time to coordinate safe loads.

Model selection is evidence-led:

- models are evaluated chronologically rather than with random time shuffling;
- simpler persistence and rule-based baselines are retained for comparison;
- the strongest individual model is not assumed to be the most complex model;
- routing is selected by horizon using validation evidence;
- a trained challenger is not promoted when it performs worse than the existing route;
- AI cannot override deterministic critical-load and controllability rules;
- the operator remains accountable for approval.

Detailed evidence is available in:

- [`docs/ai_justification.md`](docs/ai_justification.md)
- [`docs/model_card.md`](docs/model_card.md)
- [`docs/MODEL_COMPARISON_REPORT.md`](docs/MODEL_COMPARISON_REPORT.md)
- [`evidence/model_validation`](evidence/model_validation)
- [`evidence/controller_comparison`](evidence/controller_comparison)

## Data statement

Development and validation used authorised institutional half-hourly electrical measurements covering 22 monitored facilities. The raw institutional dataset is not distributed in this public repository.

The repository contains only:

- sanitised sample readings;
- public simulation scenarios;
- data schemas and field definitions;
- aggregated validation metrics and evidence.

See [`docs/dataset_statement.md`](docs/dataset_statement.md) and [`data_schema`](data_schema).

## Quick source setup

### Requirements

- Windows 11 x64 or a compatible Python environment.
- Python 3.12, 64-bit.
- The dependency files in the repository root.
- Separately provisioned model assets for full Chronos-2 inference.

### Create the environment

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dashboard.lock.txt
```

### Provision model assets

Large Chronos-2 weight files are not stored in Git. See [`models/README.md`](models/README.md) for the expected paths and distribution boundary.

### Start the backend directly

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.api.server:create_app --factory --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

### Windows launchers

- `START_SIMBA_EMS.bat` is the technical fallback.
- `SETUP_AND_START_SIMBA_EMS.bat` prepares a local environment where downloads are permitted.
- `BUILD_SIMBA_EMS_LAUNCHER.bat` builds the no-console Windows launcher from `windows_launcher/`.
- The compiled executable is distributed as a separate build artefact, not committed to source control.

## Tests and audits

Install development dependencies and run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.security_scan --output-dir evidence/validation/security
.\.venv\Scripts\python.exe -m scripts.repository_audit --output-dir evidence/validation/repository
```

The GitHub Actions workflow is under [`.github/workflows/quality.yml`](.github/workflows/quality.yml).

## API and storage

- API reference: [`docs/api_reference.md`](docs/api_reference.md)
- Data contract: [`data_schema`](data_schema)
- System architecture: [`docs/architecture.md`](docs/architecture.md)
- Operator workflow: [`docs/operator_workflow.md`](docs/operator_workflow.md)
- Software-in-the-loop specification: [`docs/SOFTWARE_IN_THE_LOOP_SPEC.md`](docs/SOFTWARE_IN_THE_LOOP_SPEC.md)

Runtime JSONL stores are created locally and intentionally ignored by Git.

## Security and operational boundary

- Normal local startup binds to `127.0.0.1`.
- Secrets are supplied locally and are not committed.
- Email messages notify but cannot approve an action.
- Write operations use authentication, validation and idempotency controls.
- Critical loads remain protected by deterministic engineering rules.
- Physical control requires an authorised load map, interlocks, manual override, commissioning and engineering approval.
- The public demonstration defaults to simulation.

See [`docs/security_and_compliance.md`](docs/security_and_compliance.md), [`docs/known_limitations.md`](docs/known_limitations.md) and [`hardware/safety_and_control_boundary.md`](hardware/safety_and_control_boundary.md).

## Repository map

```text
.github/          continuous integration checks
config/           public configuration and planning limits
dashboard/        client and administration interface
data/             public software-in-the-loop scenarios
data_schema/      meter-reading contract
docs/             architecture, AI, API, security and deployment documents
evidence/         compact validation, cost, controller and audit evidence
hardware/         edge and protected-control boundary
models/           lightweight routing/configuration; no large weights
sample_data/      sanitised example readings
scripts/          setup, training, audit, verification and benchmarking tools
src/              backend, forecasting, rules, notification and simulation code
tests/            API, model, UI, control and reliability tests
windows_launcher/ Windows launcher source, icon and manifest
```

## Known limitations

- The public repository does not contain the raw institutional dataset or large model weights.
- Live physical switching is not commissioned in this MVP.
- Cost and savings values are planning, scenario or software-in-the-loop estimates until verified against measured operation and invoices.
- Live Gmail delivery requires a locally configured account and application password.
- A pilot installation requires institutional authority, protection review, commissioning and an approved control map.

## Licence

See [`LICENSE`](LICENSE). Third-party models, frameworks and datasets remain subject to their own licences and access conditions.
