# CGC DMMR

Python driver for the CGC DMMR-8 picoammeter controller.

## What It Does

- Connects to a CGC DMMR-8 unit over a Windows COM port
- Uses the vendor `COM-DMMR-8.dll` through Python
- Scans installed DPA-1F current-measurement modules
- Reads controller and module state
- Reads live current measurements and configuration data
- Provides a safer shutdown path through `shutdown()`

## Files

- `dmmr_base.py`: low-level DLL wrapper
- `dmmr.py`: high-level DMMR API
- `vendor/`: vendor-provided DLL and header

## Platform

- Windows only

The driver depends on `COM-DMMR-8.dll`.

## Process Isolation

On Windows, the high-level `DMMR` client runs the DLL-backed controller in a
dedicated worker process when possible. This keeps a blocked vendor DLL call
from poisoning the main Python process, which is especially important in
notebooks. Advanced injected objects such as an external `logger`, `hk_thread`,
or `thread_lock` fall back to inline mode because they cannot be shared across
process boundaries.

## Notebook

- Manual notebook: [`docs/notebooks/cgc/dmmr_wrapper.ipynb`](../../../docs/notebooks/cgc/dmmr_wrapper.ipynb)

## Minimal Example

```python
from cgc.dmmr import DMMR

dmmr = DMMR("dmmr_main", com=8)
dmmr.connect()
try:
    print(dmmr.scan_modules())
finally:
    dmmr.shutdown()
```
