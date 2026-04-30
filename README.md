# ESIBD Explorer Plugins

Ready-to-use plugin bundle for [ESIBD Explorer](https://github.com/odurif0/esibd-explorer).

## Available Plugins

| Plugin   | Description |
|----------|-------------|
| `ampr_a` | Drives AMPR_A high-voltage channels and monitors output voltages |
| `ampr_b` | Drives AMPR_B high-voltage channels and monitors output voltages |
| `psu`    | Drives the 2 PSU outputs and monitors voltage/current readbacks |
| `dmmr`   | Reads DMMR module currents and monitors live picoammeter values |
| `amx`    | Drives AMX frequency and pulser timing and monitors pulser readbacks |

> **AMPR_A vs AMPR_B**: identical hardware driver, different plugin identity — use
> both when operating two AMPR controllers simultaneously in ESIBD Explorer.

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

3. **Set the `plugin path`** in ESIBD Explorer to point at that `plugins` folder,
   then restart.

4. **Enable** the plugins you need in the Plugin Manager.

## Requirements

- ESIBD Explorer `1.0.1` or later on Windows

## Repository Layout

```
esibd-explorer-plugins/
├── README.md
├── ampr_a/            # AMPR_A plugin (self-contained)
├── ampr_b/            # AMPR_B plugin (self-contained)
├── psu/               # PSU plugin (self-contained)
├── dmmr/              # DMMR plugin (self-contained)
├── amx/               # AMX plugin (self-contained)
└── tests/             # Plugin packaging and behavior tests
```

Each plugin is self-contained with its own `vendor/runtime/` subtree. To copy a
plugin elsewhere, move its entire directory — the embedded runtime must travel
with it.

## Running Tests

```powershell
pytest tests/
```

Tests validate plugin packaging, runtime integrity, and behavior contracts
against the ESIBD Explorer plugin API without requiring a running Explorer
instance.

## Portability

To use a single plugin on another machine, copy its entire directory (including
`vendor/`). The plugin has no dependency on the rest of this repository.
