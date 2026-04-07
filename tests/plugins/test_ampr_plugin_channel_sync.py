"""Channel synchronization checks for the standalone ESIBD AMPR plugin."""

from __future__ import annotations

import importlib.util
import sys
import types
from enum import Enum
from pathlib import Path


PLUGIN_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "esibd_explorer"
    / "ampr"
    / "ampr_plugin.py"
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

    class Channel:
        NAME = "Name"
        REAL = "Real"
        ENABLED = "Enabled"
        VALUE = "Value"

    class DeviceController:
        def __init__(self, controllerParent=None):
            self.controllerParent = controllerParent

        def initComplete(self):
            self.super_init_complete_called = True

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
        or name == "cgc"
        or name.startswith("cgc.")
        or name == "esibd_ampr_plugin_runtime"
        or name.startswith("esibd_ampr_plugin_runtime.")
        or name == "ampr_plugin_sync_test"
    ]:
        sys.modules.pop(name, None)


def _import_plugin_module():
    spec = importlib.util.spec_from_file_location("ampr_plugin_sync_test", PLUGIN_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_module():
    _clear_test_modules()
    _install_esibd_stubs()
    return _import_plugin_module()


def test_bootstrap_config_is_replaced_from_detected_modules():
    module = _load_module()
    default_item = {"Module": 0, "CH": 1, "Real": True, "Enabled": True}

    bootstrap_items = [
        {"Name": f"AMPR{index}", "Module": 0, "CH": 1, "Real": True, "Enabled": True}
        for index in range(1, 13)
    ]

    synced_items, log_entries = module._plan_channel_sync(
        current_items=bootstrap_items,
        detected_modules=[2, 5],
        device_name="AMPR",
        default_item=default_item,
    )

    assert len(synced_items) == 8
    assert synced_items[0]["Name"] == "AMPR_M02_CH1"
    assert synced_items[-1]["Name"] == "AMPR_M05_CH4"
    assert all(item["Enabled"] is False for item in synced_items)
    assert all(item["Real"] is True for item in synced_items)
    assert log_entries == [("AMPR bootstrap config replaced from hardware scan.", None)]


def test_sequential_names_with_user_changes_are_not_treated_as_bootstrap():
    module = _load_module()

    assert module._looks_like_bootstrap_items(
        items=[
            {"Name": "AMPR1", "Module": 0, "CH": 1, "Real": True, "Enabled": False},
            {"Name": "AMPR2", "Module": 0, "CH": 1, "Real": True, "Enabled": True},
        ],
        device_name="AMPR",
        default_item={"Module": 0, "CH": 1, "Real": True, "Enabled": True},
    ) is False


def test_existing_config_is_merged_and_new_channels_are_generic():
    module = _load_module()

    current_items = [
        {
            "Name": "UserKeep",
            "Module": 1,
            "CH": 1,
            "Real": True,
            "Enabled": True,
            "Min": -5,
            "Max": 5,
        },
        {
            "Name": "MissingLater",
            "Module": 3,
            "CH": 2,
            "Real": True,
            "Enabled": True,
            "Color": "#112233",
        },
        {
            "Name": "ComesBack",
            "Module": 2,
            "CH": 4,
            "Real": False,
            "Enabled": True,
        },
    ]

    synced_items, log_entries = module._plan_channel_sync(
        current_items=current_items,
        detected_modules=[1, 2],
        device_name="AMPR",
    )

    assert len(synced_items) == 9
    assert synced_items[0]["Name"] == "UserKeep"
    assert synced_items[0]["Real"] is True
    assert synced_items[0]["Enabled"] is True
    assert synced_items[0]["Min"] == -5
    assert synced_items[1]["Name"] == "MissingLater"
    assert synced_items[1]["Real"] is False
    assert synced_items[1]["Color"] == "#112233"
    assert synced_items[2]["Name"] == "ComesBack"
    assert synced_items[2]["Real"] is True

    added_names = {item["Name"] for item in synced_items[3:]}
    assert "AMPR_M01_CH2" in added_names
    assert "AMPR_M02_CH1" in added_names
    assert "AMPR_M02_CH3" in added_names
    assert all(item["Enabled"] is False for item in synced_items[3:])

    log_messages = [message for message, _flag in log_entries]
    assert "Added generic AMPR channels for detected modules: 1, 2" in log_messages
    assert "Marked AMPR channels virtual because modules are absent: 3" in log_messages
    assert "Reactivated AMPR channels for modules: 2" in log_messages


def test_duplicate_mappings_are_neutralized():
    module = _load_module()

    current_items = [
        {"Name": "First", "Module": 1, "CH": 1, "Real": True, "Enabled": True},
        {"Name": "Duplicate", "Module": "1", "CH": "1", "Real": "true", "Enabled": True},
    ]

    synced_items, log_entries = module._plan_channel_sync(
        current_items=current_items,
        detected_modules=[1],
        device_name="AMPR",
    )

    assert synced_items[0]["Real"] is True
    assert synced_items[1]["Real"] is False
    assert any("Duplicate AMPR mapping detected for module 1 CH1: Duplicate" == message for message, _flag in log_entries)


def test_empty_detection_does_not_modify_channels():
    module = _load_module()

    current_items = [{"Name": "UserKeep", "Module": 1, "CH": 1, "Real": True}]
    synced_items, log_entries = module._plan_channel_sync(
        current_items=current_items,
        detected_modules=[],
        device_name="AMPR",
    )

    assert synced_items == current_items
    assert log_entries == []


def test_init_complete_skips_sync_without_real_device():
    module = _load_module()

    parent = types.SimpleNamespace(main_state="", detected_modules="", sync_calls=[])
    parent._sync_channels_from_detected_modules = lambda modules: parent.sync_calls.append(
        list(modules)
    )

    controller = module.AMPRController(controllerParent=parent)
    controller.detected_module_ids = [1]
    controller.device = None

    controller.initComplete()

    assert parent.sync_calls == []
    assert controller.super_init_complete_called is True
