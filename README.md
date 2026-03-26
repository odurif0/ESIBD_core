# ESIBD_core

Base repository for ESIBD instrumentation drivers.

## What It Does
- Connects to a CGC AMPR unit over a Windows COM port
- Uses the vendor `COM-AMPR-12.dll` through Python
- Scans installed AMPR modules
- Reads device and module state
- Sets module output voltages
- Provides a safer startup/shutdown flow through `initialize()` and `shutdown()`

## Current scope
- `cgc.ampr`: first driver ported from the existing project

## Structure
- `src/cgc/ampr/ampr_base.py`: low-level DLL wrapper
- `src/cgc/ampr/ampr.py`: high-level AMPR API
- `src/cgc/ampr/helpers.py`: startup/shutdown helpers
- `src/cgc/error_codes.json`: CGC error codes
- `src/cgc/ampr/vendor/`: vendor-provided artifacts

## Notes
- The AMPR driver depends on `COM-AMPR-12.dll` and is therefore Windows-only.
- Additional device families can be added later under `cgc`, `pfeiffer`, and similar namespaces.

## Editable installation
```bash
pip install -e /home/durif/Git/ESIBD_core
```

## Minimal example
```python
from cgc.ampr import AMPR

ampr = AMPR("ampr_main", com=5)
ampr.initialize()
try:
    print(ampr.get_status())
finally:
    ampr.shutdown()
```
