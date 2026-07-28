# Meter interface options

The final interface must be selected after checking the installed UZ meters and obtaining operational approval.

## Preferred order

1. Existing meter API or approved database export.
2. Modbus TCP from an existing meter or gateway.
3. Isolated Modbus RTU through an approved RS485 interface.
4. Scheduled CSV export during the early pilot.
5. Optional additional submetering where existing coverage is insufficient.

## Required fields

- timestamp with timezone;
- facility or meter alias;
- active energy in kWh;
- apparent demand in kVA;
- power factor;
- data-quality or communication status where available.

Real meter addresses, credentials and facility mappings must not be committed to the public repository.
