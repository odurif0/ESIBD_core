"""High-level CGC PSU driver."""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .psu_base import PSUBase


class _DeviceLoggerAdapter(logging.LoggerAdapter):
    """Prefix log messages with the device identifier."""

    def process(self, msg, kwargs):
        return f"{self.extra['device_id']} - {msg}", kwargs


class PSU(PSUBase):
    """
    High-level CGC PSU driver.

    The public API is intentionally config-centric:
    load a known configuration first, then optionally adjust voltages and
    current limits at runtime.
    """

    def __init__(
        self,
        device_id: str,
        com: int,
        port: int = 0,
        baudrate: int = 230400,
        logger: Optional[logging.Logger] = None,
        thread_lock: Optional[threading.Lock] = None,
        dll_path: Optional[str] = None,
        log_dir: Optional[Path] = None,
        **kwargs,
    ):
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected PSU init kwargs: {unexpected}")

        self.device_id = device_id
        self.com = int(com)
        self.port_num = int(port)
        self.baudrate = int(baudrate)
        self.connected = False
        self._transport_poisoned = False
        self._transport_error = None

        self.thread_lock = thread_lock or threading.Lock()

        if logger is not None:
            self.logger = _DeviceLoggerAdapter(logger, {"device_id": device_id})
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            logger_name = f"PSU_{device_id}_{timestamp}"
            self.logger = logging.getLogger(logger_name)
            if not self.logger.handlers:
                root_log_dir = (
                    Path(log_dir)
                    if log_dir is not None
                    else Path(__file__).resolve().parents[3] / "logs"
                )
                root_log_dir.mkdir(parents=True, exist_ok=True)
                log_file = root_log_dir / f"psu_{device_id}_{timestamp}.log"
                handler = logging.FileHandler(log_file)
                formatter = logging.Formatter(
                    f"%(asctime)s - {device_id} - %(levelname)s - %(message)s"
                )
                handler.setFormatter(formatter)
                self.logger.addHandler(handler)
                self.logger.setLevel(logging.INFO)

        super().__init__(com=com, port=port, log=None, idn=device_id, dll_path=dll_path)

    def _raise_if_transport_poisoned(self):
        if self._transport_poisoned:
            detail = self._transport_error or "unknown transport failure"
            raise RuntimeError(
                "PSU transport is unusable after a timed-out DLL call. "
                f"{detail} Recreate the PSU instance before retrying."
            )

    def _poison_transport(self, step_name: str):
        self._transport_poisoned = True
        self._transport_error = (
            f"Timed out during '{step_name}'. "
            "The device may be powered off or unresponsive."
        )
        self.connected = False

    def _call_locked_with_timeout(self, method, timeout_s, step_name, *args, **kwargs):
        self._raise_if_transport_poisoned()
        lock_deadline = time.monotonic() + timeout_s
        while True:
            remaining = lock_deadline - time.monotonic()
            if remaining <= 0:
                self._raise_if_transport_poisoned()
                raise RuntimeError(
                    f"PSU transport lock timed out during '{step_name}'. "
                    "A previous DLL call may still be blocked."
                )
            if self.thread_lock.acquire(timeout=min(0.1, remaining)):
                break
            self._raise_if_transport_poisoned()

        result_queue = queue.Queue(maxsize=1)
        release_lock = True

        def runner():
            try:
                result_queue.put(("result", method(*args, **kwargs)))
            except Exception as exc:  # pragma: no cover - forwarded to caller
                result_queue.put(("error", exc))

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join(timeout_s)

        try:
            if thread.is_alive():
                self._poison_transport(step_name)
                release_lock = False
                raise RuntimeError(
                    f"PSU DLL call timed out during '{step_name}'. "
                    "The device may be powered off or unresponsive. "
                    "The PSU instance is now marked unusable."
                )

            kind, payload = result_queue.get()
            if kind == "error":
                raise payload
            return payload
        finally:
            if release_lock:
                self.thread_lock.release()

    def _call_locked(self, method, *args, **kwargs):
        self._raise_if_transport_poisoned()
        while True:
            if self.thread_lock.acquire(timeout=0.1):
                try:
                    self._raise_if_transport_poisoned()
                    return method(*args, **kwargs)
                finally:
                    self.thread_lock.release()
            self._raise_if_transport_poisoned()

    def _require_connected(self):
        if not self.connected:
            raise RuntimeError("PSU device is not connected.")

    def _raise_on_status(self, status: int, action: str):
        if status != self.NO_ERR:
            raise RuntimeError(f"PSU {action} failed: {self.format_status(status)}")

    def connect(self, timeout_s: float = 5.0) -> bool:
        """Connect to the PSU device."""
        try:
            if self.connected:
                self.logger.info(
                    f"PSU device {self.device_id} is already connected; skipping open_port"
                )
                return True

            self.logger.info(
                f"Connecting to PSU device {self.device_id} "
                f"on COM{self.com}, port {self.port_num}"
            )

            open_port = super().open_port
            set_baud_rate = super().set_baud_rate
            close_port = super().close_port

            status = self._call_locked_with_timeout(
                open_port, timeout_s, "open_port", self.com, self.port_num
            )
            if status != self.NO_ERR:
                raise RuntimeError(
                    f"PSU open_port failed: {self.format_status(status)}"
                )

            self.connected = True
            baud_status, actual_baud = self._call_locked_with_timeout(
                set_baud_rate, timeout_s, "set_baud_rate", self.baudrate
            )
            if baud_status == self.NO_ERR:
                self.logger.info(
                    f"Successfully connected to PSU device {self.device_id} "
                    f"(baud rate: {actual_baud})"
                )
                return True

            self.logger.error(
                f"Failed to set baud rate: {self.format_status(baud_status)}"
            )
            close_status = self._call_locked_with_timeout(
                close_port, timeout_s, "close_port"
            )
            if close_status != self.NO_ERR:
                self.logger.warning(
                    "PSU port rollback after baud-rate failure also failed: "
                    f"{self.format_status(close_status)}"
                )
            self.connected = False
            raise RuntimeError(
                f"PSU set_baud_rate failed: {self.format_status(baud_status)}"
            )
        except Exception:
            self.connected = False
            raise

    def disconnect(self) -> bool:
        """Disconnect from the PSU device."""
        was_connected = self.connected
        self.connected = False

        try:
            if self._transport_poisoned:
                self.logger.warning(
                    f"Skipping PSU close_port for {self.device_id} because the "
                    "transport is marked unusable."
                )
                return False

            if not was_connected:
                return True

            self.logger.info(f"Disconnecting PSU device {self.device_id}")
            status = self._call_locked(super().close_port)
            if status == self.NO_ERR:
                self.logger.info(
                    f"Successfully disconnected PSU device {self.device_id}"
                )
                return True

            self.logger.error(
                f"Failed to disconnect PSU device {self.device_id}: "
                f"{self.format_status(status)}"
            )
            return False
        except Exception as exc:
            self.logger.error(f"Disconnection error: {exc}")
            return False

    def get_status(self) -> dict:
        """Return the current driver status."""
        return {
            "device_id": self.device_id,
            "com": self.com,
            "port": self.port_num,
            "baudrate": self.baudrate,
            "connected": self.connected,
            "transport_poisoned": self._transport_poisoned,
        }

    def list_configs(self, include_empty: bool = False) -> list[dict]:
        """Return PSU configurations with flags and names."""
        self._require_connected()
        status, active_list, valid_list = self._call_locked(PSUBase.get_config_list, self)
        self._raise_on_status(status, "get_config_list")

        configs = []
        for index, (active, valid) in enumerate(zip(active_list, valid_list)):
            if not include_empty and not (active or valid):
                continue
            name_status, name = self._call_locked(PSUBase.get_config_name, self, index)
            self._raise_on_status(name_status, f"get_config_name({index})")
            configs.append(
                {
                    "index": index,
                    "name": name,
                    "active": active,
                    "valid": valid,
                }
            )
        return configs

    def load_user_config(self, config_number: int) -> None:
        """Load one PSU configuration from NVM."""
        self._require_connected()
        self.logger.info(f"Loading PSU config {config_number}")
        status = self._call_locked(PSUBase.load_current_config, self, config_number)
        self._raise_on_status(status, f"load_current_config({config_number})")

    def set_device_enabled(self, enable: bool) -> None:
        """Set the PSU device enable flag."""
        self._require_connected()
        status = self._call_locked(PSUBase.set_device_enable, self, enable)
        self._raise_on_status(status, f"set_device_enable({enable})")

    def get_device_enabled(self) -> bool:
        """Return the PSU device enable flag."""
        self._require_connected()
        status, enabled = self._call_locked(PSUBase.get_device_enable, self)
        self._raise_on_status(status, "get_device_enable")
        return enabled

    def set_output_enabled(self, psu0: bool, psu1: bool) -> None:
        """Set the two PSU channel enable flags."""
        self._require_connected()
        status = self._call_locked(PSUBase.set_psu_enable, self, psu0, psu1)
        self._raise_on_status(status, f"set_psu_enable({psu0}, {psu1})")

    def get_output_enabled(self) -> tuple[bool, bool]:
        """Return the two PSU channel enable flags."""
        self._require_connected()
        status, psu0, psu1 = self._call_locked(PSUBase.get_psu_enable, self)
        self._raise_on_status(status, "get_psu_enable")
        return psu0, psu1

    def set_channel_voltage(self, channel: int, voltage_v: float) -> None:
        """Set one PSU channel output voltage in volts."""
        self._require_connected()
        status = self._call_locked(
            PSUBase.set_psu_output_voltage, self, channel, voltage_v
        )
        self._raise_on_status(status, f"set_psu_output_voltage({channel}, {voltage_v})")

    def get_channel_voltage(self, channel: int) -> float:
        """Return one PSU channel output voltage in volts."""
        self._require_connected()
        status, voltage = self._call_locked(PSUBase.get_psu_output_voltage, self, channel)
        self._raise_on_status(status, f"get_psu_output_voltage({channel})")
        return voltage

    def get_channel_voltage_limits(self, channel: int) -> tuple[float, float]:
        """Return the requested and limit voltages for one channel."""
        self._require_connected()
        status, setpoint, limit = self._call_locked(
            PSUBase.get_psu_set_output_voltage, self, channel
        )
        self._raise_on_status(status, f"get_psu_set_output_voltage({channel})")
        return setpoint, limit

    def set_channel_current(self, channel: int, current_a: float) -> None:
        """Set one PSU channel output current in amperes."""
        self._require_connected()
        status = self._call_locked(
            PSUBase.set_psu_output_current, self, channel, current_a
        )
        self._raise_on_status(status, f"set_psu_output_current({channel}, {current_a})")

    def get_channel_current(self, channel: int) -> float:
        """Return one PSU channel output current in amperes."""
        self._require_connected()
        status, current = self._call_locked(PSUBase.get_psu_output_current, self, channel)
        self._raise_on_status(status, f"get_psu_output_current({channel})")
        return current

    def get_channel_current_limits(self, channel: int) -> tuple[float, float]:
        """Return the requested and limit currents for one channel."""
        self._require_connected()
        status, setpoint, limit = self._call_locked(
            PSUBase.get_psu_set_output_current, self, channel
        )
        self._raise_on_status(status, f"get_psu_set_output_current({channel})")
        return setpoint, limit

    def initialize(
        self,
        config_number: int,
        *,
        timeout_s: float = 5.0,
        enable_device: bool | None = None,
        enable_outputs: tuple[bool, bool] | None = None,
    ) -> None:
        """
        Connect and load a known configuration.

        The configuration is the reproducible starting point. Optional enable
        changes are applied only if explicitly requested.
        """
        was_connected = self.connected
        try:
            self.connect(timeout_s=timeout_s)
            self.load_user_config(config_number)
            if enable_device is not None:
                self.set_device_enabled(enable_device)
            if enable_outputs is not None:
                self.set_output_enabled(*enable_outputs)
        except Exception:
            if was_connected or self.connected or self._transport_poisoned:
                self.disconnect()
            raise

    def shutdown(
        self,
        *,
        standby_config: int | None = None,
        disable_outputs: bool = True,
        disable_device: bool = True,
    ) -> bool:
        """Safely disable outputs/device, optionally load a standby config, then disconnect."""
        disconnect_result = True
        try:
            if self.connected and standby_config is not None:
                self.load_user_config(standby_config)
            if self.connected and disable_outputs:
                self.set_output_enabled(False, False)
            if self.connected and disable_device:
                self.set_device_enabled(False)
        finally:
            disconnect_result = self.disconnect()
        return disconnect_result
