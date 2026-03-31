# CGC PSU

Python driver for the CGC `PSU-CTRL-2D` unit.

## Design

This driver follows a configuration-first workflow:

1. connect to the device
2. load a known user configuration
3. optionally adjust voltages or current limits

This matches the vendor recommendation for reproducible operation.

## What It Does

- Connects to a CGC PSU controller over a Windows COM port
- Uses the vendor `COM-HVPSU2D.dll`
- Lists and loads stored user configurations
- Enables or disables the device and both PSU outputs
- Sets and reads output voltages and currents

## Files

- `psu_base.py`: low-level DLL wrapper
- `psu.py`: high-level config-centric API
- `vendor/`: vendor-provided DLL and header

## Platform

- Windows only

## Minimal Example

```python
from cgc.psu import PSU

psu = PSU("psu_main", com=6, port=0)
psu.initialize(config_number=19)
try:
    psu.set_channel_voltage(0, 25.0)
    psu.set_channel_current(0, 0.5)
finally:
    psu.shutdown()
```
