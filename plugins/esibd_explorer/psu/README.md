# PSU Plugin

Runs the PSU in a config-first workflow from ESIBD Explorer and monitors live
voltage/current readbacks.

The plugin is self-contained: it embeds the minimal private runtime it needs,
including the PSU driver files and vendor DLL.

## Requirements

- ESIBD Explorer `0.8.x`
- Windows for real hardware communication
- No separate `ESIBD_core` installation is required for the plugin itself

## Activation

1. Open ESIBD Explorer.
2. Set the Explorer `plugin path` to the directory that contains the `psu/`
   folder.

   Example in this repository:
   `/home/durif/Git/ESIBD_core/plugins/esibd_explorer`

3. Restart ESIBD Explorer.
4. Enable the `PSU` plugin in the Plugin Manager.

## Device Configuration

- `COM`: Windows COM port number used by the PSU controller.
- `Baud rate`: serial speed passed to the PSU driver.
- `Connect timeout (s)`: timeout used to establish the transport.
- `Startup timeout (s)`: timeout used for ON/OFF startup and shutdown sequences.
- `Poll timeout (s)`: timeout used for periodic housekeeping reads.
- `Standby config`: standby slot loaded on ON. Use `-1` to skip.
- `Operating config`: optional operating slot loaded after standby. Use `-1` to skip.
- `Shutdown config`: optional slot loaded on OFF. Use `-1` to use software shutdown.
- `Available configs`: live list of config slots reported by the connected PSU.

Toolbar notes:

- `Available`: list of config slots currently reported by the controller.
- `Standby`: selector for the slot loaded first during `ON`.
- `Operating`: selector for the optional slot loaded after standby.
- `HV outputs`: CH0/CH1 enable readback, not the measured voltage value.
- `Device flags`: low-level PSU state flags reported by the controller.

The plugin keeps a fixed 2-channel layout matching the physical PSU outputs.
It is intentionally not a free-form PSU editor. The normal workflow is:

1. press `ON`
2. the plugin runs `initialize()` with the configured standby/operating slots
3. the UI displays controller readbacks only
4. press `OFF` to run `shutdown()`

Each channel exposes read-only hardware state:

- output ON/OFF readback
- configured voltage setpoint readback
- configured current setpoint readback
- measured voltage monitor
- measured current indicator

If an expert user needs to tweak live setpoints directly, that should be done
outside this plugin with the driver or another dedicated tool.

## Portability Note

To copy this plugin to another machine, keep the whole `psu/` directory
together, including the embedded `vendor/` subtree.
