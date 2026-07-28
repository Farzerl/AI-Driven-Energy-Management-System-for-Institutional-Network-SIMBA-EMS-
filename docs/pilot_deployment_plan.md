# Cost-Impact Pilot Deployment Plan

## Proposed pilot scope

Start with Central Kitchens and one selected hostel because the study identifies catering and high-occupancy facilities as important demand contributors.

## Data path

Existing meter or temporary submeter -> Raspberry Pi edge gateway -> local buffer -> institutional API -> forecast and peak-risk service -> Estates and Facilities dashboard.

## Operating boundary

- Advisory and operator-confirmed actions only.
- No critical loads controlled.
- No direct mains actuation in the submission prototype.
- Manual override and protection review required before any future supervised control.

## Roles

- Estates and Facilities: operational decisions and site access.
- Electrical and Electronic Engineering: modelling, gateway support and analysis.
- Finance: authorised tariff and bill verification inside UZ.
- ICT or ZCHPC: hosting, backup and controlled access where approved.

## Cost evidence produced by the public prototype

- estimated monthly expenditure range;
- estimated maximum-demand range;
- January and April component breakdown;
- conservative, central and stretch peak-management scenarios;
- explicit assumptions and claim limitations.

## Decision gate

Proceed to a controlled pilot only after:

1. site meter interface is confirmed;
2. controllable loads are identified;
3. critical-load exclusions are signed off;
4. tariff inputs are verified against the account;
5. baseline data quality is accepted.
