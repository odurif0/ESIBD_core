"""Drive PSU outputs from ESIBD Explorer and monitor live readbacks."""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

import numpy as np

from esibd.core import (
    PARAMETERTYPE,
    PLUGINTYPE,
    PRINT,
    Channel,
    DeviceController,
    Parameter,
    ToolButton,
    parameterDict,
)
from esibd.plugins import Device, Plugin

_BUNDLED_RUNTIME_DIRNAME = "runtime"
_BUNDLED_RUNTIME_NAMESPACE_PREFIX = "_esibd_bundled_psu_runtime"
_PSU_DRIVER_CLASS: type[Any] | None = None
_CHANNEL_NAME_KEY = getattr(Parameter, "NAME", getattr(Channel, "NAME", "Name"))
_CHANNEL_ENABLED_KEY = getattr(Channel, "ENABLED", "Enabled")
_CHANNEL_REAL_KEY = getattr(Channel, "REAL", "Real")
_PARAMETER_MIN_KEY = getattr(Parameter, "MIN", "Min")
_PARAMETER_MAX_KEY = getattr(Parameter, "MAX", "Max")
_PARAMETER_ADVANCED_KEY = getattr(Parameter, "ADVANCED", "Advanced")
_PARAMETER_TOOLTIP_KEY = getattr(Parameter, "TOOLTIP", "Tooltip")
_PARAMETER_EVENT_KEY = getattr(Parameter, "EVENT", "Event")
_PSU_CHANNEL_KEY = "CH"
_PSU_CHANNEL_IDS = (0, 1)
_PSU_POWER_ON_ICON = "switch-medium_on.png"
_PSU_POWER_OFF_ICON = "switch-medium_off.png"
_PSU_CHANNEL_ON_LABEL = "HV ON"
_PSU_CHANNEL_OFF_LABEL = "HV OFF"
_PSU_FLOAT_SENTINEL = -1


def _is_nan(value: Any) -> bool:
    try:
        return bool(np.isnan(value))
    except TypeError:
        return False


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default
    return bool(value)


def _format_current_text(current_a: Any) -> str:
    value = _coerce_float(current_a, np.nan)
    if _is_nan(value):
        return "n/a"
    return f"{value:.6g} A"


def _format_voltage_text(voltage_v: Any) -> str:
    value = _coerce_float(voltage_v, np.nan)
    if _is_nan(value):
        return "n/a"
    return f"{value:.6g} V"


def _channel_key_from_item(item: dict[str, Any]) -> int:
    return _coerce_int(item.get(_PSU_CHANNEL_KEY), 0)


def _generic_channel_name(device_name: str, channel_id: int) -> str:
    return f"{device_name}_CH{channel_id}"


def _build_generic_channel_item(
    device_name: str,
    channel_id: int,
    default_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = dict(default_item or {})
    item[_CHANNEL_NAME_KEY] = _generic_channel_name(device_name, channel_id)
    item[_PSU_CHANNEL_KEY] = str(channel_id)
    item[_CHANNEL_REAL_KEY] = True
    item[_CHANNEL_ENABLED_KEY] = False
    return item


def _looks_like_bootstrap_items(
    items: list[dict[str, Any]],
    device_name: str,
    default_item: dict[str, Any] | None = None,
) -> bool:
    if not items:
        return False

    expected_names = [f"{device_name}{index}" for index in range(1, len(items) + 1)]
    item_names = [str(item.get(_CHANNEL_NAME_KEY, "")) for item in items]
    if item_names != expected_names:
        return False

    if default_item is None:
        return all(_channel_key_from_item(item) == 0 for item in items)

    for item in items:
        for key, default_value in default_item.items():
            if key == _CHANNEL_NAME_KEY:
                continue
            item_value = item.get(key, default_value)
            if key == _PSU_CHANNEL_KEY:
                if _coerce_int(item_value, _coerce_int(default_value, 0)) != _coerce_int(
                    default_value,
                    0,
                ):
                    return False
                continue
            if isinstance(default_value, bool):
                if _coerce_bool(item_value, default=default_value) != default_value:
                    return False
                continue
            if _is_nan(default_value):
                if not _is_nan(item_value):
                    return False
                continue
            if isinstance(default_value, int) and not isinstance(default_value, bool):
                if _coerce_int(item_value, default_value) != default_value:
                    return False
                continue
            if isinstance(default_value, float):
                if _coerce_float(item_value, default_value) != default_value:
                    return False
                continue
            if item_value != default_value:
                return False
    return True


def _strip_legacy_bootstrap_residue(
    items: list[dict[str, Any]],
    device_name: str,
    default_item: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[str, PRINT | None]]]:
    if not items or default_item is None:
        return items, []

    default_key = _channel_key_from_item(default_item)
    residue_indices: list[int] = []
    residue_count = 0
    indexed_names = {
        str(item.get(_CHANNEL_NAME_KEY, "")): index
        for index, item in enumerate(items)
    }

    while True:
        residue_count += 1
        index = indexed_names.get(f"{device_name}{residue_count}")
        if index is None:
            residue_count -= 1
            break
        residue_indices.append(index)

    if residue_count < 2 or residue_count == len(items):
        return items, []

    residue_items = [items[index] for index in residue_indices]
    if any(_channel_key_from_item(item) != default_key for item in residue_items):
        return items, []

    cleaned_items = [
        item for index, item in enumerate(items) if index not in set(residue_indices)
    ]
    if not cleaned_items:
        return items, []

    return cleaned_items, [
        (
            f"Removed legacy PSU bootstrap channels: "
            f"{device_name}1..{device_name}{residue_count}",
            None,
        )
    ]


def _plan_channel_sync(
    current_items: list[dict[str, Any]],
    device_name: str,
    default_item: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[str, PRINT | None]]]:
    if _looks_like_bootstrap_items(current_items, device_name, default_item=default_item):
        return [
            _build_generic_channel_item(device_name, channel_id, default_item=default_item)
            for channel_id in _PSU_CHANNEL_IDS
        ], [("PSU bootstrap config replaced with fixed hardware channels.", None)]

    current_items, cleanup_logs = _strip_legacy_bootstrap_residue(
        current_items,
        device_name=device_name,
        default_item=default_item,
    )

    target_ids = set(_PSU_CHANNEL_IDS)
    kept_keys: set[int] = set()
    added_channels: list[int] = []
    virtualized_channels: list[int] = []
    reactivated_channels: list[int] = []
    duplicate_entries: list[tuple[str, int]] = []
    synced_items: list[dict[str, Any]] = []

    for item in current_items:
        synced_item = dict(item)
        channel_id = _channel_key_from_item(synced_item)
        if channel_id in kept_keys:
            duplicate_entries.append(
                (str(synced_item.get(_CHANNEL_NAME_KEY, "")), channel_id)
            )
            synced_item[_CHANNEL_REAL_KEY] = False
            synced_items.append(synced_item)
            continue

        kept_keys.add(channel_id)
        if channel_id in target_ids:
            if not _coerce_bool(synced_item.get(_CHANNEL_REAL_KEY), default=True):
                reactivated_channels.append(channel_id)
            synced_item[_CHANNEL_REAL_KEY] = True
        else:
            if _coerce_bool(synced_item.get(_CHANNEL_REAL_KEY), default=True):
                virtualized_channels.append(channel_id)
            synced_item[_CHANNEL_REAL_KEY] = False
        synced_items.append(synced_item)

    for channel_id in _PSU_CHANNEL_IDS:
        if channel_id in kept_keys:
            continue
        synced_items.append(
            _build_generic_channel_item(
                device_name,
                channel_id,
                default_item=default_item,
            )
        )
        added_channels.append(channel_id)

    log_entries: list[tuple[str, PRINT | None]] = list(cleanup_logs)
    if added_channels:
        log_entries.append(
            (
                "Added generic PSU channels: "
                + ", ".join(f"CH{channel_id}" for channel_id in added_channels),
                None,
            )
        )
    if virtualized_channels:
        log_entries.append(
            (
                "Marked PSU channels virtual because they do not exist on hardware: "
                + ", ".join(f"CH{channel_id}" for channel_id in virtualized_channels),
                None,
            )
        )
    if reactivated_channels:
        log_entries.append(
            (
                "Reactivated PSU channels: "
                + ", ".join(f"CH{channel_id}" for channel_id in reactivated_channels),
                None,
            )
        )
    for channel_name, channel_id in duplicate_entries:
        log_entries.append(
            (
                f"Duplicate PSU mapping detected for CH{channel_id}: {channel_name}",
                PRINT.WARNING,
            )
        )
    return synced_items, log_entries


def _bundled_runtime_module_name(plugin_dir: Path | None = None) -> str:
    resolved_plugin_dir = Path(__file__).resolve().parent if plugin_dir is None else plugin_dir
    plugin_key = resolved_plugin_dir.name.replace("-", "_")
    return f"{_BUNDLED_RUNTIME_NAMESPACE_PREFIX}_{plugin_key}"


def _load_private_runtime_package(module_name: str, package_dir: Path) -> None:
    if module_name in sys.modules:
        return

    init_file = package_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_file,
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(
            f"Could not create an import spec for bundled PSU runtime at {package_dir}."
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise


def _get_psu_driver_class() -> type[Any]:
    global _PSU_DRIVER_CLASS

    if _PSU_DRIVER_CLASS is not None:
        return _PSU_DRIVER_CLASS

    plugin_dir = Path(__file__).resolve().parent
    bundled_runtime_dir = plugin_dir / "vendor" / _BUNDLED_RUNTIME_DIRNAME
    bundled_runtime_init = bundled_runtime_dir / "__init__.py"
    if not bundled_runtime_init.exists():
        raise ModuleNotFoundError(
            "Bundled PSU runtime not found in vendor/runtime; "
            "plugin installation is incomplete."
        )

    runtime_module_name = _bundled_runtime_module_name(plugin_dir)
    _load_private_runtime_package(runtime_module_name, bundled_runtime_dir)
    module = importlib.import_module(f"{runtime_module_name}.psu")
    _PSU_DRIVER_CLASS = cast(type[Any], module.PSU)
    return _PSU_DRIVER_CLASS


def providePlugins() -> "list[type[Plugin]]":
    return [PSUDevice]


class PSUDevice(Device):
    """Drive the PSU through validated configs and monitor readbacks."""

    documentation = (
        "Loads validated PSU configurations and monitors live voltage/current readbacks."
    )

    name = "PSU"
    version = "0.1.0"
    supportedVersion = "0.8"
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = "V"
    useMonitors = True
    useOnOffLogic = True
    iconFile = "psu.png"
    channels: "list[PSUChannel]"

    COM = "COM"
    BAUDRATE = "Baud rate"
    CONNECT_TIMEOUT = "Connect timeout (s)"
    STARTUP_TIMEOUT = "Startup timeout (s)"
    POLL_TIMEOUT = "Poll timeout (s)"
    STANDBY_CONFIG = "Standby config"
    OPERATING_CONFIG = "Operating config"
    SHUTDOWN_CONFIG = "Shutdown config"
    STATE = "State"
    OUTPUTS = "Outputs"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = PSUChannel

    def initGUI(self) -> None:
        super().initGUI()
        self.controller = PSUController(controllerParent=self)

    def getChannels(self) -> "list[PSUChannel]":
        return cast("list[PSUChannel]", super().getChannels())

    com: int
    baudrate: int
    connect_timeout_s: float
    startup_timeout_s: float
    poll_timeout_s: float
    standby_config: int
    operating_config: int
    shutdown_config: int
    main_state: str
    output_summary: str

    def _current_channel_items(self) -> list[dict[str, Any]]:
        return [channel.asDict() for channel in self.getChannels()]

    def _default_channel_item(self) -> dict[str, Any]:
        return self.channelType(channelParent=self, tree=None).asDict()

    def _apply_channel_items(self, items: list[dict[str, Any]]) -> None:
        update_channel_config = getattr(self, "updateChannelConfig", None)
        export_config = getattr(self, "exportConfiguration", None)
        custom_config_file = getattr(self, "customConfigFile", None)
        config_name = getattr(self, "confINI", None)
        if not callable(update_channel_config) or not callable(custom_config_file):
            return

        config_file = custom_config_file(config_name)
        self.loading = True
        try:
            update_channel_config(items, config_file)
        finally:
            self.loading = False
        if callable(export_config):
            export_config(useDefaultFile=True)

    def _sync_channels(self) -> bool:
        current_items = self._current_channel_items()
        target_items, log_entries = _plan_channel_sync(
            current_items=current_items,
            device_name=self.name,
            default_item=self._default_channel_item(),
        )
        if target_items == current_items:
            return False
        self._apply_channel_items(target_items)
        for message, flag in log_entries:
            if flag is None:
                self.print(message)
            else:
                self.print(message, flag=flag)
        return True

    def getDefaultSettings(self) -> dict[str, dict]:
        settings = super().getDefaultSettings()
        settings[f"{self.name}/{self.COM}"] = parameterDict(
            value=1,
            minimum=1,
            maximum=255,
            toolTip="Windows COM port number used by the PSU controller.",
            parameterType=PARAMETERTYPE.INT,
            attr="com",
        )
        settings[f"{self.name}/{self.BAUDRATE}"] = parameterDict(
            value=230400,
            minimum=1,
            maximum=1_000_000,
            toolTip="Baud rate passed to cgc.psu.PSU.",
            parameterType=PARAMETERTYPE.INT,
            attr="baudrate",
        )
        settings[f"{self.name}/{self.CONNECT_TIMEOUT}"] = parameterDict(
            value=5.0,
            minimum=1.0,
            maximum=60.0,
            toolTip="Timeout in seconds used to connect and validate the PSU transport.",
            parameterType=PARAMETERTYPE.FLOAT,
            attr="connect_timeout_s",
        )
        settings[f"{self.name}/{self.STARTUP_TIMEOUT}"] = parameterDict(
            value=10.0,
            minimum=1.0,
            maximum=120.0,
            toolTip="Timeout in seconds used for PSU startup and shutdown sequences.",
            parameterType=PARAMETERTYPE.FLOAT,
            attr="startup_timeout_s",
        )
        settings[f"{self.name}/{self.POLL_TIMEOUT}"] = parameterDict(
            value=5.0,
            minimum=0.5,
            maximum=30.0,
            toolTip="Timeout in seconds used to poll PSU housekeeping.",
            parameterType=PARAMETERTYPE.FLOAT,
            attr="poll_timeout_s",
        )
        settings[f"{self.name}/{self.STANDBY_CONFIG}"] = parameterDict(
            value=1,
            minimum=_PSU_FLOAT_SENTINEL,
            maximum=255,
            toolTip="Startup standby config index. Use -1 to skip config loading.",
            parameterType=PARAMETERTYPE.INT,
            attr="standby_config",
        )
        settings[f"{self.name}/{self.OPERATING_CONFIG}"] = parameterDict(
            value=-1,
            minimum=_PSU_FLOAT_SENTINEL,
            maximum=255,
            toolTip="Optional operating config index applied after standby. Use -1 to skip.",
            parameterType=PARAMETERTYPE.INT,
            attr="operating_config",
        )
        settings[f"{self.name}/{self.SHUTDOWN_CONFIG}"] = parameterDict(
            value=-1,
            minimum=_PSU_FLOAT_SENTINEL,
            maximum=255,
            toolTip="Optional shutdown config index. Use -1 to disable config-based shutdown.",
            parameterType=PARAMETERTYPE.INT,
            attr="shutdown_config",
        )
        settings[f"{self.name}/{self.STATE}"] = parameterDict(
            value="Disconnected",
            toolTip="Latest PSU controller state reported by the driver.",
            parameterType=PARAMETERTYPE.LABEL,
            attr="main_state",
            indicator=True,
            internal=True,
            restore=False,
        )
        settings[f"{self.name}/{self.OUTPUTS}"] = parameterDict(
            value="CH0=OFF, CH1=OFF",
            toolTip="Latest PSU output enable summary.",
            parameterType=PARAMETERTYPE.LABEL,
            attr="output_summary",
            indicator=True,
            internal=True,
            advanced=True,
            restore=False,
        )
        settings[f"{self.name}/Interval"][Parameter.VALUE] = 1000
        settings[f"{self.name}/{self.MAXDATAPOINTS}"][Parameter.VALUE] = 100000
        return settings

    def _set_on_ui_state(self, on: bool) -> None:
        if hasattr(self, "onAction"):
            self.onAction.state = bool(on)

    def closeCommunication(self) -> None:
        controller = getattr(self, "controller", None)
        if controller and getattr(controller, "initialized", False):
            self.shutdownCommunication()
            return
        if hasattr(self, "onAction"):
            self.onAction.state = False
        if controller:
            controller.closeCommunication()

    def shutdownCommunication(self) -> None:
        if hasattr(self, "onAction"):
            self.onAction.state = False
        controller = getattr(self, "controller", None)
        if controller:
            controller.shutdownCommunication()

    def setOn(self, on: "bool | None" = None) -> None:
        controller = getattr(self, "controller", None)
        current_state = self.isOn() if hasattr(self, "isOn") else False
        if controller and (
            getattr(controller, "initializing", False)
            or getattr(controller, "transitioning", False)
        ):
            if hasattr(self, "onAction"):
                self.onAction.state = current_state
            self.print(
                f"{self.name} ON/OFF transition already in progress; ignoring additional request.",
                flag=PRINT.WARNING,
            )
            return

        if on is not None and hasattr(self, "onAction"):
            self.onAction.state = bool(on)
        if getattr(self, "loading", False):
            return

        if controller and getattr(controller, "initialized", False):
            begin_transition = getattr(controller, "_begin_transition", None)
            can_start = not callable(begin_transition) or begin_transition(self.isOn())
            if can_start:
                toggle_thread = getattr(controller, "toggleOnFromThread", None)
                if callable(toggle_thread):
                    toggle_thread(parallel=True)
                else:
                    controller.toggleOn()


class PSUChannel(Channel):
    """PSU output channel definition."""

    ID = "CH"
    OUTPUT_STATE = "Output"
    VOLTAGE_SET = "Voltage set"
    CURRENT_SET = "Current set"
    CURRENT_MONITOR = "Current monitor"
    channelParent: PSUDevice

    def getDefaultChannel(self) -> dict[str, dict]:
        self.id: int
        self.output_state: str
        self.voltage_set: str
        self.current_set: str
        self.current_monitor: str

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = "Reference"
        channel[self.VALUE][_PARAMETER_ADVANCED_KEY] = True
        channel[self.VALUE][_PARAMETER_TOOLTIP_KEY] = (
            "Unused by the PSU plugin. The plugin is config-driven and displays "
            "controller readbacks instead of applying channel setpoints."
        )
        channel[self.ENABLED][_PARAMETER_ADVANCED_KEY] = True
        channel[self.ACTIVE][_PARAMETER_ADVANCED_KEY] = True
        channel[self.DISPLAY][Parameter.HEADER] = "Display"
        channel[self.DISPLAY][_PARAMETER_EVENT_KEY] = self.displayChanged
        channel[self.SCALING][Parameter.VALUE] = "large"
        channel[self.ID] = parameterDict(
            value="0",
            parameterType=PARAMETERTYPE.LABEL,
            advanced=False,
            indicator=True,
            header="CH",
            attr="id",
        )
        channel[self.OUTPUT_STATE] = parameterDict(
            value="OFF",
            parameterType=PARAMETERTYPE.LABEL,
            advanced=False,
            indicator=True,
            header="Out",
            attr="output_state",
            toolTip="Latest PSU output enable readback for this channel.",
        )
        channel[self.VOLTAGE_SET] = parameterDict(
            value="n/a",
            parameterType=PARAMETERTYPE.LABEL,
            advanced=False,
            indicator=True,
            header="Vset",
            attr="voltage_set",
            toolTip="Configured PSU voltage setpoint read back from the controller.",
        )
        channel[self.CURRENT_SET] = parameterDict(
            value="n/a",
            parameterType=PARAMETERTYPE.LABEL,
            advanced=False,
            indicator=True,
            header="Iset",
            attr="current_set",
            toolTip="Configured PSU current setpoint read back from the controller.",
        )
        channel[self.CURRENT_MONITOR] = parameterDict(
            value="n/a",
            parameterType=PARAMETERTYPE.LABEL,
            advanced=False,
            indicator=True,
            header="Imon",
            attr="current_monitor",
            toolTip="Measured PSU output current read back from the controller.",
        )
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        displayed = getattr(self, "displayedParameters", [])
        for parameter_name in (self.OPTIMIZE, self.VALUE, self.ENABLED, self.ACTIVE):
            if parameter_name in displayed:
                displayed.remove(parameter_name)
        displayed.extend(
            [
                self.ID,
                self.OUTPUT_STATE,
                self.VOLTAGE_SET,
                self.CURRENT_SET,
                self.CURRENT_MONITOR,
            ]
        )

    def channel_number(self) -> int:
        return _coerce_int(self.id, 0)

    def _set_parameter_value_without_events(self, parameter_name: str, value: Any) -> bool:
        getter = getattr(self, "getParameterByName", None)
        if not callable(getter):
            return False
        parameter = getter(parameter_name)
        if parameter is None:
            return False
        current_value = getattr(parameter, "value", None)
        if current_value == value:
            return False
        setter = getattr(parameter, "setValueWithoutEvents", None)
        if callable(setter):
            setter(value)
        else:
            parameter.value = value
        return True

    def displayChanged(self) -> None:
        update_display = getattr(super(), "updateDisplay", None)
        if callable(update_display):
            update_display()

    def realChanged(self) -> None:
        getter = getattr(self, "getParameterByName", None)
        if callable(getter):
            for parameter_name in (
                self.ID,
                self.OUTPUT_STATE,
                self.VOLTAGE_SET,
                self.CURRENT_SET,
                self.CURRENT_MONITOR,
            ):
                parameter = getter(parameter_name)
                if parameter is not None and hasattr(parameter, "setVisible"):
                    parameter.setVisible(self.real)
        real_changed = getattr(super(), "realChanged", None)
        if callable(real_changed):
            real_changed()

    def setCurrentMonitorText(self, text: str) -> None:
        self._set_parameter_value_without_events(self.CURRENT_MONITOR, text)

    def setOutputStateText(self, text: str) -> None:
        self._set_parameter_value_without_events(self.OUTPUT_STATE, text)

    def setVoltageSetText(self, text: str) -> None:
        self._set_parameter_value_without_events(self.VOLTAGE_SET, text)

    def setCurrentSetText(self, text: str) -> None:
        self._set_parameter_value_without_events(self.CURRENT_SET, text)


class PSUController(DeviceController):
    """PSU hardware controller used by the ESIBD Explorer plugin."""

    controllerParent: PSUDevice

    def __init__(self, controllerParent) -> None:
        super().__init__(controllerParent=controllerParent)
        self.device: Any | None = None
        self.main_state = "Disconnected"
        self.output_state_summary = "CH0=OFF, CH1=OFF"
        self.device_state_summary = "n/a"
        self.initialized = False
        self.transitioning = False
        self.transition_target_on: bool | None = None
        self.values: dict[int, float] = {}
        self.current_values: dict[int, float] = {}
        self.output_enabled_by_channel: dict[int, bool] = {}
        self.voltage_setpoints: dict[int, str] = {}
        self.current_setpoints: dict[int, str] = {}

    def initializeValues(self, reset: bool = False) -> None:
        if getattr(self, "values", None) is None or reset:
            self.values = {
                channel.channel_number(): np.nan
                for channel in self.controllerParent.getChannels()
                if channel.real
            }
            self.current_values = {
                channel.channel_number(): np.nan
                for channel in self.controllerParent.getChannels()
                if channel.real
            }
            self.output_enabled_by_channel = {
                channel.channel_number(): False
                for channel in self.controllerParent.getChannels()
                if channel.real
            }
            self.voltage_setpoints = {
                channel.channel_number(): "n/a"
                for channel in self.controllerParent.getChannels()
                if channel.real
            }
            self.current_setpoints = {
                channel.channel_number(): "n/a"
                for channel in self.controllerParent.getChannels()
                if channel.real
            }

    def runInitialization(self) -> None:
        self.initialized = False
        self._dispose_device()
        try:
            driver_class = _get_psu_driver_class()
            self.device = driver_class(
                device_id=f"{self.controllerParent.name.lower()}_com{int(self.controllerParent.com)}",
                com=int(self.controllerParent.com),
                baudrate=int(self.controllerParent.baudrate),
            )
            backend_reason = str(
                getattr(self.device, "_process_backend_disabled_reason", "")
            ).strip()
            if backend_reason:
                self.print(backend_reason, flag=PRINT.WARNING)
            self.device.connect(timeout_s=float(self.controllerParent.connect_timeout_s))
            self._update_state()
            self.signalComm.initCompleteSignal.emit()
        except Exception as exc:  # noqa: BLE001
            self._restore_off_ui_state()
            self.print(
                f"PSU initialization failed on COM{int(self.controllerParent.com)}: "
                f"{self._format_exception(exc)}",
                flag=PRINT.ERROR,
            )
            self._dispose_device()
        finally:
            self.initializing = False

    def initComplete(self) -> None:
        if self.device is not None:
            self.controllerParent._sync_channels()
        self.initializeValues(reset=True)
        self.initialized = True
        self.super_init_complete_called = True
        self._sync_status_to_gui()

    def _startup_kwargs(self) -> dict[str, Any]:
        standby_config = _coerce_int(
            getattr(self.controllerParent, "standby_config", -1),
            -1,
        )
        operating_config = _coerce_int(
            getattr(self.controllerParent, "operating_config", -1),
            -1,
        )
        kwargs: dict[str, Any] = {}
        if standby_config >= 0:
            kwargs["standby_config"] = standby_config
        if operating_config >= 0:
            kwargs["operating_config"] = operating_config
        return kwargs

    def _shutdown_kwargs(self) -> dict[str, Any]:
        shutdown_config = _coerce_int(
            getattr(self.controllerParent, "shutdown_config", -1),
            -1,
        )
        if shutdown_config >= 0:
            return {
                "standby_config": shutdown_config,
                "disable_outputs": False,
                "disable_device": False,
            }
        return {}

    def readNumbers(self) -> None:
        if self.device is None or not getattr(self, "initialized", False):
            self.initializeValues(reset=True)
            return

        timeout_s = float(getattr(self.controllerParent, "poll_timeout_s", 5.0))
        try:
            with self._controller_lock_section(
                "Could not acquire lock to read PSU housekeeping."
            ):
                device = self.device
                if device is None:
                    return
                snapshot = device.collect_housekeeping(timeout_s=timeout_s)
        except TimeoutError:
            self.errorCount += 1
            self.print("Timed out while polling PSU housekeeping.", flag=PRINT.ERROR)
            self.initializeValues(reset=True)
            return
        except Exception as exc:  # noqa: BLE001
            self.errorCount += 1
            self.print(
                f"Failed to read PSU housekeeping: {self._format_exception(exc)}",
                flag=PRINT.ERROR,
            )
            self.initializeValues(reset=True)
            return

        self._apply_snapshot(snapshot)

    def _apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.main_state = str(
            snapshot.get("main_state", {}).get("name", "Unknown")
        )
        flags = snapshot.get("device_state", {}).get("flags", [])
        self.device_state_summary = ", ".join(str(flag) for flag in flags) if flags else "OK"
        output_enabled = tuple(snapshot.get("output_enabled", (False, False)))
        self.output_state_summary = ", ".join(
            f"CH{index}={'ON' if bool(enabled) else 'OFF'}"
            for index, enabled in enumerate(output_enabled)
        )

        measured_voltages: dict[int, float] = {}
        measured_currents: dict[int, float] = {}
        output_enabled_map: dict[int, bool] = {}
        voltage_setpoints: dict[int, str] = {}
        current_setpoints: dict[int, str] = {}
        for channel_snapshot in snapshot.get("channels", []):
            channel_no = _coerce_int(channel_snapshot.get("channel"), -1)
            if channel_no < 0:
                continue
            output_enabled_map[channel_no] = bool(channel_snapshot.get("enabled", False))
            measured_voltages[channel_no] = _coerce_float(
                channel_snapshot.get("voltage", {}).get("measured_v"),
                np.nan,
            )
            measured_currents[channel_no] = _coerce_float(
                channel_snapshot.get("current", {}).get("measured_a"),
                np.nan,
            )
            voltage_setpoints[channel_no] = _format_voltage_text(
                channel_snapshot.get("voltage", {}).get("set_v")
            )
            current_setpoints[channel_no] = _format_current_text(
                channel_snapshot.get("current", {}).get("set_a")
            )

        self.values = measured_voltages
        self.current_values = measured_currents
        self.output_enabled_by_channel = output_enabled_map
        self.voltage_setpoints = voltage_setpoints
        self.current_setpoints = current_setpoints
        self._sync_status_to_gui()

    def updateValues(self) -> None:
        if self.values is None:
            return

        self._sync_status_to_gui()
        for channel in self.controllerParent.getChannels():
            channel_no = channel.channel_number()
            if channel.real:
                channel.monitor = self.values.get(channel_no, np.nan)
                channel.setCurrentMonitorText(
                    _format_current_text(self.current_values.get(channel_no, np.nan))
                )
                channel.setOutputStateText(
                    "ON" if self.output_enabled_by_channel.get(channel_no, False) else "OFF"
                )
                channel.setVoltageSetText(self.voltage_setpoints.get(channel_no, "n/a"))
                channel.setCurrentSetText(self.current_setpoints.get(channel_no, "n/a"))
                channel._set_parameter_value_without_events(
                    channel.ENABLED,
                    self.output_enabled_by_channel.get(channel_no, False),
                )
                continue
            channel.monitor = np.nan
            channel.setCurrentMonitorText("n/a")
            channel.setOutputStateText("n/a")
            channel.setVoltageSetText("n/a")
            channel.setCurrentSetText("n/a")
            channel._set_parameter_value_without_events(channel.ENABLED, False)

    def toggleOn(self) -> None:
        target_on = bool(getattr(self.controllerParent, "isOn", lambda: False)())
        device = self.device
        if device is None:
            self._end_transition()
            return

        stop_acquisition = getattr(self, "stopAcquisition", None)
        if callable(stop_acquisition):
            stop_acquisition()
            self.acquiring = False

        timeout_s = float(getattr(self.controllerParent, "startup_timeout_s", 10.0))

        try:
            if target_on:
                with self._controller_lock_section(
                    "Could not acquire lock to start the PSU."
                ):
                    device = self.device
                    if device is None:
                        self._restore_off_ui_state()
                        return
                    startup_kwargs = self._startup_kwargs()
                    if not startup_kwargs:
                        raise RuntimeError(
                            "PSU plugin requires a standby and/or operating config. "
                            "Configure startup slots instead of editing live setpoints."
                        )
                    device.initialize(timeout_s=timeout_s, **startup_kwargs)
                self._update_state()
                start_acquisition = getattr(self, "startAcquisition", None)
                if callable(start_acquisition):
                    start_acquisition()
                self.print("PSU startup sequence completed from controller configs.")
            else:
                self.shutdownCommunication()
                return
        except Exception as exc:  # noqa: BLE001
            self.errorCount += 1
            if target_on:
                self._restore_off_ui_state()
            self.print(
                f"Failed to toggle PSU: {self._format_exception(exc)}",
                flag=PRINT.ERROR,
            )
        finally:
            self._end_transition()
            self._sync_status_to_gui()

    def shutdownCommunication(self) -> None:
        device = self.device
        if device is None:
            self.closeCommunication()
            return

        stop_acquisition = getattr(self, "stopAcquisition", None)
        if callable(stop_acquisition):
            stop_acquisition()
            self.acquiring = False
        self.print("Starting PSU shutdown sequence.")
        try:
            device.shutdown(
                timeout_s=float(getattr(self.controllerParent, "startup_timeout_s", 10.0)),
                **self._shutdown_kwargs(),
            )
        except Exception as exc:  # noqa: BLE001
            self.errorCount += 1
            self.print(
                f"PSU shutdown failed: {self._format_exception(exc)}",
                flag=PRINT.ERROR,
            )
        else:
            self.print("PSU shutdown sequence completed.")
        finally:
            self.closeCommunication()

    def closeCommunication(self) -> None:
        base_close = getattr(super(), "closeCommunication", None)
        if callable(base_close):
            base_close()
        self.main_state = "Disconnected"
        self.output_state_summary = "CH0=OFF, CH1=OFF"
        self.device_state_summary = "n/a"
        self.initializeValues(reset=True)
        self._sync_status_to_gui()
        self._dispose_device()
        self.initialized = False

    def _update_state(self) -> None:
        device = self.device
        if device is None:
            self.main_state = "Disconnected"
            self.output_state_summary = "CH0=OFF, CH1=OFF"
            self.device_state_summary = "n/a"
            return

        try:
            snapshot = device.collect_housekeeping(
                timeout_s=float(getattr(self.controllerParent, "poll_timeout_s", 5.0))
            )
        except Exception:
            try:
                self.main_state = str(device.get_status().get("connected", False))
            except Exception:
                self.main_state = "Unknown"
            self.device_state_summary = "Unknown"
            self.output_state_summary = "Unknown"
            return

        self._apply_snapshot(snapshot)

    def _sync_status_to_gui(self) -> None:
        self.controllerParent.main_state = self.main_state
        self.controllerParent.output_summary = self.output_state_summary

    def _dispose_device(self) -> None:
        device = self.device
        self.device = None
        self.initialized = False
        if device is None:
            return
        try:
            device.disconnect()
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                device.close()

    def _restore_off_ui_state(self) -> None:
        sync_on_state = getattr(self.controllerParent, "_set_on_ui_state", None)
        if callable(sync_on_state):
            sync_on_state(False)
            return
        if hasattr(self.controllerParent, "onAction"):
            self.controllerParent.onAction.state = False

    @contextlib.contextmanager
    def _controller_lock_section(self, timeout_message: str):
        acquire_timeout = getattr(self.lock, "acquire_timeout", None)
        if callable(acquire_timeout):
            with acquire_timeout(1, timeoutMessage=timeout_message) as lock_acquired:
                if not lock_acquired:
                    raise TimeoutError(timeout_message)
                yield
            return

        acquire = getattr(self.lock, "acquire", None)
        release = getattr(self.lock, "release", None)
        if callable(acquire) and callable(release):
            if not acquire(timeout=1):
                self.print(timeout_message, flag=PRINT.ERROR)
                raise TimeoutError(timeout_message)
            try:
                yield
            finally:
                release()
            return

        raise TypeError(
            "PSU controller lock must provide either acquire_timeout() or acquire()/release()."
        )

    def _begin_transition(self, target_on: bool) -> bool:
        if self.transitioning:
            return False
        self.transitioning = True
        self.transition_target_on = bool(target_on)
        return True

    def _end_transition(self) -> None:
        self.transitioning = False
        self.transition_target_on = None

    def _format_exception(self, exc: Exception) -> str:
        return str(exc).strip()
