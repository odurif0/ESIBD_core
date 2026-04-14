"""Packaging checks for the standalone ESIBD Explorer PSU plugin."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import types
from enum import Enum
from pathlib import Path

from PIL import Image


PLUGIN_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "esibd_explorer"
    / "psu"
    / "psu_plugin.py"
)
ICON_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "esibd_explorer"
    / "psu"
    / "psu.png"
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
        ADVANCED = "Advanced"
        EVENT = "Event"
        TOOLTIP = "Tooltip"
        PARAMETER_TYPE = "PARAMETER_TYPE"

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

        def __init__(self, channelParent=None, tree=None):
            self.channelParent = channelParent
            self.tree = tree

        def getDefaultChannel(self):
            return {
                self.ACTIVE: {Parameter.HEADER: "A"},
                self.DISPLAY: {Parameter.HEADER: "D"},
                self.REAL: {Parameter.HEADER: "R"},
                self.ENABLED: {Parameter.HEADER: "E"},
                self.VALUE: {Parameter.HEADER: "Value"},
                self.SCALING: {Parameter.VALUE: "normal"},
                self.MIN: {},
                self.MAX: {},
            }

        def getSortedDefaultChannel(self):
            return self.getDefaultChannel()

    class DeviceController:
        def __init__(self, controllerParent=None):
            self.controllerParent = controllerParent

    class ToolButton:
        pass

    class Device:
        MAXDATAPOINTS = "Max data points"

    class Plugin:
        pass

    def parameterDict(**kwargs):
        parameter = {}
        if "value" in kwargs:
            parameter[Parameter.VALUE] = kwargs["value"]
        if "advanced" in kwargs:
            parameter[Parameter.ADVANCED] = kwargs["advanced"]
        if "header" in kwargs:
            parameter[Parameter.HEADER] = kwargs["header"]
        if "toolTip" in kwargs:
            parameter[Parameter.TOOLTIP] = kwargs["toolTip"]
        if "parameterType" in kwargs:
            parameter[Parameter.PARAMETER_TYPE] = kwargs["parameterType"]
        parameter.update(kwargs)
        return parameter

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
        or name in {"psu_plugin_test", "psu_plugin_missing_runtime_test"}
    ]:
        sys.modules.pop(name, None)


def _import_plugin_module_from_path(module_name: str, plugin_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, plugin_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_psu_plugin_exposes_expected_metadata():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    assert ICON_PATH.exists()
    assert module.providePlugins() == [module.PSUDevice]
    assert module.PSUDevice.name == "PSU"
    assert module.PSUDevice.unit == "V"
    assert module.PSUDevice.useMonitors is True
    assert module.PSUDevice.useOnOffLogic is True
    assert module.PSUDevice.iconFile == "psu.png"
    with Image.open(ICON_PATH) as image:
        assert image.size == (128, 128)


def test_psu_plugin_loads_driver_from_private_runtime():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)
    driver_class = module._get_psu_driver_class()

    assert driver_class.__name__ == "PSU"
    assert driver_class.__module__.startswith("_esibd_bundled_psu_runtime_")


def test_psu_plugin_fails_cleanly_when_runtime_is_missing(tmp_path):
    _clear_test_modules()
    _install_esibd_stubs()

    plugin_copy = tmp_path / "psu_plugin.py"
    shutil.copy2(PLUGIN_PATH, plugin_copy)
    module = _import_plugin_module_from_path(
        "psu_plugin_missing_runtime_test",
        plugin_copy,
    )

    try:
        module._get_psu_driver_class()
    except ModuleNotFoundError as exc:
        assert "vendor/runtime; plugin installation is incomplete" in str(exc)
    else:
        raise AssertionError("Expected ModuleNotFoundError when vendor/runtime is missing")
