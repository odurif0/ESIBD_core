"""Behavior checks for the standalone ESIBD Explorer AMX plugin."""

from __future__ import annotations

import importlib.util
import sys
import threading
import types
from enum import Enum
from pathlib import Path

import pytest


PLUGIN_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "esibd_explorer"
    / "amx"
    / "amx_plugin.py"
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
        or name.startswith("_esibd_bundled_amx_runtime")
        or name == "amx_plugin_behavior_test"
    ]:
        sys.modules.pop(name, None)


def _load_module():
    _clear_test_modules()
    _install_esibd_stubs()
    spec = importlib.util.spec_from_file_location("amx_plugin_behavior_test", PLUGIN_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_config_is_replaced_with_fixed_pulsers():
    module = _load_module()
    default_item = {
        "Pulser": "0",
        "Real": True,
        "Enabled": True,
        "Delay ticks": 0,
    }

    bootstrap_items = [
        {"Name": f"AMX{index}", "Pulser": 0, "Real": True, "Enabled": True}
        for index in range(1, 5)
    ]

    synced_items, log_entries = module._plan_channel_sync(
        current_items=bootstrap_items,
        device_name="AMX",
        default_item=default_item,
    )

    assert [item["Name"] for item in synced_items] == [
        "AMX_P0",
        "AMX_P1",
        "AMX_P2",
        "AMX_P3",
    ]
    assert all(item["Enabled"] is False for item in synced_items)
    assert all(item["Real"] is True for item in synced_items)
    assert log_entries == [("AMX bootstrap config replaced with fixed pulser channels.", None)]


def test_existing_config_is_merged_and_duplicates_are_neutralized():
    module = _load_module()

    current_items = [
        {"Name": "Keep0", "Pulser": 0, "Real": True, "Enabled": True},
        {"Name": "Duplicate0", "Pulser": "0", "Real": True, "Enabled": False},
        {"Name": "Legacy6", "Pulser": 6, "Real": True, "Enabled": True},
    ]

    synced_items, log_entries = module._plan_channel_sync(
        current_items=current_items,
        device_name="AMX",
    )

    assert synced_items[0]["Real"] is True
    assert synced_items[1]["Real"] is False
    assert synced_items[2]["Real"] is False
    added_names = {item["Name"] for item in synced_items[3:]}
    assert added_names == {"AMX_P1", "AMX_P2", "AMX_P3"}
    assert ("Added generic AMX pulser channels: P1, P2, P3", None) in log_entries
    assert (
        "Marked AMX pulser channels virtual because they do not exist on hardware: P6",
        None,
    ) in log_entries
    assert (
        "Duplicate AMX mapping detected for P0: Duplicate0",
        module.PRINT.WARNING,
    ) in log_entries


def test_controller_read_numbers_maps_pulser_snapshot():
    module = _load_module()

    class FakeDevice:
        OSC_OFFSET = 2
        PULSER_WIDTH_OFFSET = 2

        def collect_housekeeping(self, timeout_s=None):
            return {
                "device_enabled": True,
                "main_state": {"name": "ST_ON"},
                "device_state": {"flags": ["DEVST_OK"]},
                "controller_state": {"flags": ["CTRLST_OK"]},
                "oscillator": {"period": 99998},
                "pulsers": [
                    {"pulser": 0, "width_ticks": 49998, "burst": 3},
                    {"pulser": 1, "width_ticks": 24998, "burst": None},
                ],
            }

    class FakeChannel:
        def __init__(self, pulser):
            self._pulser = pulser
            self.real = True
            self.enabled = True

        def pulser_number(self):
            return self._pulser

    parent = types.SimpleNamespace(
        poll_timeout_s=2.5,
        getChannels=lambda: [FakeChannel(0), FakeChannel(1)],
        main_state="",
        device_enabled_state="",
    )

    controller = module.AMXController(parent)
    controller.device = FakeDevice()
    controller.initialized = True

    controller.readNumbers()

    assert controller.main_state == "ST_ON"
    assert controller.device_enabled_state == "ON"
    assert controller.device_state_summary == "DEVST_OK"
    assert controller.controller_state_summary == "CTRLST_OK"
    assert controller.values[0] == pytest.approx(50.0)
    assert controller.values[1] == pytest.approx(25.0)
    assert controller.width_values == {0: "49998", 1: "24998"}
    assert controller.burst_values == {0: "3", 1: "n/a"}
    assert parent.main_state == "ST_ON"
    assert parent.device_enabled_state == "ON"
