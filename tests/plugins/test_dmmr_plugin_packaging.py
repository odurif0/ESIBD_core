"""Packaging checks for the standalone ESIBD Explorer DMMR plugin."""

from __future__ import annotations

import importlib
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
    / "dmmr"
    / "dmmr_plugin.py"
)
ICON_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "esibd_explorer"
    / "dmmr"
    / "dmmr.png"
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

        def getDefaultChannel(self):
            return {
                self.ACTIVE: {Parameter.HEADER: "A", Parameter.ADVANCED: True},
                self.DISPLAY: {Parameter.HEADER: "D", Parameter.ADVANCED: False},
                self.REAL: {Parameter.HEADER: "R", Parameter.ADVANCED: True},
                self.ENABLED: {Parameter.HEADER: "E", Parameter.ADVANCED: True},
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
        def __init__(self):
            self.style = None
            self.checked = None

        def setStyleSheet(self, style):
            self.style = style

        def setChecked(self, checked):
            self.checked = checked

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
        if "indicator" in kwargs:
            parameter[Parameter.INDICATOR] = kwargs["indicator"]
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
        or name.startswith("_esibd_bundled_dmmr_runtime")
        or name in {"dmmr_plugin_test", "dmmr_plugin_missing_runtime_test"}
    ]:
        sys.modules.pop(name, None)


def _import_plugin_module_from_path(module_name: str, plugin_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, plugin_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dmmr_plugin_exposes_expected_metadata():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    assert ICON_PATH.exists()
    assert module.providePlugins() == [module.DMMRDevice]
    assert module.DMMRDevice.name == "DMMR"
    assert module.DMMRDevice.unit == "A"
    assert module.DMMRDevice.useMonitors is True
    assert module.DMMRDevice.useOnOffLogic is True
    assert module.DMMRDevice.iconFile == "dmmr.png"
    with Image.open(ICON_PATH) as image:
        assert image.size == (128, 128)


def test_dmmr_plugin_loads_driver_from_private_runtime():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)
    driver_class = module._get_dmmr_driver_class()

    assert driver_class.__name__ == "DMMR"
    assert driver_class.__module__.startswith("_esibd_bundled_dmmr_runtime_")


def test_dmmr_plugin_runtime_supports_explicit_process_backend_when_supported(monkeypatch):
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)
    driver_class = module._get_dmmr_driver_class()
    runtime_root = driver_class.__module__.rsplit(".", 2)[0]
    driver_common = importlib.import_module(f"{runtime_root}._driver_common")
    created = {}

    class FakeProxy:
        def __init__(self, controller_path, controller_kwargs, *, label, startup_timeout_s):
            created["controller_path"] = controller_path
            created["controller_kwargs"] = controller_kwargs
            created["label"] = label
            created["startup_timeout_s"] = startup_timeout_s
            self.closed = False

        def close(self):
            self.closed = True

    class FakeController:
        def __init__(self, **kwargs):
            created["inline_kwargs"] = kwargs

    monkeypatch.setattr(driver_common, "RUNTIME_IS_WINDOWS", True)
    monkeypatch.setattr(driver_common, "ControllerProcessProxy", FakeProxy)
    monkeypatch.setattr(driver_class, "_PROCESS_CONTROLLER_CLASS", FakeController)

    dmmr = driver_class("dmmr_process", com=8, process_backend=True)

    assert dmmr._backend_mode == "process"
    assert created["controller_path"].endswith(".dmmr:_DMMRController")
    assert created["label"] == "DMMR dmmr_process"
    assert created["controller_kwargs"]["device_id"] == "dmmr_process"
    assert created["controller_kwargs"]["com"] == 8
    assert created["controller_kwargs"]["logger"] is None
    assert "inline_kwargs" not in created

    dmmr.close()

    assert dmmr._backend.closed is True


def test_dmmr_plugin_runtime_defaults_to_inline_backend(monkeypatch):
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)
    driver_class = module._get_dmmr_driver_class()
    runtime_root = driver_class.__module__.rsplit(".", 2)[0]
    driver_common = importlib.import_module(f"{runtime_root}._driver_common")
    created = {}

    class FakeProxy:
        def __init__(self, *args, **kwargs):  # pragma: no cover - should stay unused
            created["proxy_called"] = True

    class FakeController:
        def __init__(self, **kwargs):
            created["inline_kwargs"] = kwargs

    monkeypatch.setattr(driver_common, "RUNTIME_IS_WINDOWS", True)
    monkeypatch.setattr(driver_common, "ControllerProcessProxy", FakeProxy)
    monkeypatch.setattr(driver_class, "_PROCESS_CONTROLLER_CLASS", FakeController)

    dmmr = driver_class("dmmr_inline", com=8)

    assert dmmr._backend_mode == "inline"
    assert created["inline_kwargs"]["device_id"] == "dmmr_inline"
    assert "proxy_called" not in created
    assert dmmr._process_backend_disabled_reason == ""


def test_dmmr_plugin_fails_cleanly_when_runtime_is_missing(tmp_path):
    _clear_test_modules()
    _install_esibd_stubs()

    plugin_copy = tmp_path / "dmmr_plugin.py"
    shutil.copy2(PLUGIN_PATH, plugin_copy)
    module = _import_plugin_module_from_path(
        "dmmr_plugin_missing_runtime_test",
        plugin_copy,
    )

    try:
        module._get_dmmr_driver_class()
    except ModuleNotFoundError as exc:
        assert "vendor/runtime; plugin installation is incomplete" in str(exc)
    else:
        raise AssertionError("Expected ModuleNotFoundError when vendor/runtime is missing")


def test_dmmr_recording_action_reflects_device_readiness():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    class FakeAction:
        def __init__(self):
            self.state = False
            self.enabled = None
            self.blocks = []

        def setEnabled(self, enabled):
            self.enabled = enabled

        def blockSignals(self, blocked):
            self.blocks.append(blocked)

    class FakeDisplayAction(FakeAction):
        def __init__(self, tooltip):
            super().__init__()
            self._tooltip = tooltip

        def toolTip(self):
            return self._tooltip

    class FakeTitleBar:
        def __init__(self, actions):
            self._actions = actions

        def actions(self):
            return list(self._actions)

    device = object.__new__(module.DMMRDevice)
    device.recording = False
    device.recordingAction = FakeAction()
    display_close = FakeDisplayAction("Close DMMR communication.")
    display_init = FakeDisplayAction("Initialize DMMR communication.")
    device.liveDisplay = types.SimpleNamespace(
        recordingAction=FakeAction(),
        titleBar=FakeTitleBar([display_close, display_init]),
    )
    device.controller = types.SimpleNamespace(
        device=object(),
        initializing=False,
        initialized=False,
        transitioning=False,
        main_state="ST_STBY",
    )
    device.isOn = lambda: True

    module.DMMRDevice._sync_acquisition_controls(device)
    assert device.recordingAction.enabled is False
    assert device.liveDisplay.recordingAction.enabled is False
    assert display_close.enabled is False
    assert display_init.enabled is True

    device.controller.initialized = True
    device.controller.main_state = "ST_ON"
    module.DMMRDevice._sync_acquisition_controls(device)
    assert device.recordingAction.enabled is True
    assert device.liveDisplay.recordingAction.enabled is True
    assert display_close.enabled is True
    assert display_init.enabled is False


def test_dmmr_toggle_recording_rejects_unready_device():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)
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

    device = object.__new__(module.DMMRDevice)
    device.name = "DMMR"
    device.recording = False
    device.recordingAction = FakeAction()
    device.controller = types.SimpleNamespace(
        device=object(),
        initializing=False,
        initialized=True,
        transitioning=False,
        main_state="ST_STBY",
    )
    device.isOn = lambda: True
    device.printed = []
    device.print = lambda message, flag=None: device.printed.append((message, flag))

    module.DMMRDevice.toggleRecording(device, on=True, manual=True)

    assert super_calls == []
    assert device.recordingAction.state is False
    assert device.recordingAction.enabled is False
    assert device.printed == [
        ("Cannot start DMMR data acquisition: state is ST_STBY.", module.PRINT.WARNING)
    ]


def test_dmmr_channel_keeps_active_parameter_for_core_init():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    channel = object.__new__(module.DMMRChannel)
    original_set_displayed = getattr(module.Channel, "setDisplayedParameters", None)
    module.Channel.setDisplayedParameters = lambda self: setattr(
        self,
        "displayedParameters",
        ["Collapse", "Enabled", "Name", "Value", "Display", "Active", "Real", "Optimize"],
    )
    try:
        module.DMMRChannel.setDisplayedParameters(channel)
    finally:
        if original_set_displayed is None:
            delattr(module.Channel, "setDisplayedParameters")
        else:
            module.Channel.setDisplayedParameters = original_set_displayed

    assert "Active" in channel.displayedParameters
    assert channel.displayedParameters[-2:] == ["Module", "Display"]


def test_dmmr_channel_marks_reference_as_advanced_only():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    channel = object.__new__(module.DMMRChannel)
    defaults = module.DMMRChannel.getDefaultChannel(channel)

    assert defaults["Value"][module.Parameter.HEADER] == "Reference (A)"
    assert defaults["Value"][module.Parameter.ADVANCED] is True


def test_dmmr_channel_renames_monitor_header_to_current():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    channel = object.__new__(module.DMMRChannel)
    original_get_default = getattr(module.Channel, "getDefaultChannel", None)
    module.Channel.getDefaultChannel = lambda self: {
        "Value": {module.Parameter.HEADER: "Value"},
        "Monitor": {module.Parameter.HEADER: "Monitor"},
        "Enabled": {},
        "Display": {},
        "Active": {},
    }
    try:
        defaults = module.DMMRChannel.getDefaultChannel(channel)
    finally:
        if original_get_default is None:
            delattr(module.Channel, "getDefaultChannel")
        else:
            module.Channel.getDefaultChannel = original_get_default

    assert defaults["Monitor"][module.Parameter.HEADER] == "Current"


def test_dmmr_formats_currents_with_si_prefixes():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    assert module._format_si_current(0.0) == ("0 A", "0.000000e+00 A")
    assert module._format_si_current(2.3e-12) == ("2.3 pA", "2.300000e-12 A")
    assert module._format_si_current(7.1e-9) == ("7.1 nA", "7.100000e-09 A")
    assert module._format_si_current(4.2e-6) == ("4.2 uA", "4.200000e-06 A")
    assert module._format_si_current(5.5e-3) == ("5.5 mA", "5.500000e-03 A")
    assert module._format_si_current(1.25) == ("1.25 A", "1.250000e+00 A")


def test_dmmr_sync_monitor_widget_uses_formatted_current_and_tooltip():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    class FakeLineEdit:
        def __init__(self):
            self.text = None
            self.tooltip = None

        def setText(self, text):
            self.text = text

        def setToolTip(self, tooltip):
            self.tooltip = tooltip

    class FakeWidget:
        def __init__(self):
            self.tooltip = None
            self._line_edit = FakeLineEdit()

        def lineEdit(self):
            return self._line_edit

        def setToolTip(self, tooltip):
            self.tooltip = tooltip

    class FakeParameter:
        def __init__(self, widget):
            self.widget = widget
            self.toolTip = "Measured DMMR module current."

        def getWidget(self):
            return self.widget

    widget = FakeWidget()
    channel = object.__new__(module.DMMRChannel)
    channel.monitor = 2.3e-12
    channel.getParameterByName = lambda name: {"Monitor": FakeParameter(widget)}[name]

    module.DMMRChannel._sync_monitor_widget(channel)

    assert widget.lineEdit().text == "2.3 pA"
    assert "2.3 pA (2.300000e-12 A)" in widget.tooltip
    assert widget.lineEdit().tooltip == widget.tooltip


def test_dmmr_status_badge_shows_off_when_ui_is_off():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    device = object.__new__(module.DMMRDevice)
    device.main_state = "ST_ON"
    device.detected_modules = "3"
    device.device_state_summary = "DEVICE_OK"
    device.voltage_state_summary = "VS_3V3_OK"
    device.temperature_state_summary = "TEMPERATURE_OK"
    device.isOn = lambda: False

    assert module.DMMRDevice._display_main_state(device) == "OFF"
    tooltip = module.DMMRDevice._status_tooltip_text(device)
    assert "State: OFF" in tooltip
    assert "Hardware state: ST_ON" in tooltip


def test_dmmr_status_badge_keeps_unconfirmed_shutdown_visible_when_ui_is_off():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    device = object.__new__(module.DMMRDevice)
    device.main_state = module._DMMR_SHUTDOWN_UNCONFIRMED_STATE
    device.detected_modules = "3"
    device.device_state_summary = "n/a"
    device.voltage_state_summary = "n/a"
    device.temperature_state_summary = "n/a"
    device.isOn = lambda: False

    assert (
        module.DMMRDevice._display_main_state(device)
        == module._DMMR_SHUTDOWN_UNCONFIRMED_STATE
    )


def test_dmmr_device_shutdown_keeps_ui_on_when_shutdown_is_unconfirmed():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    device = object.__new__(module.DMMRDevice)
    device.useOnOffLogic = True
    device.onAction = types.SimpleNamespace(state=False)
    sync_states = []
    device._sync_local_on_action = lambda: sync_states.append(device.onAction.state)
    device._update_status_widgets = lambda: None
    device.stopAcquisition = lambda: None
    device._sync_acquisition_controls = lambda: None
    warnings = []
    device.print = lambda message, flag=None: warnings.append((message, flag))
    device.controller = types.SimpleNamespace(shutdownCommunication=lambda: False)
    device.recording = True

    module.DMMRDevice.shutdownCommunication(device)

    assert device.onAction.state is True
    assert sync_states == [True]
    assert any("shutdown could not be confirmed" in message for message, _ in warnings)


def test_dmmr_device_close_communication_bypasses_shutdown_when_transport_is_lost():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    close_calls = []
    device = object.__new__(module.DMMRDevice)
    device.useOnOffLogic = True
    device.onAction = types.SimpleNamespace(state=True)
    sync_states = []
    device._sync_local_on_action = lambda: sync_states.append(device.onAction.state)
    device.stopAcquisition = lambda: None
    device._sync_acquisition_controls = lambda: None
    device.recording = True
    device.initialized = True
    device.shutdownCommunication = lambda: (_ for _ in ()).throw(
        AssertionError("forced communication loss must not call shutdownCommunication")
    )
    device.controller = types.SimpleNamespace(
        initialized=True,
        _forced_close_state=module._DMMR_COMMUNICATION_LOST_STATE,
        closeCommunication=lambda final_state=None: close_calls.append(final_state),
    )

    module.DMMRDevice.closeCommunication(device)

    assert device.onAction.state is False
    assert sync_states == [False]
    assert close_calls == [module._DMMR_COMMUNICATION_LOST_STATE]


def test_dmmr_device_set_on_uses_controller_initialized_for_restart():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    init_calls = []
    toggle_calls = []
    device = object.__new__(module.DMMRDevice)
    device.loading = False
    device.initialized = True  # Simulate a stale framework flag after a forced close.
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

    module.DMMRDevice.setOn(device, on=True)

    assert init_calls == [True]
    assert toggle_calls == []


def test_dmmr_channel_keeps_display_widget_default():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    channel = object.__new__(module.DMMRChannel)
    channel.useDisplays = True
    channel.scalingChanged = lambda: setattr(channel, "scaling_changed", True)
    upgrade_calls = []
    channel._upgrade_toggle_widget = (
        lambda parameter_name, label, minimum_width: upgrade_calls.append(
            (parameter_name, label, minimum_width)
        )
    )

    original_init_gui = getattr(module.Channel, "initGUI", None)
    module.Channel.initGUI = lambda self, item: setattr(self, "super_init_gui_called", item)
    try:
        module.DMMRChannel.initGUI(channel, {"Name": "dummy"})
    finally:
        if original_init_gui is None:
            delattr(module.Channel, "initGUI")
        else:
            module.Channel.initGUI = original_init_gui

    assert channel.super_init_gui_called == {"Name": "dummy"}
    assert upgrade_calls == [("Enabled", "Read", 52)]
    assert channel.scaling_changed is True


def test_dmmr_channel_init_gui_applies_neutral_display_and_module_styles():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    neutral_calls = []
    channel = object.__new__(module.DMMRChannel)
    channel.useDisplays = True
    channel._upgrade_monitor_widget = lambda: None
    channel._upgrade_toggle_widget = lambda *args, **kwargs: None
    channel._sync_enabled_toggle_widget = lambda: None
    channel.scalingChanged = lambda: None
    channel._sync_neutral_parameter_styles = lambda: neutral_calls.append(True)

    original_init_gui = getattr(module.Channel, "initGUI", None)
    module.Channel.initGUI = lambda self, item: setattr(self, "super_init_gui_called", item)
    try:
        module.DMMRChannel.initGUI(channel, {"Name": "dummy"})
    finally:
        if original_init_gui is None:
            delattr(module.Channel, "initGUI")
        else:
            module.Channel.initGUI = original_init_gui

    assert neutral_calls == [True]


def test_dmmr_channel_neutralizes_display_and_module_widgets():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    class FakeWidget:
        def __init__(self):
            self.styles = []
            self.container = types.SimpleNamespace(
                styles=[],
                setStyleSheet=lambda style: self.container.styles.append(style),
            )

        def setStyleSheet(self, style):
            self.styles.append(style)

    class FakeParameter:
        def __init__(self, widget):
            self.widget = widget

        def getWidget(self):
            return self.widget

    display_widget = FakeWidget()
    module_widget = FakeWidget()
    parameters = {
        "Display": FakeParameter(display_widget),
        "Module": FakeParameter(module_widget),
    }

    channel = object.__new__(module.DMMRChannel)
    channel.DISPLAY = "Display"
    channel.MODULE = "Module"
    channel.getParameterByName = lambda name: parameters[name]

    module.DMMRChannel._sync_neutral_parameter_styles(channel)

    assert display_widget.styles[-1] == module._DMMR_NEUTRAL_WIDGET_STYLE
    assert display_widget.container.styles[-1] == module._DMMR_NEUTRAL_WIDGET_STYLE
    assert module_widget.styles[-1] == module._DMMR_NEUTRAL_WIDGET_STYLE
    assert module_widget.container.styles[-1] == module._DMMR_NEUTRAL_WIDGET_STYLE


def test_dmmr_enabled_toggle_widget_syncs_checked_state_and_style():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    widget = module.ToolButton()
    parameter = types.SimpleNamespace(check=widget)
    channel = object.__new__(module.DMMRChannel)
    channel.enabled = True
    channel.getParameterByName = lambda name: {"Enabled": parameter}[name]

    module.DMMRChannel._sync_enabled_toggle_widget(channel)

    assert widget.checked is True
    assert "#1f2933" in widget.style


def test_dmmr_parameter_widget_style_updates_widget_and_container():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("dmmr_plugin_test", PLUGIN_PATH)

    class FakeContainer:
        def __init__(self):
            self.styles = []
            self.children = []

        def setStyleSheet(self, style):
            self.styles.append(style)

        def findChildren(self, _type):
            return list(self.children)

    class FakeLineEdit:
        def __init__(self):
            self.styles = []

        def setStyleSheet(self, style):
            self.styles.append(style)

    class FakeWidget:
        def __init__(self):
            self.container = FakeContainer()
            self.styles = []
            self.line_edit = FakeLineEdit()
            self.child = FakeLineEdit()
            self.container.children.append(self.child)

        def setStyleSheet(self, style):
            self.styles.append(style)

        def lineEdit(self):
            return self.line_edit

        def findChildren(self, _type):
            return []

    widget = FakeWidget()
    parameter = types.SimpleNamespace(getWidget=lambda: widget)
    channel = object.__new__(module.DMMRChannel)
    channel.getParameterByName = lambda name: {"Display": parameter}[name]

    module.DMMRChannel._set_parameter_widget_style(
        channel,
        "Display",
        module._DMMR_NEUTRAL_WIDGET_STYLE,
    )

    assert widget.container.styles == [module._DMMR_NEUTRAL_WIDGET_STYLE]
    assert widget.styles == [module._DMMR_NEUTRAL_WIDGET_STYLE]
    assert widget.line_edit.styles == [module._DMMR_NEUTRAL_WIDGET_STYLE]
    assert widget.child.styles == [module._DMMR_NEUTRAL_WIDGET_STYLE]
