"""Behavior checks for the standalone ESIBD Explorer DMMR plugin."""

from __future__ import annotations

import importlib.util
import sys
import threading
import types
from enum import Enum
from pathlib import Path

import numpy as np


PLUGIN_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "esibd_explorer"
    / "dmmr"
    / "dmmr_plugin.py"
)


def _install_esibd_stubs() -> None:
    esibd = types.ModuleType("esibd")
    core = types.ModuleType("esibd.core")
    plugins = types.ModuleType("esibd.plugins")

    class PARAMETERTYPE(Enum):
        INT = "INT"
        FLOAT = "FLOAT"
        LABEL = "LABEL"

    class _PluginTypeValue:
        def __init__(self, value):
            self.value = value

    class PLUGINTYPE(Enum):
        INPUTDEVICE = _PluginTypeValue("INPUTDEVICE")

    class PRINT(Enum):
        WARNING = "WARNING"
        ERROR = "ERROR"

    class Parameter:
        VALUE = "Value"
        HEADER = "Header"
        NAME = "Name"
        ADVANCED = "Advanced"
        EVENT = "Event"
        TOOLTIP = "Tooltip"

    class Channel:
        COLLAPSE = "Collapse"
        NAME = "Name"
        ACTIVE = "Active"
        DISPLAY = "Display"
        REAL = "Real"
        ENABLED = "Enabled"
        VALUE = "Value"
        SCALING = "Scaling"
        MIN = "Min"
        MAX = "Max"
        OPTIMIZE = "Optimize"

    class _Signal:
        def emit(self, *args, **kwargs):
            self.last_emit = (args, kwargs)

    class DeviceController:
        def __init__(self, controllerParent=None):
            self.controllerParent = controllerParent
            self.lock = threading.Lock()
            self.signalComm = types.SimpleNamespace(initCompleteSignal=_Signal())
            self.errorCount = 0
            self.initializing = False
            self.acquiring = False
            self.values = None
            self.print = lambda *args, **kwargs: None

        def startAcquisition(self):
            self.acquiring = True

        def stopAcquisition(self):
            self.acquiring = False

        def toggleOn(self):
            return None

        def closeCommunication(self):
            return None

    class ToolButton:
        pass

    class Device:
        pass

    class Plugin:
        pass

    def parameterDict(**kwargs):
        return kwargs

    core.PARAMETERTYPE = PARAMETERTYPE
    core.PLUGINTYPE = PLUGINTYPE
    core.PRINT = PRINT
    core.Channel = Channel
    core.DeviceController = DeviceController
    core.Parameter = Parameter
    core.ToolButton = ToolButton
    core.parameterDict = parameterDict
    plugins.Device = Device
    plugins.Plugin = Plugin

    sys.modules["esibd"] = esibd
    sys.modules["esibd.core"] = core
    sys.modules["esibd.plugins"] = plugins


def _clear_test_modules() -> None:
    for name in [
        name
        for name in list(sys.modules)
        if name == "esibd"
        or name.startswith("esibd.")
        or name.startswith("_esibd_bundled_dmmr_runtime")
        or name == "dmmr_plugin_behavior_test"
    ]:
        sys.modules.pop(name, None)


def _load_module():
    _clear_test_modules()
    _install_esibd_stubs()
    spec = importlib.util.spec_from_file_location("dmmr_plugin_behavior_test", PLUGIN_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_config_is_replaced_from_detected_modules():
    module = _load_module()
    default_item = {
        "Module": "0",
        "Real": True,
        "Enabled": True,
    }

    bootstrap_items = [
        {"Name": f"DMMR{index}", "Module": 0, "Real": True, "Enabled": True}
        for index in range(1, 7)
    ]

    synced_items, log_entries = module._plan_channel_sync(
        current_items=bootstrap_items,
        detected_modules=[2, 5],
        device_name="DMMR",
        default_item=default_item,
    )

    assert synced_items == [
        {"Name": "DMMR_M02", "Module": "2", "Real": True, "Enabled": True},
        {"Name": "DMMR_M05", "Module": "5", "Real": True, "Enabled": True},
    ]
    assert log_entries == [("DMMR bootstrap config replaced from hardware scan.", None)]


def test_existing_config_is_merged_and_duplicates_are_neutralized():
    module = _load_module()

    current_items = [
        {"Name": "Keep", "Module": 1, "Real": True, "Enabled": True},
        {"Name": "MissingLater", "Module": 3, "Real": True, "Enabled": False},
        {"Name": "Duplicate", "Module": "1", "Real": True, "Enabled": True},
    ]

    synced_items, log_entries = module._plan_channel_sync(
        current_items=current_items,
        detected_modules=[1, 2],
        device_name="DMMR",
    )

    assert synced_items[0]["Real"] is True
    assert synced_items[1]["Real"] is False
    assert synced_items[2]["Real"] is False
    assert synced_items[3] == {
        "Name": "DMMR_M02",
        "Module": "2",
        "Real": True,
        "Enabled": True,
    }
    assert ("Added generic DMMR channels for detected modules: 2", None) in log_entries
    assert ("Marked DMMR channels virtual because modules are absent: 3", None) in log_entries
    assert (
        "Duplicate DMMR mapping detected for module 1: Duplicate",
        module.PRINT.WARNING,
    ) in log_entries


def test_controller_read_numbers_polls_module_currents():
    module = _load_module()

    class FakeDevice:
        NO_ERR = 0

        def get_state(self, timeout_s=None):
            return self.NO_ERR, "0x0000", "ST_ON"

        def get_device_state(self, timeout_s=None):
            return self.NO_ERR, "0x0000", ["DEVICE_OK"]

        def get_voltage_state(self, timeout_s=None):
            return self.NO_ERR, "0x0007", ["VS_3V3_OK", "VS_5V0_OK", "VS_12V_OK"]

        def get_temperature_state(self, timeout_s=None):
            return self.NO_ERR, "0x0000", ["TEMPERATURE_OK"]

        def get_module_current(self, module, timeout_s=None):
            return self.NO_ERR, module * 1e-12, module + 10

    class FakeChannel:
        def __init__(self, module):
            self._module = module
            self.real = True
            self.enabled = True

        def module_address(self):
            return self._module

    parent = types.SimpleNamespace(
        poll_timeout_s=2.5,
        isOn=lambda: True,
        getChannels=lambda: [FakeChannel(1), FakeChannel(2)],
        getConfiguredModules=lambda: [1, 2],
        main_state="",
        detected_modules="",
        device_state_summary="",
        voltage_state_summary="",
        temperature_state_summary="",
        _update_status_widgets=lambda: None,
    )

    controller = module.DMMRController(parent)
    controller.device = FakeDevice()
    controller.initialized = True
    controller.detected_module_ids = [1, 2]

    controller.readNumbers()

    assert controller.main_state == "ST_ON"
    assert controller.values == {1: 1e-12, 2: 2e-12}
    assert controller.device_state_summary == "DEVICE_OK"
    assert controller.voltage_state_summary == "VS_3V3_OK, VS_5V0_OK, VS_12V_OK"


def test_controller_toggle_on_enables_measurement():
    module = _load_module()
    calls = []

    class FakeDevice:
        NO_ERR = 0

        def set_enable(self, enabled, timeout_s=None):
            calls.append(("set_enable", enabled, timeout_s))
            return self.NO_ERR

        def set_automatic_current(self, enabled, timeout_s=None):
            calls.append(("set_automatic_current", enabled, timeout_s))
            return self.NO_ERR

        def get_state(self, timeout_s=None):
            return self.NO_ERR, "0x0000", "ST_ON"

        def get_device_state(self, timeout_s=None):
            return self.NO_ERR, "0x0000", ["DEVICE_OK"]

        def get_voltage_state(self, timeout_s=None):
            return self.NO_ERR, "0x0007", ["VS_3V3_OK", "VS_5V0_OK", "VS_12V_OK"]

        def get_temperature_state(self, timeout_s=None):
            return self.NO_ERR, "0x0000", ["TEMPERATURE_OK"]

        def format_status(self, status):
            return str(status)

    parent = types.SimpleNamespace(
        connect_timeout_s=7.0,
        poll_timeout_s=2.0,
        isOn=lambda: True,
        _update_status_widgets=lambda: None,
        main_state="",
        detected_modules="",
        device_state_summary="",
        voltage_state_summary="",
        temperature_state_summary="",
    )

    controller = module.DMMRController(parent)
    controller.device = FakeDevice()

    controller.toggleOn()

    assert calls == [
        ("set_enable", True, 7.0),
        ("set_automatic_current", True, 7.0),
    ]
    assert controller.acquiring is True


def test_controller_toggle_off_syncs_status_back_to_gui():
    module = _load_module()
    calls = []
    sync_calls = []

    class FakeDevice:
        NO_ERR = 0

        def set_enable(self, enabled, timeout_s=None):
            calls.append(("set_enable", enabled, timeout_s))
            return self.NO_ERR

        def set_automatic_current(self, enabled, timeout_s=None):
            calls.append(("set_automatic_current", enabled, timeout_s))
            return self.NO_ERR

        def get_state(self, timeout_s=None):
            return self.NO_ERR, "0x0000", "ST_ON"

        def get_device_state(self, timeout_s=None):
            return self.NO_ERR, "0x0000", ["DEVICE_OK"]

        def get_voltage_state(self, timeout_s=None):
            return self.NO_ERR, "0x0007", ["VS_3V3_OK", "VS_5V0_OK", "VS_12V_OK"]

        def get_temperature_state(self, timeout_s=None):
            return self.NO_ERR, "0x0000", ["TEMPERATURE_OK"]

        def format_status(self, status):
            return str(status)

    parent = types.SimpleNamespace(
        connect_timeout_s=7.0,
        poll_timeout_s=2.0,
        isOn=lambda: False,
        _update_status_widgets=lambda: sync_calls.append("sync"),
        main_state="",
        detected_modules="",
        device_state_summary="",
        voltage_state_summary="",
        temperature_state_summary="",
    )

    controller = module.DMMRController(parent)
    controller.device = FakeDevice()
    controller.detected_modules_text = "3"

    controller.toggleOn()

    assert calls == [
        ("set_automatic_current", False, 7.0),
        ("set_enable", False, 7.0),
    ]
    assert parent.main_state == "ST_ON"
    assert sync_calls == ["sync"]


def test_controller_read_numbers_reuses_acquisition_lock_without_deadlock():
    module = _load_module()

    class FakeDevice:
        NO_ERR = 0

        def __init__(self):
            self.calls = []

        def get_state(self, timeout_s=None):
            return self.NO_ERR, "0x0000", "ST_ON"

        def get_device_state(self, timeout_s=None):
            return self.NO_ERR, "0x0000", ["DEVICE_OK"]

        def get_voltage_state(self, timeout_s=None):
            return self.NO_ERR, "0x0007", ["VS_3V3_OK", "VS_5V0_OK", "VS_12V_OK"]

        def get_temperature_state(self, timeout_s=None):
            return self.NO_ERR, "0x0000", ["TEMPERATURE_OK"]

        def get_module_current(self, module, timeout_s=None):
            self.calls.append((module, timeout_s))
            return self.NO_ERR, 3.2e-12, 13

    class FakeTimeoutLock:
        def __init__(self):
            self.calls = []

        class _Section:
            def __init__(self, owner, timeout, timeout_message, already_acquired):
                self.owner = owner
                self.payload = (timeout, timeout_message, already_acquired)

            def __enter__(self):
                self.owner.calls.append(self.payload)
                return True

            def __exit__(self, exc_type, exc, tb):
                return False

        def acquire_timeout(self, timeout, timeoutMessage="", already_acquired=False):
            return self._Section(self, timeout, timeoutMessage, already_acquired)

    class FakeChannel:
        def __init__(self, module):
            self._module = module
            self.real = True
            self.enabled = True

        def module_address(self):
            return self._module

    device = FakeDevice()
    lock = FakeTimeoutLock()
    parent = types.SimpleNamespace(
        poll_timeout_s=2.5,
        isOn=lambda: True,
        getChannels=lambda: [FakeChannel(3)],
        getConfiguredModules=lambda: [3],
        main_state="",
        detected_modules="",
        device_state_summary="",
        voltage_state_summary="",
        temperature_state_summary="",
        _update_status_widgets=lambda: None,
    )

    controller = module.DMMRController(parent)
    controller.device = device
    controller.lock = lock
    controller.initialized = True
    controller.detected_module_ids = [3]

    controller.readNumbers()

    assert lock.calls[0] == (
        1,
        "Could not acquire lock to read DMMR module 3.",
        True,
    )
    assert device.calls == [(3, 2.5)]
    assert controller.values == {3: 3.2e-12}


def test_controller_formats_open_port_timeout_with_operator_hint():
    module = _load_module()

    parent = types.SimpleNamespace(com=3)
    controller = module.DMMRController(parent)

    message = controller._format_exception(
        RuntimeError(
            "DMMR DLL call timed out during 'open_port'. "
            "The device may be powered off or unresponsive. "
            "The DMMR instance is now marked unusable."
        )
    )

    assert "RuntimeError:" in message
    assert "Selected COM3 did not respond." in message
    assert "configured COM port is correct" in message


def test_controller_formats_open_port_error_with_operator_hint():
    module = _load_module()

    parent = types.SimpleNamespace(com=3)
    controller = module.DMMRController(parent)

    message = controller._format_exception(
        RuntimeError("DMMR open_port failed: -2 (Error opening port)")
    )

    assert "RuntimeError:" in message
    assert "Windows could not open COM3." in message
    assert "already in use" in message


def test_controller_update_values_clears_monitor_when_channel_is_disabled():
    module = _load_module()

    class FakeChannel:
        def __init__(self, module, enabled):
            self._module = module
            self.real = True
            self.enabled = enabled
            self.monitor = 99.0

        def module_address(self):
            return self._module

    channel_enabled = FakeChannel(1, True)
    channel_disabled = FakeChannel(2, False)
    parent = types.SimpleNamespace(
        isOn=lambda: True,
        getChannels=lambda: [channel_enabled, channel_disabled],
        main_state="",
        detected_modules="",
        device_state_summary="",
        voltage_state_summary="",
        temperature_state_summary="",
        _update_status_widgets=lambda: None,
    )

    controller = module.DMMRController(parent)
    controller.values = {1: 1.5e-12, 2: 2.5e-12}

    controller.updateValues()

    assert channel_enabled.monitor == 1.5e-12
    assert np.isnan(channel_disabled.monitor)
