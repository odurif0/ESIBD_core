"""Packaging checks for the standalone ESIBD Explorer AMX plugin."""

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
    / "amx"
    / "amx_plugin.py"
)
ICON_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "esibd_explorer"
    / "amx"
    / "amx.png"
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
        INDICATOR = "Indicator"
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

        def setDisplayedParameters(self):
            self.displayedParameters = [
                self.NAME,
                self.VALUE,
                self.ACTIVE,
                self.REAL,
                self.OPTIMIZE,
                self.DISPLAY,
            ]

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

        def initGUI(self, item):
            self.super_init_gui_called = item

        def scalingChanged(self):
            self.rowHeight = 18

    class DeviceController:
        def __init__(self, controllerParent=None):
            self.controllerParent = controllerParent

    class ToolButton:
        def __init__(self):
            self.maximum_height = None
            self.minimum_width = None
            self.text = None
            self.checkable = None
            self.auto_raise = None

        def setMaximumHeight(self, height):
            self.maximum_height = height

        def setMinimumWidth(self, width):
            self.minimum_width = width

        def setText(self, text):
            self.text = text

        def setCheckable(self, checkable):
            self.checkable = checkable

        def setAutoRaise(self, auto_raise):
            self.auto_raise = auto_raise

    class Device:
        MAXDATAPOINTS = "Max data points"

        def toggleAdvanced(self, advanced=False):
            self.super_toggle_advanced_called = advanced

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
        or name.startswith("_esibd_bundled_amx_runtime")
        or name in {"amx_plugin_test", "amx_plugin_missing_runtime_test"}
    ]:
        sys.modules.pop(name, None)


def _import_plugin_module_from_path(module_name: str, plugin_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, plugin_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_amx_plugin_exposes_expected_metadata():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("amx_plugin_test", PLUGIN_PATH)

    assert ICON_PATH.exists()
    assert module.providePlugins() == [module.AMXDevice]
    assert module.AMXDevice.name == "AMX"
    assert module.AMXDevice.unit == "%"
    assert module.AMXDevice.useMonitors is True
    assert module.AMXDevice.useOnOffLogic is True
    assert module.AMXDevice.iconFile == "amx.png"
    with Image.open(ICON_PATH) as image:
        assert image.size == (128, 128)


def test_amx_plugin_defaults_to_direct_software_drive_without_operating_config():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("amx_plugin_test", PLUGIN_PATH)
    device = object.__new__(module.AMXDevice)
    device.name = "AMX"
    device.MAXDATAPOINTS = "Max data points"
    original_settings = getattr(module.Device, "getDefaultSettings", None)
    module.Device.getDefaultSettings = lambda self: {
        "AMX/Interval": {module.Parameter.VALUE: 1000},
        "AMX/Max data points": {module.Parameter.VALUE: 100000},
    }
    try:
        settings = module.AMXDevice.getDefaultSettings(device)
    finally:
        if original_settings is None:
            delattr(module.Device, "getDefaultSettings")
        else:
            module.Device.getDefaultSettings = original_settings

    assert settings["AMX/Operating config"][module.Parameter.VALUE] == -1
    assert "without loading a stored controller config" in settings["AMX/Operating config"][
        module.Parameter.TOOLTIP
    ]
    assert settings["AMX/Available configs"][module.Parameter.VALUE] == "n/a"
    assert "reported by the controller after connect" in settings["AMX/Available configs"][
        module.Parameter.TOOLTIP
    ]


def test_amx_plugin_loads_driver_from_private_runtime():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("amx_plugin_test", PLUGIN_PATH)
    driver_class = module._get_amx_driver_class()

    assert driver_class.__name__ == "AMX"
    assert driver_class.__module__.startswith("_esibd_bundled_amx_runtime_")


def test_amx_plugin_fails_cleanly_when_runtime_is_missing(tmp_path):
    _clear_test_modules()
    _install_esibd_stubs()

    plugin_copy = tmp_path / "amx_plugin.py"
    shutil.copy2(PLUGIN_PATH, plugin_copy)
    module = _import_plugin_module_from_path(
        "amx_plugin_missing_runtime_test",
        plugin_copy,
    )

    try:
        module._get_amx_driver_class()
    except ModuleNotFoundError as exc:
        assert "vendor/runtime; plugin installation is incomplete" in str(exc)
    else:
        raise AssertionError("Expected ModuleNotFoundError when vendor/runtime is missing")


def test_plugin_hides_framework_collapse_and_real_columns():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("amx_plugin_test", PLUGIN_PATH)

    class FakeTree:
        def __init__(self):
            self.calls = []

        def setColumnHidden(self, index, hidden):
            self.calls.append((index, hidden))

    class FakeChannel:
        def getSortedDefaultChannel(self):
            return {
                "Collapse": {},
                "Real": {},
                "Name": {},
                "Value": {},
                "Pulser": {},
            }

    device = object.__new__(module.AMXDevice)
    device.tree = FakeTree()
    device.channels = [FakeChannel()]

    module.AMXDevice._update_channel_column_visibility(device)

    assert device.tree.calls == [(0, True), (1, True)]


def test_missing_default_config_keeps_amx_empty_until_first_initialization(tmp_path):
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("amx_plugin_test", PLUGIN_PATH)

    class FakeAction:
        def __init__(self):
            self.visible = None
            self.state = False

        def setVisible(self, visible):
            self.visible = visible

    class FakeTree:
        def __init__(self):
            self.updates = []
            self.root_decorated = []
            self.headers = []
            self.hidden = []
            self.layouts = 0

        def setUpdatesEnabled(self, enabled):
            self.updates.append(enabled)

        def setRootIsDecorated(self, value):
            self.root_decorated.append(value)

        def setHeaderLabels(self, labels):
            self.headers.append(labels)

        def setColumnHidden(self, index, hidden):
            self.hidden.append((index, hidden))

        def scheduleDelayedItemsLayout(self):
            self.layouts += 1

    global_updates = []
    logs = []
    device = object.__new__(module.AMXDevice)
    device.name = "AMX"
    device.confINI = "AMX.ini"
    device.inout = "IN"
    device.channels = []
    device.loading = False
    device.tree = FakeTree()
    device.advancedAction = FakeAction()
    device.importAction = FakeAction()
    device.exportAction = FakeAction()
    device.duplicateChannelAction = FakeAction()
    device.deleteChannelAction = FakeAction()
    device.moveChannelUpAction = FakeAction()
    device.moveChannelDownAction = FakeAction()
    device.channelType = module.AMXChannel
    device.pluginManager = types.SimpleNamespace(
        DeviceManager=types.SimpleNamespace(
            globalUpdate=lambda inout: global_updates.append(inout)
        )
    )
    device.customConfigFile = lambda _name: tmp_path / "AMX.ini"
    device.print = lambda message, flag=None: logs.append((message, flag))

    module.AMXDevice.loadConfiguration(device, useDefaultFile=True)

    assert device.channels == []
    assert logs == [
        (
            f"AMX config file {tmp_path / 'AMX.ini'} not found. "
            "Channels will be created after successful hardware initialization.",
            None,
        )
    ]
    assert device.tree.headers
    assert device.tree.updates == [False, True]
    assert global_updates == ["IN"]


def test_estimate_storage_handles_empty_pre_initialization_state():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("amx_plugin_test", PLUGIN_PATH)

    class FakeWidget:
        def __init__(self):
            self.tooltips = []

        def setToolTip(self, tooltip):
            self.tooltips.append(tooltip)

    widget = FakeWidget()
    device = object.__new__(module.AMXDevice)
    device.channels = []
    device.name = "AMX"
    device.MAXDATAPOINTS = "Max data points"
    device.pluginManager = types.SimpleNamespace(
        Settings=types.SimpleNamespace(
            settings={
                "AMX/Max data points": types.SimpleNamespace(getWidget=lambda: widget)
            }
        )
    )

    module.AMXDevice.estimateStorage(device)

    assert device.maxDataPoints == 0
    assert widget.tooltips == [
        "Storage estimate will be available after the first successful "
        "AMX hardware initialization."
    ]


def test_finalize_init_relabels_advanced_action_for_amx():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("amx_plugin_test", PLUGIN_PATH)

    class FakeAdvancedAction:
        def __init__(self):
            self.toolTipFalse = ""
            self.toolTipTrue = ""
            self.tooltips = []

        def setToolTip(self, tooltip):
            self.tooltips.append(tooltip)

    device = object.__new__(module.AMXDevice)
    device.name = "AMX"
    device.advancedAction = FakeAdvancedAction()
    device._ensure_local_on_action = lambda: None
    device._ensure_status_widgets = lambda: None
    device._update_channel_column_visibility = lambda: None

    original_finalize_init = getattr(module.Device, "finalizeInit", None)
    module.Device.finalizeInit = lambda self: None
    try:
        module.AMXDevice.finalizeInit(device)
    finally:
        if original_finalize_init is None:
            delattr(module.Device, "finalizeInit")
        else:
            module.Device.finalizeInit = original_finalize_init

    assert device.advancedAction.toolTipFalse == (
        "Show expert columns and channel layout actions for AMX."
    )
    assert device.advancedAction.toolTipTrue == (
        "Hide expert columns and channel layout actions for AMX."
    )
    assert device.advancedAction.tooltips == [
        "Show expert columns and channel layout actions for AMX."
    ]


def test_finalize_init_adds_local_on_action_and_set_on_keeps_it_synced():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("amx_plugin_test", PLUGIN_PATH)

    class FakeStateAction:
        def __init__(self):
            self._state = None
            self.blocked = []

        @property
        def state(self):
            return self._state

        @state.setter
        def state(self, value):
            self._state = value

        def blockSignals(self, blocked):
            self.blocked.append(blocked)

    added = []
    toggle_calls = []
    device = object.__new__(module.AMXDevice)
    device.name = "AMX"
    device.useOnOffLogic = True
    device.closeCommunicationAction = object()
    device._ensure_status_widgets = lambda: None
    device._update_channel_column_visibility = lambda: None
    device.makeIcon = lambda name, path=None, desaturate=False: f"local:{name}"
    device.onAction = types.SimpleNamespace(state=True)
    device.isOn = lambda: device.onAction.state
    device.addStateAction = lambda **kwargs: added.append(kwargs) or FakeStateAction()
    device.controller = types.SimpleNamespace(
        initialized=True,
        initializing=False,
        transitioning=False,
        toggleOnFromThread=lambda parallel=True: toggle_calls.append(parallel),
    )
    device.loading = False

    original_finalize_init = getattr(module.Device, "finalizeInit", None)
    module.Device.finalizeInit = lambda self: None
    try:
        module.AMXDevice.finalizeInit(device)
        module.AMXDevice.setOn(device, on=False)
    finally:
        if original_finalize_init is None:
            delattr(module.Device, "finalizeInit")
        else:
            module.Device.finalizeInit = original_finalize_init

    assert len(added) == 1
    assert added[0]["toolTipFalse"] == "Turn AMX ON."
    assert added[0]["toolTipTrue"] == "Turn AMX OFF and disconnect."
    assert added[0]["iconFalse"] == "local:switch-medium_on.png"
    assert added[0]["iconTrue"] == "local:switch-medium_off.png"
    assert added[0]["before"] is device.closeCommunicationAction
    assert added[0]["restore"] is False
    assert added[0]["defaultState"] is False
    assert isinstance(device.deviceOnAction, FakeStateAction)
    assert device.deviceOnAction.state is False
    assert device.deviceOnAction.blocked == [True, False, True, False]
    assert toggle_calls == [True]


def test_amx_set_on_ui_state_updates_both_actions_and_status_widgets():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("amx_plugin_test", PLUGIN_PATH)

    class FakeSignal:
        def __init__(self):
            self.values = []

        def emit(self, value):
            self.values.append(value)

    class FakeAction:
        def __init__(self):
            self.state = None
            self.signalComm = types.SimpleNamespace(
                setValueFromThreadSignal=FakeSignal()
            )

    sync_calls = []
    status_calls = []
    device = object.__new__(module.AMXDevice)
    device.onAction = FakeAction()
    device.deviceOnAction = FakeAction()
    device._sync_local_on_action = lambda: sync_calls.append(True)
    device._update_status_widgets = lambda: status_calls.append(True)

    module.AMXDevice._set_on_ui_state(device, False)

    assert device.onAction.signalComm.setValueFromThreadSignal.values == [False]
    assert device.deviceOnAction.signalComm.setValueFromThreadSignal.values == [False]
    assert sync_calls == [True]
    assert status_calls == [True]


def test_amx_set_on_initializes_communication_when_turning_on():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("amx_plugin_test", PLUGIN_PATH)

    init_calls = []
    toggle_calls = []
    device = object.__new__(module.AMXDevice)
    device.onAction = types.SimpleNamespace(state=True)
    device.isOn = lambda: True
    device._sync_local_on_action = lambda: None
    device._update_status_widgets = lambda: None
    device.initializeCommunication = lambda: init_calls.append(True)
    device.controller = types.SimpleNamespace(
        initialized=False,
        initializing=False,
        transitioning=False,
        transition_target_on=None,
        toggleOnFromThread=lambda parallel=True: toggle_calls.append(parallel),
    )
    device.loading = False

    module.AMXDevice.setOn(device, on=True)

    assert init_calls == [True]
    assert toggle_calls == []


def test_amx_toggle_recording_rejects_disconnected_device():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("amx_plugin_test", PLUGIN_PATH)
    super_calls = []

    def fake_super_toggle(self, on=None, manual=True):
        super_calls.append((on, manual))

    module.Device.toggleRecording = fake_super_toggle

    class FakeAction:
        def __init__(self):
            self.state = True
            self.enabled = None
            self.blocks = []

        def setEnabled(self, enabled):
            self.enabled = enabled

        def blockSignals(self, blocked):
            self.blocks.append(blocked)

    device = object.__new__(module.AMXDevice)
    device.name = "AMX"
    device.recording = False
    device.recordingAction = FakeAction()
    device.controller = types.SimpleNamespace(
        device=None,
        initializing=False,
        initialized=False,
        transitioning=False,
        main_state="Disconnected",
    )
    device.isOn = lambda: False
    device.printed = []
    device.print = lambda message, flag=None: device.printed.append((message, flag))

    module.AMXDevice.toggleRecording(device, on=True, manual=True)

    assert super_calls == []
    assert device.recordingAction.state is False
    assert device.recordingAction.enabled is False
    assert device.printed == [
        (
            "Cannot start AMX data acquisition: device disconnected.",
            module.PRINT.WARNING,
        )
    ]


def test_status_widgets_summarize_global_amx_state():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("amx_plugin_test", PLUGIN_PATH)

    class FakeLabel:
        def __init__(self, text=""):
            self.text = text
            self.tooltips = []
            self.styles = []
            self.object_names = []

        def setObjectName(self, name):
            self.object_names.append(name)

        def setText(self, text):
            self.text = text

        def setToolTip(self, tooltip):
            self.tooltips.append(tooltip)

        def setStyleSheet(self, style):
            self.styles.append(style)

    class FakeTitleBar:
        def __init__(self):
            self.inserted = []

        def insertWidget(self, before, widget):
            self.inserted.append((before, widget))

    device = object.__new__(module.AMXDevice)
    device.name = "AMX"
    device.titleBar = FakeTitleBar()
    device.titleBarLabel = FakeLabel()
    device.stretchAction = object()
    device.onAction = types.SimpleNamespace(state=False)
    device.isOn = lambda: device.onAction.state
    device.main_state = "Disconnected"
    device.device_enabled_state = "OFF"
    device.available_configs_text = "0:Standby; 1:Operate"
    device.controller = types.SimpleNamespace(
        device_state_summary="OK",
        controller_state_summary="CTRL_READY",
    )

    module.AMXDevice._ensure_status_widgets(device)

    assert len(device.titleBar.inserted) == 2
    assert device.statusBadgeLabel.text == "Disconnected"
    assert (
        device.statusSummaryLabel.text
        == "Device: OFF | Faults: OK | Configs: 0:Standby +1"
    )
    tooltip = device.statusBadgeLabel.tooltips[-1]
    assert "State: Disconnected" in tooltip
    assert "Device enabled: OFF" in tooltip
    assert "Faults: OK" in tooltip
    assert "Controller: CTRL_READY" in tooltip
    assert "Configs: 0:Standby; 1:Operate" in tooltip
    assert "#718096" in device.statusBadgeLabel.styles[-1]


def test_channel_uses_explicit_toggle_buttons_and_minimum_row_height():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("amx_plugin_test", PLUGIN_PATH)

    class FakeParameter:
        def __init__(self, value):
            self.value = value
            self.widget = None
            self.check = None
            self.rowHeight = 18
            self.heights = []

        def applyWidget(self):
            self.check = self.widget

        def setHeight(self, height):
            self.rowHeight = height
            self.heights.append(height)

    class FakeTree:
        def __init__(self):
            self.layouts = 0

        def scheduleDelayedItemsLayout(self):
            self.layouts += 1

    original_display_widget = object()
    channel = object.__new__(module.AMXChannel)
    parameters = {
        "Enabled": FakeParameter(True),
        "Active": FakeParameter(False),
        "Display": FakeParameter(True),
    }
    parameters["Display"].widget = original_display_widget
    parameters["Display"].check = original_display_widget
    channel.parameters = list(parameters.values())
    channel.enabled = True
    channel.rowHeight = 18
    channel.loading = False
    channel.tree = FakeTree()
    channel.getParameterByName = lambda name: parameters[name]

    module.AMXChannel.initGUI(channel, {"Name": "dummy"})

    assert channel.super_init_gui_called == {"Name": "dummy"}
    assert channel.rowHeight == 28
    assert parameters["Enabled"].check.text == "ON"
    assert parameters["Enabled"].check.minimum_width == 48
    assert parameters["Enabled"].check.maximum_height == 28
    assert parameters["Enabled"].check.checkable is True
    assert parameters["Active"].check.text == "Manual"
    assert parameters["Active"].check.minimum_width == 72
    assert parameters["Display"].check is original_display_widget
    assert channel.tree.layouts == 1
