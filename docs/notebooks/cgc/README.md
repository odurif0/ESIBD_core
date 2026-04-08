# CGC Notebooks

Manual documentation and hardware-test notebooks for the CGC instrument family.

Files:

- `ampr_wrapper.ipynb`: AMPR simple sequence, then wrapper methods one by one
- `amx_wrapper.ipynb`: AMX simple sequence, then wrapper methods one by one
- `dmmr_wrapper.ipynb`: DMMR simple sequence, then wrapper methods one by one
- `psu_wrapper.ipynb`: PSU simple sequence, then wrapper methods one by one

Notes:

- Run them on Windows with real CGC hardware.
- Each notebook starts with a simple recommended workflow.
- After that, methods are organized into explicit test cells so they can be run one by one.
- Low-level transport primitives such as `open_port()` and `close_port()` are isolated in their own section and should not be mixed with high-level `connect()` / `initialize()` calls on the same object.
