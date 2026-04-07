"""Packaging checks for the standalone ESIBD Explorer AMPR plugin."""

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
VENDOR_ROOT = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "esibd_explorer"
    / "ampr"
    / "vendor"
)
ICON_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "esibd_explorer"
    / "ampr"
    / "ampr.png"
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
        VALUE = "value"
        HEADER = "header"

    class Channel:
        COLLAPSE = "Collapse"
        NAME = "Name"
        REAL = "Real"
        ENABLED = "Enabled"
        VALUE = "Value"
        OPTIMIZE = "Optimize"

        def setDisplayedParameters(self):
            self.displayedParameters = [
                self.NAME,
                self.VALUE,
                self.OPTIMIZE,
                "Display",
            ]

        def getDefaultChannel(self):
            return {
                self.VALUE: {Parameter.HEADER: "Unit"},
            }

    class DeviceController:
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
        or name == "ampr_plugin_test"
    ]:
        sys.modules.pop(name, None)


def _import_plugin_module():
    spec = importlib.util.spec_from_file_location("ampr_plugin_test", PLUGIN_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_import_is_lazy_and_does_not_load_runtime(monkeypatch):
    _clear_test_modules()
    _install_esibd_stubs()
    monkeypatch.syspath_prepend("/tmp/nonexistent-sentinel")

    vendor_root_str = str(VENDOR_ROOT)
    assert vendor_root_str not in sys.path

    module = _import_plugin_module()

    assert callable(module._get_ampr_driver_class)
    assert vendor_root_str not in sys.path
    assert "cgc" not in sys.modules
    assert "esibd_ampr_plugin_runtime" not in sys.modules


def test_plugin_prefers_private_bundled_runtime(monkeypatch):
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module()
    driver_class = module._get_ampr_driver_class()

    assert driver_class.__module__.startswith("esibd_ampr_plugin_runtime.ampr")
    assert str(VENDOR_ROOT) in sys.path
    assert (
        sys.modules["esibd_ampr_plugin_runtime"].__file__
        == str(VENDOR_ROOT / "esibd_ampr_plugin_runtime" / "__init__.py")
    )


def test_plugin_icon_is_a_valid_png_asset():
    assert ICON_PATH.exists()
    with ICON_PATH.open("rb") as handle:
        assert handle.read(8) == b"\x89PNG\r\n\x1a\n"


def test_plugin_enables_monitors_and_hides_optimize_column():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module()

    assert module.AMPRDevice.useMonitors is True

    channel = object.__new__(module.AMPRChannel)
    module.AMPRChannel.setDisplayedParameters(channel)

    assert "Optimize" not in channel.displayedParameters
    assert channel.displayedParameters[-2:] == ["Module", "CH"]

    channel_defaults = module.AMPRChannel.getDefaultChannel(channel)
    assert channel_defaults["Module"]["advanced"] is False
    assert channel_defaults["CH"]["advanced"] is False
    assert channel_defaults["Module"]["indicator"] is True
    assert channel_defaults["CH"]["indicator"] is True


def test_plugin_hides_framework_collapse_column():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module()

    class FakeTree:
        def __init__(self):
            self.calls = []

        def setColumnHidden(self, index, hidden):
            self.calls.append((index, hidden))

    class FakeChannel:
        def getSortedDefaultChannel(self):
            return {
                "Collapse": {},
                "Name": {},
                "Value": {},
                "Module": {},
                "CH": {},
            }

    device = object.__new__(module.AMPRDevice)
    device.tree = FakeTree()
    device.channels = [FakeChannel()]

    module.AMPRDevice._update_channel_column_visibility(device)

    assert device.tree.calls == [(0, True)]
