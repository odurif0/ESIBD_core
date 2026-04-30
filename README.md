# ESIBD Explorer Plugins

Ready-to-use plugin bundle for [ESIBD Explorer](https://github.com/ioneater/ESIBD-Explorer).

One plugin for one device.

## Available Plugins

| Plugin   | Description |
|----------|-------------|
| `ampr_a` | Drives AMPR_A high-voltage channels and monitors output voltages |
| `ampr_b` | Drives AMPR_B high-voltage channels and monitors output voltages |
| `psu`    | Drives the 2 PSU outputs and monitors voltage/current readbacks |
| `dmmr`   | Reads DMMR module currents and monitors live picoammeter values |
| `amx`    | Drives AMX frequency and pulser timing and monitors pulser readbacks |

## Quick Start

1. **Download the latest release** `esibd-explorer-plugins-v0.1.0.zip` from the
   [Releases page](https://github.com/odurif0/esibd-explorer-plugins/releases).

2. **Extract the zip** into your ESIBD Explorer `plugins` folder.
   The extracted directory structure should look like this:

   ```
   <ESIBD Explorer>/plugins/
   ├── ampr_a/
   ├── ampr_b/
   ├── psu/
   ├── dmmr/
   └── amx/
   ```

3. **Enable** the plugins you need in the Plugin Manager. That's it!

## Requirements

- ESIBD Explorer `1.0.1` on Windows


## Running Tests

Tests validate plugin packaging, runtime integrity, and behavior contracts
against the ESIBD Explorer plugin API for development.
