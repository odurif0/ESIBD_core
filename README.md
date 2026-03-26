# ESIBD_core

Base repository for ESIBD instrumentation drivers.

## Scope

This repository is intended to host multiple instrument drivers over time.

Current package families:
- `cgc`

Current implemented instrument:
- `cgc.ampr`

## Repository Layout

- `src/<manufacturer>/`: driver packages for each manufacturer
- `tests/`: regression tests
- `examples/`: small usage examples

## Instrument-Specific Documentation

- AMPR: [`src/cgc/ampr/README.md`](src/cgc/ampr/README.md)

## Installation

### Install

```powershell
python -m pip install --no-cache-dir git+https://github.com/odurif0/ESIBD_core.git
```

### Update

```powershell
python -m pip install --force-reinstall --no-cache-dir git+https://github.com/odurif0/ESIBD_core.git
```
