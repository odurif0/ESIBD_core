# CGC Instruments

This package contains the instrument drivers for CGC hardware supported by
`esibd-core`.

## Instruments

- `cgc.ampr`: AMPR controller driver
- `cgc.amx`: AMX controller driver
- `cgc.dmmr`: DMMR picoammeter driver
- `cgc.psu`: PSU controller driver

## Package Layout

- `src/cgc/__init__.py`: top-level CGC exports
- `src/cgc/error_codes.json`: shared vendor error descriptions
- `src/cgc/ampr/`: AMPR driver, vendor DLL, and AMPR-specific documentation
- `src/cgc/amx/`: AMX driver, vendor DLL, and AMX-specific documentation
- `src/cgc/dmmr/`: DMMR driver, vendor DLL, and DMMR-specific documentation
- `src/cgc/psu/`: PSU driver, vendor DLL, and PSU-specific documentation

## Process Isolation

On Windows, the high-level CGC clients (`AMPR`, `AMX`, `DMMR`, and `PSU`) run their
DLL-backed controllers inside dedicated worker processes when possible. This
contains blocked vendor DLL calls to the worker process instead of poisoning
the main Python process, which is especially useful in notebooks. Advanced
injected objects such as external loggers or shared thread primitives fall back
to inline mode because they cannot be shared cleanly across process
boundaries.

## Examples And Tests

- `docs/examples/cgc/`: usage examples for CGC instruments
- `tests/cgc/`: regression tests for CGC instruments
- `docs/notebooks/cgc/`: manual documentation and hardware-test notebooks
