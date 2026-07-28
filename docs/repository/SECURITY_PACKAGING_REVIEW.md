# Security packaging review

Generated: 2026-07-28 22:37:33 +02:00

- High-confidence findings: 0
- Manual-review findings: 7

The scan checks for private-key blocks, common cloud access-key formats, GitHub/Slack tokens, completed .env files and assigned credential-like values. It does not replace a professional security audit.

## Manual review
- $(@{File=.env.example; Type=Assigned credential-like value; Detail=Review the assigned value manually. The value is not reproduced in this report.}.File): Assigned credential-like value. Review the assigned value manually. The value is not reproduced in this report.
- $(@{File=src\admin\auth.py; Type=Assigned credential-like value; Detail=Review the assigned value manually. The value is not reproduced in this report.}.File): Assigned credential-like value. Review the assigned value manually. The value is not reproduced in this report.
- $(@{File=src\api\schemas.py; Type=Assigned credential-like value; Detail=Review the assigned value manually. The value is not reproduced in this report.}.File): Assigned credential-like value. Review the assigned value manually. The value is not reproduced in this report.
- $(@{File=src\edge\collector.py; Type=Assigned credential-like value; Detail=Review the assigned value manually. The value is not reproduced in this report.}.File): Assigned credential-like value. Review the assigned value manually. The value is not reproduced in this report.
- $(@{File=src\notifications\service.py; Type=Assigned credential-like value; Detail=Review the assigned value manually. The value is not reproduced in this report.}.File): Assigned credential-like value. Review the assigned value manually. The value is not reproduced in this report.
- $(@{File=src\notifications\service.py; Type=Assigned credential-like value; Detail=Review the assigned value manually. The value is not reproduced in this report.}.File): Assigned credential-like value. Review the assigned value manually. The value is not reproduced in this report.
- $(@{File=tests\test_simulation_api.py; Type=Assigned credential-like value; Detail=Review the assigned value manually. The value is not reproduced in this report.}.File): Assigned credential-like value. Review the assigned value manually. The value is not reproduced in this report.
