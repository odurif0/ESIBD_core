"""High-level CGC PSU driver."""

from __future__ import annotations

import logging
import queue
import threading
import time
import weakref
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

    _active_connections_lock = threading.Lock()
    _active_connections: dict[int, dict[str, object]] = {}
    CHANNEL_LABELS = {
        PSUBase.PSU_POS: "positive",
        PSUBase.PSU_NEG: "negative",
    }

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
        self._dll_port_claimed = False
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

    @classmethod
    def _purge_stale_connections(cls):
        stale = []
        for instance_id, entry in cls._active_connections.items():
            instance = entry["ref"]()
            if instance is None or not instance._dll_port_claimed:
                stale.append(instance_id)
        for instance_id in stale:
            cls._active_connections.pop(instance_id, None)

    def _register_connected_instance(self):
        cls = type(self)
        with cls._active_connections_lock:
            cls._purge_stale_connections()
            cls._active_connections[id(self)] = {
                "ref": weakref.ref(self),
                "device_id": self.device_id,
                "com": self.com,
                "port": self.port_num,
            }

    def _unregister_connected_instance(self):
        cls = type(self)
        with cls._active_connections_lock:
            cls._active_connections.pop(id(self), None)
            cls._purge_stale_connections()

    def _set_port_claimed(self, claimed: bool):
        self._dll_port_claimed = bool(claimed)
        if self._dll_port_claimed:
            self._register_connected_instance()
        else:
            self._unregister_connected_instance()

    def _warn_on_other_process_ports(self):
        cls = type(self)
        with cls._active_connections_lock:
            cls._purge_stale_connections()
            others = [
                entry
                for instance_id, entry in cls._active_connections.items()
                if instance_id != id(self)
            ]

        if not others:
            return

        same_port = [entry for entry in others if entry["port"] == self.port_num]
        if same_port:
            other_devices = ", ".join(
                f"{entry['device_id']}@COM{entry['com']}" for entry in same_port
            )
            self.logger.warning(
                "Another PSU instance in this process already claims the same DLL "
                f"port {self.port_num}: {other_devices}. Reusing the same DLL port "
                "can reassign or close the shared vendor channel unexpectedly."
            )
            return

        all_ports = sorted({entry["port"] for entry in others} | {self.port_num})
        other_devices = ", ".join(
            f"{entry['device_id']}@COM{entry['com']}/port{entry['port']}"
            for entry in others
        )
        self.logger.warning(
            "Multiple PSU instances in this process currently claim DLL ports "
            f"{all_ports}: {other_devices}. The vendor DLL exposes independent "
            "ports, but keep one active instance per port in each workflow."
        )

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
        self._set_port_claimed(True)

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
                self._set_port_claimed(True)
                self.logger.info(
                    f"PSU device {self.device_id} is already connected; skipping open_port"
                )
                return True

            self._warn_on_other_process_ports()
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
            self._set_port_claimed(True)
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
            else:
                self._set_port_claimed(False)
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
                self._set_port_claimed(True)
                self.logger.warning(
                    f"Skipping PSU close_port for {self.device_id} because the "
                    "transport is marked unusable."
                )
                return False

            if not was_connected:
                if not self._dll_port_claimed:
                    self._set_port_claimed(False)
                return True

            self.logger.info(f"Disconnecting PSU device {self.device_id}")
            status = self._call_locked(super().close_port)
            if status == self.NO_ERR:
                self._set_port_claimed(False)
                self.logger.info(
                    f"Successfully disconnected PSU device {self.device_id}"
                )
                return True

            self._set_port_claimed(True)
            self.logger.error(
                f"Failed to disconnect PSU device {self.device_id}: "
                f"{self.format_status(status)}"
            )
            return False
        except Exception as exc:
            self._set_port_claimed(True)
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

    def _get_product_info_unlocked(self) -> dict:
        product_no_status, product_no = PSUBase.get_product_no(self)
        self._raise_on_status(product_no_status, "get_product_no")
        product_id_status, product_id = PSUBase.get_product_id(self)
        self._raise_on_status(product_id_status, "get_product_id")
        fw_version_status, fw_version = PSUBase.get_fw_version(self)
        self._raise_on_status(fw_version_status, "get_fw_version")
        fw_date_status, fw_date = PSUBase.get_fw_date(self)
        self._raise_on_status(fw_date_status, "get_fw_date")
        hw_type_status, hw_type = PSUBase.get_hw_type(self)
        self._raise_on_status(hw_type_status, "get_hw_type")
        hw_version_status, hw_version = PSUBase.get_hw_version(self)
        self._raise_on_status(hw_version_status, "get_hw_version")
        return {
            "product_no": product_no,
            "product_id": product_id,
            "firmware": {
                "version": fw_version,
                "date": fw_date,
            },
            "hardware": {
                "type": hw_type,
                "version": hw_version,
            },
        }

    def get_product_info(self) -> dict:
        """Return stable product, firmware and hardware metadata."""
        self._require_connected()
        return self._call_locked(self._get_product_info_unlocked)

    def _collect_housekeeping_unlocked(self) -> dict:
        main_status, main_state_hex, main_state_name = PSUBase.get_main_state(self)
        self._raise_on_status(main_status, "get_main_state")
        device_state_status, device_state_hex, device_state_flags = PSUBase.get_device_state(self)
        self._raise_on_status(device_state_status, "get_device_state")
        device_enabled_status, device_enabled = PSUBase.get_device_enable(self)
        self._raise_on_status(device_enabled_status, "get_device_enable")
        outputs_enabled_status, psu0_enabled, psu1_enabled = PSUBase.get_psu_enable(self)
        self._raise_on_status(outputs_enabled_status, "get_psu_enable")
        housekeeping_status, volt_rect, volt_5v0, volt_3v3, temp_cpu = PSUBase.get_housekeeping(self)
        self._raise_on_status(housekeeping_status, "get_housekeeping")
        sensor_status, temperatures = PSUBase.get_sensor_data(self)
        self._raise_on_status(sensor_status, "get_sensor_data")
        fan_status, fan_enabled, fan_failed, fan_set_rpm, fan_measured_rpm, fan_pwm = PSUBase.get_fan_data(self)
        self._raise_on_status(fan_status, "get_fan_data")
        led_status, led_red, led_green, led_blue = PSUBase.get_led_data(self)
        self._raise_on_status(led_status, "get_led_data")
        cpu_status, cpu_load, cpu_frequency = PSUBase.get_cpu_data(self)
        self._raise_on_status(cpu_status, "get_cpu_data")
        uptime_status, uptime_s, uptime_ms, operation_s = PSUBase.get_uptime(self)
        self._raise_on_status(uptime_status, "get_uptime")
        total_time_status, total_uptime_s, total_operation_s = PSUBase.get_total_time(self)
        self._raise_on_status(total_time_status, "get_total_time")

        output_enabled = (psu0_enabled, psu1_enabled)
        channels = []
        for channel in range(self.PSU_NUM):
            measured_status, measured_voltage, measured_current, dropout_voltage = PSUBase.get_psu_data(self, channel)
            self._raise_on_status(measured_status, f"get_psu_data({channel})")
            voltage_status, set_voltage, limit_voltage = PSUBase.get_psu_set_output_voltage(self, channel)
            self._raise_on_status(voltage_status, f"get_psu_set_output_voltage({channel})")
            current_status, set_current, limit_current = PSUBase.get_psu_set_output_current(self, channel)
            self._raise_on_status(current_status, f"get_psu_set_output_current({channel})")
            adc_status, volt_avdd, volt_dvdd, volt_aldo, volt_dldo, volt_ref, temp_adc = PSUBase.get_adc_housekeeping(self, channel)
            self._raise_on_status(adc_status, f"get_adc_housekeeping({channel})")
            rail_status, volt_24vp, volt_12vp, volt_12vn, rail_ref = PSUBase.get_psu_housekeeping(self, channel)
            self._raise_on_status(rail_status, f"get_psu_housekeeping({channel})")
            channels.append(
                {
                    "channel": channel,
                    "label": self.CHANNEL_LABELS.get(channel, str(channel)),
                    "enabled": output_enabled[channel],
                    "voltage": {
                        "measured_v": measured_voltage,
                        "set_v": set_voltage,
                        "limit_v": limit_voltage,
                    },
                    "current": {
                        "measured_a": measured_current,
                        "set_a": set_current,
                        "limit_a": limit_current,
                    },
                    "dropout_v": dropout_voltage,
                    "adc": {
                        "volt_avdd_v": volt_avdd,
                        "volt_dvdd_v": volt_dvdd,
                        "volt_aldo_v": volt_aldo,
                        "volt_dldo_v": volt_dldo,
                        "volt_ref_v": volt_ref,
                        "temp_adc_c": temp_adc,
                    },
                    "rails": {
                        "volt_24vp_v": volt_24vp,
                        "volt_12vp_v": volt_12vp,
                        "volt_12vn_v": volt_12vn,
                        "volt_ref_v": rail_ref,
                    },
                }
            )

        return {
            "device_enabled": device_enabled,
            "output_enabled": output_enabled,
            "main_state": {
                "hex": main_state_hex,
                "name": main_state_name,
            },
            "device_state": {
                "hex": device_state_hex,
                "flags": device_state_flags,
            },
            "housekeeping": {
                "volt_rect_v": volt_rect,
                "volt_5v0_v": volt_5v0,
                "volt_3v3_v": volt_3v3,
                "temp_cpu_c": temp_cpu,
            },
            "sensors_c": temperatures,
            "fans": [
                {
                    "fan": index,
                    "enabled": fan_enabled[index],
                    "failed": fan_failed[index],
                    "set_rpm": fan_set_rpm[index],
                    "measured_rpm": fan_measured_rpm[index],
                    "pwm": fan_pwm[index],
                }
                for index in range(self.FAN_NUM)
            ],
            "led": {
                "red": led_red,
                "green": led_green,
                "blue": led_blue,
            },
            "cpu": {
                "load": cpu_load,
                "frequency_hz": cpu_frequency,
            },
            "uptime": {
                "seconds": uptime_s,
                "milliseconds": uptime_ms,
                "operation_seconds": operation_s,
                "total_uptime_seconds": total_uptime_s,
                "total_operation_seconds": total_operation_s,
            },
            "channels": channels,
        }

    def collect_housekeeping(self) -> dict:
        """Return a structured runtime snapshot suitable for monitoring."""
        self._require_connected()
        return self._call_locked(self._collect_housekeeping_unlocked)

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
            if not was_connected and (self.connected or self._transport_poisoned):
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
