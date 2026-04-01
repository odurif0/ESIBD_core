# CGC Instruments

This package contains the instrument drivers for CGC hardware supported by
`esibd-core`.

## Instruments

- `cgc.ampr`: AMPR controller driver
- `cgc.amx`: AMX controller driver
- `cgc.psu`: PSU controller driver

## Package Layout

- `src/cgc/__init__.py`: top-level CGC exports
- `src/cgc/error_codes.json`: shared vendor error descriptions
- `src/cgc/ampr/`: AMPR driver, vendor DLL, and AMPR-specific documentation
- `src/cgc/amx/`: AMX driver, vendor DLL, and AMX-specific documentation
- `src/cgc/psu/`: PSU driver, vendor DLL, and PSU-specific documentation

## Examples And Tests

- `examples/cgc/`: usage examples for CGC instruments
- `tests/cgc/`: regression tests for CGC instruments
