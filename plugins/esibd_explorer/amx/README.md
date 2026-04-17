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
- `Available configs`: live list of config slots reported by the connected AMX.
- `Frequency (kHz)`: oscillator frequency applied after startup.

Runtime timing notes:

- switch trigger `rise` and `fall` delays are coarse AMX controller ticks in
  the range `0..15`
- switch enable delay is also limited to `0..15`
- values outside that hardware range are rejected by the AMX wrapper on purpose

## AMX Configurations

AMX configuration slots are stored in the controller NVM and are specific to
the actual hardware and firmware content of that controller. Do not assume that
an index seen on another AMX, in an old notebook, or in a previous experiment
will exist on the current unit.

Important points:

- `Standby config` and `Operating config` are optional.
- Set them to `-1` when you want the plugin to connect and drive the AMX
  directly from software without loading a saved controller config.
- The plugin `OFF` action always performs a full software shutdown and
  disconnect. There is no config-based shutdown slot anymore.
- Before choosing a config index, query the controller with the AMX wrapper
  notebook or `cgc.amx.AMX.list_configs()`.
- In the Python wrapper, `cgc.amx.AMX.initialize()` can be called without an
  explicit slot number. It will connect first and, when the controller exposes
  a valid config named `Standby`, auto-load that config into runtime memory.
- Do not hard-code slot `40`. On the AMX tested on April 14, 2026, slot `40`
  was not valid and `load_current_config(40)` failed with `-10`.

Example observed on one AMX controller on April 14, 2026:

- `0`: `Standby`
- `9`: `Static:Out0-3=Hi-Z`
- `10`: `Static:Out0-3=Vneg`
- `11`: `Static:Out0-3=Vpos`
- `19`: `Static:DIO0-DIO6=log0`
- `20`: `Static:DIO0-DIO6=log1`
- `21`: `1kHz->DIO0-DIO6`
- `29`: `DIO0->2xSwitchSym,DIO0->DIO1,1kHz->DIO2`
- `30`: `DIO0->Switch0-3,DIO0->DIO1,1kHz->DIO2`
- `39`: `1kHz->SwitchSym+DIO0,Osc->DIO1`
- `44`: `1kHz->SwitchSym(0/90deg)+DIO0/1,Osc->DIO2`
- `49`: `10kHz->SwitchSym+DIO0,Osc->DIO1`
- `59`: `100kHz->SwitchSym+DIO0,Osc->DIO1`
- `69`: `250kHz->SwitchSym+DIO0,Osc->DIO1`
- `74`: `400kHz->SwitchSym+DIO0,Osc->DIO1`
- `79`: `500kHz->SwitchSym+DIO0,Osc->DIO1`
- `89`: `1MHz->SwitchSym+DIO0,Osc->DIO1`
- `99`: `0.84MHz->SwitchSym+DIO0,Osc->DIO1`
- `101`: `1.2MHz->SwitchSym+DIO0,Osc->DIO1`
- `102`: `1.5MHz->SwitchSym+DIO0,Osc->DIO1`
- `103`: `2MHz->SwitchSym+DIO0,Osc->DIO1`
- `104`: `3MHz->SwitchSym+DIO0,Osc->DIO1`
- `105`: `4MHz->SwitchSym+DIO0,Osc->DIO1`
- `106`: `5MHz->SwitchSym+DIO0,Osc->DIO1`
- `109`: `5000x1MHz->SwitchSym+DIO0,Pause->Vneg+DIO1`

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

## Process Backend

The embedded AMX runtime now defaults to the inline controller path. This
avoids the repeated "worker timed out during worker startup" warnings seen on
some Explorer deployments. Process isolation remains available for debugging or
special cases by constructing the AMX runtime with `process_backend=True`.

## Portability Note

To copy this plugin to another machine, keep the whole `amx/` directory
together, including the embedded `vendor/` subtree.
