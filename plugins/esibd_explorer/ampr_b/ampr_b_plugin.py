"""ESIBD Explorer plugin for the CGC AMPR amplifier."""

from __future__ import annotations

import contextlib
import importlib
import sys
import time
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

_BUNDLED_RUNTIME_PACKAGE = "esibd_ampr_b_plugin_runtime"
_AMPR_DRIVER_CLASS: type[Any] | None = None
_CHANNEL_NAME_KEY = getattr(Parameter, "NAME", getattr(Channel, "NAME", "Name"))
_CHANNEL_ENABLED_KEY = getattr(Channel, "ENABLED", "Enabled")
_CHANNEL_REAL_KEY = getattr(Channel, "REAL", "Real")
_PARAMETER_MIN_KEY = getattr(Parameter, "MIN", "Min")
_PARAMETER_MAX_KEY = getattr(Parameter, "MAX", "Max")
_PARAMETER_ADVANCED_KEY = getattr(Parameter, "ADVANCED", "Advanced")
_PARAMETER_TOOLTIP_KEY = getattr(Parameter, "TOOLTIP", "Tooltip")
_PARAMETER_EVENT_KEY = getattr(Parameter, "EVENT", "Event")
_AMPR_MODULE_KEY = "Module"
_AMPR_CHANNEL_ID_KEY = "CH"
_CHANNELS_PER_MODULE = 4
_AMPR_ABS_VOLTAGE_LIMIT = 1000.0
_AMPR_MIN_ROW_HEIGHT = 28
_AMPR_RAMP_STEP_S = 0.1


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


def _channel_key_from_item(item: dict[str, Any]) -> tuple[int, int]:
    """Return the physical AMPR output addressed by one channel item."""
    return (
        _coerce_int(item.get(_AMPR_MODULE_KEY), 0),
        _coerce_int(item.get(_AMPR_CHANNEL_ID_KEY), 1),
    )


def _generic_channel_name(device_name: str, module: int, channel_id: int) -> str:
    """Generate a stable generic channel name from the physical mapping."""
    return f"{device_name}_M{module:02d}_CH{channel_id}"


def _build_generic_channel_item(
    device_name: str,
    module: int,
    channel_id: int,
    default_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a generic channel config for a newly detected physical output."""
    item = dict(default_item or {})
    item[_CHANNEL_NAME_KEY] = _generic_channel_name(device_name, module, channel_id)
    item[_AMPR_MODULE_KEY] = str(module)
    item[_AMPR_CHANNEL_ID_KEY] = str(channel_id)
    item[_CHANNEL_REAL_KEY] = True
    item[_CHANNEL_ENABLED_KEY] = False
    return item


def _detected_output_keys(detected_modules: list[int]) -> list[tuple[int, int]]:
    """Expand detected modules into the full ordered list of physical outputs."""
    return [
        (module, channel_id)
        for module in sorted({_coerce_int(module, -1) for module in detected_modules})
        if module >= 0
        for channel_id in range(1, _CHANNELS_PER_MODULE + 1)
    ]


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
        return all(_channel_key_from_item(item) == (0, 1) for item in items)

    for item in items:
        for key, default_value in default_item.items():
            if key == _CHANNEL_NAME_KEY:
                continue
            item_value = item.get(key, default_value)
            if key in {_AMPR_MODULE_KEY, _AMPR_CHANNEL_ID_KEY}:
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
    """Remove stale AMPR1..N bootstrap channels from previously polluted configs."""
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
            f"Removed legacy AMPR bootstrap channels: "
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
    detected_keys = _detected_output_keys(detected_modules)
    if not detected_keys:
        return current_items, []

    if _looks_like_bootstrap_items(current_items, device_name, default_item=default_item):
        bootstrap_items = [
            _build_generic_channel_item(
                device_name,
                module,
                channel_id,
                default_item=default_item,
            )
            for module, channel_id in detected_keys
        ]
        return bootstrap_items, [
            (
                "AMPR bootstrap config replaced from hardware scan.",
                None,
            )
        ]

    current_items, cleanup_logs = _strip_legacy_bootstrap_residue(
        current_items,
        device_name=device_name,
        default_item=default_item,
    )

    detected_set = set(detected_keys)
    kept_keys: set[tuple[int, int]] = set()
    added_modules: set[int] = set()
    virtualized_modules: set[int] = set()
    reactivated_modules: set[int] = set()
    duplicate_entries: list[tuple[str, int, int]] = []
    synced_items: list[dict[str, Any]] = []

    for item in current_items:
        synced_item = dict(item)
        module, channel_id = _channel_key_from_item(synced_item)
        key = (module, channel_id)
        if key in kept_keys:
            duplicate_entries.append(
                (str(synced_item.get(_CHANNEL_NAME_KEY, "")), module, channel_id)
            )
            synced_item[_CHANNEL_REAL_KEY] = False
            synced_items.append(synced_item)
            continue

        kept_keys.add(key)
        if key in detected_set:
            if not _coerce_bool(synced_item.get(_CHANNEL_REAL_KEY), default=True):
                reactivated_modules.add(module)
            synced_item[_CHANNEL_REAL_KEY] = True
        else:
            if _coerce_bool(synced_item.get(_CHANNEL_REAL_KEY), default=True):
                virtualized_modules.add(module)
            synced_item[_CHANNEL_REAL_KEY] = False
        synced_items.append(synced_item)

    for module, channel_id in detected_keys:
        key = (module, channel_id)
        if key in kept_keys:
            continue
        synced_items.append(
            _build_generic_channel_item(
                device_name,
                module,
                channel_id,
                default_item=default_item,
            )
        )
        added_modules.add(module)

    log_entries: list[tuple[str, PRINT | None]] = list(cleanup_logs)
    if added_modules:
        log_entries.append(
            (
                "Added generic AMPR channels for detected modules: "
                + ", ".join(str(module) for module in sorted(added_modules)),
                None,
            )
        )
    if virtualized_modules:
        log_entries.append(
            (
                "Marked AMPR channels virtual because modules are absent: "
                + ", ".join(str(module) for module in sorted(virtualized_modules)),
                None,
            )
        )
    if reactivated_modules:
        log_entries.append(
            (
                "Reactivated AMPR channels for modules: "
                + ", ".join(str(module) for module in sorted(reactivated_modules)),
                None,
            )
        )
    for channel_name, module, channel_id in duplicate_entries:
        log_entries.append(
            (
                f"Duplicate AMPR mapping detected for module {module} CH{channel_id}: {channel_name}",
                PRINT.WARNING,
            )
        )
    return synced_items, log_entries


def _prepend_sys_path(path: Path) -> None:
    """Prepend a path to ``sys.path`` once."""
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def _find_repo_src_path(start: Path) -> Path | None:
    """Locate the repository ``src`` directory containing ``cgc``."""
    for parent in (start, *start.parents):
        src_path = parent / "src"
        if (src_path / "cgc" / "__init__.py").exists():
            return src_path
    return None


def _get_ampr_driver_class() -> type[Any]:
    """Load the AMPR driver lazily from the bundled runtime or a fallback."""
    global _AMPR_DRIVER_CLASS

    if _AMPR_DRIVER_CLASS is not None:
        return _AMPR_DRIVER_CLASS

    plugin_dir = Path(__file__).resolve().parent
    bundled_runtime_root = plugin_dir / "vendor"
    bundled_runtime_package = (
        bundled_runtime_root / _BUNDLED_RUNTIME_PACKAGE / "__init__.py"
    )
    if bundled_runtime_package.exists():
        _prepend_sys_path(bundled_runtime_root)
        module = importlib.import_module(f"{_BUNDLED_RUNTIME_PACKAGE}.ampr")
        _AMPR_DRIVER_CLASS = cast(type[Any], module.AMPR)
        return _AMPR_DRIVER_CLASS

    try:
        module = importlib.import_module("cgc.ampr")
    except ModuleNotFoundError as exc:
        if exc.name not in {"cgc", "cgc.ampr"}:
            raise

        src_path = _find_repo_src_path(plugin_dir)
        if src_path is None:
            raise

        _prepend_sys_path(src_path)
        module = importlib.import_module("cgc.ampr")

    _AMPR_DRIVER_CLASS = cast(type[Any], module.AMPR)
    return _AMPR_DRIVER_CLASS


def providePlugins() -> "list[type[Plugin]]":
    """Return the plugins provided by this module."""
    return [AMPRDevice]


class AMPRDevice(Device):
    """Expose the CGC AMPR amplifier as an ESIBD Explorer device plugin.

    The plugin maps each ESIBD channel to one AMPR module address and one AMPR
    output channel. Channel values are applied as voltage setpoints and the
    measured channel voltages are exposed as monitors.
    """

    documentation = (
        "External ESIBD Explorer plugin for the CGC AMPR amplifier. "
        "It bundles the minimal cgc.ampr runtime it needs, applies channel "
        "values as module setpoints, and exposes measured voltages as monitors."
    )

    name = "AMPR_B"
    version = "0.1.0"
    supportedVersion = "0.8"
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = "V"
    useMonitors = True
    useOnOffLogic = True
    iconFile = "ampr.png"
    channels: "list[AMPRChannel]"

    COM = "COM"
    BAUDRATE = "Baud rate"
    CONNECT_TIMEOUT = "Connect timeout (s)"
    STARTUP_TIMEOUT = "Startup timeout (s)"
    RAMP_RATE = "Ramp rate (V/s)"
    STATE = "State"
    DETECTED_MODULES = "Detected modules"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = AMPRChannel

    def initGUI(self) -> None:
        super().initGUI()
        if hasattr(self, "initAction"):
            self.initAction.setVisible(False)
        if hasattr(self, "closeCommunicationAction"):
            shutdown_tooltip = f"Shutdown {self.name} and close communication."
            with contextlib.suppress(TypeError):
                self.closeCommunicationAction.triggered.disconnect()
            self.closeCommunicationAction.triggered.connect(self.shutdownCommunication)
            self.closeCommunicationAction.setToolTip(shutdown_tooltip)
            self.closeCommunicationAction.setText(shutdown_tooltip)
        self.controller = AMPRController(controllerParent=self)

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

    def getChannels(self) -> "list[AMPRChannel]":
        return cast("list[AMPRChannel]", super().getChannels())

    com: int
    baudrate: int
    connect_timeout_s: float
    startup_timeout_s: float
    ramp_rate_v_s: float
    main_state: str
    detected_modules: str
    device_state_summary: str
    interlock_state_summary: str
    voltage_state_summary: str

    def getConfiguredModules(self) -> list[int]:
        """Return sorted module addresses referenced by real channels."""
        return sorted(
            {channel.module_address() for channel in self.getChannels() if channel.real}
        )

    def _current_channel_items(self) -> list[dict[str, Any]]:
        """Snapshot current channels into config dictionaries."""
        return [
            channel.asDict()
            for channel in self.getChannels()
        ]

    def _default_channel_template(self) -> dict[str, dict[str, Any]]:
        """Return the default AMPR channel parameter definitions."""
        return self.channelType(channelParent=self, tree=None).getSortedDefaultChannel()

    def _default_channel_item(self) -> dict[str, Any]:
        """Return the persisted default AMPR channel configuration."""
        return self.channelType(channelParent=self, tree=None).asDict()

    def _ensure_local_on_action(self) -> None:
        """Expose the global AMPR ON/OFF control directly in the plugin toolbar."""
        if (
            not self.useOnOffLogic
            or hasattr(self, "deviceOnAction")
            or not hasattr(self, "closeCommunicationAction")
        ):
            return

        self.deviceOnAction = self.addStateAction(
            event=lambda checked=False: self.setOn(on=checked),
            toolTipFalse=f"Initialize communication if needed and turn {self.name} ON.",
            iconFalse=self.makeCoreIcon("rocket-fly.png"),
            toolTipTrue=f"Turn {self.name} OFF.",
            iconTrue=self.getIcon(),
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

    def _ensure_status_widgets(self) -> None:
        """Add compact global AMPR status labels to the plugin toolbar."""
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
        """Return a compact badge style that reflects the AMPR main state."""
        state = str(getattr(self, "main_state", "Disconnected") or "Disconnected")
        if state == "ST_ON":
            background = "#2f855a"
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
        """Return the compact AMPR runtime summary displayed in the toolbar."""
        modules = str(getattr(self, "detected_modules", "") or "None")
        interlock = _compact_status_text(
            getattr(self, "interlock_state_summary", None),
            default="n/a",
        )
        faults = _compact_status_text(
            getattr(self, "device_state_summary", None),
            default="n/a",
        )
        return f"Modules: {modules} | Interlock: {interlock} | Faults: {faults}"

    def _status_tooltip_text(self) -> str:
        """Return the full AMPR status tooltip for the toolbar widgets."""
        return "\n".join(
            (
                f"State: {getattr(self, 'main_state', 'Disconnected') or 'Disconnected'}",
                f"Modules: {getattr(self, 'detected_modules', '') or 'None'}",
                f"Interlock: {getattr(self, 'interlock_state_summary', '') or 'n/a'}",
                f"Faults: {getattr(self, 'device_state_summary', '') or 'n/a'}",
                f"Voltage rails: {getattr(self, 'voltage_state_summary', '') or 'n/a'}",
            )
        )

    def _update_status_widgets(self) -> None:
        """Refresh the global AMPR status labels in the toolbar."""
        badge = getattr(self, "statusBadgeLabel", None)
        summary = getattr(self, "statusSummaryLabel", None)
        if badge is None or summary is None:
            return

        badge_text = str(getattr(self, "main_state", "Disconnected") or "Disconnected")
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
        """Hide framework columns that are not useful for the AMPR UI."""
        if self.tree is None or not self.channels:
            return

        parameter_names = list(self.channels[0].getSortedDefaultChannel())
        for hidden_name in (Channel.COLLAPSE, Channel.REAL):
            if hidden_name in parameter_names:
                self.tree.setColumnHidden(parameter_names.index(hidden_name), True)

    def _sync_channels_from_detected_modules(self, detected_modules: list[int]) -> bool:
        """Synchronize channels from the latest detected AMPR module scan."""
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
                    header.setSectionResizeMode(
                        type(header).ResizeMode.ResizeToContents
                    )
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
        """Skip the generic 9-channel bootstrap until AMPR hardware is initialized."""
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
                    f"AMPR config file {file} not found. "
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
        """Handle advanced columns without hiding AMPR channels."""
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
        """Avoid division by zero before the first AMPR channel discovery."""
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
                "AMPR hardware initialization."
            )

    def getDefaultSettings(self) -> dict[str, dict]:
        settings = super().getDefaultSettings()
        settings[f"{self.name}/{self.COM}"] = parameterDict(
            value=1,
            minimum=1,
            maximum=255,
            toolTip="Windows COM port number used by the AMPR controller.",
            parameterType=PARAMETERTYPE.INT,
            attr="com",
        )
        settings[f"{self.name}/{self.BAUDRATE}"] = parameterDict(
            value=230400,
            minimum=1,
            maximum=1_000_000,
            toolTip="Baud rate passed to cgc.ampr.AMPR.",
            parameterType=PARAMETERTYPE.INT,
            attr="baudrate",
        )
        settings[f"{self.name}/{self.CONNECT_TIMEOUT}"] = parameterDict(
            value=5.0,
            minimum=1.0,
            maximum=30.0,
            toolTip="Timeout in seconds used to connect and validate the controller.",
            parameterType=PARAMETERTYPE.FLOAT,
            attr="connect_timeout_s",
        )
        settings[f"{self.name}/{self.STARTUP_TIMEOUT}"] = parameterDict(
            value=20.0,
            minimum=1.0,
            maximum=120.0,
            toolTip="Timeout in seconds used to wait for the AMPR to reach ST_ON after pressing ON.",
            parameterType=PARAMETERTYPE.FLOAT,
            attr="startup_timeout_s",
        )
        settings[f"{self.name}/{self.RAMP_RATE}"] = parameterDict(
            value=10.0,
            minimum=0.0,
            maximum=_AMPR_ABS_VOLTAGE_LIMIT,
            toolTip=(
                "Software ramp rate used for AMPR global ON/OFF transitions. "
                "Set to 0 to disable ramping."
            ),
            parameterType=PARAMETERTYPE.FLOAT,
            attr="ramp_rate_v_s",
        )
        settings[f"{self.name}/{self.STATE}"] = parameterDict(
            value="Disconnected",
            toolTip="Latest AMPR controller state reported by the driver.",
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

    def closeCommunication(self) -> None:
        """Close communication safely even if plugin finalization failed early."""
        if self.useOnOffLogic and not hasattr(self, "onAction"):
            self.stopAcquisition()
            if self.controller:
                self.controller.closeCommunication()
            self.recording = False
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

    def shutdownCommunication(self) -> None:
        """Run the full AMPR hardware shutdown sequence from the toolbar action."""
        if self.useOnOffLogic and hasattr(self, "onAction"):
            self.onAction.state = False
            self._sync_local_on_action()
        self.stopAcquisition()
        if self.controller:
            self.controller.shutdownCommunication()
        self.recording = False

    def setOn(self, on: "bool | None" = None) -> None:
        """Toggle the AMPR without the generic immediate apply=True jump."""
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
        if getattr(self, "loading", False):
            return

        if getattr(self, "initialized", False):
            begin_transition = getattr(self.controller, "_begin_transition", None) if self.controller else None
            if self.controller and (not callable(begin_transition) or begin_transition(self.isOn())):
                self.controller.toggleOnFromThread(parallel=True)
            else:
                for channel in self.channels:
                    if channel.controller:
                        channel.controller.toggleOnFromThread(parallel=True)
        elif hasattr(self, "onAction") and self.isOn():
            self.initializeCommunication()


class AMPRChannel(Channel):
    """AMPR output channel definition."""

    MODULE = "Module"
    ID = "CH"
    channelParent: AMPRDevice

    def getDefaultChannel(self) -> dict[str, dict]:
        self.module: int
        self.id: int

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = "Voltage (V)"
        channel[self.VALUE][_PARAMETER_MIN_KEY] = -_AMPR_ABS_VOLTAGE_LIMIT
        channel[self.VALUE][_PARAMETER_MAX_KEY] = _AMPR_ABS_VOLTAGE_LIMIT
        channel[self.ENABLED][_PARAMETER_ADVANCED_KEY] = False
        channel[self.ENABLED][Parameter.HEADER] = "On"
        channel[self.ENABLED][_PARAMETER_TOOLTIP_KEY] = (
            "Enable this AMPR output channel. Disabled channels are held at 0 V."
        )
        channel[self.ACTIVE][Parameter.HEADER] = "Manual"
        channel[self.ACTIVE][_PARAMETER_TOOLTIP_KEY] = (
            "If enabled, this channel uses its manual voltage setpoint. "
            "If disabled, ESIBD will drive it from the channel equation."
        )
        channel[self.DISPLAY][Parameter.HEADER] = "Display"
        channel[self.DISPLAY][_PARAMETER_EVENT_KEY] = self.displayChanged
        channel[self.SCALING][Parameter.VALUE] = "large"
        channel[self.MIN][Parameter.VALUE] = -_AMPR_ABS_VOLTAGE_LIMIT
        channel[self.MIN][_PARAMETER_ADVANCED_KEY] = False
        channel[self.MIN][_PARAMETER_MIN_KEY] = -_AMPR_ABS_VOLTAGE_LIMIT
        channel[self.MIN][_PARAMETER_MAX_KEY] = _AMPR_ABS_VOLTAGE_LIMIT
        channel[self.MIN][_PARAMETER_EVENT_KEY] = self.minChanged
        channel[self.MAX][Parameter.VALUE] = _AMPR_ABS_VOLTAGE_LIMIT
        channel[self.MAX][_PARAMETER_ADVANCED_KEY] = False
        channel[self.MAX][_PARAMETER_MIN_KEY] = -_AMPR_ABS_VOLTAGE_LIMIT
        channel[self.MAX][_PARAMETER_MAX_KEY] = _AMPR_ABS_VOLTAGE_LIMIT
        channel[self.MAX][_PARAMETER_EVENT_KEY] = self.maxChanged
        channel[self.MODULE] = parameterDict(
            value="0",
            parameterType=PARAMETERTYPE.LABEL,
            advanced=False,
            indicator=True,
            header="Mod",
            attr="module",
        )
        channel[self.ID] = parameterDict(
            value="1",
            parameterType=PARAMETERTYPE.LABEL,
            advanced=False,
            indicator=True,
            header="CH ",
            attr="id",
        )
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        if self.OPTIMIZE in self.displayedParameters:
            self.displayedParameters.remove(self.OPTIMIZE)
        self.displayedParameters.append(self.MODULE)
        self.displayedParameters.append(self.ID)

    def initGUI(self, item: dict) -> None:
        super().initGUI(item)
        self._upgrade_toggle_widget(self.ENABLED, "On", 40)
        self._upgrade_toggle_widget(self.ACTIVE, "Manual", 72)
        if self.useDisplays:
            self._upgrade_toggle_widget(self.DISPLAY, "Display", 72)
        self.scalingChanged()

    def scalingChanged(self) -> None:
        super().scalingChanged()
        if self.rowHeight >= _AMPR_MIN_ROW_HEIGHT:
            return
        self.rowHeight = _AMPR_MIN_ROW_HEIGHT
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
            parameter.check.setMaximumHeight(max(parameter.rowHeight, _AMPR_MIN_ROW_HEIGHT))
            parameter.check.setMinimumWidth(minimum_width)
            parameter.check.setText(label)
            parameter.check.setCheckable(True)
            if hasattr(parameter.check, "setAutoRaise"):
                parameter.check.setAutoRaise(False)
        parameter.value = initial_value

    def monitorChanged(self) -> None:
        self.updateWarningState(
            self.enabled
            and self.channelParent.controller.acquiring
            and (
                (
                    self.channelParent.isOn()
                    and not np.isnan(self.monitor)
                    and abs(self.monitor - self.value) > 1
                )
                or (
                    not self.channelParent.isOn()
                    and not np.isnan(self.monitor)
                    and abs(self.monitor) > 1
                )
            )
        )

    def _channel_log_prefix(self) -> str:
        return (
            f"AMPR channel {getattr(self, 'name', 'Unknown')} "
            f"(module {self.module_address()} CH{self.channel_number()})"
        )

    def _log_channel_event(self, message: str) -> None:
        if getattr(self.channelParent, "loading", False):
            return
        self.channelParent.print(f"{self._channel_log_prefix()}: {message}")

    def nameChanged(self) -> None:
        super().nameChanged()
        self._log_channel_event(f"Name changed to {self.name!r}.")

    def valueChanged(self) -> None:
        super().valueChanged()
        self._log_channel_event(f"Voltage setpoint changed to {float(self.value):.3f} V.")

    def equationChanged(self) -> None:
        super().equationChanged()
        if str(getattr(self, "equation", "")).strip():
            self._log_channel_event(f"Equation changed to {self.equation!r}.")
            return
        self._log_channel_event("Equation cleared.")

    def activeChanged(self) -> None:
        super().activeChanged()
        mode = "manual" if self.active else "equation"
        self._log_channel_event(f"Control mode changed to {mode}.")

    def realChanged(self) -> None:
        self.getParameterByName(self.MODULE).setVisible(self.real)
        self.getParameterByName(self.ID).setVisible(self.real)
        super().realChanged()

    def enabledChanged(self) -> None:
        super().enabledChanged()
        if not self.enabled:
            self.monitor = np.nan
        state = "ON" if self.enabled else "OFF"
        self._log_channel_event(f"Output switched {state}.")

    def displayChanged(self) -> None:
        super().updateDisplay()
        state = "ON" if self.display else "OFF"
        self._log_channel_event(f"Display switched {state}.")

    def minChanged(self) -> None:
        super().updateMin()
        self._log_channel_event(f"Minimum changed to {float(self.min):.3f} V.")

    def maxChanged(self) -> None:
        super().updateMax()
        self._log_channel_event(f"Maximum changed to {float(self.max):.3f} V.")

    def module_address(self) -> int:
        """Return the configured AMPR module address as an integer."""
        return _coerce_int(self.module, 0)

    def channel_number(self) -> int:
        """Return the configured AMPR channel number as an integer."""
        return _coerce_int(self.id, 1)


class AMPRController(DeviceController):
    """AMPR hardware controller used by the ESIBD Explorer plugin."""

    controllerParent: AMPRDevice

    def __init__(self, controllerParent) -> None:
        super().__init__(controllerParent=controllerParent)
        self.device: Any | None = None
        self.detected_module_ids: list[int] = []
        self.detected_modules_text = ""
        self.main_state = "Disconnected"
        self.device_state_summary = "n/a"
        self.interlock_state_summary = "n/a"
        self.voltage_state_summary = "n/a"
        self.initialized = False
        self.ramping = False
        self.transitioning = False
        self.transition_target_on: bool | None = None

    def initializeValues(self, reset: bool = False) -> None:
        if getattr(self, "values", None) is None or reset:
            get_channels = getattr(self.controllerParent, "getChannels", None)
            if not callable(get_channels):
                self.values = {}
                return
            self.values = {
                (channel.module_address(), channel.channel_number()): np.nan
                for channel in get_channels()
                if channel.real
            }

    def runInitialization(self) -> None:
        self.initialized = False
        self._dispose_device()
        try:
            ampr_driver_class = _get_ampr_driver_class()
            self.device = ampr_driver_class(
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
            self._refresh_module_scan()
            self._update_state()
            self.signalComm.initCompleteSignal.emit()
        except Exception as exc:  # noqa: BLE001
            self.print(
                f"AMPR initialization failed on COM{int(self.controllerParent.com)}: "
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
                "AMPR initialization simulated because ESIBD Test mode is active. "
                "No hardware communication was attempted.",
                flag=PRINT.WARNING,
            )
            return

        modules_text = self.detected_modules_text or "None"
        self.print(
            f"AMPR initialized on COM{int(self.controllerParent.com)}. "
            f"State: {self.main_state}. Detected modules: {modules_text}."
        )
        if self.main_state == "ST_ON":
            start_acquisition = getattr(self, "startAcquisition", None)
            if callable(start_acquisition):
                start_acquisition()
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
        if self.main_state != "ST_ON":
            self.initializeValues(reset=True)
            return

        new_values = {
            (channel.module_address(), channel.channel_number()): np.nan
            for channel in self.controllerParent.getChannels()
            if channel.real
        }

        configured_modules = set(self.controllerParent.getConfiguredModules())
        detected_modules = set(self.detected_module_ids)
        if detected_modules:
            poll_modules = sorted(configured_modules & detected_modules)
        else:
            poll_modules = sorted(configured_modules)

        for module in poll_modules:
            try:
                voltages = self.device.get_module_voltages(module)
            except Exception as exc:  # noqa: BLE001
                self.errorCount += 1
                self.print(f"Failed to read module {module}: {exc}", flag=PRINT.ERROR)
                continue

            for channel_id, voltage_data in voltages.items():
                measured = voltage_data.get("measured")
                new_values[(module, channel_id)] = (
                    np.nan if measured is None else float(measured)
                )

        self.values = new_values

    def fakeNumbers(self) -> None:
        self.initializeValues(reset=True)
        # Do not fabricate AMPR output readbacks in ESIBD test mode.
        # Showing random "measured" voltages is misleading because no hardware
        # communication happened at all in that mode.

    def applyValue(self, channel: AMPRChannel) -> None:
        device = self.device
        if (
            device is None
            or not getattr(self, "initialized", False)
            or getattr(self, "ramping", False)
            or getattr(self, "transitioning", False)
            or not self.controllerParent.isOn()
            or self.main_state != "ST_ON"
        ):
            return

        target_voltage = float(channel.value if channel.enabled else 0.0)
        module = channel.module_address()
        channel_id = channel.channel_number()
        with self.lock.acquire_timeout(
            1,
            timeoutMessage=(
                f"Could not acquire lock to apply module {module} CH{channel_id}."
            ),
        ) as lock_acquired:
            if not lock_acquired:
                return
            device = self.device
            if device is None:
                return
            try:
                status = device.set_module_voltage(module, channel_id, target_voltage)
            except Exception as exc:  # noqa: BLE001
                self.errorCount += 1
                self.print(
                    f"Failed to apply {target_voltage:.3f} V to module "
                    f"{module} CH{channel_id}: {exc}",
                    flag=PRINT.ERROR,
                )
                return

        if status != device.NO_ERR:
            self.errorCount += 1
            self.print(
                f"AMPR rejected {target_voltage:.3f} V for module "
                f"{module} CH{channel_id}: {self._format_status(status, device=device)}",
                flag=PRINT.ERROR,
            )

    def updateValues(self) -> None:
        if self.values is None:
            return

        self._sync_status_to_gui()
        device_is_on = self.controllerParent.isOn()
        for channel in self.controllerParent.getChannels():
            if channel.enabled and channel.real and device_is_on:
                channel.monitor = np.nan if channel.waitToStabilize else self.values.get(
                    (channel.module_address(), channel.channel_number()),
                    np.nan,
                )
                continue
            channel.monitor = np.nan

    def toggleOn(self) -> None:
        super().toggleOn()
        device = self.device
        if device is None:
            return
        if getattr(self, "acquiring", False):
            self.stopAcquisition()

        startup_timeout_s = float(
            getattr(
                self.controllerParent,
                "startup_timeout_s",
                self.controllerParent.connect_timeout_s,
            )
        )
        ramp_rate_v_s = max(
            0.0,
            float(getattr(self.controllerParent, "ramp_rate_v_s", 0.0)),
        )
        state_updated = False
        startup_completed = False
        startup_targets: dict[tuple[int, int], float] = {}

        try:
            if self.controllerParent.isOn():
                with contextlib.suppress(Exception):
                    self.controllerParent.updateValues(apply=False)
                startup_targets = self._channel_target_voltages(respect_device_state=True)
                if startup_targets:
                    self._apply_target_voltages(
                        {key: 0.0 for key in startup_targets},
                        timeout_message="Could not acquire lock to prime AMPR outputs.",
                    )
                with self.lock.acquire_timeout(
                    1,
                    timeoutMessage="Could not acquire lock to toggle the AMPR PSU.",
                ) as lock_acquired:
                    if not lock_acquired:
                        return
                    device = self.device
                    if device is None:
                        return
                    self.print(
                        f"Starting AMPR PSU. Waiting up to {startup_timeout_s:.1f} s for ST_ON."
                    )
                    device.initialize(timeout_s=startup_timeout_s)
                    status = device.NO_ERR
                    startup_completed = True
                if status == device.NO_ERR:
                    self._refresh_module_scan()
                    self._update_state()
                    state_updated = True
                    self._ramp_target_voltages(
                        start_targets={key: 0.0 for key in startup_targets},
                        end_targets=startup_targets,
                        rate_v_s=ramp_rate_v_s,
                        label="up",
                    )
            else:
                start_targets = self._channel_target_voltages(respect_device_state=False)
                self._ramp_target_voltages(
                    start_targets=start_targets,
                    end_targets={key: 0.0 for key in start_targets},
                    rate_v_s=ramp_rate_v_s,
                    label="down",
                )
                with self.lock.acquire_timeout(
                    1,
                    timeoutMessage="Could not acquire lock to toggle the AMPR PSU.",
                ) as lock_acquired:
                    if not lock_acquired:
                        return
                    device = self.device
                    if device is None:
                        return
                    status, _enabled = device.enable_psu(False)
        except Exception as exc:  # noqa: BLE001
            self.errorCount += 1
            if startup_completed:
                self._safe_disable_after_toggle_failure(startup_targets)
            self._update_state()
            self.print(
                f"Failed to toggle AMPR PSU: {self._format_exception(exc)}"
                f"{self._runtime_diagnostics(device=device)}",
                flag=PRINT.ERROR,
            )
            return
        finally:
            self._end_transition()

        if status != device.NO_ERR:
            self.errorCount += 1
            self.print(
                f"Failed to toggle AMPR PSU: {self._format_status(status, device=device)}",
                flag=PRINT.ERROR,
            )

        if not state_updated:
            self._update_state()
        if self.controllerParent.isOn() and self.main_state == "ST_ON":
            start_acquisition = getattr(self, "startAcquisition", None)
            if callable(start_acquisition):
                start_acquisition()
        self.print(
            f"AMPR PSU turned {'ON' if self.controllerParent.isOn() else 'OFF'}. "
            f"State: {self.main_state}."
        )

    def closeCommunication(self) -> None:
        super().closeCommunication()
        self.main_state = "Disconnected"
        self.detected_module_ids = []
        self.detected_modules_text = ""
        self.device_state_summary = "n/a"
        self.interlock_state_summary = "n/a"
        self.voltage_state_summary = "n/a"
        self._sync_status_to_gui()
        self._dispose_device()
        self.initialized = False

    def shutdownCommunication(self) -> None:
        """Run the AMPR shutdown sequence before releasing communication resources."""
        device = self.device
        if device is None:
            self.closeCommunication()
            return

        super().closeCommunication()
        self.print("Starting AMPR shutdown sequence.")
        try:
            device.shutdown()
        except Exception as exc:  # noqa: BLE001
            self.errorCount += 1
            self._update_state()
            self.print(
                f"AMPR shutdown failed: {self._format_exception(exc)}"
                f"{self._runtime_diagnostics(device=device)}",
                flag=PRINT.ERROR,
            )
        else:
            self.print("AMPR shutdown sequence completed.")
        finally:
            self.main_state = "Disconnected"
            self.detected_module_ids = []
            self.detected_modules_text = ""
            self.device_state_summary = "n/a"
            self.interlock_state_summary = "n/a"
            self.voltage_state_summary = "n/a"
            self._sync_status_to_gui()
            self._dispose_device()
            self.initialized = False

    def _refresh_module_scan(self) -> None:
        if self.device is None:
            return

        try:
            status, mismatch, rating_failure = self.device.get_scanned_module_state()
        except Exception as exc:  # noqa: BLE001
            self.print(f"Could not query scanned AMPR module state: {exc}", flag=PRINT.WARNING)
            status = None
            mismatch = False
            rating_failure = False

        if (
            self.device is not None
            and status == self.device.NO_ERR
            and (mismatch or rating_failure)
        ):
            rescan_status = self.device.rescan_modules()
            if rescan_status != self.device.NO_ERR:
                raise RuntimeError(
                    f"AMPR rescan failed: {self._format_status(rescan_status)}"
                )
            persist_status = self.device.set_scanned_module_state()
            if persist_status != self.device.NO_ERR:
                raise RuntimeError(
                    "AMPR scanned module state could not be stored: "
                    f"{self._format_status(persist_status)}"
                )

        module_info = self.device.scan_modules()
        self.detected_module_ids = sorted(module_info)
        self.detected_modules_text = (
            ", ".join(str(module) for module in self.detected_module_ids)
            if self.detected_module_ids
            else "None"
        )

        configured_modules = set(self.controllerParent.getConfiguredModules())
        current_items = self.controllerParent._current_channel_items()
        default_item = self.controllerParent._default_channel_item()
        if _looks_like_bootstrap_items(
            current_items,
            device_name=self.controllerParent.name,
            default_item=default_item,
        ):
            configured_modules.discard(0)
        missing_modules = sorted(configured_modules - set(self.detected_module_ids))
        if missing_modules:
            self.print(
                "Configured modules not detected during AMPR scan: "
                + ", ".join(str(module) for module in missing_modules),
                flag=PRINT.WARNING,
            )

    def _update_state(self) -> None:
        if self.device is None:
            self.main_state = "Disconnected"
            self.device_state_summary = "n/a"
            self.interlock_state_summary = "n/a"
            self.voltage_state_summary = "n/a"
            return

        try:
            status, _state_hex, state_name = self.device.get_state()
        except Exception as exc:  # noqa: BLE001
            self.errorCount += 1
            self.main_state = "State error"
            self.print(f"Failed to read AMPR state: {exc}", flag=PRINT.ERROR)
            self.device_state_summary = (
                self._safe_query_state("get_device_state") or "Unknown"
            )
            self.interlock_state_summary = (
                self._safe_query_state("get_interlock_state") or "Unknown"
            )
            self.voltage_state_summary = (
                self._safe_query_state("get_voltage_state") or "Unknown"
            )
            return

        if status == self.device.NO_ERR:
            self.main_state = state_name
        else:
            self.main_state = "State error"
            self.errorCount += 1
            self.print(
                f"Failed to read AMPR state: {self._format_status(status)}",
                flag=PRINT.ERROR,
            )
        self.device_state_summary = self._safe_query_state("get_device_state") or "Unknown"
        self.interlock_state_summary = (
            self._safe_query_state("get_interlock_state") or "Unknown"
        )
        self.voltage_state_summary = self._safe_query_state("get_voltage_state") or "Unknown"

    def _sync_status_to_gui(self) -> None:
        self.controllerParent.main_state = self.main_state
        self.controllerParent.detected_modules = self.detected_modules_text
        self.controllerParent.device_state_summary = self.device_state_summary
        self.controllerParent.interlock_state_summary = self.interlock_state_summary
        self.controllerParent.voltage_state_summary = self.voltage_state_summary
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
            try:
                device.close()
            except Exception:  # noqa: BLE001
                pass

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
            status, _state_hex, state = getter()
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
            ("interlock state", "get_interlock_state"),
        ):
            state = self._safe_query_state(getter_name, device=device)
            if state:
                diagnostics.append(f"{label}: {state}")
        if not diagnostics:
            return ""
        return " (" + "; ".join(diagnostics) + ")"

    def _begin_transition(self, target_on: bool) -> bool:
        """Mark a global AMPR ON/OFF transition as active."""
        if self.transitioning:
            return False
        self.transitioning = True
        self.transition_target_on = bool(target_on)
        return True

    def _end_transition(self) -> None:
        """Clear transition bookkeeping after a global AMPR ON/OFF sequence."""
        self.transitioning = False
        self.transition_target_on = None

    def _channel_target_voltages(
        self,
        *,
        respect_device_state: bool,
    ) -> dict[tuple[int, int], float]:
        """Return target voltages keyed by (module, channel)."""
        targets: dict[tuple[int, int], float] = {}
        device_is_on = getattr(self.controllerParent, "isOn", lambda: False)()
        get_channels = getattr(self.controllerParent, "getChannels", None)
        if not callable(get_channels):
            return targets
        for channel in get_channels():
            if not channel.real:
                continue
            target_voltage = 0.0
            if channel.enabled and (device_is_on or not respect_device_state):
                target_voltage = float(channel.value)
            targets[(channel.module_address(), channel.channel_number())] = target_voltage
        return targets

    @staticmethod
    def _group_target_voltages(
        targets: dict[tuple[int, int], float],
    ) -> dict[int, dict[int, float]]:
        """Group per-channel targets by module."""
        grouped_targets: dict[int, dict[int, float]] = {}
        for (module, channel_id), voltage in targets.items():
            grouped_targets.setdefault(module, {})[channel_id] = float(voltage)
        return grouped_targets

    def _apply_target_voltages_locked(
        self,
        targets: dict[tuple[int, int], float],
        *,
        device: Any,
    ) -> None:
        """Apply a full AMPR target map while the controller lock is held."""
        for module, module_targets in sorted(self._group_target_voltages(targets).items()):
            if hasattr(device, "set_module_voltages"):
                statuses = device.set_module_voltages(module, module_targets)
                for channel_id, status in statuses.items():
                    if status != device.NO_ERR:
                        raise RuntimeError(
                            "AMPR rejected "
                            f"{float(module_targets[channel_id]):.3f} V for module "
                            f"{module} CH{channel_id}: {self._format_status(status, device=device)}"
                        )
                continue

            for channel_id, voltage in sorted(module_targets.items()):
                status = device.set_module_voltage(module, channel_id, voltage)
                if status != device.NO_ERR:
                    raise RuntimeError(
                        "AMPR rejected "
                        f"{float(voltage):.3f} V for module "
                        f"{module} CH{channel_id}: {self._format_status(status, device=device)}"
                    )

    def _apply_target_voltages(
        self,
        targets: dict[tuple[int, int], float],
        *,
        timeout_message: str,
    ) -> None:
        """Apply a full AMPR target map under the controller lock."""
        if not targets:
            return

        with self.lock.acquire_timeout(1, timeoutMessage=timeout_message) as lock_acquired:
            if not lock_acquired:
                raise TimeoutError(timeout_message)
            device = self.device
            if device is None:
                raise RuntimeError("AMPR device is not available.")
            self._apply_target_voltages_locked(targets, device=device)

    def _ramp_target_voltages(
        self,
        *,
        start_targets: dict[tuple[int, int], float],
        end_targets: dict[tuple[int, int], float],
        rate_v_s: float,
        label: str,
    ) -> None:
        """Ramp all AMPR output targets simultaneously."""
        output_keys = sorted(set(start_targets) | set(end_targets))
        if not output_keys:
            return

        normalized_start = {
            key: float(start_targets.get(key, 0.0))
            for key in output_keys
        }
        normalized_end = {
            key: float(end_targets.get(key, 0.0))
            for key in output_keys
        }
        max_delta = max(
            abs(normalized_end[key] - normalized_start[key]) for key in output_keys
        )
        if max_delta <= 0.0:
            return

        if rate_v_s <= 0.0:
            self._apply_target_voltages(
                normalized_end,
                timeout_message="Could not acquire lock to apply AMPR voltages.",
            )
            return

        estimated_duration_s = max_delta / rate_v_s
        steps = max(1, int(np.ceil(estimated_duration_s / _AMPR_RAMP_STEP_S)))
        self.print(
            f"Starting AMPR ramp-{label} at {rate_v_s:.1f} V/s "
            f"(estimated {estimated_duration_s:.1f} s)."
        )
        self.ramping = True
        try:
            for step in range(1, steps + 1):
                fraction = step / steps
                step_targets = {
                    key: normalized_start[key]
                    + (normalized_end[key] - normalized_start[key]) * fraction
                    for key in output_keys
                }
                self._apply_target_voltages(
                    step_targets,
                    timeout_message="Could not acquire lock to apply AMPR ramp step.",
                )
                if step < steps:
                    time.sleep(_AMPR_RAMP_STEP_S)
        finally:
            self.ramping = False
        if getattr(self.controllerParent, "isOn", lambda: False)():
            updated_targets = self._channel_target_voltages(respect_device_state=True)
            if updated_targets != normalized_end:
                self.print("Applying updated AMPR targets queued during ramp.")
                self._apply_target_voltages(
                    updated_targets,
                    timeout_message="Could not acquire lock to apply queued AMPR targets.",
                )
        self.print(f"AMPR ramp-{label} completed.")

    def _safe_disable_after_toggle_failure(
        self,
        targets: dict[tuple[int, int], float],
    ) -> None:
        """Best-effort cleanup after a failed AMPR startup/ramp sequence."""
        device = self.device
        if device is None:
            return

        cleanup_errors: list[str] = []
        zero_targets = {key: 0.0 for key in targets}
        with self.lock.acquire_timeout(
            1,
            timeoutMessage="Could not acquire lock for AMPR failure cleanup.",
        ) as lock_acquired:
            if not lock_acquired:
                cleanup_errors.append("lock timeout")
            else:
                device = self.device
                if device is None:
                    cleanup_errors.append("device disappeared")
                else:
                    if zero_targets:
                        try:
                            self._apply_target_voltages_locked(zero_targets, device=device)
                        except Exception as cleanup_exc:  # noqa: BLE001
                            cleanup_errors.append(f"zeroing failed: {cleanup_exc}")
                    try:
                        status, _enabled = device.enable_psu(False)
                    except Exception as cleanup_exc:  # noqa: BLE001
                        cleanup_errors.append(f"disable_psu failed: {cleanup_exc}")
                    else:
                        if status != device.NO_ERR:
                            cleanup_errors.append(
                                f"disable_psu failed: {self._format_status(status, device=device)}"
                            )

        if cleanup_errors:
            self.print(
                "AMPR startup cleanup encountered issues: " + "; ".join(cleanup_errors),
                flag=PRINT.WARNING,
            )
            return
        self.print("AMPR startup cleanup disabled the PSU after failure.", flag=PRINT.WARNING)

    @staticmethod
    def _format_exception(exc: Exception) -> str:
        message = str(exc).strip()
        if message:
            return f"{type(exc).__name__}: {message}"
        return repr(exc)
