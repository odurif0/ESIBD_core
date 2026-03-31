"""High-level CGC AMX driver."""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .amx_base import AMXBase


class _DeviceLoggerAdapter(logging.LoggerAdapter):
    """Prefix log messages with the device identifier."""

    def process(self, msg, kwargs):
        return f"{self.extra['device_id']} - {msg}", kwargs


class AMX(AMXBase):
    """
    High-level CGC AMX driver.

    The preferred workflow is:
    1. connect
    2. load a known user configuration
    3. adjust frequency, duty cycle or delays only
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
            raise TypeError(f"Unexpected AMX init kwargs: {unexpected}")

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
            logger_name = f"AMX_{device_id}_{timestamp}"
            self.logger = logging.getLogger(logger_name)
            if not self.logger.handlers:
                root_log_dir = (
                    Path(log_dir)
                    if log_dir is not None
                    else Path(__file__).resolve().parents[3] / "logs"
                )
                root_log_dir.mkdir(parents=True, exist_ok=True)
                log_file = root_log_dir / f"amx_{device_id}_{timestamp}.log"
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
                "AMX transport is unusable after a timed-out DLL call. "
                f"{detail} Recreate the AMX instance before retrying."
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
                    f"AMX transport lock timed out during '{step_name}'. "
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
                    f"AMX DLL call timed out during '{step_name}'. "
                    "The device may be powered off or unresponsive. "
                    "The AMX instance is now marked unusable."
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
            raise RuntimeError("AMX device is not connected.")

    def _raise_on_status(self, status: int, action: str):
        if status != self.NO_ERR:
            raise RuntimeError(f"AMX {action} failed: {self.format_status(status)}")

    def connect(self, timeout_s: float = 5.0) -> bool:
        """Connect to the AMX device."""
        try:
            if self.connected:
                self.logger.info(
                    f"AMX device {self.device_id} is already connected; skipping open_port"
                )
                return True

            self.logger.info(
                f"Connecting to AMX device {self.device_id} "
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
                    f"AMX open_port failed: {self.format_status(status)}"
                )

            self.connected = True
            baud_status, actual_baud = self._call_locked_with_timeout(
                set_baud_rate, timeout_s, "set_baud_rate", self.baudrate
            )
            if baud_status == self.NO_ERR:
                self.logger.info(
                    f"Successfully connected to AMX device {self.device_id} "
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
                    "AMX port rollback after baud-rate failure also failed: "
                    f"{self.format_status(close_status)}"
                )
            self.connected = False
            raise RuntimeError(
                f"AMX set_baud_rate failed: {self.format_status(baud_status)}"
            )
        except Exception:
            self.connected = False
            raise

    def disconnect(self) -> bool:
        """Disconnect from the AMX device."""
        was_connected = self.connected
        self.connected = False

        try:
            if self._transport_poisoned:
                self.logger.warning(
                    f"Skipping AMX close_port for {self.device_id} because the "
                    "transport is marked unusable."
                )
                return False

            if not was_connected:
                return True

            self.logger.info(f"Disconnecting AMX device {self.device_id}")
            status = self._call_locked(super().close_port)
            if status == self.NO_ERR:
                self.logger.info(
                    f"Successfully disconnected AMX device {self.device_id}"
                )
                return True

            self.logger.error(
                f"Failed to disconnect AMX device {self.device_id}: "
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
        """Return AMX configurations with flags and names."""
        self._require_connected()
        status, active_list, valid_list = self._call_locked(AMXBase.get_config_list, self)
        self._raise_on_status(status, "get_config_list")

        configs = []
        for index, (active, valid) in enumerate(zip(active_list, valid_list)):
            if not include_empty and not (active or valid):
                continue
            name_status, name = self._call_locked(AMXBase.get_config_name, self, index)
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
        """Load one AMX configuration from NVM."""
        self._require_connected()
        self.logger.info(f"Loading AMX config {config_number}")
        status = self._call_locked(AMXBase.load_current_config, self, config_number)
        self._raise_on_status(status, f"load_current_config({config_number})")

    def set_device_enabled(self, enable: bool) -> None:
        """Set the AMX device enable flag."""
        self._require_connected()
        status = self._call_locked(AMXBase.set_device_enable, self, enable)
        self._raise_on_status(status, f"set_device_enable({enable})")

    def get_device_enabled(self) -> bool:
        """Return the AMX device enable flag."""
        self._require_connected()
        status, enabled = self._call_locked(AMXBase.get_device_enable, self)
        self._raise_on_status(status, "get_device_enable")
        return enabled

    def get_frequency_hz(self) -> float:
        """Return the oscillator frequency in hertz."""
        self._require_connected()
        status, period = self._call_locked(AMXBase.get_oscillator_period, self)
        self._raise_on_status(status, "get_oscillator_period")
        return self.CLOCK / (period + self.OSC_OFFSET)

    def set_frequency_hz(self, frequency_hz: float) -> None:
        """Set the oscillator frequency in hertz."""
        if frequency_hz <= 0:
            raise ValueError("frequency_hz must be > 0")
        period = round((self.CLOCK / float(frequency_hz)) - self.OSC_OFFSET)
        if period < 1 or period > 0xFFFFFFFF:
            raise ValueError(
                f"frequency_hz={frequency_hz} results in an invalid "
                f"oscillator period register: {period}"
            )
        self._require_connected()
        status = self._call_locked(AMXBase.set_oscillator_period, self, period)
        self._raise_on_status(status, f"set_oscillator_period({period})")

    def set_frequency_khz(self, frequency_khz: float) -> None:
        """Set the oscillator frequency in kilohertz."""
        self.set_frequency_hz(float(frequency_khz) * 1000.0)

    def get_pulser_delay_ticks(self, pulser_no: int) -> int:
        """Return one pulser delay register."""
        self._require_connected()
        status, delay = self._call_locked(AMXBase.get_pulser_delay, self, pulser_no)
        self._raise_on_status(status, f"get_pulser_delay({pulser_no})")
        return delay

    def set_pulser_delay_ticks(self, pulser_no: int, delay: int) -> None:
        """Set one pulser delay register."""
        if int(delay) < 0:
            raise ValueError("delay must be >= 0")
        self._require_connected()
        status = self._call_locked(AMXBase.set_pulser_delay, self, pulser_no, delay)
        self._raise_on_status(status, f"set_pulser_delay({pulser_no}, {delay})")

    def get_pulser_delay_seconds(self, pulser_no: int) -> float:
        """Return one pulser delay in seconds."""
        delay = self.get_pulser_delay_ticks(pulser_no)
        return (delay + self.PULSER_DELAY_OFFSET) / self.CLOCK

    def set_pulser_width_ticks(self, pulser_no: int, width: int) -> None:
        """Set one pulser width register."""
        if int(width) < 0:
            raise ValueError("width must be >= 0")
        self._require_connected()
        status = self._call_locked(AMXBase.set_pulser_width, self, pulser_no, width)
        self._raise_on_status(status, f"set_pulser_width({pulser_no}, {width})")

    def get_pulser_width_ticks(self, pulser_no: int) -> int:
        """Return one pulser width register."""
        self._require_connected()
        status, width = self._call_locked(AMXBase.get_pulser_width, self, pulser_no)
        self._raise_on_status(status, f"get_pulser_width({pulser_no})")
        return width

    def get_pulser_width_seconds(self, pulser_no: int) -> float:
        """Return one pulser width in seconds."""
        width = self.get_pulser_width_ticks(pulser_no)
        return (width + self.PULSER_WIDTH_OFFSET) / self.CLOCK

    def set_pulser_duty_cycle(self, pulser_no: int, duty_cycle: float) -> None:
        """Set one pulser duty cycle using the current oscillator period."""
        if not 0 < duty_cycle <= 1:
            raise ValueError("duty_cycle must satisfy 0 < duty_cycle <= 1")

        self._require_connected()
        status, period = self._call_locked(AMXBase.get_oscillator_period, self)
        self._raise_on_status(status, "get_oscillator_period")

        total_ticks = period + self.OSC_OFFSET
        width_register = round(total_ticks * float(duty_cycle) - self.PULSER_WIDTH_OFFSET)
        if width_register < 0:
            raise ValueError(
                f"duty_cycle={duty_cycle} produces an invalid width register: "
                f"{width_register}"
            )

        status = self._call_locked(
            AMXBase.set_pulser_width, self, pulser_no, width_register
        )
        self._raise_on_status(
            status, f"set_pulser_width({pulser_no}, {width_register})"
        )

    def set_switch_trigger_delay(
        self, switch_no: int, rise_delay: int, fall_delay: int
    ) -> None:
        """Set one switch coarse trigger rise/fall delays."""
        self._require_connected()
        status = self._call_locked(
            AMXBase.set_switch_trigger_delay,
            self,
            switch_no,
            rise_delay,
            fall_delay,
        )
        self._raise_on_status(
            status,
            f"set_switch_trigger_delay({switch_no}, {rise_delay}, {fall_delay})",
        )

    def get_switch_trigger_delay(self, switch_no: int) -> tuple[int, int]:
        """Return one switch coarse trigger rise/fall delays."""
        self._require_connected()
        status, rise_delay, fall_delay = self._call_locked(
            AMXBase.get_switch_trigger_delay, self, switch_no
        )
        self._raise_on_status(status, f"get_switch_trigger_delay({switch_no})")
        return rise_delay, fall_delay

    def set_switch_enable_delay(self, switch_no: int, delay: int) -> None:
        """Set one switch coarse enable delay."""
        self._require_connected()
        status = self._call_locked(
            AMXBase.set_switch_enable_delay, self, switch_no, delay
        )
        self._raise_on_status(
            status, f"set_switch_enable_delay({switch_no}, {delay})"
        )

    def get_switch_enable_delay(self, switch_no: int) -> int:
        """Return one switch coarse enable delay."""
        self._require_connected()
        status, delay = self._call_locked(
            AMXBase.get_switch_enable_delay, self, switch_no
        )
        self._raise_on_status(status, f"get_switch_enable_delay({switch_no})")
        return delay

    def initialize(
        self,
        config_number: int,
        *,
        timeout_s: float = 5.0,
        enable_device: bool | None = None,
    ) -> None:
        """Connect and load a known AMX configuration."""
        was_connected = self.connected
        try:
            self.connect(timeout_s=timeout_s)
            self.load_user_config(config_number)
            if enable_device is not None:
                self.set_device_enabled(enable_device)
        except Exception:
            if was_connected or self.connected or self._transport_poisoned:
                self.disconnect()
            raise

    def shutdown(
        self,
        *,
        standby_config: int | None = None,
        disable_device: bool = True,
    ) -> bool:
        """Safely disable the device, optionally load a standby config, then disconnect."""
        disconnect_result = True
        try:
            if self.connected and standby_config is not None:
                self.load_user_config(standby_config)
            if self.connected and disable_device:
                self.set_device_enabled(False)
        finally:
            disconnect_result = self.disconnect()
        return disconnect_result
