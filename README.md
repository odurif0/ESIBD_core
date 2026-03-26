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
pip install -e git+https://github.com/odurif0/ESIBD_core.git#egg=esibd-core
```

### Update

```powershell
pip install -U git+https://github.com/odurif0/ESIBD_core.git#egg=esibd-core
```
