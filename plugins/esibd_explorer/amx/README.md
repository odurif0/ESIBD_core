# AMX Plugin

Drives AMX frequency and pulser timing from ESIBD Explorer and monitors live
pulser readbacks.

The plugin is self-contained: it embeds the minimal private runtime it needs,
including the AMX driver files and vendor DLL.

## Requirements

- ESIBD Explorer `0.8.x`
- Windows for real hardware communication
- No separate `ESIBD_core` installation is required for the plugin itself

## Activation

1. Open ESIBD Explorer.
2. Set the Explorer `plugin path` to the directory that contains the `amx/`
   folder.

   Example in this repository:
   `/home/durif/Git/ESIBD_core/plugins/esibd_explorer`

3. Restart ESIBD Explorer.
4. Enable the `AMX` plugin in the Plugin Manager.

## Device Configuration

- `COM`: Windows COM port number used by the AMX controller.
- `Baud rate`: serial speed passed to the AMX driver.
- `Connect timeout (s)`: timeout used to establish the transport.
- `Startup timeout (s)`: timeout used for ON/OFF startup and shutdown sequences.
- `Poll timeout (s)`: timeout used for periodic housekeeping reads.
- `Standby config`: optional standby slot loaded on ON. Use `-1` to skip.
- `Operating config`: optional operating slot loaded on ON. Use `-1` to skip.
- `Shutdown config`: optional slot loaded on OFF. Use `-1` to use software shutdown.
- `Frequency (kHz)`: oscillator frequency applied after startup.

The plugin keeps a fixed 4-channel pulser layout matching the AMX hardware.
Each channel exposes:

- duty-cycle setpoint in percent
- pulser delay in ticks
- channel ON/OFF state for whether the pulser is actively applied
- measured duty-cycle monitor
- width and burst readbacks

Switch topology and routing remain managed by the saved AMX controller
configurations. The plugin focuses on the runtime timing adjustments typically
changed between experiments.

## Portability Note

To copy this plugin to another machine, keep the whole `amx/` directory
together, including the embedded `vendor/` subtree.
