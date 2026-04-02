# CGC AMX

Python driver for the CGC `AMX-CTRL-4ED` power switch unit.

## Design

This driver follows the vendor recommendation:

1. connect to the device
2. load a known user configuration first
3. keep that configuration as the reproducible operating mode
4. adjust only frequency, duty cycle or delays at runtime

## Recommended API

For normal application code:

- construct the driver with `AMX(..., com=..., port=...)`
- call `connect()`
- then call `load_config(config_number)`
- use the high-level frequency, pulser and switch helpers
- use `get_product_info()` for stable metadata
- use `collect_housekeeping()` for a structured runtime snapshot
- finish with `shutdown()`

`load_config(config_number)` applies the configuration stored in the controller
NVM. Depending on how that CGC configuration was saved, it may also apply
device enable and active timing or switching settings.

Do not treat `open_port()` as the normal entry point. It is a low-level DLL
primitive exposed by `amx_base.py`. `connect()` remains the safe transport
entry point when you need a manual workflow.

## What It Does

- Connects to a CGC AMX controller over a Windows COM port
- Uses the vendor `COM-HVAMX4ED.dll`
- Lists and loads stored user configurations
- Enables or disables the device
- Adjusts oscillator frequency
- Adjusts pulser duty cycle, width and delay
- Adjusts coarse switch trigger and enable delays
- Exposes `get_product_info()` for product, firmware and hardware metadata
- Exposes `collect_housekeeping()` for structured monitoring data

## Files

- `amx_base.py`: low-level DLL wrapper
- `amx.py`: high-level config-centric API
- `vendor/`: vendor-provided DLL and header

## Platform

- Windows only

## Process Isolation

On Windows, the high-level `AMX` client runs the DLL-backed controller in a
dedicated worker process when possible. This keeps a blocked vendor DLL call
from poisoning the main Python process, which is especially important in
notebooks. Advanced injected objects such as an external `logger` or
`thread_lock` fall back to inline mode because they cannot be shared across
process boundaries.

## Notebook

- Manual notebook: [`docs/notebooks/cgc/amx_wrapper.ipynb`](../../../docs/notebooks/cgc/amx_wrapper.ipynb)

## Minimal Example

```python
from cgc.amx import AMX

amx = AMX("amx_main", com=8, port=0)
amx.connect()
amx.load_config(40)
try:
    amx.set_frequency_hz(2_000.0)
    amx.set_pulser_duty_cycle(0, 0.5)
finally:
    amx.shutdown()
```

## Low-Level Primitives

Low-level communication primitives are implemented in `amx_base.py`. They are
useful for debugging or vendor-level investigations, but they are not the
recommended application API.

Transport:

- `open_port(com_number, port_number=None)`
- `close_port()`
- `set_baud_rate(baud_rate)`
- `purge()`
- `device_purge()`
- `get_buffer_state()`

Device state and housekeeping:

- `get_main_state()`
- `get_device_state()`
- `get_housekeeping()`
- `get_sensor_data()`
- `get_fan_data()`
- `get_led_data()`
- `get_controller_state()`

Enable and timing:

- `get_device_enable()`
- `set_device_enable(enable)`
- `get_oscillator_period()`
- `set_oscillator_period(period)`
- `get_pulser_delay(pulser_no)`
- `set_pulser_delay(pulser_no, delay)`
- `get_pulser_width(pulser_no)`
- `set_pulser_width(pulser_no, width)`
- `get_pulser_burst(pulser_no)`
- `get_switch_trigger_config(switch_no)`
- `get_switch_enable_config(switch_no)`
- `get_switch_trigger_delay(switch_no)`
- `set_switch_trigger_delay(switch_no, rise_delay, fall_delay)`
- `get_switch_enable_delay(switch_no)`
- `set_switch_enable_delay(switch_no, delay)`

Configurations:

- `save_current_config(config_number)`
- `load_current_config(config_number)`
- `get_config_name(config_number)`
- `get_config_flags(config_number)`
- `get_config_list()`

Device information:

- `get_cpu_data()`
- `get_uptime()`
- `get_total_time()`
- `get_hw_type()`
- `get_hw_version()`
- `get_fw_version()`
- `get_fw_date()`
- `get_product_id()`
- `get_product_no()`
