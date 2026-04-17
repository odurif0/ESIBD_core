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
        MONITOR = "Monitor"
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
                self.MONITOR: {Parameter.HEADER: "Monitor"},
                self.SCALING: {Parameter.VALUE: "normal"},
                self.MIN: {},
                self.MAX: {},
            }

        def getSortedDefaultChannel(self):
            return self.getDefaultChannel()

        def setDisplayedParameters(self):
            self.displayedParameters = [
                self.COLLAPSE,
                self.ENABLED,
                self.NAME,
                self.VALUE,
                self.DISPLAY,
                self.ACTIVE,
                self.REAL,
                self.SCALING,
                self.MIN,
                self.MAX,
                self.OPTIMIZE,
            ]

        def initGUI(self, item):
            self.super_init_gui_called = item
            self.updateColor()

        def updateColor(self):
            return bool(self.active)

        def realChanged(self):
            enabled_widget = self.getParameterByName(self.ENABLED).getWidget()
            if enabled_widget is not None:
                self.base_real_changed_called = True

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


def test_psu_plugin_exposes_available_configs_setting():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)
    device = object.__new__(module.PSUDevice)
    device.name = "PSU"
    device.MAXDATAPOINTS = "Max data points"
    original_settings = getattr(module.Device, "getDefaultSettings", None)
    module.Device.getDefaultSettings = lambda self: {
        "PSU/Interval": {module.Parameter.VALUE: 1000},
        "PSU/Max data points": {module.Parameter.VALUE: 100000},
    }
    try:
        settings = module.PSUDevice.getDefaultSettings(device)
    finally:
        if original_settings is None:
            delattr(module.Device, "getDefaultSettings")
        else:
            module.Device.getDefaultSettings = original_settings

    assert settings["PSU/Available configs"][module.Parameter.VALUE] == "n/a"
    assert "reported by the controller after connect" in settings["PSU/Available configs"][
        module.Parameter.TOOLTIP
    ]


def test_config_list_text_uses_detected_configs_verbatim():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    device = object.__new__(module.PSUDevice)
    device.available_configs_text = "1:Standby; 7:Operate 5 kV"

    assert module.PSUDevice._config_list_text(device) == "1:Standby; 7:Operate 5 kV"
    assert (
        module.PSUDevice._config_list_tooltip_text(device)
        == "Available PSU configs:\n1:Standby; 7:Operate 5 kV"
    )


def test_estimate_storage_handles_no_channels_before_hardware_sync():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    class FakeWidget:
        def __init__(self):
            self.tooltip = None

        def setToolTip(self, tooltip):
            self.tooltip = tooltip

    widget = FakeWidget()
    device = object.__new__(module.PSUDevice)
    device.name = "PSU"
    device.MAXDATAPOINTS = "Max data points"
    device.channels = []
    device.pluginManager = types.SimpleNamespace(
        Settings=types.SimpleNamespace(
            settings={
                "PSU/Max data points": types.SimpleNamespace(getWidget=lambda: widget),
            }
        )
    )

    module.PSUDevice.estimateStorage(device)

    assert device.maxDataPoints == 0
    assert (
        widget.tooltip
        == "Storage estimate unavailable until PSU channels are synchronized with hardware."
    )


def test_load_configuration_bootstraps_transient_channels_when_ini_is_missing(tmp_path):
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    bootstrapped_items = [
        {"Name": "PSU_CH0", "CH": "0", "Real": True, "Enabled": False, "Output": "OFF"},
        {"Name": "PSU_CH1", "CH": "1", "Real": True, "Enabled": False, "Output": "OFF"},
    ]
    applied = []
    global_updates = []
    logs = []

    device = object.__new__(module.PSUDevice)
    device.name = "PSU"
    device.confINI = "PSU.ini"
    device.channels = []
    device.inout = "IN"
    device.customConfigFile = lambda _name: tmp_path / "PSU.ini"
    device._bootstrap_channel_items = lambda: list(bootstrapped_items)
    device._apply_channel_items = lambda items, persist=True: applied.append((items, persist))
    device.pluginManager = types.SimpleNamespace(
        DeviceManager=types.SimpleNamespace(
            globalUpdate=lambda inout: global_updates.append(inout)
        )
    )
    device.print = lambda message, flag=None: logs.append((message, flag))

    module.PSUDevice.loadConfiguration(device, useDefaultFile=True)

    assert applied == [(bootstrapped_items, False)]
    assert logs == [
        (
            f"PSU config file {tmp_path / 'PSU.ini'} not found. "
            "Bootstrapping transient CH0/CH1 channels until hardware initialization.",
            None,
        )
    ]
    assert global_updates == ["IN"]


def test_finalize_init_adds_local_on_action_and_set_on_keeps_it_synced():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

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
    device = object.__new__(module.PSUDevice)
    device.name = "PSU"
    device.useOnOffLogic = True
    device.closeCommunicationAction = object()
    device._ensure_status_widgets = lambda: None
    device._ensure_config_selectors = lambda: None
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
        module.PSUDevice.finalizeInit(device)
        module.PSUDevice.setOn(device, on=False)
    finally:
        if original_finalize_init is None:
            delattr(module.Device, "finalizeInit")
        else:
            module.Device.finalizeInit = original_finalize_init

    assert len(added) == 1
    assert added[0]["toolTipFalse"] == "Turn PSU ON."
    assert added[0]["toolTipTrue"] == "Turn PSU OFF and disconnect."
    assert added[0]["iconFalse"] == "local:switch-medium_on.png"
    assert added[0]["iconTrue"] == "local:switch-medium_off.png"
    assert added[0]["before"] is device.closeCommunicationAction
    assert added[0]["restore"] is False
    assert added[0]["defaultState"] is False
    assert isinstance(device.deviceOnAction, FakeStateAction)
    assert device.deviceOnAction.state is False
    assert device.deviceOnAction.blocked == [True, False, True, False]
    assert toggle_calls == [True]


def test_psu_set_on_ui_state_updates_both_actions_and_status_widgets():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

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
    device = object.__new__(module.PSUDevice)
    device.onAction = FakeAction()
    device.deviceOnAction = FakeAction()
    device._sync_local_on_action = lambda: sync_calls.append(True)
    device._update_status_widgets = lambda: status_calls.append(True)

    module.PSUDevice._set_on_ui_state(device, False)

    assert device.onAction.signalComm.setValueFromThreadSignal.values == [False]
    assert device.deviceOnAction.signalComm.setValueFromThreadSignal.values == [False]
    assert sync_calls == [True]
    assert status_calls == [True]


def test_psu_set_on_initializes_communication_when_turning_on():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    init_calls = []
    toggle_calls = []
    device = object.__new__(module.PSUDevice)
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

    module.PSUDevice.setOn(device, on=True)

    assert init_calls == [True]
    assert toggle_calls == []


def test_psu_toggle_recording_rejects_disconnected_device():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)
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

    device = object.__new__(module.PSUDevice)
    device.name = "PSU"
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

    module.PSUDevice.toggleRecording(device, on=True, manual=True)

    assert super_calls == []
    assert device.recordingAction.state is False
    assert device.recordingAction.enabled is False
    assert device.printed == [
        (
            "Cannot start PSU data acquisition: device disconnected.",
            module.PRINT.WARNING,
        )
    ]


def test_status_widgets_summarize_global_psu_state():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

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

    device = object.__new__(module.PSUDevice)
    device.name = "PSU"
    device.titleBar = FakeTitleBar()
    device.titleBarLabel = FakeLabel()
    device.stretchAction = object()
    device.onAction = types.SimpleNamespace(state=False)
    device.isOn = lambda: device.onAction.state
    device.main_state = "Disconnected"
    device.output_summary = "CH0=OFF, CH1=OFF"
    device.available_configs_text = "0:Standby; 1:Operate"
    device.controller = types.SimpleNamespace(device_state_summary="OK")

    module.PSUDevice._ensure_status_widgets(device)

    assert len(device.titleBar.inserted) == 2
    assert device.statusBadgeLabel.text == "Disconnected"
    assert device.statusSummaryLabel.text == "HV outputs: CH0=OFF +1 | Device flags: OK"
    tooltip = device.statusBadgeLabel.tooltips[-1]
    assert "State: Disconnected" in tooltip
    assert "HV outputs: CH0=OFF, CH1=OFF" in tooltip
    assert "Device flags: OK" in tooltip
    assert "Available configs: 0:Standby; 1:Operate" in tooltip
    assert "#718096" in device.statusBadgeLabel.styles[-1]


def test_status_widgets_show_hardware_state_when_psu_state_is_harmonized():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

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

    device = object.__new__(module.PSUDevice)
    device.name = "PSU"
    device.titleBar = FakeTitleBar()
    device.titleBarLabel = FakeLabel()
    device.stretchAction = object()
    device.onAction = types.SimpleNamespace(state=True)
    device.isOn = lambda: device.onAction.state
    device.main_state = "ST_STBY"
    device.hardware_main_state = "STATE_ERR_PSU_DIS"
    device.output_summary = "CH0=OFF, CH1=OFF"
    device.available_configs_text = "0:Standby; 9:Operate"
    device.controller = types.SimpleNamespace(device_state_summary="DEVST_PSU_DIS")

    module.PSUDevice._ensure_status_widgets(device)

    assert device.statusBadgeLabel.text == "ST_STBY"
    tooltip = device.statusBadgeLabel.tooltips[-1]
    assert "State: ST_STBY" in tooltip
    assert "Hardware state: STATE_ERR_PSU_DIS" in tooltip
    assert "#b7791f" in device.statusBadgeLabel.styles[-1]


def test_config_controls_show_available_configs():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    class FakeSignal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

    class FakeCombo:
        def __init__(self):
            self.items = []
            self._current_index = -1
            self.tooltips = []
            self.currentIndexChanged = FakeSignal()

        def setMinimumWidth(self, _width):
            return None

        def setMaxVisibleItems(self, _count):
            return None

        def setSizeAdjustPolicy(self, _policy):
            return None

        def clear(self):
            self.items = []

        def addItem(self, text, value=None):
            self.items.append((text, value))

        def findData(self, value):
            for index, item in enumerate(self.items):
                if item[1] == value:
                    return index
            return -1

        def itemData(self, index):
            return self.items[index][1]

        def setCurrentIndex(self, index):
            self._current_index = index

        def currentIndex(self):
            return self._current_index

        def setToolTip(self, tooltip):
            self.tooltips.append(tooltip)

        def blockSignals(self, _blocked):
            return None

    class FakeLabel:
        def __init__(self, text=""):
            self.text = text
            self.tooltips = []
            self.object_names = []
            self.styles = []

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
            return widget

    device = object.__new__(module.PSUDevice)
    device.name = "PSU"
    device.titleBar = FakeTitleBar()
    device.titleBarLabel = FakeLabel()
    device.stretchAction = object()
    device.available_configs = [
        {"index": 1, "name": "Standby", "active": True, "valid": True},
        {"index": 7, "name": "Operate 5 kV", "active": True, "valid": True},
    ]
    device.available_configs_text = "1:Standby; 7:Operate 5 kV"
    device.standby_config = 1
    device.operating_config = 7
    device._create_config_selector_widget = lambda: FakeCombo()
    device._update_status_widgets = lambda: None

    module.PSUDevice._ensure_config_selectors(device)

    assert len(device.titleBar.inserted) == 6
    assert device.availableConfigsLabel.text == "Available:"
    assert device.availableConfigsValueLabel.text == "1:Standby; 7:Operate 5 kV"
    assert device.standbyConfigLabel.text == "Standby:"
    assert device.standbyConfigCombo.items == [
        ("Skip (-1)", -1),
        ("1:Standby", 1),
        ("7:Operate 5 kV", 7),
    ]
    assert device.standbyConfigCombo.currentIndex() == 1
    assert device.operatingConfigLabel.text == "Operating:"
    assert device.operatingConfigCombo.items == [
        ("Skip (-1)", -1),
        ("1:Standby", 1),
        ("7:Operate 5 kV", 7),
    ]
    assert device.operatingConfigCombo.currentIndex() == 2
    assert (
        device.availableConfigsValueLabel.tooltips[-1]
        == "Available PSU configs:\n1:Standby; 7:Operate 5 kV"
    )
    assert "Available PSU configs:" in device.standbyConfigCombo.tooltips[-1]


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


def test_channel_init_gui_handles_legacy_config_without_active_flag():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    channel = object.__new__(module.PSUChannel)
    channel.ACTIVE = "Active"
    channel.ENABLED = "Enabled"
    channel.REAL = "Real"

    module.PSUChannel.initGUI(
        channel,
        {
            "Name": "test",
            "CH": "0",
            "Real": True,
            "Enabled": False,
            # Simulates legacy configs where Active was not initialized yet.
        },
    )

    assert channel.super_init_gui_called["Name"] == "test"
    assert channel.active is True
    assert channel.enabled is False
    assert channel.real is True


def test_channel_default_headers_match_psu_table_conventions():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    channel = object.__new__(module.PSUChannel)
    defaults = module.PSUChannel.getDefaultChannel(channel)

    assert defaults[channel.MONITOR][module.Parameter.HEADER] == "Vmon"
    assert defaults[channel.ID][module.Parameter.HEADER] == "CH "
    assert defaults[channel.OUTPUT_STATE][module.Parameter.HEADER] == "On"
    assert defaults[channel.CURRENT_MONITOR][module.Parameter.HEADER] == "Imon"
    assert defaults[channel.SCALING][module.Parameter.VALUE] == "normal"


def test_channel_keeps_framework_bootstrap_parameters_in_displayed_list():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    channel = object.__new__(module.PSUChannel)
    channel.displayedParameters = []

    module.PSUChannel.setDisplayedParameters(channel)

    assert channel.VALUE in channel.displayedParameters
    assert channel.ENABLED in channel.displayedParameters
    assert channel.ACTIVE in channel.displayedParameters
    assert channel.OPTIMIZE not in channel.displayedParameters


def test_channel_display_order_prioritizes_psu_readbacks():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    channel = object.__new__(module.PSUChannel)
    channel.displayedParameters = []

    module.PSUChannel.setDisplayedParameters(channel)

    assert channel.displayedParameters[:9] == [
        "Collapse",
        "Select",
        "Name",
        "Output",
        "Voltage set",
        "Monitor",
        "Current set",
        "Current monitor",
        "CH",
    ]
    assert channel.displayedParameters[-1] == "Display"


def test_psu_output_state_badge_style_matches_on_off_states():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    assert "#1f2933" in module._psu_output_state_badge_style("ON")
    assert "#4a5568" in module._psu_output_state_badge_style("OFF")
    assert module._psu_output_state_badge_style("n/a") == module._PSU_NEUTRAL_WIDGET_STYLE


def test_device_column_visibility_hides_internal_columns_and_enables_manual_resize():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    class FakeHeader:
        class ResizeMode:
            Interactive = "Interactive"

        def __init__(self):
            self.resize_modes = []
            self.resized = []

        def setSectionResizeMode(self, *args):
            self.resize_modes.append(args)

        def resizeSection(self, index, width):
            self.resized.append((index, width))

    class FakeTree:
        def __init__(self):
            self.hidden_columns = []
            self._header = FakeHeader()

        def setColumnHidden(self, index, hidden):
            self.hidden_columns.append((index, hidden))

        def header(self):
            return self._header

    class FakeChannel:
        def getSortedDefaultChannel(self):
            return {
                "Collapse": {},
                "Select": {},
                "Enabled": {},
                "Name": {},
                "Value": {},
                "Monitor": {},
                "Display": {},
                "Active": {},
                "Real": {},
                "Equation": {},
                "Min": {},
                "Max": {},
                "CH": {},
                "Output": {},
                "Voltage set": {},
                "Current set": {},
                "Current monitor": {},
            }

    device = object.__new__(module.PSUDevice)
    device.tree = FakeTree()
    device.channels = [FakeChannel()]
    device.channelType = module.PSUChannel

    module.PSUDevice._update_channel_column_visibility(device)

    assert set(device.tree.hidden_columns) == {
        (0, True),
        (2, True),
        (4, True),
        (7, True),
        (8, True),
        (9, True),
        (10, True),
        (11, True),
    }
    assert device.tree._header.resize_modes == [
        (5, "Interactive"),
        (12, "Interactive"),
        (13, "Interactive"),
        (14, "Interactive"),
        (15, "Interactive"),
        (16, "Interactive"),
    ]
    assert device.tree._header.resized == [
        (5, 88),
        (12, 44),
        (13, 58),
        (14, 90),
        (15, 90),
        (16, 92),
    ]


def test_device_toggle_advanced_reapplies_column_visibility():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    device = object.__new__(module.PSUDevice)
    calls = []
    device._update_channel_column_visibility = lambda: calls.append(True)
    original_toggle_advanced = getattr(module.Device, "toggleAdvanced", None)
    module.Device.toggleAdvanced = lambda self, advanced=False: setattr(
        self, "super_toggle_advanced", advanced
    )
    try:
        module.PSUDevice.toggleAdvanced(device, advanced=True)
    finally:
        if original_toggle_advanced is None:
            delattr(module.Device, "toggleAdvanced")
        else:
            module.Device.toggleAdvanced = original_toggle_advanced

    assert device.super_toggle_advanced is True
    assert calls == [True]


def test_channel_real_changed_skips_framework_handler_until_enabled_exists():
    _clear_test_modules()
    _install_esibd_stubs()

    module = _import_plugin_module_from_path("psu_plugin_test", PLUGIN_PATH)

    class FakeParameter:
        def __init__(self):
            self.visible = None

        def setVisible(self, visible):
            self.visible = visible

    channel = object.__new__(module.PSUChannel)
    channel.ID = "CH"
    channel.OUTPUT_STATE = "Output"
    channel.VOLTAGE_SET = "Vset"
    channel.CURRENT_SET = "Iset"
    channel.CURRENT_MONITOR = "Imon"
    channel.ENABLED = "Enabled"
    channel.real = True

    parameters = {
        channel.ID: FakeParameter(),
        channel.OUTPUT_STATE: FakeParameter(),
        channel.VOLTAGE_SET: FakeParameter(),
        channel.CURRENT_SET: FakeParameter(),
        channel.CURRENT_MONITOR: FakeParameter(),
    }
    channel.getParameterByName = lambda name: parameters.get(name)

    module.PSUChannel.realChanged(channel)

    assert parameters[channel.ID].visible is True
    assert parameters[channel.OUTPUT_STATE].visible is True
    assert parameters[channel.VOLTAGE_SET].visible is True
    assert parameters[channel.CURRENT_SET].visible is True
    assert parameters[channel.CURRENT_MONITOR].visible is True
    assert not hasattr(channel, "base_real_changed_called")
