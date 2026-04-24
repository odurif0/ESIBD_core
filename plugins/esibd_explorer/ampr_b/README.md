# AMPR_B Plugin

Drives AMPR_B high-voltage channels and monitors measured output voltages.

The plugin is self-contained: it embeds the minimal private runtime it needs,
including the AMPR driver files and vendor DLL.

## Requirements

- ESIBD Explorer `1.0.1`
- Windows for real hardware communication
- No separate `ESIBD_core` installation is required for the plugin itself

## Activation

1. Open ESIBD Explorer.
2. Set the Explorer `plugin path` to the directory that contains the `ampr_b/`
   folder.

   Example in this repository:
   `/home/durif/Git/ESIBD_core/plugins/esibd_explorer`

3. Restart ESIBD Explorer.
4. Enable the `AMPR_B` plugin in the Plugin Manager.

The plugin lazily loads its bundled local `vendor/runtime` package under a
private Python module namespace when communication is initialized. If that
bundled copy is missing, the plugin fails explicitly because the installation
is incomplete.

## Device Configuration

- `COM`: Windows COM port number used by the AMPR controller.
- `Baud rate`: serial speed passed to the AMPR driver.
- `Connect timeout (s)`: timeout used during controller connection.

Each real channel must be configured with:

- `Module`: AMPR module address from `0` to `11`
- `CH`: channel number from `1` to `4`

The plugin reads measured voltages as channel monitors and applies channel
setpoints through the AMPR driver.

## Portability Note

To copy this plugin to another machine, keep the whole `ampr_b/` directory
together, including the embedded `vendor/` subtree.
