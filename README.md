# ESIBD_core

Base repository for ESIBD instrumentation drivers.

## Scope

This repository is intended to host multiple instrument drivers over time.

Current package families:
- `cgc`

Current implemented CGC instruments:
- `cgc.ampr`
- `cgc.amx`
- `cgc.psu`

## Repository Layout

- `src/<manufacturer>/`: driver packages for each manufacturer
- `src/cgc/`: CGC instrument family
- `plugins/esibd_explorer/`: external ESIBD Explorer plugins backed by this repository
- `tests/<manufacturer>/`: regression tests grouped by manufacturer
- `docs/examples/<manufacturer>/`: small usage examples grouped by manufacturer
- `docs/notebooks/<manufacturer>/`: manual documentation and hardware-test notebooks

## Instrument-Specific Documentation

- CGC family: [`src/cgc/README.md`](src/cgc/README.md)
- AMPR: [`src/cgc/ampr/README.md`](src/cgc/ampr/README.md)
- AMX: [`src/cgc/amx/README.md`](src/cgc/amx/README.md)
- PSU: [`src/cgc/psu/README.md`](src/cgc/psu/README.md)
- ESIBD Explorer AMPR plugin (self-contained): [`plugins/esibd_explorer/ampr/README.md`](plugins/esibd_explorer/ampr/README.md)
- CGC notebooks: [`docs/notebooks/cgc/README.md`](docs/notebooks/cgc/README.md)

## Installation

### Install

```powershell
python -m pip install --no-cache-dir git+https://github.com/odurif0/ESIBD_core.git
```

### Update

```powershell
python -m pip install --force-reinstall --no-cache-dir git+https://github.com/odurif0/ESIBD_core.git
```
