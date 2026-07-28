# Security and Compliance

- Secrets are loaded from environment configuration and are not committed.
- Write endpoints can require `X-API-Key` in the demonstration.
- A field deployment should use institutional identity, role-based access, TLS, and a reverse proxy.
- Meter readings, forecasts, operator decisions, model version, and control events are logged.
- Duplicate readings are rejected by deterministic identifiers.
- The release excludes source workbooks, credentials, runtime logs, and local environment files.
- Physical control must comply with electrical protection, isolation, commissioning, and institutional authorization requirements.
