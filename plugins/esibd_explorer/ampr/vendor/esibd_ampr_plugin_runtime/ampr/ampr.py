"""
AMPR (Amplifier) device controller.

This module provides the AMPR class for communicating with CGC AMPR-12 amplifier
devices via the AMPR base hardware interface with added logging functionality.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

from .._driver_common import (
    ProcessIsolatedClientMixin,
    TimeoutSafeDllMixin,
    build_device_logger,
)
from .ampr_base import AMPRBase

class _AMPRController(TimeoutSafeDllMixin, AMPRBase):
    """
    AMPR device communication class with logging functionality.

    This class inherits from AMPRBase and provides logging capabilities,
    device identification, housekeeping thread management, and enhanced
    function call monitoring similar to other devices in the system.

    The AMPR-12 is an amplifier device that can manage up to 12 modules,
    where each module can hold up to 4 individual voltage supplies.

    Example:
        ampr = AMPR("main_ampr", com=5)
        ampr.connect()
        ampr.enable_psu(True)
        voltage_state = ampr.get_voltage_state()
        ampr.disconnect()

    Recommended high-level flow:
        ampr = AMPR("main_ampr", com=5)
        ampr.initialize()
        ampr.set_module_voltage(0, 1, 50.0)
        ampr.shutdown()
    """

    _INSTRUMENT_NAME = "AMPR"

    def __init__(
        self,
        device_id: str,
        com: int,
        baudrate: int = 230400,
        logger: Optional[logging.Logger] = None,
        hk_thread: Optional[threading.Thread] = None,
        thread_lock: Optional[threading.Lock] = None,
        hk_interval_s: float = 5.0,
        dll_path: Optional[str] = None,
        log_dir: Optional[Path] = None,
        **kwargs,
    ):
        """
        Initialize AMPR device with logging and threading support.
        """
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected AMPR init kwargs: {unexpected}")

        self._validate_init_args(
            device_id=device_id,
            com=com,
            baudrate=baudrate,
            hk_interval_s=hk_interval_s,
        )

        # Store parameters for AMPR functionality
        self.device_id = device_id
        self.com = com
        self.baudrate = baudrate
        self.hk_interval_s = hk_interval_s
        
        # Connection status
        self.connected = False
        self._transport_poisoned = False
        self._transport_error = None
        
        # Housekeeping setup
        self.hk_running = False
        self.hk_stop_event = threading.Event()
        
        # Determine if using external or internal thread management
        self.external_thread = hk_thread is not None
        self.external_lock = thread_lock is not None
        
        # Setup thread lock (for communication)
        if thread_lock is not None:
            self.thread_lock = thread_lock
        else:
            self.thread_lock = threading.Lock()

        # Setup housekeeping lock (separate from communication lock)
        self.hk_lock = threading.Lock()

        # Setup housekeeping thread
        if hk_thread is not None:
            self.hk_thread = hk_thread
            # For external threads, we don't manage the thread lifecycle
        else:
            self.hk_thread = threading.Thread(
                target=self._hk_worker, name=f"HK_{device_id}", daemon=True
            )

        self.logger = build_device_logger(
            instrument_name=self._INSTRUMENT_NAME,
            device_id=device_id,
            logger=logger,
            log_dir=log_dir,
            source_file=__file__,
        )

        super().__init__(com=com, log=None, idn=device_id, dll_path=dll_path)

    @staticmethod
    def _validate_init_args(device_id, com, baudrate, hk_interval_s):
        """Validate public constructor arguments before touching the DLL."""
        if not isinstance(device_id, str):
            raise TypeError("AMPR device_id must be a string.")
        if not device_id.strip():
            raise ValueError("AMPR device_id must be a non-empty string.")

        if isinstance(com, bool) or not isinstance(com, int):
            raise TypeError("AMPR com must be an integer between 1 and 255.")
        if not 1 <= com <= 255:
            raise ValueError("AMPR com must be between 1 and 255.")

        if isinstance(baudrate, bool) or not isinstance(baudrate, int):
            raise TypeError("AMPR baudrate must be a positive integer.")
        if baudrate <= 0:
            raise ValueError("AMPR baudrate must be a positive integer.")

        if isinstance(hk_interval_s, bool) or not isinstance(hk_interval_s, (int, float)):
            raise TypeError("AMPR hk_interval_s must be a positive number.")
        if hk_interval_s <= 0:
            raise ValueError("AMPR hk_interval_s must be greater than 0.")

    def _rollback_connect_failure(self, close_port, timeout_s, reason):
        """Close the vendor channel after a failed connect sequence."""
        try:
            close_status = self._call_locked_with_timeout(
                close_port, timeout_s, "close_port"
            )
        except Exception as exc:
            self.logger.warning(
                f"AMPR port rollback after {reason} also failed: {exc}"
            )
            return

        if close_status != self.NO_ERR:
            self.logger.warning(
                f"AMPR port rollback after {reason} also failed: "
                f"{self.format_status(close_status)}"
            )

    def _verify_device_type(self, timeout_s: float) -> int:
        """Confirm that the connected controller reports the AMPR device type."""
        status, device_type = self._call_locked_with_timeout(
            super().get_device_type, timeout_s, "get_device_type"
        )
        if status != self.NO_ERR:
            raise RuntimeError(
                f"AMPR get_device_type failed: {self.format_status(status)}"
            )
        if device_type != self.DEVICE_TYPE:
            raise RuntimeError(
                "AMPR device type mismatch: "
                f"expected 0x{self.DEVICE_TYPE:04X}, got 0x{device_type:04X}."
            )
        return device_type

    def connect(self, timeout_s: float = 5.0) -> bool:
        """Connect to the AMPR device."""
        close_port = None
        try:
            if self.connected:
                self.logger.info(
                    f"AMPR device {self.device_id} is already connected; skipping open_port"
                )
                return True

            self.logger.info(f"Connecting to AMPR device {self.device_id} on COM{self.com}")

            open_port = super().open_port
            set_baud_rate = super().set_baud_rate
            close_port = super().close_port

            status = self._call_locked_with_timeout(
                open_port, timeout_s, "open_port", self.com
            )

            if status == self.NO_ERR:
                self.connected = True

                baud_status, actual_baud = self._call_locked_with_timeout(
                    set_baud_rate, timeout_s, "set_baud_rate", self.baudrate
                )
                if baud_status == self.NO_ERR:
                    device_type = self._verify_device_type(timeout_s)
                    self.logger.info(
                        f"Successfully connected to AMPR device {self.device_id} "
                        f"(baud rate: {actual_baud}, device_type: 0x{device_type:04X})"
                    )
                    return True

                self.logger.error(
                    f"Failed to set baud rate: {self.format_status(baud_status)}"
                )
                self._rollback_connect_failure(
                    close_port, timeout_s, "baud-rate failure"
                )
                self.connected = False
                raise RuntimeError(
                    f"AMPR set_baud_rate failed: {self.format_status(baud_status)}"
                )

            self.logger.error(
                "Failed to connect to AMPR device "
                f"{self.device_id}: {self.format_status(status)}"
            )
            self.connected = False
            raise RuntimeError(
                f"AMPR open_port failed: {self.format_status(status)}"
            )
                
        except Exception as e:
            if close_port is not None and self.connected and not self._transport_poisoned:
                self._rollback_connect_failure(
                    close_port, timeout_s, "connect verification failure"
                )
            self.logger.error(f"Connection error: {e}")
            self.connected = False
            raise

    def disconnect(self) -> bool:
        """Disconnect from the AMPR device."""
        try:
            self.stop_housekeeping()
            
            self.logger.info(f"Disconnecting AMPR device {self.device_id}")

            if self._transport_poisoned:
                self.connected = False
                self.logger.error(
                    "Skipping AMPR close_port because the transport is unusable "
                    "after a timed-out DLL call. Recreate the AMPR instance."
                )
                return False
            
            status = self._call_locked(super().close_port)
            
            if status == self.NO_ERR:
                self.connected = False
                self.logger.info(f"Successfully disconnected AMPR device {self.device_id}")
                return True

            self.connected = False
            self.logger.error(
                "Failed to disconnect AMPR device "
                f"{self.device_id}: {self.format_status(status)}. "
                "Object marked disconnected locally to avoid further unsafe reuse."
            )
            return False
                
        except Exception as e:
            self.connected = False
            self.logger.error(f"Disconnection error: {e}")
            return False

    def initialize(self, timeout_s: float = 5.0, poll_s: float = 0.2) -> None:
        """Run the recommended AMPR startup sequence."""
        get_scanned_module_state = super().get_scanned_module_state
        rescan_modules = super().rescan_modules
        set_scanned_module_state = super().set_scanned_module_state
        enable_psu = super().enable_psu
        get_state = super().get_state
        psu_enabled = False
        was_connected = self.connected

        try:
            if self.connected:
                status, _, state = self._call_locked_with_timeout(
                    get_state, timeout_s, "get_state_before_initialize"
                )
                if status == self.NO_ERR and state == "ST_ON":
                    self.logger.info(
                        f"AMPR device {self.device_id} is already initialized (ST_ON); "
                        "skipping startup sequence"
                    )
                    return
            else:
                self.connect(timeout_s=timeout_s)

            status, mismatch, rating_failure = self._call_locked_with_timeout(
                get_scanned_module_state, timeout_s, "get_scanned_module_state"
            )
            if status != self.NO_ERR:
                raise RuntimeError(
                    f"Unable to read scanned module state: {self.format_status(status)}"
                )

            if mismatch or rating_failure:
                status = self._call_locked_with_timeout(
                    rescan_modules, timeout_s, "rescan_modules"
                )
                if status != self.NO_ERR:
                    raise RuntimeError(
                        f"AMPR rescan failed: {self.format_status(status)}"
                    )

                status = self._call_locked_with_timeout(
                    set_scanned_module_state, timeout_s, "set_scanned_module_state"
                )
                if status != self.NO_ERR:
                    raise RuntimeError(
                        "AMPR set scanned module state failed: "
                        f"{self.format_status(status)}"
                    )

            status, enabled = self._call_locked_with_timeout(
                enable_psu, timeout_s, "enable_psu", True
            )
            if status != self.NO_ERR:
                raise RuntimeError(
                    f"AMPR enable_psu failed: {self.format_status(status)}"
                )
            psu_enabled = bool(enabled)

            deadline = time.time() + timeout_s
            while time.time() < deadline:
                status, _, state = self._call_locked_with_timeout(
                    get_state, timeout_s, "get_state"
                )
                if status == self.NO_ERR and state == "ST_ON":
                    return
                time.sleep(poll_s)

            raise RuntimeError("AMPR did not reach ST_ON")
        except Exception:
            if psu_enabled:
                if self._transport_poisoned:
                    self.logger.critical(
                        "AMPR initialization failed after enabling the PSU, and the "
                        "transport is now unusable. Manually verify that the hardware "
                        "is in a safe state before continuing."
                    )
                else:
                    try:
                        disable_status, _ = self._call_locked_with_timeout(
                            enable_psu,
                            timeout_s,
                            "disable_psu_after_initialize_failure",
                            False,
                        )
                        if disable_status != self.NO_ERR:
                            self.logger.error(
                                "AMPR cleanup failed to disable PSU after initialization "
                                f"error: {self.format_status(disable_status)}"
                            )
                    except Exception as cleanup_error:
                        self.logger.error(
                            "AMPR cleanup failed while disabling PSU after initialization "
                            f"error: {cleanup_error}"
                        )
            if was_connected or self.connected or self._transport_poisoned:
                self.disconnect()
            raise

    def shutdown(self) -> None:
        """Run the recommended AMPR shutdown sequence."""
        modules = self.scan_modules()

        for module in modules:
            for channel in range(1, self.CHANNEL_NUM + 1):
                status = self.set_module_voltage(module, channel, 0.0)
                if status != self.NO_ERR:
                    raise RuntimeError(
                        "Failed to set AMPR module "
                        f"{module} channel {channel} to 0 V: {self.format_status(status)}"
                    )

        status, _ = self.enable_psu(False)
        if status != self.NO_ERR:
            raise RuntimeError(
                f"AMPR disable_psu failed: {self.format_status(status)}"
            )

        if not self.disconnect():
            raise RuntimeError("AMPR disconnect failed")

    def _hk_worker(self):
        """
        Internal housekeeping worker thread function.
        Runs continuously until stop_event is set.
        """
        self.logger.info(f"Housekeeping worker started for {self.device_id}")
        
        while not self.hk_stop_event.is_set() and self.hk_running:
            try:
                if self.connected:
                    self.hk_monitor()
                    # Wait for interval or stop event
                    self.hk_stop_event.wait(timeout=self.hk_interval_s)
                else:
                    # If not connected, wait a short time before checking again
                    self.hk_stop_event.wait(timeout=1.0)

            except Exception as e:
                self.logger.error(f"Housekeeping worker error: {e}")
                self.hk_stop_event.wait(timeout=1.0)  # Wait before retrying

        self.logger.info(f"Housekeeping worker stopped for {self.device_id}")

    # Individual housekeeping functions with structured logging
    
    def _hk_product_info(self):
        """Get and log product information."""
        status, product_no = AMPRBase.get_product_no(self)
        if status == self.NO_ERR:
            self.logger.info(f"Product number: {product_no}")
        return status == self.NO_ERR

    def _hk_main_state(self):
        """Get and log main device state."""
        status, state_hex, state_name = AMPRBase.get_state(self)
        if status == self.NO_ERR:
            self.logger.info(f"Main state: {state_name} ({state_hex})")
        return status == self.NO_ERR

    def _hk_device_state(self):
        """Get and log device state."""
        status, state_hex, state_names = AMPRBase.get_device_state(self)
        if status == self.NO_ERR:
            self.logger.info(f"Device state: {', '.join(state_names)} ({state_hex})")
        return status == self.NO_ERR

    def _hk_general_housekeeping(self):
        """Get and log general housekeeping data."""
        status, volt_12v, volt_5v0, volt_3v3, volt_agnd, volt_12vp, volt_12vn, \
        volt_hvp, volt_hvn, temp_cpu, temp_adc, temp_av, temp_hvp, temp_hvn, line_freq = AMPRBase.get_housekeeping(self)
        
        if status == self.NO_ERR:
            self.logger.info("get_housekeeping() results:")
            self.logger.info(f"  12V Supply: {volt_12v:.2f}V")
            self.logger.info(f"  5V Supply: {volt_5v0:.2f}V")
            self.logger.info(f"  3.3V Supply: {volt_3v3:.2f}V")
            self.logger.info(f"  AGND Voltage: {volt_agnd:.2f}V")
            self.logger.info(f"  +12Va Supply: {volt_12vp:.2f}V")
            self.logger.info(f"  -12Va Supply: {volt_12vn:.2f}V")
            self.logger.info(f"  +HV Supply: {volt_hvp:.2f}V")
            self.logger.info(f"  -HV Supply: {volt_hvn:.2f}V")
            self.logger.info(f"  CPU Temperature: {temp_cpu:.1f}degC")
            self.logger.info(f"  ADC Temperature: {temp_adc:.1f}degC")
            self.logger.info(f"  AV Temperature: {temp_av:.1f}degC")
            self.logger.info(f"  +HV Temperature: {temp_hvp:.1f}degC")
            self.logger.info(f"  -HV Temperature: {temp_hvn:.1f}degC")
            self.logger.info(f"  Line Frequency: {line_freq:.1f}Hz")
        return status == self.NO_ERR

    def _hk_voltage_state(self):
        """Get and log voltage state."""
        status, state_hex, state_names = AMPRBase.get_voltage_state(self)
        if status == self.NO_ERR:
            self.logger.info(f"Voltage state: {', '.join(state_names)} ({state_hex})")
        return status == self.NO_ERR

    def _hk_temperature_state(self):
        """Get and log temperature state."""
        status, state_hex, state_names = AMPRBase.get_temperature_state(self)
        if status == self.NO_ERR:
            self.logger.info(f"Temperature state: {', '.join(state_names)} ({state_hex})")
        return status == self.NO_ERR

    def _hk_interlock_state(self):
        """Get and log interlock state."""
        status, state_hex, state_names = AMPRBase.get_interlock_state(self)
        if status == self.NO_ERR:
            self.logger.info(f"Interlock state: {', '.join(state_names)} ({state_hex})")
        return status == self.NO_ERR

    def _hk_fan_data(self):
        """Get and log fan data."""
        status, failed, max_rpm, set_rpm, measured_rpm, pwm = AMPRBase.get_fan_data(self)
        if status == self.NO_ERR:
            self.logger.info("get_fan_data() results:")
            self.logger.info(f"  Failed: {failed}")
            self.logger.info(f"  Max RPM: {max_rpm}")
            self.logger.info(f"  Set RPM: {set_rpm}")
            self.logger.info(f"  Measured RPM: {measured_rpm}")
            self.logger.info(f"  PWM: {pwm} ({pwm/100:.1f}%)")
        return status == self.NO_ERR

    def _hk_led_data(self):
        """Get and log LED data."""
        status, red, green, blue = AMPRBase.get_led_data(self)
        if status == self.NO_ERR:
            self.logger.info(f"LED state: R={red}, G={green}, B={blue}")
        return status == self.NO_ERR

    def _hk_cpu_data(self):
        """Get and log CPU data."""
        status, load, frequency = AMPRBase.get_cpu_data(self)
        if status == self.NO_ERR:
            self.logger.info(f"CPU: Load={load*100:.1f}%, Frequency={frequency/1e6:.1f}MHz")
        return status == self.NO_ERR

    def _hk_module_presence(self):
        """Get and log module presence."""
        status, valid, max_module, presence_list = AMPRBase.get_module_presence(self)
        if status == self.NO_ERR:
            present_modules = [
                i
                for i, present in enumerate(presence_list[: self.MODULE_NUM])
                if present == self.MODULE_PRESENT
            ]
            self.logger.info(f"Modules present: {present_modules} (Max: {max_module}, Valid: {valid})")
        return status == self.NO_ERR

    def hk_monitor(self):
        """
        Perform housekeeping monitoring of AMPR device data.
        This method executes all individual housekeeping functions.
        """
        try:
            # Housekeeping holds the transport lock for the whole batch, so it must
            # call the low-level AMPRBase methods directly and avoid the public
            # wrappers that would try to reacquire the same lock.
            with self.thread_lock:
                self._hk_product_info()
                self._hk_main_state()
                self._hk_device_state()
                self._hk_general_housekeeping()
                self._hk_voltage_state()
                self._hk_temperature_state()
                self._hk_interlock_state()
                self._hk_fan_data()
                self._hk_led_data()
                self._hk_cpu_data()
                self._hk_module_presence()
                
        except Exception as e:
            self.logger.error(f"Housekeeping monitoring failed: {e}")

    # =============================================================================
    #     Housekeeping and Threading Methods
    # =============================================================================

    def start_housekeeping(self, interval_s: Optional[float] = None) -> bool:
        """
        Start housekeeping monitoring. Works automatically in both internal and external thread modes.

        - Internal mode (no thread passed to __init__): Creates and manages its own thread
        - External mode (thread passed to __init__): Enables monitoring for external thread control

        Args:
            interval_s (float | None): Monitoring interval in seconds
                (default: uses hk_interval_s from __init__)

        Returns:
            bool: True if started successfully, False otherwise
        """
        if not self.connected:
            self.logger.warning("Cannot start housekeeping: device not connected")
            return False

        if interval_s is not None:
            if isinstance(interval_s, bool) or not isinstance(interval_s, (int, float)):
                raise TypeError("AMPR interval_s must be a positive number.")
            if interval_s <= 0:
                raise ValueError("AMPR interval_s must be greater than 0.")

        with self.hk_lock:
            if self.hk_running:
                self.logger.warning("Housekeeping already running")
                return True

            try:
                # Set the monitoring interval
                if interval_s is not None:
                    self.hk_interval_s = interval_s

                # Clear stop event
                self.hk_stop_event.clear()
                self.hk_running = True

                if self.external_thread:
                    # External thread mode - just enable monitoring
                    self.logger.info("Housekeeping enabled for external thread control")
                else:
                    # Internal thread mode - start our own thread
                    if not self.hk_thread.is_alive():
                        # Create new thread if the old one has finished
                        self.hk_thread = threading.Thread(
                            target=self._hk_worker, name=f"HK_{self.device_id}", daemon=True
                        )
                    self.hk_thread.start()
                    self.logger.info(
                        f"Housekeeping thread started with {self.hk_interval_s}s interval"
                    )

                return True

            except Exception as e:
                self.logger.error(f"Failed to start housekeeping: {e}")
                self.hk_running = False
                return False

    def stop_housekeeping(self) -> bool:
        """
        Stop housekeeping monitoring. Works in both internal and external modes.

        Returns:
            bool: True if stopped successfully, False otherwise
        """
        if not self.hk_running:
            return True

        with self.hk_lock:
            try:
                self.hk_running = False
                self.hk_stop_event.set()

                if not self.external_thread and self.hk_thread.is_alive():
                    # Internal thread mode - wait for thread to finish
                    self.hk_thread.join(timeout=2.0)
                    if self.hk_thread.is_alive():
                        self.logger.warning("Housekeeping thread did not stop cleanly")
                    else:
                        self.logger.info("Housekeeping thread stopped")
                else:
                    # External thread mode
                    self.logger.info("Housekeeping monitoring disabled")

                return True

            except Exception as e:
                self.logger.error(f"Failed to stop housekeeping: {e}")
                return False

    def do_housekeeping_cycle(self) -> bool:
        """
        Perform one housekeeping cycle. Use this in external threads.

        This is the main method for external thread control - call it periodically
        in your external thread loop.

        Returns:
            bool: True if cycle completed successfully, False otherwise
        """
        if not self.hk_running:
            return False

        try:
            if self.connected:
                self.hk_monitor()
                return True
            else:
                self.logger.warning("Housekeeping cycle skipped: device not connected")
                return False

        except Exception as e:
            self.logger.error(f"Housekeeping cycle error: {e}")
            return False

    def get_status(self) -> dict:
        """
        Get current AMPR device status.

        Returns:
            Dict: Dictionary containing device status information
        """
        return {
            "device_id": self.device_id,
            "com": self.com,
            "baudrate": self.baudrate,
            "connected": self.connected,
            "transport_poisoned": self._transport_poisoned,
            "hk_running": self.hk_running,
            "hk_interval_s": self.hk_interval_s,
            "external_thread": self.external_thread,
            "external_lock": self.external_lock,
        }

    # Override key methods with logging
    
    def enable_psu(self, enable):
        """Enable/disable PSUs with logging."""
        self.logger.info(f"Setting PSU enable to {enable}")
        try:
            status, enable_value = self._call_locked(super().enable_psu, enable)
            if status == self.NO_ERR:
                self.logger.info(f"PSU enable set to {enable_value}")
            else:
                self.logger.error(
                    f"Failed to set PSU enable: {self.format_status(status)}"
                )
            return status, enable_value
        except Exception as e:
            self.logger.error(f"Error setting PSU enable: {e}")
            raise

    def get_state(self):
        """Get main state with logging."""
        status, state_hex, state_name = self._call_locked(super().get_state)
        if status == self.NO_ERR:
            self.logger.info(f"Main state: {state_name} ({state_hex})")
        else:
            self.logger.error(
                f"Failed to get main state: {self.format_status(status)}"
            )
        return status, state_hex, state_name

    def restart(self):
        """Restart device with logging."""
        self.logger.info("Restarting AMPR device")
        try:
            status = self._call_locked(super().restart)
            if status == self.NO_ERR:
                self.logger.info("Device restart successful")
            else:
                self.logger.error(
                    f"Device restart failed: {self.format_status(status)}"
                )
            return status
        except Exception as e:
            self.logger.error(f"Error restarting device: {e}")
            raise

    def get_scanned_module_state(self):
        """Get the scanned module state through the shared DLL lock."""
        return self._call_locked(super().get_scanned_module_state)

    def rescan_modules(self):
        """Rescan module addresses through the shared DLL lock."""
        return self._call_locked(super().rescan_modules)

    def set_scanned_module_state(self):
        """Persist the current module scan through the shared DLL lock."""
        return self._call_locked(super().set_scanned_module_state)

    # Module management convenience methods with logging
    
    def scan_modules(self):
        """Scan and log all connected modules."""
        self.logger.info("Scanning for connected modules")
        try:
            modules = self._call_locked(super().scan_all_modules)
            if modules:
                self.logger.info(f"Found {len(modules)} modules:")
                for addr, info in modules.items():
                    self.logger.info(f"  Module {addr}: Product {info.get('product_no', 'Unknown')}, "
                                   f"FW {info.get('fw_version', 'Unknown')}, "
                                   f"State {info.get('state', 'Unknown')}")
            else:
                self.logger.warning("No modules found")
            return modules
        except Exception as e:
            self.logger.error(f"Error scanning modules: {e}")
            raise

    def set_module_voltage(self, address, channel, voltage):
        """Set module voltage with logging."""
        self.logger.info(f"Setting module {address} channel {channel} voltage to {voltage:.3f}V")
        try:
            status = self._call_locked(super().set_module_voltage, address, channel, voltage)
            if status == self.NO_ERR:
                self.logger.info(f"Module {address} channel {channel} voltage set successfully")
            else:
                self.logger.error(
                    "Failed to set module "
                    f"{address} channel {channel} voltage: {self.format_status(status)}"
                )
            return status
        except Exception as e:
            self.logger.error(f"Error setting module voltage: {e}")
            raise

    def get_module_voltages(self, address):
        """Get all voltages for a module with logging."""
        self.logger.info(f"Getting voltages for module {address}")
        try:
            voltages = self._call_locked(super().get_all_module_voltages, address)
            for channel, data in voltages.items():
                setpoint = data.get('setpoint', 'N/A')
                measured = data.get('measured', 'N/A')
                self.logger.info(f"Module {address} Ch{channel}: Set={setpoint}V, Meas={measured}V")
            return voltages
        except Exception as e:
            self.logger.error(f"Error getting module voltages: {e}")
            raise

    def set_module_voltages(self, address, voltages):
        """Set multiple module voltages with logging."""
        self.logger.info(f"Setting multiple voltages for module {address}")
        try:
            results = self._call_locked(super().set_all_module_voltages, address, voltages)
            success_count = sum(1 for status in results.values() if status == self.NO_ERR)
            self.logger.info(f"Set {success_count}/{len(results)} voltages successfully on module {address}")
            
            for channel, status in results.items():
                if status != self.NO_ERR:
                    self.logger.error(
                        f"Failed to set module {address} channel {channel}: "
                        f"{self.format_status(status)}"
                    )
            
            return results
        except Exception as e:
            self.logger.error(f"Error setting module voltages: {e}")
            raise

    def get_module_info(self, address):
        """Get detailed module information with logging."""
        self.logger.info(f"Getting information for module {address}")
        try:
            info = {}
            
            status, product_no = self._call_locked(super().get_module_product_no, address)
            if status == self.NO_ERR:
                info["product_no"] = product_no

            status, fw_version = self._call_locked(super().get_module_fw_version, address)
            if status == self.NO_ERR:
                info["fw_version"] = fw_version

            status, hw_type = self._call_locked(super().get_module_hw_type, address)
            if status == self.NO_ERR:
                info["hw_type"] = hw_type

            status, hw_version = self._call_locked(super().get_module_hw_version, address)
            if status == self.NO_ERR:
                info["hw_version"] = hw_version

            status, state = self._call_locked(super().get_module_state, address)
            if status == self.NO_ERR:
                info["state"] = state

            hk_status, volt_24vp, volt_24vn, volt_12vp, volt_12vn, volt_5v0, volt_3v3, temp_psu, temp_board, volt_ref = self._call_locked(
                super().get_module_housekeeping, address
            )
            if hk_status == self.NO_ERR:
                info['housekeeping'] = {
                    'volt_24vp': volt_24vp,
                    'volt_24vn': volt_24vn,
                    'volt_12vp': volt_12vp,
                    'volt_12vn': volt_12vn,
                    'volt_5v0': volt_5v0,
                    'volt_3v3': volt_3v3,
                    'temp_psu': temp_psu,
                    'temp_board': temp_board,
                    'volt_ref': volt_ref
                }
            
            # Get voltage data for all channels
            info['voltages'] = self.get_module_voltages(address)
            
            self.logger.info(f"Retrieved information for module {address}")
            return info
            
        except Exception as e:
            self.logger.error(f"Error getting module {address} info: {e}")
            raise

    def restart_module(self, address):
        """Restart specific module with logging."""
        self.logger.info(f"Restarting module {address}")
        try:
            status = self._call_locked(super().restart_module, address)
            if status == self.NO_ERR:
                self.logger.info(f"Module {address} restart successful")
            else:
                self.logger.error(
                    f"Module {address} restart failed: {self.format_status(status)}"
                )
            return status
        except Exception as e:
            self.logger.error(f"Error restarting module {address}: {e}")
            raise


class AMPR(ProcessIsolatedClientMixin):
    """Public AMPR client with process isolation on Windows."""

    _INSTRUMENT_NAME = "AMPR"
    _PROCESS_CONTROLLER_CLASS = _AMPRController
    _PROCESS_CONTROLLER_PATH = (
        "esibd_ampr_plugin_runtime.ampr.ampr:_AMPRController"
    )
    _PROCESS_TIMEOUT_RULES = {
        "connect": (4.0, 5.0, 15.0),
        "initialize": (8.0, 5.0, 30.0),
    }

    def __init__(
        self,
        device_id: str,
        com: int,
        baudrate: int = 230400,
        logger: Optional[logging.Logger] = None,
        hk_thread: Optional[threading.Thread] = None,
        thread_lock: Optional[threading.Lock] = None,
        hk_interval_s: float = 5.0,
        dll_path: Optional[str] = None,
        log_dir: Optional[Path] = None,
        **kwargs,
    ):
        backend_kwargs = {
            "device_id": device_id,
            "com": com,
            "baudrate": baudrate,
            "logger": logger,
            "hk_thread": hk_thread,
            "thread_lock": thread_lock,
            "hk_interval_s": hk_interval_s,
            "dll_path": dll_path,
            "log_dir": log_dir,
            **kwargs,
        }
        self._initialize_process_backend(
            backend_kwargs=backend_kwargs,
            incompatible_objects={
                "logger": logger,
                "hk_thread": hk_thread,
                "thread_lock": thread_lock,
            },
        )
