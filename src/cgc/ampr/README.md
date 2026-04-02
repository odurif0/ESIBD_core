# CGC AMPR

Python driver for the CGC AMPR unit.

## What It Does

- Connects to a CGC AMPR unit over a Windows COM port
- Uses the vendor `COM-AMPR-12.dll` through Python
- Scans installed AMPR modules
- Reads device and module state
- Sets module output voltages
- Provides a safer startup/shutdown flow through `initialize()` and `shutdown()`

## Files

- `ampr_base.py`: low-level DLL wrapper
- `ampr.py`: high-level AMPR API
- `vendor/`: vendor-provided DLL and header

## Platform

- Windows only

The driver depends on `COM-AMPR-12.dll`.

## Process Isolation

On Windows, the high-level `AMPR` client runs the DLL-backed controller in a
dedicated worker process when possible. This keeps a blocked vendor DLL call
from poisoning the main Python process, which is especially important in
notebooks. Advanced injected objects such as an external `logger`, `hk_thread`,
or `thread_lock` fall back to inline mode because they cannot be shared across
process boundaries.

## Notebook

- Manual notebook: [`docs/notebooks/cgc/ampr_wrapper.ipynb`](../../../docs/notebooks/cgc/ampr_wrapper.ipynb)

## Minimal Example

```python
from cgc.ampr import AMPR

ampr = AMPR("ampr_main", com=5)
ampr.initialize()
try:
    print(ampr.get_status())
finally:
    ampr.shutdown()
```
