"""High-level helper functions for AMPR startup and shutdown."""

import time


def initialize_ampr(ampr, timeout_s=5.0, poll_s=0.2):
    """Connect an AMPR, validate module scan state, and wait until it is on."""
    if not ampr.connect():
        raise RuntimeError("AMPR connection failed")

    try:
        status, mismatch, rating_failure = ampr.get_scanned_module_state()
        if status != ampr.NO_ERR:
            raise RuntimeError(f"Unable to read scanned module state: {status}")

        if mismatch or rating_failure:
            status = ampr.rescan_modules()
            if status != ampr.NO_ERR:
                raise RuntimeError(f"AMPR rescan failed: {status}")

            status = ampr.set_scanned_module_state()
            if status != ampr.NO_ERR:
                raise RuntimeError(f"AMPR set scanned module state failed: {status}")

        status, _ = ampr.enable_psu(True)
        if status != ampr.NO_ERR:
            raise RuntimeError(f"AMPR enable_psu failed: {status}")

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            status, _, state = ampr.get_state()
            if status == ampr.NO_ERR and state == "ST_ON":
                return
            time.sleep(poll_s)

        raise RuntimeError("AMPR did not reach ST_ON")
    except Exception:
        ampr.disconnect()
        raise


def shutdown_ampr(ampr):
    """Set all detected module channels to 0 V, disable PSU, and disconnect."""
    modules = ampr.scan_modules()

    for module in modules:
        for channel in range(1, 5):
            status = ampr.set_module_voltage(module, channel, 0.0)
            if status != ampr.NO_ERR:
                raise RuntimeError(
                    f"Failed to set AMPR module {module} channel {channel} to 0 V: {status}"
                )

    status, _ = ampr.enable_psu(False)
    if status != ampr.NO_ERR:
        raise RuntimeError(f"AMPR disable_psu failed: {status}")

    if not ampr.disconnect():
        raise RuntimeError("AMPR disconnect failed")
