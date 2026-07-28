# Cost-Impact Evidence

This folder contains a public-source planning estimate for the SIMBA-EMS submission.

## Main results

- Study-period energy: 3,753,338 kWh.
- Indicative eight-month bill: USD 603,536.75.
- Estimated monthly bill range: approximately USD 50,673 to USD 90,921.
- Estimated maximum-demand range: approximately 743 to 1,267 kVA.
- Central planning scenario: approximately USD 14,170 or 2.35% of the indicative baseline.

## Important limitation

These are not actual UZ invoices or realised savings. The public national average tariff and study-level aggregate figures are used because the institutional bill and account tariff are confidential.

## Files

- `cost_impact_summary.json`: dashboard-ready summary.
- `monthly_bill_estimates.csv`: monthly energy, load factor, demand and bill estimates.
- `scenario_savings.csv`: conservative, central and stretch scenarios.
- `study_cost_breakdown_estimates.csv`: January and April component estimates.
- `study_figures/`: cropped figures from the supplied institutional study.
- `AI4I_EMS_Public_Tariff_and_Cost_Model.xlsx`: transparent formula model.

Rebuild the JSON and CSV evidence using:

```powershell
.\.venv\Scripts\python.exe scripts\build_public_cost_estimate.py
```
