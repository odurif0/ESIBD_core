"""Behavior checks for the standalone ESIBD Explorer PSU plugin."""

from __future__ import annotations

import importlib.util
import sys
import threading
import types
from enum import Enum
from pathlib import Path

import numpy as np
import pytest


PLUGIN_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "esibd_explorer"
    / "psu"
    / "psu_plugin.py"
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
        NAME = "Name"
        REAL = "Real"
        ENABLED = "Enabled"
        VALUE = "Value"

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
            self.print = lambda *args, **kwargs: None

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
        or name.startswith("_esibd_bundled_psu_runtime")
        or name == "psu_plugin_behavior_test"
    ]:
        sys.modules.pop(name, None)


def _load_module():
    _clear_test_modules()
    _install_esibd_stubs()
    spec = importlib.util.spec_from_file_location("psu_plugin_behavior_test", PLUGIN_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_config_is_replaced_with_fixed_channels():
    module = _load_module()
    default_item = {
        "CH": "0",
        "Real": True,
        "Enabled": True,
        "Output": "OFF",
    }

    bootstrap_items = [
        {"Name": f"PSU{index}", "CH": 0, "Real": True, "Enabled": True}
        for index in range(1, 5)
    ]

    synced_items, log_entries = module._plan_channel_sync(
        current_items=bootstrap_items,
        device_name="PSU",
        default_item=default_item,
    )

    assert synced_items == [
        {
            "Name": "PSU_CH0",
            "CH": "0",
            "Real": True,
            "Enabled": False,
            "Output": "OFF",
        },
        {
            "Name": "PSU_CH1",
            "CH": "1",
            "Real": True,
            "Enabled": False,
            "Output": "OFF",
        },
    ]
    assert log_entries == [("PSU bootstrap config replaced with fixed hardware channels.", None)]


def test_existing_config_is_merged_and_duplicates_are_neutralized():
    module = _load_module()

    current_items = [
        {"Name": "Keep0", "CH": 0, "Real": True, "Enabled": True},
        {"Name": "Duplicate0", "CH": "0", "Real": True, "Enabled": False},
        {"Name": "Legacy5", "CH": 5, "Real": True, "Enabled": True},
    ]

    synced_items, log_entries = module._plan_channel_sync(
        current_items=current_items,
        device_name="PSU",
    )

    assert synced_items[0]["Real"] is True
    assert synced_items[1]["Real"] is False
    assert synced_items[2]["Real"] is False
    assert synced_items[3] == {
        "Name": "PSU_CH1",
        "CH": "1",
        "Real": True,
        "Enabled": False,
    }
    assert ("Added generic PSU channels: CH1", None) in log_entries
    assert (
        "Marked PSU channels virtual because they do not exist on hardware: CH5",
        None,
    ) in log_entries
    assert (
        "Duplicate PSU mapping detected for CH0: Duplicate0",
        module.PRINT.WARNING,
    ) in log_entries


def test_controller_read_numbers_maps_housekeeping_snapshot():
    module = _load_module()

    class FakeDevice:
        def collect_housekeeping(self, timeout_s=None):
            return {
                "device_enabled": True,
                "output_enabled": (True, False),
                "main_state": {"name": "ST_ON"},
                "device_state": {"flags": ["DEVICE_OK"]},
                "channels": [
                    {
                        "channel": 0,
                        "enabled": True,
                        "voltage": {"measured_v": 25.0, "set_v": 30.0},
                        "current": {"measured_a": 0.4, "set_a": 0.5},
                    },
                    {
                        "channel": 1,
                        "enabled": False,
                        "voltage": {"measured_v": 0.0, "set_v": 0.0},
                        "current": {"measured_a": 0.0, "set_a": 0.0},
                    },
                ],
            }

    class FakeChannel:
        def __init__(self, channel):
            self._channel = channel
            self.real = True
            self.enabled = True

        def channel_number(self):
            return self._channel

    parent = types.SimpleNamespace(
        poll_timeout_s=2.5,
        getChannels=lambda: [FakeChannel(0), FakeChannel(1)],
        main_state="",
        output_summary="",
    )

    controller = module.PSUController(parent)
    controller.device = FakeDevice()
    controller.initialized = True

    controller.readNumbers()

    assert controller.main_state == "ST_ON"
    assert controller.device_state_summary == "DEVICE_OK"
    assert controller.output_state_summary == "CH0=ON, CH1=OFF"
    assert controller.values == {0: 25.0, 1: 0.0}
    assert controller.current_values == {0: 0.4, 1: 0.0}
    assert controller.output_enabled_by_channel == {0: True, 1: False}
    assert controller.voltage_setpoints == {0: "30 V", 1: "0 V"}
    assert controller.current_setpoints == {0: "0.5 A", 1: "0 A"}
    assert parent.main_state == "ST_ON"
    assert parent.output_summary == "CH0=ON, CH1=OFF"


def test_format_current_text_handles_nan():
    module = _load_module()

    assert module._format_current_text(np.nan) == "n/a"
    assert module._format_current_text(0.125) == "0.125 A"


def test_format_voltage_text_handles_nan():
    module = _load_module()

    assert module._format_voltage_text(np.nan) == "n/a"
    assert module._format_voltage_text(25.0) == "25 V"


def test_toggle_on_uses_config_startup_only():
    module = _load_module()
    calls = []

    class FakeDevice:
        def initialize(self, timeout_s=None, **kwargs):
            calls.append(("initialize", timeout_s, kwargs))

        def collect_housekeeping(self, timeout_s=None):
            calls.append(("collect_housekeeping", timeout_s))
            return {
                "device_enabled": True,
                "output_enabled": (True, True),
                "main_state": {"name": "ST_ON"},
                "device_state": {"flags": ["DEVICE_OK"]},
                "channels": [
                    {
                        "channel": 0,
                        "enabled": True,
                        "voltage": {"measured_v": 10.0, "set_v": 10.0},
                        "current": {"measured_a": 0.1, "set_a": 0.1},
                    },
                    {
                        "channel": 1,
                        "enabled": True,
                        "voltage": {"measured_v": 20.0, "set_v": 20.0},
                        "current": {"measured_a": 0.2, "set_a": 0.2},
                    },
                ],
            }

        def __getattr__(self, name):
            if name.startswith("set_channel_") or name in {
                "set_output_enabled",
                "set_device_enabled",
            }:
                raise AssertionError(f"Unexpected live override call: {name}")
            raise AttributeError(name)

    parent = types.SimpleNamespace(
        startup_timeout_s=9.0,
        standby_config=1,
        operating_config=2,
        isOn=lambda: True,
        getChannels=lambda: [],
        main_state="",
        output_summary="",
    )

    controller = module.PSUController(parent)
    controller.device = FakeDevice()
    controller.initialized = True
    controller._begin_transition(True)
    controller.toggleOn()

    assert calls == [
        ("initialize", 9.0, {"standby_config": 1, "operating_config": 2}),
        ("collect_housekeeping", 5.0),
    ]
