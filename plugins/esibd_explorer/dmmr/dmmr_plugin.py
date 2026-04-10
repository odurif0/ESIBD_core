"""Read DMMR module currents and monitor live picoammeter measurements."""

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
_BUNDLED_RUNTIME_NAMESPACE_PREFIX = "_esibd_bundled_dmmr_runtime"
_DMMR_DRIVER_CLASS: type[Any] | None = None
_CHANNEL_NAME_KEY = getattr(Parameter, "NAME", getattr(Channel, "NAME", "Name"))
_CHANNEL_ENABLED_KEY = getattr(Channel, "ENABLED", "Enabled")
_CHANNEL_REAL_KEY = getattr(Channel, "REAL", "Real")
_PARAMETER_ADVANCED_KEY = getattr(Parameter, "ADVANCED", "Advanced")
_PARAMETER_TOOLTIP_KEY = getattr(Parameter, "TOOLTIP", "Tooltip")
_PARAMETER_EVENT_KEY = getattr(Parameter, "EVENT", "Event")
_DMMR_MODULE_KEY = "Module"
_DMMR_MIN_ROW_HEIGHT = 28
_DMMR_POWER_ON_ICON = "switch-medium_on.png"
_DMMR_POWER_OFF_ICON = "switch-medium_off.png"


def _is_nan(value: Any) -> bool:
    """Return True when a value is NaN-like."""
    try:
        return bool(np.isnan(value))
    except TypeError:
        return False


def _coerce_int(value: Any, default: int) -> int:
    """Return an integer value from config-like input."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    """Return a float value from config-like input."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Return a boolean value from config-like input."""
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


def _compact_status_text(value: Any, default: str = "n/a") -> str:
    """Return a short one-line representation for toolbar status widgets."""
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) <= 1:
        return text
    return f"{parts[0]} +{len(parts) - 1}"


def _action_label(action: Any) -> str:
    """Extract a stable label from QAction-like objects and test doubles."""
    for attr_name in ("toolTip", "text", "objectName"):
        attr = getattr(action, attr_name, None)
        value = attr() if callable(attr) else attr
        if isinstance(value, str) and value:
            return value
    return ""


def _format_si_current(value_amps: Any) -> tuple[str, str]:
    """Format a current stored in amps using a readable SI prefix."""
    value = _coerce_float(value_amps, np.nan)
    if _is_nan(value):
        return ("NaN", "NaN")

    abs_value = abs(value)
    if abs_value == 0:
        scaled_value, unit = 0.0, "A"
    elif abs_value >= 1.0:
        scaled_value, unit = value, "A"
    elif abs_value >= 1e-3:
        scaled_value, unit = value * 1e3, "mA"
    elif abs_value >= 1e-6:
        scaled_value, unit = value * 1e6, "uA"
    elif abs_value >= 1e-9:
        scaled_value, unit = value * 1e9, "nA"
    elif abs_value >= 1e-12:
        scaled_value, unit = value * 1e12, "pA"
    else:
        scaled_value, unit = value * 1e15, "fA"

    if scaled_value == 0:
        number_text = "0"
    else:
        number_text = f"{scaled_value:.3f}".rstrip("0").rstrip(".")
    return (f"{number_text} {unit}", f"{value:.6e} A")


def _module_key_from_item(item: dict[str, Any]) -> int:
    """Return the physical DMMR module addressed by one channel item."""
    return _coerce_int(item.get(_DMMR_MODULE_KEY), 0)


def _generic_channel_name(device_name: str, module: int) -> str:
    """Generate a stable generic channel name from the physical mapping."""
    return f"{device_name}_M{module:02d}"


def _build_generic_channel_item(
    device_name: str,
    module: int,
    default_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a generic channel config for a newly detected DMMR module."""
    item = dict(default_item or {})
    item[_CHANNEL_NAME_KEY] = _generic_channel_name(device_name, module)
    item[_DMMR_MODULE_KEY] = str(module)
    item[_CHANNEL_REAL_KEY] = True
    item[_CHANNEL_ENABLED_KEY] = True
    return item


def _looks_like_bootstrap_items(
    items: list[dict[str, Any]],
    device_name: str,
    default_item: dict[str, Any] | None = None,
) -> bool:
    """Detect the default auto-generated ESIBD channel bootstrap."""
    if not items:
        return False

    expected_names = [f"{device_name}{index}" for index in range(1, len(items) + 1)]
    item_names = [str(item.get(_CHANNEL_NAME_KEY, "")) for item in items]
    if item_names != expected_names:
        return False

    if default_item is None:
        return all(_module_key_from_item(item) == 0 for item in items)

    for item in items:
        for key, default_value in default_item.items():
            if key == _CHANNEL_NAME_KEY:
                continue
            item_value = item.get(key, default_value)
            if key == _DMMR_MODULE_KEY:
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
    """Remove stale DMMR1..N bootstrap channels from polluted configs."""
    if not items or default_item is None:
        return items, []

    default_key = _module_key_from_item(default_item)
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
    if any(_module_key_from_item(item) != default_key for item in residue_items):
        return items, []

    cleaned_items = [
        item for index, item in enumerate(items) if index not in set(residue_indices)
    ]
    if not cleaned_items:
        return items, []

    return cleaned_items, [
        (
            f"Removed legacy DMMR bootstrap channels: "
            f"{device_name}1..{device_name}{residue_count}",
            None,
        )
    ]


def _plan_channel_sync(
    current_items: list[dict[str, Any]],
    detected_modules: list[int],
    device_name: str,
    default_item: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[str, PRINT | None]]]:
    """Return the target channel config and corresponding sync log entries."""
    detected_modules = sorted({_coerce_int(module, -1) for module in detected_modules if _coerce_int(module, -1) >= 0})
    if not detected_modules:
        return current_items, []

    if _looks_like_bootstrap_items(current_items, device_name, default_item=default_item):
        bootstrap_items = [
            _build_generic_channel_item(
                device_name,
                module,
                default_item=default_item,
            )
            for module in detected_modules
        ]
        return bootstrap_items, [
            ("DMMR bootstrap config replaced from hardware scan.", None)
        ]

    current_items, cleanup_logs = _strip_legacy_bootstrap_residue(
        current_items,
        device_name=device_name,
        default_item=default_item,
    )

    detected_set = set(detected_modules)
    kept_keys: set[int] = set()
    added_modules: set[int] = set()
    virtualized_modules: set[int] = set()
    reactivated_modules: set[int] = set()
    duplicate_entries: list[tuple[str, int]] = []
    synced_items: list[dict[str, Any]] = []

    for item in current_items:
        synced_item = dict(item)
        module = _module_key_from_item(synced_item)
        if module in kept_keys:
            duplicate_entries.append((str(synced_item.get(_CHANNEL_NAME_KEY, "")), module))
            synced_item[_CHANNEL_REAL_KEY] = False
            synced_items.append(synced_item)
            continue

        kept_keys.add(module)
        if module in detected_set:
            if not _coerce_bool(synced_item.get(_CHANNEL_REAL_KEY), default=True):
                reactivated_modules.add(module)
            synced_item[_CHANNEL_REAL_KEY] = True
        else:
            if _coerce_bool(synced_item.get(_CHANNEL_REAL_KEY), default=True):
                virtualized_modules.add(module)
            synced_item[_CHANNEL_REAL_KEY] = False
        synced_items.append(synced_item)

    for module in detected_modules:
        if module in kept_keys:
            continue
        synced_items.append(
            _build_generic_channel_item(
                device_name,
                module,
                default_item=default_item,
            )
        )
        added_modules.add(module)

    log_entries: list[tuple[str, PRINT | None]] = list(cleanup_logs)
    if added_modules:
        log_entries.append(
            (
                "Added generic DMMR channels for detected modules: "
                + ", ".join(str(module) for module in sorted(added_modules)),
                None,
            )
        )
    if virtualized_modules:
        log_entries.append(
            (
                "Marked DMMR channels virtual because modules are absent: "
                + ", ".join(str(module) for module in sorted(virtualized_modules)),
                None,
            )
        )
    if reactivated_modules:
        log_entries.append(
            (
                "Reactivated DMMR channels for modules: "
                + ", ".join(str(module) for module in sorted(reactivated_modules)),
                None,
            )
        )
    for channel_name, module in duplicate_entries:
        log_entries.append(
            (
                f"Duplicate DMMR mapping detected for module {module}: {channel_name}",
                PRINT.WARNING,
            )
        )
    return synced_items, log_entries


def _bundled_runtime_module_name(plugin_dir: Path | None = None) -> str:
    """Return the private Python module namespace used for the bundled runtime."""
    resolved_plugin_dir = Path(__file__).resolve().parent if plugin_dir is None else plugin_dir
    plugin_key = resolved_plugin_dir.name.replace("-", "_")
    return f"{_BUNDLED_RUNTIME_NAMESPACE_PREFIX}_{plugin_key}"


def _load_private_runtime_package(module_name: str, package_dir: Path) -> None:
    """Load a bundled runtime package from disk under a private module name."""
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
            f"Could not create an import spec for bundled DMMR runtime at {package_dir}."
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise


def _get_dmmr_driver_class() -> type[Any]:
    """Load the DMMR driver lazily from the bundled runtime only."""
    global _DMMR_DRIVER_CLASS

    if _DMMR_DRIVER_CLASS is not None:
        return _DMMR_DRIVER_CLASS

    plugin_dir = Path(__file__).resolve().parent
    bundled_runtime_dir = plugin_dir / "vendor" / _BUNDLED_RUNTIME_DIRNAME
    bundled_runtime_init = bundled_runtime_dir / "__init__.py"
    if not bundled_runtime_init.exists():
        raise ModuleNotFoundError(
            "Bundled DMMR runtime not found in vendor/runtime; "
            "plugin installation is incomplete."
        )

    runtime_module_name = _bundled_runtime_module_name(plugin_dir)
    _load_private_runtime_package(runtime_module_name, bundled_runtime_dir)
    module = importlib.import_module(f"{runtime_module_name}.dmmr")
    _DMMR_DRIVER_CLASS = cast(type[Any], module.DMMR)
    return _DMMR_DRIVER_CLASS


def providePlugins() -> "list[type[Plugin]]":
    """Return the plugins provided by this module."""
    return [DMMRDevice]


class DMMRDevice(Device):
    """Read DMMR module currents and expose live current monitors."""

    documentation = (
        "Reads DMMR module currents and exposes live picoammeter measurements."
    )

    name = "DMMR"
    version = "0.1.0"
    supportedVersion = "0.8"
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = "A"
    useMonitors = True
    useOnOffLogic = True
    iconFile = "dmmr.png"
    channels: "list[DMMRChannel]"

    COM = "COM"
    BAUDRATE = "Baud rate"
    CONNECT_TIMEOUT = "Connect timeout (s)"
    POLL_TIMEOUT = "Poll timeout (s)"
    STATE = "State"
    DETECTED_MODULES = "Detected modules"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = DMMRChannel

    def initGUI(self) -> None:
        super().initGUI()
        if hasattr(self, "initAction"):
            self.initAction.setVisible(False)
        if hasattr(self, "closeCommunicationAction"):
            shutdown_tooltip = f"Shutdown {self.name} and disconnect."
            with contextlib.suppress(TypeError):
                self.closeCommunicationAction.triggered.disconnect()
            self.closeCommunicationAction.triggered.connect(self.shutdownCommunication)
            self.closeCommunicationAction.setToolTip(shutdown_tooltip)
            self.closeCommunicationAction.setText(shutdown_tooltip)
            self.closeCommunicationAction.setVisible(False)
        self.controller = DMMRController(controllerParent=self)

    def finalizeInit(self) -> None:
        super().finalizeInit()
        if hasattr(self, "advancedAction"):
            self.advancedAction.toolTipFalse = (
                f"Show expert columns and channel layout actions for {self.name}."
            )
            self.advancedAction.toolTipTrue = (
                f"Hide expert columns and channel layout actions for {self.name}."
            )
            self.advancedAction.setToolTip(self.advancedAction.toolTipFalse)
        self._ensure_local_on_action()
        self._ensure_status_widgets()
        self._update_channel_column_visibility()
        self._sync_acquisition_controls()

    def getChannels(self) -> "list[DMMRChannel]":
        return cast("list[DMMRChannel]", super().getChannels())

    com: int
    baudrate: int
    connect_timeout_s: float
    poll_timeout_s: float
    main_state: str
    detected_modules: str
    device_state_summary: str
    voltage_state_summary: str
    temperature_state_summary: str

    def getConfiguredModules(self) -> list[int]:
        """Return sorted module addresses referenced by real channels."""
        return sorted(
            {channel.module_address() for channel in self.getChannels() if channel.real}
        )

    def _current_channel_items(self) -> list[dict[str, Any]]:
        """Snapshot current channels into config dictionaries."""
        return [channel.asDict() for channel in self.getChannels()]

    def _default_channel_template(self) -> dict[str, dict[str, Any]]:
        """Return the default DMMR channel parameter definitions."""
        return self.channelType(channelParent=self, tree=None).getSortedDefaultChannel()

    def _default_channel_item(self) -> dict[str, Any]:
        """Return the persisted default DMMR channel configuration."""
        return self.channelType(channelParent=self, tree=None).asDict()

    def _ensure_local_on_action(self) -> None:
        """Expose the global DMMR ON/OFF control directly in the plugin toolbar."""
        if (
            not self.useOnOffLogic
            or hasattr(self, "deviceOnAction")
            or not hasattr(self, "closeCommunicationAction")
        ):
            return

        self.deviceOnAction = self.addStateAction(
            event=lambda checked=False: self.setOn(on=checked),
            toolTipFalse=f"Turn {self.name} ON.",
            iconFalse=self.makeIcon(_DMMR_POWER_ON_ICON),
            toolTipTrue=f"Turn {self.name} OFF and disconnect.",
            iconTrue=self.makeIcon(_DMMR_POWER_OFF_ICON),
            before=self.closeCommunicationAction,
            restore=False,
            defaultState=False,
        )
        self._sync_local_on_action()

    def _sync_local_on_action(self) -> None:
        """Keep the local toolbar ON/OFF button synchronized with the device state."""
        action = getattr(self, "deviceOnAction", None)
        if action is None:
            return
        action.blockSignals(True)
        try:
            action.state = self.isOn()
        finally:
            action.blockSignals(False)

    def _display_main_state(self) -> str:
        """Return the operator-facing state shown in the toolbar badge."""
        raw_state = str(getattr(self, "main_state", "Disconnected") or "Disconnected")
        is_on = getattr(self, "isOn", None)
        if raw_state != "Disconnected" and callable(is_on) and not bool(is_on()):
            return "OFF"
        return raw_state

    def _ensure_status_widgets(self) -> None:
        """Add compact global DMMR status labels to the plugin toolbar."""
        if (
            getattr(self, "titleBar", None) is None
            or getattr(self, "titleBarLabel", None) is None
            or hasattr(self, "statusBadgeLabel")
        ):
            return

        label_type = type(self.titleBarLabel)
        self.statusBadgeLabel = label_type("")
        self.statusSummaryLabel = label_type("")

        if hasattr(self.statusBadgeLabel, "setObjectName"):
            self.statusBadgeLabel.setObjectName(f"{self.name}StatusBadge")
        if hasattr(self.statusSummaryLabel, "setObjectName"):
            self.statusSummaryLabel.setObjectName(f"{self.name}StatusSummary")
        if hasattr(self.statusSummaryLabel, "setStyleSheet"):
            self.statusSummaryLabel.setStyleSheet("QLabel { padding-left: 6px; }")

        insert_before = getattr(self, "stretchAction", None)
        if insert_before is not None and hasattr(self.titleBar, "insertWidget"):
            self.titleBar.insertWidget(insert_before, self.statusBadgeLabel)
            self.titleBar.insertWidget(insert_before, self.statusSummaryLabel)
        elif hasattr(self.titleBar, "addWidget"):
            self.titleBar.addWidget(self.statusBadgeLabel)
            self.titleBar.addWidget(self.statusSummaryLabel)

        self._update_status_widgets()

    def _status_badge_style(self) -> str:
        """Return a compact badge style that reflects the DMMR main state."""
        state = self._display_main_state()
        if state == "ST_ON":
            background = "#2f855a"
        elif state == "OFF":
            background = "#4a5568"
        elif state == "ST_STBY":
            background = "#b7791f"
        elif state == "Disconnected":
            background = "#718096"
        elif state == "ST_OVERLOAD" or state.startswith("ST_ERR") or "error" in state.lower():
            background = "#c53030"
        else:
            background = "#4a5568"
        return (
            "QLabel {"
            f" background-color: {background};"
            " color: white;"
            " border-radius: 3px;"
            " padding: 2px 6px;"
            " font-weight: 600;"
            " }"
        )

    def _status_summary_text(self) -> str:
        """Return the compact DMMR runtime summary displayed in the toolbar."""
        modules = str(getattr(self, "detected_modules", "") or "None")
        faults = _compact_status_text(
            getattr(self, "device_state_summary", None),
            default="n/a",
        )
        voltage = _compact_status_text(
            getattr(self, "voltage_state_summary", None),
            default="n/a",
        )
        return f"Modules: {modules} | Faults: {faults} | Rails: {voltage}"

    def _status_tooltip_text(self) -> str:
        """Return the full DMMR status tooltip for the toolbar widgets."""
        display_state = self._display_main_state()
        hardware_state = str(getattr(self, "main_state", "Disconnected") or "Disconnected")
        lines = [f"State: {display_state}"]
        if display_state != hardware_state:
            lines.append(f"Hardware state: {hardware_state}")
        lines.extend(
            (
                f"Modules: {getattr(self, 'detected_modules', '') or 'None'}",
                f"Faults: {getattr(self, 'device_state_summary', '') or 'n/a'}",
                f"Voltage rails: {getattr(self, 'voltage_state_summary', '') or 'n/a'}",
                f"Temperature: {getattr(self, 'temperature_state_summary', '') or 'n/a'}",
            )
        )
        return "\n".join(lines)

    def _update_status_widgets(self) -> None:
        """Refresh the global DMMR status labels in the toolbar."""
        badge = getattr(self, "statusBadgeLabel", None)
        summary = getattr(self, "statusSummaryLabel", None)
        self._sync_acquisition_controls()
        if badge is None or summary is None:
            return

        badge_text = self._display_main_state()
        summary_text = self._status_summary_text()
        tooltip = self._status_tooltip_text()

        if hasattr(badge, "setText"):
            badge.setText(badge_text)
        if hasattr(badge, "setToolTip"):
            badge.setToolTip(tooltip)
        if hasattr(badge, "setStyleSheet"):
            badge.setStyleSheet(self._status_badge_style())

        if hasattr(summary, "setText"):
            summary.setText(summary_text)
        if hasattr(summary, "setToolTip"):
            summary.setToolTip(tooltip)

    def _set_channel_headers_from_template(self) -> None:
        """Apply channel headers even when no concrete channel exists yet."""
        if self.tree is None:
            return
        self.tree.setHeaderLabels(
            [
                parameter_dict.get(Parameter.HEADER, "") or name.title()
                for name, parameter_dict in self._default_channel_template().items()
            ]
        )

    def _update_channel_column_visibility(self) -> None:
        """Hide framework columns that are not useful for the DMMR UI."""
        if self.tree is None or not self.channels:
            return

        parameter_names = list(self.channels[0].getSortedDefaultChannel())
        for hidden_name in (Channel.COLLAPSE, Channel.REAL, Channel.ACTIVE):
            if hidden_name in parameter_names:
                self.tree.setColumnHidden(parameter_names.index(hidden_name), True)

    def _sync_channels_from_detected_modules(self, detected_modules: list[int]) -> bool:
        """Synchronize channels from the latest detected DMMR module scan."""
        current_items = self._current_channel_items()
        target_items, log_entries = _plan_channel_sync(
            current_items=current_items,
            detected_modules=detected_modules,
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
        self.exportConfiguration(useDefaultFile=True)
        return True

    def _apply_channel_items(self, items: list[dict[str, Any]]) -> None:
        """Apply a rebuilt channel configuration using the standard ESIBD flow."""
        config_file = self.customConfigFile(self.confINI)
        self.loading = True
        if self.tree is not None:
            self.tree.setUpdatesEnabled(False)
        try:
            self.updateChannelConfig(items, config_file)
            if self.channels and self.tree is not None:
                self.tree.setHeaderLabels(
                    [
                        parameter_dict.get(Parameter.HEADER, "") or name.title()
                        for name, parameter_dict in self.channels[0].getSortedDefaultChannel().items()
                    ]
                )
                header = self.tree.header()
                if header is not None:
                    header.setStretchLastSection(False)
                    header.setMinimumSectionSize(0)
                    header.setSectionResizeMode(type(header).ResizeMode.ResizeToContents)
                for channel in self.getChannels():
                    channel.collapseChanged(toggle=False)
                self.tree.scheduleDelayedItemsLayout()
            if hasattr(self, "advancedAction"):
                self.toggleAdvanced(advanced=self.advancedAction.state)
            self._update_channel_column_visibility()
            self.estimateStorage()
            self.pluginManager.DeviceManager.globalUpdate(inout=self.inout)
        finally:
            if self.tree is not None:
                self.tree.setUpdatesEnabled(True)
                self.tree.scheduleDelayedItemsLayout()
                self.tree.viewport().update()
            self.processEvents()
            self.loading = False

    def loadConfiguration(
        self,
        file: "Path | None" = None,
        useDefaultFile: bool = False,
        append: bool = False,
    ) -> None:
        """Skip the generic bootstrap until DMMR hardware is initialized."""
        if useDefaultFile:
            file = self.customConfigFile(self.confINI)

        if (
            useDefaultFile
            and file not in {None, Path()}
            and cast(Path, file).suffix.lower() == ".ini"
            and not cast(Path, file).exists()
            and not self.channels
        ):
            self.loading = True
            if self.tree is not None:
                self.tree.setUpdatesEnabled(False)
                self.tree.setRootIsDecorated(False)
            try:
                self.print(
                    f"DMMR config file {file} not found. "
                    "Channels will be created after successful hardware initialization."
                )
                self._set_channel_headers_from_template()
                if hasattr(self, "advancedAction"):
                    self.toggleAdvanced(advanced=self.advancedAction.state)
                if self.tree is not None:
                    self.tree.scheduleDelayedItemsLayout()
                self.pluginManager.DeviceManager.globalUpdate(inout=self.inout)
            finally:
                if self.tree is not None:
                    self.tree.setUpdatesEnabled(True)
                self.loading = False
            return

        super().loadConfiguration(file=file, useDefaultFile=False, append=append)

    def toggleAdvanced(self, advanced: "bool | None" = False) -> None:
        """Handle advanced columns without hiding DMMR channels."""
        if self.channels:
            super().toggleAdvanced(advanced=advanced)
            for channel in self.getChannels():
                channel.setHidden(False)
            self._update_channel_column_visibility()
            return

        if advanced is not None:
            self.advancedAction.state = advanced
        for action_name in (
            "importAction",
            "exportAction",
            "duplicateChannelAction",
            "deleteChannelAction",
            "moveChannelUpAction",
            "moveChannelDownAction",
        ):
            action = getattr(self, action_name, None)
            if action is not None:
                action.setVisible(self.advancedAction.state)
        if self.tree is None:
            return
        for index, item in enumerate(self._default_channel_template().values()):
            if item.get(_PARAMETER_ADVANCED_KEY, False):
                self.tree.setColumnHidden(index, not self.advancedAction.state)

    def estimateStorage(self) -> None:
        """Avoid division by zero before the first DMMR channel discovery."""
        if self.channels:
            super().estimateStorage()
            return

        self.maxDataPoints = 0
        widget = self.pluginManager.Settings.settings[
            f"{self.name}/{self.MAXDATAPOINTS}"
        ].getWidget()
        if widget:
            widget.setToolTip(
                "Storage estimate will be available after the first successful "
                "DMMR hardware initialization."
            )

    def getDefaultSettings(self) -> dict[str, dict]:
        settings = super().getDefaultSettings()
        settings[f"{self.name}/{self.COM}"] = parameterDict(
            value=1,
            minimum=1,
            maximum=255,
            toolTip="Windows COM port number used by the DMMR controller.",
            parameterType=PARAMETERTYPE.INT,
            attr="com",
        )
        settings[f"{self.name}/{self.BAUDRATE}"] = parameterDict(
            value=230400,
            minimum=1,
            maximum=1_000_000,
            toolTip="Baud rate passed to cgc.dmmr.DMMR.",
            parameterType=PARAMETERTYPE.INT,
            attr="baudrate",
        )
        settings[f"{self.name}/{self.CONNECT_TIMEOUT}"] = parameterDict(
            value=10.0,
            minimum=1.0,
            maximum=60.0,
            toolTip="Timeout in seconds used to initialize and shutdown the controller.",
            parameterType=PARAMETERTYPE.FLOAT,
            attr="connect_timeout_s",
        )
        settings[f"{self.name}/{self.POLL_TIMEOUT}"] = parameterDict(
            value=5.0,
            minimum=0.5,
            maximum=30.0,
            toolTip="Timeout in seconds used for polling DMMR state and module currents.",
            parameterType=PARAMETERTYPE.FLOAT,
            attr="poll_timeout_s",
        )
        settings[f"{self.name}/{self.STATE}"] = parameterDict(
            value="Disconnected",
            toolTip="Latest DMMR controller state reported by the driver.",
            parameterType=PARAMETERTYPE.LABEL,
            attr="main_state",
            indicator=True,
            internal=True,
            restore=False,
        )
        settings[f"{self.name}/{self.DETECTED_MODULES}"] = parameterDict(
            value="",
            toolTip="Module addresses detected during initialization.",
            parameterType=PARAMETERTYPE.LABEL,
            attr="detected_modules",
            indicator=True,
            internal=True,
            advanced=True,
            restore=False,
        )
        settings[f"{self.name}/Interval"][Parameter.VALUE] = 1000
        settings[f"{self.name}/{self.MAXDATAPOINTS}"][Parameter.VALUE] = 100000
        return settings

    def _acquisition_readiness(self) -> tuple[bool, str]:
        """Return whether manual recording can start and, if not, why."""
        controller = getattr(self, "controller", None)
        if controller is None:
            return False, "controller unavailable"
        if getattr(controller, "device", None) is None:
            return False, "device disconnected"
        if getattr(controller, "initializing", False):
            return False, "initialization in progress"
        if not getattr(controller, "initialized", False):
            return False, "communication not initialized"
        if getattr(controller, "transitioning", False):
            return False, "ON/OFF transition in progress"
        is_on = getattr(self, "isOn", None)
        if not callable(is_on) or not bool(is_on()):
            return False, "device is OFF"
        main_state = str(getattr(controller, "main_state", "Disconnected") or "Disconnected")
        if main_state != "ST_ON":
            return False, f"state is {main_state}"
        return True, ""

    def _set_action_enabled(self, action: Any | None, enabled: bool) -> None:
        """Update QAction-like enabled state while tolerating lightweight test doubles."""
        if action is None:
            return
        if hasattr(action, "setEnabled"):
            action.setEnabled(enabled)
            return
        setattr(action, "enabled", enabled)

    def _force_recording_action_state(self, state: bool) -> None:
        """Force the acquisition action state without re-entering its callbacks."""
        for action in (
            getattr(self, "recordingAction", None),
            getattr(getattr(self, "liveDisplay", None), "recordingAction", None),
        ):
            if action is None:
                continue
            blocker = getattr(action, "blockSignals", None)
            if callable(blocker):
                blocker(True)
            try:
                if hasattr(action, "state"):
                    action.state = bool(state)
                elif hasattr(action, "setChecked"):
                    action.setChecked(bool(state))
            finally:
                if callable(blocker):
                    blocker(False)

    def _display_communication_actions(self) -> tuple[Any | None, Any | None]:
        """Return the Live Display init/close actions when available."""
        live_display = getattr(self, "liveDisplay", None)
        if live_display is None:
            return None, None

        close_action = getattr(live_display, "closeCommunicationAction", None)
        init_action = getattr(live_display, "initCommunicationAction", None)
        if close_action is not None and init_action is not None:
            return close_action, init_action

        title_bar = getattr(live_display, "titleBar", None)
        get_actions = getattr(title_bar, "actions", None)
        if not callable(get_actions):
            return close_action, init_action

        close_label = f"Close {self.name} communication."
        init_label = f"Initialize {self.name} communication."
        for action in get_actions():
            label = _action_label(action)
            if close_action is None and label == close_label:
                close_action = action
                setattr(live_display, "closeCommunicationAction", action)
            elif init_action is None and label == init_label:
                init_action = action
                setattr(live_display, "initCommunicationAction", action)
        return close_action, init_action

    def _sync_display_communication_controls(self) -> None:
        """Enable display-side communication actions only when applicable."""
        close_action, init_action = self._display_communication_actions()
        controller = getattr(self, "controller", None)
        initializing = bool(getattr(controller, "initializing", False))
        initialized = bool(getattr(controller, "initialized", False))
        self._set_action_enabled(close_action, initialized and not initializing)
        self._set_action_enabled(init_action, (not initialized) and (not initializing))

    def _sync_acquisition_controls(self) -> None:
        """Disable manual acquisition controls until the DMMR is actually ready."""
        ready, _reason = self._acquisition_readiness()
        self._sync_display_communication_controls()
        self._set_action_enabled(getattr(self, "recordingAction", None), ready)
        self._set_action_enabled(
            getattr(getattr(self, "liveDisplay", None), "recordingAction", None),
            ready,
        )
        if not ready and not bool(getattr(self, "recording", False)):
            self._force_recording_action_state(False)

    def toggleRecording(self, on: "bool | None" = None, manual: bool = True) -> None:
        """Only allow data recording when the DMMR is initialized and in ST_ON."""
        requested_on = (not bool(getattr(self, "recording", False))) if on is None else bool(on)
        ready, reason = self._acquisition_readiness()
        if requested_on and not ready:
            self._force_recording_action_state(False)
            self._sync_acquisition_controls()
            if manual:
                self.print(
                    f"Cannot start {self.name} data acquisition: {reason}.",
                    flag=PRINT.WARNING,
                )
            return

        super().toggleRecording(on=on, manual=manual)
        self._sync_acquisition_controls()

    def closeCommunication(self) -> None:
        """Close communication safely even if plugin finalization failed early."""
        if self.useOnOffLogic and not hasattr(self, "onAction"):
            self.stopAcquisition()
            if self.controller:
                self.controller.closeCommunication()
            self.recording = False
            self._sync_acquisition_controls()
            return

        if self.controller and getattr(self, "initialized", False):
            self.shutdownCommunication()
            return

        if self.useOnOffLogic and hasattr(self, "onAction"):
            self.onAction.state = False
            self._sync_local_on_action()
        self.stopAcquisition()
        if self.controller:
            self.controller.closeCommunication()
        self.recording = False
        self._sync_acquisition_controls()

    def shutdownCommunication(self) -> None:
        """Run the full DMMR hardware shutdown sequence from the toolbar action."""
        if self.useOnOffLogic and hasattr(self, "onAction"):
            self.onAction.state = False
            self._sync_local_on_action()
        self.stopAcquisition()
        if self.controller:
            self.controller.shutdownCommunication()
        self.recording = False
        self._sync_acquisition_controls()

    def _set_on_ui_state(self, on: bool) -> None:
        """Synchronize the ESIBD and local DMMR ON/OFF actions."""
        state = bool(on)
        for action_name in ("onAction", "deviceOnAction"):
            action = getattr(self, action_name, None)
            if action is None:
                continue
            signal_comm = getattr(action, "signalComm", None)
            thread_signal = getattr(signal_comm, "setValueFromThreadSignal", None)
            if thread_signal is not None:
                thread_signal.emit(state)
            else:
                action.state = state
        self._sync_local_on_action()
        self._update_status_widgets()

    def setOn(self, on: "bool | None" = None) -> None:
        """Toggle the DMMR without relying on a channel apply path."""
        controller = self.controller if hasattr(self, "controller") else None
        current_state = self.isOn() if hasattr(self, "onAction") else False
        transition_target = getattr(controller, "transition_target_on", None)
        if controller and (
            getattr(controller, "initializing", False)
            or getattr(controller, "transitioning", False)
        ):
            restored_state = current_state if transition_target is None else bool(transition_target)
            if hasattr(self, "onAction"):
                self.onAction.state = restored_state
            self._sync_local_on_action()
            self.print(
                f"{self.name} ON/OFF transition already in progress; ignoring additional request.",
                flag=PRINT.WARNING,
            )
            return

        if on is not None and hasattr(self, "onAction") and self.onAction.state is not on:
            self.onAction.state = on
        self._sync_local_on_action()
        self._update_status_widgets()
        if getattr(self, "loading", False):
            return

        if getattr(self, "initialized", False):
            begin_transition = getattr(self.controller, "_begin_transition", None) if self.controller else None
            if self.controller and (not callable(begin_transition) or begin_transition(self.isOn())):
                self.controller.toggleOnFromThread(parallel=True)
        elif hasattr(self, "onAction") and self.isOn():
            self.initializeCommunication()


class DMMRChannel(Channel):
    """DMMR module channel definition."""

    MODULE = "Module"
    channelParent: DMMRDevice

    def getDefaultChannel(self) -> dict[str, dict]:
        self.module: int

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = "Reference (A)"
        channel[self.VALUE][_PARAMETER_ADVANCED_KEY] = True
        channel[self.VALUE][_PARAMETER_TOOLTIP_KEY] = (
            "Reference field only. The DMMR plugin is read-only; live current is "
            "shown through channel monitors."
        )
        channel[self.ENABLED][_PARAMETER_ADVANCED_KEY] = False
        channel[self.ENABLED][Parameter.HEADER] = "Read"
        channel[self.ENABLED][_PARAMETER_TOOLTIP_KEY] = (
            "Enable or mute this DMMR module in the UI."
        )
        channel[self.DISPLAY][Parameter.HEADER] = "Display"
        channel[self.DISPLAY][_PARAMETER_EVENT_KEY] = self.displayChanged
        channel[self.ACTIVE][_PARAMETER_ADVANCED_KEY] = True
        monitor_name = getattr(self, "MONITOR", "Monitor")
        if monitor_name in channel:
            channel[monitor_name][Parameter.HEADER] = "Current"
            channel[monitor_name][_PARAMETER_TOOLTIP_KEY] = (
                "Measured DMMR module current. Values are stored in amps and "
                "displayed with automatic SI prefixes."
            )
        channel[self.MODULE] = parameterDict(
            value="0",
            parameterType=PARAMETERTYPE.LABEL,
            advanced=False,
            indicator=True,
            header="Mod",
            attr="module",
        )
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        if self.OPTIMIZE in self.displayedParameters:
            self.displayedParameters.remove(self.OPTIMIZE)
        self.displayedParameters.append(self.MODULE)

    def initGUI(self, item: dict) -> None:
        super().initGUI(item)
        self._upgrade_toggle_widget(self.ENABLED, "Read", 52)
        self.scalingChanged()

    def scalingChanged(self) -> None:
        super().scalingChanged()
        if self.rowHeight >= _DMMR_MIN_ROW_HEIGHT:
            return
        self.rowHeight = _DMMR_MIN_ROW_HEIGHT
        for parameter in self.parameters:
            parameter.setHeight(self.rowHeight)
        if not self.loading and self.tree:
            self.tree.scheduleDelayedItemsLayout()

    def _upgrade_toggle_widget(
        self,
        parameter_name: str,
        label: str,
        minimum_width: int,
    ) -> None:
        parameter = self.getParameterByName(parameter_name)
        if parameter is None:
            return

        initial_value = bool(parameter.value)
        parameter.widget = ToolButton()
        parameter.applyWidget()
        if parameter.check:
            parameter.check.setMaximumHeight(max(parameter.rowHeight, _DMMR_MIN_ROW_HEIGHT))
            parameter.check.setMinimumWidth(minimum_width)
            parameter.check.setText(label)
            parameter.check.setCheckable(True)
            if hasattr(parameter.check, "setAutoRaise"):
                parameter.check.setAutoRaise(False)
        parameter.value = initial_value

    def realChanged(self) -> None:
        self.getParameterByName(self.MODULE).setVisible(self.real)
        super().realChanged()

    def enabledChanged(self) -> None:
        super().enabledChanged()
        if not self.enabled:
            self.monitor = np.nan

    def monitorChanged(self) -> None:
        super().monitorChanged()
        self._sync_monitor_widget()

    def displayChanged(self) -> None:
        super().updateDisplay()

    def module_address(self) -> int:
        """Return the configured DMMR module address as an integer."""
        return _coerce_int(self.module, 0)

    def _sync_monitor_widget(self) -> None:
        """Render the live current with automatic SI prefixes in the monitor field."""
        getter = getattr(self, "getParameterByName", None)
        if not callable(getter):
            return

        monitor_name = getattr(self, "MONITOR", "Monitor")
        parameter = getter(monitor_name)
        if parameter is None:
            return

        widget = getattr(parameter, "getWidget", lambda: None)()
        if widget is None:
            return

        formatted_text, raw_text = _format_si_current(getattr(self, "monitor", np.nan))
        line_edit = getattr(widget, "lineEdit", lambda: None)()
        if line_edit is not None and hasattr(line_edit, "setText"):
            line_edit.setText(formatted_text)
        elif hasattr(widget, "setText"):
            widget.setText(formatted_text)

        tooltip_base = str(getattr(parameter, "toolTip", "") or "").strip()
        tooltip = f"{formatted_text} ({raw_text})"
        if tooltip_base:
            tooltip = f"{tooltip_base}\n{tooltip}"
        if hasattr(widget, "setToolTip"):
            widget.setToolTip(tooltip)
        if line_edit is not None and hasattr(line_edit, "setToolTip"):
            line_edit.setToolTip(tooltip)


class DMMRController(DeviceController):
    """DMMR hardware controller used by the ESIBD Explorer plugin."""

    controllerParent: DMMRDevice

    def __init__(self, controllerParent) -> None:
        super().__init__(controllerParent=controllerParent)
        self.device: Any | None = None
        self.detected_module_ids: list[int] = []
        self.detected_modules_text = ""
        self.main_state = "Disconnected"
        self.device_state_summary = "n/a"
        self.voltage_state_summary = "n/a"
        self.temperature_state_summary = "n/a"
        self.initialized = False
        self.transitioning = False
        self.transition_target_on: bool | None = None

    def initializeValues(self, reset: bool = False) -> None:
        if getattr(self, "values", None) is None or reset:
            get_channels = getattr(self.controllerParent, "getChannels", None)
            if not callable(get_channels):
                self.values = {}
                return
            self.values = {
                channel.module_address(): np.nan
                for channel in get_channels()
                if channel.real
            }

    @staticmethod
    def _is_optional_status(device: Any, status: int) -> bool:
        return int(status) in {
            int(getattr(device, "ERR_COMMAND_RECEIVE", -10)),
            int(getattr(device, "ERR_DATA_RECEIVE", -11)),
        }

    def _measurement_modules(self) -> list[int]:
        configured_modules_getter = getattr(self.controllerParent, "getConfiguredModules", None)
        configured_modules = (
            {
                _coerce_int(module, -1)
                for module in configured_modules_getter()
                if _coerce_int(module, -1) >= 0
            }
            if callable(configured_modules_getter)
            else set()
        )
        detected_modules = {
            _coerce_int(module, -1)
            for module in getattr(self, "detected_module_ids", [])
            if _coerce_int(module, -1) >= 0
        }
        if detected_modules:
            return sorted(configured_modules & detected_modules) if configured_modules else sorted(
                detected_modules
            )
        return sorted(configured_modules)

    def runInitialization(self) -> None:
        self.initialized = False
        self._dispose_device()
        try:
            dmmr_driver_class = _get_dmmr_driver_class()
            self.device = dmmr_driver_class(
                device_id=f"{self.controllerParent.name.lower()}_com{int(self.controllerParent.com)}",
                com=int(self.controllerParent.com),
                baudrate=int(self.controllerParent.baudrate),
            )
            backend_reason = str(
                getattr(self.device, "_process_backend_disabled_reason", "")
            ).strip()
            if backend_reason:
                self.print(backend_reason, flag=PRINT.WARNING)
            module_info = self.device.initialize(
                timeout_s=float(self.controllerParent.connect_timeout_s)
            )
            self.detected_module_ids = sorted(module_info)
            self.detected_modules_text = (
                ", ".join(str(module) for module in self.detected_module_ids)
                if self.detected_module_ids
                else "None"
            )
            self._update_state()
            self.signalComm.initCompleteSignal.emit()
        except Exception as exc:  # noqa: BLE001
            self._restore_off_ui_state()
            self.print(
                f"DMMR initialization failed on COM{int(self.controllerParent.com)}: "
                f"{self._format_exception(exc)}",
                flag=PRINT.ERROR,
            )
            self._dispose_device()
        finally:
            self.initializing = False

    def initComplete(self) -> None:
        if self.device is not None and self.detected_module_ids:
            self.controllerParent._sync_channels_from_detected_modules(
                self.detected_module_ids
            )
        self.initializeValues()
        self.initialized = True
        self.super_init_complete_called = True
        self._sync_status_to_gui()
        if self.device is None:
            self.print(
                "DMMR initialization simulated because ESIBD Test mode is active. "
                "No hardware communication was attempted.",
                flag=PRINT.WARNING,
            )
            return

        modules_text = self.detected_modules_text or "None"
        self.print(
            f"DMMR initialized on COM{int(self.controllerParent.com)}. "
            f"State: {self.main_state}. Detected modules: {modules_text}."
        )
        if getattr(self.controllerParent, "isOn", lambda: False)():
            with contextlib.suppress(Exception):
                self.controllerParent.updateValues(apply=False)
            if self._begin_transition(True):
                self.toggleOnFromThread(parallel=True)

    def readNumbers(self) -> None:
        if self.device is None or not getattr(self, "initialized", False):
            self.initializeValues(reset=True)
            return

        self._update_state()
        self.initializeValues(reset=True)

        if not getattr(self.controllerParent, "isOn", lambda: False)():
            return

        new_values = {
            channel.module_address(): np.nan
            for channel in self.controllerParent.getChannels()
            if channel.real
        }
        poll_modules = self._measurement_modules()

        for module in poll_modules:
            status = None
            measured_current = np.nan
            try:
                with self._controller_lock_section(
                    f"Could not acquire lock to read DMMR module {module}.",
                    already_acquired=True,
                ):
                    device = self.device
                    if device is None:
                        return
                    status, measured_current, _meas_range = device.get_module_current(
                        module,
                        timeout_s=float(self.controllerParent.poll_timeout_s),
                    )
            except TimeoutError:
                self.errorCount += 1
                self.print(
                    f"Timed out while reading DMMR module {module}; keeping partial results.",
                    flag=PRINT.ERROR,
                )
                break
            except Exception as exc:  # noqa: BLE001
                self.errorCount += 1
                self.print(
                    f"Failed to read DMMR module {module}: {exc}",
                    flag=PRINT.ERROR,
                )
                continue

            if status == getattr(device, "NO_ERR", status):
                new_values[module] = float(measured_current)
                continue

            self.errorCount += 1
            self.print(
                f"DMMR rejected current read for module {module}: "
                f"{self._format_status(status)}",
                flag=PRINT.ERROR,
            )

        self.values = new_values

    def fakeNumbers(self) -> None:
        self.initializeValues(reset=True)

    def applyValue(self, channel: DMMRChannel) -> None:
        """DMMR is a read-only measurement device at channel level."""
        return

    def updateValues(self) -> None:
        if self.values is None:
            return

        self._sync_status_to_gui()
        device_is_on = self.controllerParent.isOn()
        for channel in self.controllerParent.getChannels():
            if channel.enabled and channel.real and device_is_on:
                channel.monitor = self.values.get(channel.module_address(), np.nan)
                continue
            channel.monitor = np.nan

    def toggleOn(self) -> None:
        base_toggle_on = getattr(super(), "toggleOn", None)
        if callable(base_toggle_on):
            base_toggle_on()

        device = self.device
        if device is None:
            self._end_transition()
            return

        if getattr(self, "acquiring", False):
            self.stopAcquisition()
            self.acquiring = False

        try:
            if self.controllerParent.isOn():
                measurement_modules = self._measurement_modules()
                with self._controller_lock_section(
                    "Could not acquire lock to enable DMMR acquisition."
                ):
                    device = self.device
                    if device is None:
                        self._restore_off_ui_state()
                        return
                    enable_status = device.set_enable(
                        True,
                        timeout_s=float(self.controllerParent.connect_timeout_s),
                    )
                    if enable_status != device.NO_ERR:
                        raise RuntimeError(
                            f"set_enable(True) failed: {self._format_status(enable_status, device=device)}"
                        )
                    set_module_auto_range = getattr(device, "set_module_auto_range", None)
                    if callable(set_module_auto_range):
                        for module in measurement_modules:
                            auto_range_status = set_module_auto_range(
                                module,
                                True,
                                timeout_s=float(self.controllerParent.connect_timeout_s),
                            )
                            if auto_range_status == device.NO_ERR:
                                continue
                            if self._is_optional_status(device, auto_range_status):
                                self.print(
                                    "DMMR module auto-range command is unavailable on this controller; "
                                    f"continuing without it for module {module}.",
                                    flag=PRINT.WARNING,
                                )
                                continue
                            if auto_range_status != device.NO_ERR:
                                raise RuntimeError(
                                    f"set_module_auto_range({module}, True) failed: "
                                    f"{self._format_status(auto_range_status, device=device)}"
                                )
                    automatic_status = device.set_automatic_current(
                        True,
                        timeout_s=float(self.controllerParent.connect_timeout_s),
                    )
                    if automatic_status != device.NO_ERR:
                        raise RuntimeError(
                            "set_automatic_current(True) failed: "
                            f"{self._format_status(automatic_status, device=device)}"
                        )
                self._update_state()
                start_acquisition = getattr(self, "startAcquisition", None)
                if callable(start_acquisition):
                    start_acquisition()
                self.print("DMMR acquisition enabled.")
            else:
                with self._controller_lock_section(
                    "Could not acquire lock to disable DMMR acquisition."
                ):
                    device = self.device
                    if device is None:
                        return
                    automatic_status = device.set_automatic_current(
                        False,
                        timeout_s=float(self.controllerParent.connect_timeout_s),
                    )
                    if automatic_status != device.NO_ERR:
                        raise RuntimeError(
                            "set_automatic_current(False) failed: "
                            f"{self._format_status(automatic_status, device=device)}"
                        )
                    enable_status = device.set_enable(
                        False,
                        timeout_s=float(self.controllerParent.connect_timeout_s),
                    )
                    if enable_status != device.NO_ERR:
                        raise RuntimeError(
                            f"set_enable(False) failed: {self._format_status(enable_status, device=device)}"
                        )
                self._update_state()
                self.print("DMMR acquisition disabled.")
        except Exception as exc:  # noqa: BLE001
            self.errorCount += 1
            self._restore_off_ui_state()
            self._update_state()
            self.print(
                f"Failed to toggle DMMR acquisition: {self._format_exception(exc)}"
                f"{self._runtime_diagnostics(device=device)}",
                flag=PRINT.ERROR,
            )
        finally:
            self._end_transition()
            self._sync_status_to_gui()

    def closeCommunication(self) -> None:
        base_close = getattr(super(), "closeCommunication", None)
        if callable(base_close):
            base_close()
        self.main_state = "Disconnected"
        self.detected_module_ids = []
        self.detected_modules_text = ""
        self.device_state_summary = "n/a"
        self.voltage_state_summary = "n/a"
        self.temperature_state_summary = "n/a"
        self._sync_status_to_gui()
        self._dispose_device()
        self.initialized = False

    def shutdownCommunication(self) -> None:
        """Run the DMMR shutdown sequence before releasing communication resources."""
        device = self.device
        if device is None:
            self.closeCommunication()
            return

        if getattr(self, "acquiring", False):
            self.stopAcquisition()
            self.acquiring = False
        self.print("Starting DMMR shutdown sequence.")
        try:
            device.shutdown(timeout_s=float(self.controllerParent.connect_timeout_s))
        except Exception as exc:  # noqa: BLE001
            self.errorCount += 1
            self._update_state()
            self.print(
                f"DMMR shutdown failed: {self._format_exception(exc)}"
                f"{self._runtime_diagnostics(device=device)}",
                flag=PRINT.ERROR,
            )
        else:
            self.print("DMMR shutdown sequence completed.")
        finally:
            self.closeCommunication()

    def _update_state(self) -> None:
        if self.device is None:
            self.main_state = "Disconnected"
            self.device_state_summary = "n/a"
            self.voltage_state_summary = "n/a"
            self.temperature_state_summary = "n/a"
            return

        timeout_s = float(self.controllerParent.poll_timeout_s)
        try:
            status, _state_hex, state_name = self.device.get_state(timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001
            self.errorCount += 1
            self.main_state = "State error"
            self.print(f"Failed to read DMMR state: {exc}", flag=PRINT.ERROR)
            self.device_state_summary = self._safe_query_state("get_device_state") or "Unknown"
            self.voltage_state_summary = self._safe_query_state("get_voltage_state") or "Unknown"
            self.temperature_state_summary = self._safe_query_state("get_temperature_state") or "Unknown"
            return

        if status == self.device.NO_ERR:
            self.main_state = state_name
        else:
            self.main_state = "State error"
            self.errorCount += 1
            self.print(
                f"Failed to read DMMR state: {self._format_status(status)}",
                flag=PRINT.ERROR,
            )

        self.device_state_summary = self._safe_query_state("get_device_state") or "Unknown"
        self.voltage_state_summary = self._safe_query_state("get_voltage_state") or "Unknown"
        self.temperature_state_summary = (
            self._safe_query_state("get_temperature_state") or "Unknown"
        )

    def _sync_status_to_gui(self) -> None:
        self.controllerParent.main_state = self.main_state
        self.controllerParent.detected_modules = self.detected_modules_text
        self.controllerParent.device_state_summary = self.device_state_summary
        self.controllerParent.voltage_state_summary = self.voltage_state_summary
        self.controllerParent.temperature_state_summary = self.temperature_state_summary
        if hasattr(self.controllerParent, "_update_status_widgets"):
            self.controllerParent._update_status_widgets()

    def _dispose_device(self) -> None:
        device = self.device
        self.device = None
        self.initialized = False
        if device is None:
            return

        try:
            device.disconnect()
        except Exception:  # noqa: BLE001
            pass
        finally:
            with contextlib.suppress(Exception):
                device.close()

    def _format_status(self, status: int, device: Any | None = None) -> str:
        device = self.device if device is None else device
        if device is None:
            return str(status)
        try:
            return str(device.format_status(status))
        except Exception:  # noqa: BLE001
            return str(status)

    def _safe_query_state(self, getter_name: str, device: Any | None = None) -> str | None:
        device = self.device if device is None else device
        if device is None:
            return None
        getter = getattr(device, getter_name, None)
        if getter is None:
            return None
        try:
            status, _state_hex, state = getter(timeout_s=float(self.controllerParent.poll_timeout_s))
        except Exception:  # noqa: BLE001
            return None
        if status != getattr(device, "NO_ERR", status):
            return None
        if isinstance(state, list):
            return ", ".join(str(entry) for entry in state) if state else "OK"
        return str(state)

    def _runtime_diagnostics(self, device: Any | None = None) -> str:
        diagnostics: list[str] = []
        for label, getter_name in (
            ("main state", "get_state"),
            ("device state", "get_device_state"),
            ("voltage state", "get_voltage_state"),
            ("temperature state", "get_temperature_state"),
        ):
            state = self._safe_query_state(getter_name, device=device)
            if state:
                diagnostics.append(f"{label}: {state}")
        if not diagnostics:
            return ""
        return " (" + "; ".join(diagnostics) + ")"

    def _restore_off_ui_state(self) -> None:
        """Reset toolbar ON/OFF widgets back to OFF after a failed startup."""
        sync_on_state = getattr(self.controllerParent, "_set_on_ui_state", None)
        if callable(sync_on_state):
            sync_on_state(False)
            return
        if hasattr(self.controllerParent, "onAction"):
            self.controllerParent.onAction.state = False
        sync_local = getattr(self.controllerParent, "_sync_local_on_action", None)
        if callable(sync_local):
            sync_local()

    @contextlib.contextmanager
    def _controller_lock_section(
        self,
        timeout_message: str,
        *,
        already_acquired: bool = False,
    ):
        """Acquire the controller lock without swallowing hardware exceptions."""
        acquire_timeout = getattr(self.lock, "acquire_timeout", None)
        if callable(acquire_timeout):
            with acquire_timeout(
                1,
                timeoutMessage=timeout_message,
                already_acquired=already_acquired,
            ) as lock_acquired:
                if not lock_acquired:
                    raise TimeoutError(timeout_message)
                yield
            return

        acquire = getattr(self.lock, "acquire", None)
        release = getattr(self.lock, "release", None)
        if callable(acquire) and callable(release):
            if already_acquired:
                yield
                return
            if not acquire(timeout=1):
                self.print(timeout_message, flag=PRINT.ERROR)
                raise TimeoutError(timeout_message)
            try:
                yield
            finally:
                release()
            return

        raise TypeError(
            "DMMR controller lock must provide either acquire_timeout() or "
            "acquire()/release()."
        )

    def _begin_transition(self, target_on: bool) -> bool:
        """Mark a global DMMR ON/OFF transition as active."""
        if self.transitioning:
            return False
        self.transitioning = True
        self.transition_target_on = bool(target_on)
        return True

    def _end_transition(self) -> None:
        """Clear transition bookkeeping after a global DMMR ON/OFF sequence."""
        self.transitioning = False
        self.transition_target_on = None

    def _format_exception(self, exc: Exception) -> str:
        message = str(exc).strip()
        lower_message = message.lower()
        com_number = _coerce_int(getattr(self.controllerParent, "com", None), 0)

        if "timed out during 'open_port'" in lower_message:
            hint = (
                f" Selected COM{com_number} did not respond. Check that the DMMR is "
                "powered, that the configured COM port is correct, and that no other "
                "application is holding the port."
            )
            message = f"{message}{hint}"
        elif "open_port failed:" in lower_message and "error opening port" in lower_message:
            hint = (
                f" Windows could not open COM{com_number}. The port is likely wrong, "
                "already in use, or stale after a previous connection failure. Close "
                "other serial tools and replug or power-cycle the DMMR before retrying."
            )
            message = f"{message}{hint}"

        if message:
            return f"{type(exc).__name__}: {message}"
        return repr(exc)
