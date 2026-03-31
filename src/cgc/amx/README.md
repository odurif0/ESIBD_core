# CGC AMX

Python driver for the CGC `AMX-CTRL-4ED` power switch unit.

## Design

This driver follows the vendor recommendation:

1. load a known user configuration first
2. keep that configuration as the reproducible operating mode
3. adjust only frequency, duty cycle or delays at runtime

## What It Does

- Connects to a CGC AMX controller over a Windows COM port
- Uses the vendor `COM-HVAMX4ED.dll`
- Lists and loads stored user configurations
- Enables or disables the device
- Adjusts oscillator frequency
- Adjusts pulser duty cycle, width and delay
- Adjusts coarse switch trigger and enable delays

## Files

- `amx_base.py`: low-level DLL wrapper
- `amx.py`: high-level config-centric API
- `vendor/`: vendor-provided DLL and header

## Platform

- Windows only

## Minimal Example

```python
from cgc.amx import AMX

amx = AMX("amx_main", com=8, port=0)
amx.initialize(config_number=40)
try:
    amx.set_frequency_hz(2_000.0)
    amx.set_pulser_duty_cycle(0, 0.5)
finally:
    amx.shutdown()
```
