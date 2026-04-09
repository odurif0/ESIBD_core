# DMMR Plugin For ESIBD Explorer

This plugin exposes the `DMMR` driver as an external `Device` plugin for ESIBD
Explorer.

The plugin is self-contained: it embeds the minimal private runtime it needs,
including the DMMR driver files and vendor DLL.

## Requirements

- ESIBD Explorer `0.8.x`
- Windows for real hardware communication
- No separate `ESIBD_core` installation is required for the plugin itself

## Activation

1. Open ESIBD Explorer.
2. Set the Explorer `plugin path` to the directory that contains the `dmmr/`
   folder.

   Example in this repository:
   `/home/durif/Git/ESIBD_core/plugins/esibd_explorer`

3. Restart ESIBD Explorer.
4. Enable the `DMMR` plugin in the Plugin Manager.

The plugin lazily loads its bundled local `vendor/runtime` package under a
private Python module namespace when communication is initialized. If that
bundled copy is missing, the plugin fails explicitly because the installation
is incomplete.

## Device Configuration

- `COM`: Windows COM port number used by the DMMR controller.
- `Baud rate`: serial speed passed to the DMMR driver.
- `Connect timeout (s)`: timeout used during initialization and shutdown.
- `Poll timeout (s)`: timeout used for periodic state and current reads.

Each real channel must be configured with:

- `Module`: DMMR module address from `0` to `7`

The plugin auto-discovers installed modules, creates one channel per detected
module, reads live current measurements as channel monitors, and exposes a
global ON/OFF control that enables or disables DMMR acquisition.

## Portability Note

To copy this plugin to another machine, keep the whole `dmmr/` directory
together, including the embedded `vendor/` subtree.
