# Test Evidence

Run from the repository root:

```text
python -m compileall -q src scripts tests
node --check dashboard/static/app.js
python -m pytest -q
python scripts/security_scan.py
python scripts/repository_audit.py
python scripts/benchmark_edge_runtime.py
```

The final release validation covers:

- meter schema and deterministic duplicate protection;
- edge buffering and retry behaviour;
- HGB, LSTM and Transformer loading;
- validation-weighted hybrid inference;
- automatic and manual model routing;
- four-horizon prediction;
- incomplete-history and interval-gap rejection;
- live forecast API;
- risk and recommendation rules;
- Gmail delivery and recipient settings;
- authenticated Admin access;
- password hashing and password change;
- dashboard-only operator decisions;
- controlled four-value proof-test isolation;
- idempotent replay and conflicting request-ID rejection;
- operational guardrail validation;
- critical-load action rejection;
- rebound and service constraints;
- controller comparison;
- cost endpoints;
- client and Admin interface assets;
- release structure;
- repository and security checks.

Generated reports are stored under `evidence/validation` and `evidence/edge_runtime`.
