"""Drive PSU outputs from ESIBD Explorer and monitor live readbacks."""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import logging
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
_PSU_MIN_ROW_HEIGHT = 28
_PSU_TABLE_SCALING = "normal"
_PSU_LEGACY_OVERSIZED_SCALINGS = {"large", "larger", "huge"}
_PSU_NEUTRAL_WIDGET_STYLE = "background: transparent;"
_PSU_OUTPUT_ON_STYLE = (
    "background-color: #1f2933; color: #ffffff; margin:0px; padding:0px 6px;"
)
_PSU_OUTPUT_OFF_STYLE = (
    "background-color: #4a5568; color: #ffffff; margin:0px; padding:0px 6px;"
)
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


def _compact_status_text(value: Any, default: str = "n/a") -> str:
    """Return a short one-line representation for toolbar status widgets."""
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    normalized = text.replace(";", ",")
    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    if len(parts) <= 1:
        return text
    return f"{parts[0]} +{len(parts) - 1}"


def _normalize_runtime_state(state: Any) -> str:
    """Normalize fallback transport states into operator-facing labels."""
    text = str(state or "").strip()
    if not text:
        return "Disconnected"
    normalized = text.lower()
    if normalized in {"false", "disconnected"}:
        return "Disconnected"
    if normalized in {"true", "connected"}:
        return "Connected"
    return text


def _status_requires_operator_attention(state: Any) -> bool:
    """Return True when the raw state describes a fault or uncertain condition."""
    normalized = str(state or "").strip().lower()
    return any(
        token in normalized
        for token in ("err", "error", "fail", "fault", "lost", "overload", "timeout", "unknown")
    )


def _action_label(action: Any) -> str:
    """Extract a stable label from QAction-like objects and test doubles."""
    for attr_name in ("toolTip", "text", "objectName"):
        attr = getattr(action, attr_name, None)
        value = attr() if callable(attr) else attr
        if isinstance(value, str) and value:
            return value
    return ""


def _format_available_configs(configs: list[dict[str, Any]]) -> str:
    if not configs:
        return "None"

    formatted = []
    for config in configs:
        index = _coerce_int(config.get("index"), -1)
        name = str(config.get("name", "") or "").strip() or "<unnamed>"
        suffixes: list[str] = []
        if not _coerce_bool(config.get("valid"), True):
            suffixes.append("invalid")
        if not _coerce_bool(config.get("active"), True):
            suffixes.append("inactive")
        label = f"{index}:{name}" if index >= 0 else name
        if suffixes:
            label = f"{label} ({', '.join(suffixes)})"
        formatted.append(label)
    return "; ".join(formatted)


def _psu_output_state_badge_style(state: Any) -> str:
    normalized = str(state or "").strip().upper()
    if normalized == "ON":
        return _PSU_OUTPUT_ON_STYLE
    if normalized == "OFF":
        return _PSU_OUTPUT_OFF_STYLE
    return _PSU_NEUTRAL_WIDGET_STYLE


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
    removed_channels: list[int] = []
    duplicate_entries: list[tuple[str, int]] = []
    synced_items: list[dict[str, Any]] = []

    for item in current_items:
        synced_item = dict(item)
        channel_id = _channel_key_from_item(synced_item)
        if channel_id not in target_ids:
            removed_channels.append(channel_id)
            continue
        if channel_id in kept_keys:
            duplicate_entries.append(
                (str(synced_item.get(_CHANNEL_NAME_KEY, "")), channel_id)
            )
            continue

        kept_keys.add(channel_id)
        synced_item[_CHANNEL_REAL_KEY] = True
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
    if removed_channels:
        log_entries.append(
            (
                "Removed PSU channels not present on hardware: "
                + ", ".join(f"CH{channel_id}" for channel_id in removed_channels),
                None,
            )
        )
    for channel_name, channel_id in duplicate_entries:
        log_entries.append(
            (
                f"Removed duplicate PSU mapping for CH{channel_id}: {channel_name}",
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
    AVAILABLE_CONFIGS = "Available configs"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = PSUChannel

    def initGUI(self) -> None:
        super().initGUI()
        self.available_configs = []
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
        self.controller = PSUController(controllerParent=self)
        self._update_channel_column_visibility()

    def finalizeInit(self) -> None:
        super().finalizeInit()
        self._ensure_local_on_action()
        self._ensure_status_widgets()
        self._ensure_config_selectors()
        self._update_channel_column_visibility()
        self._sync_acquisition_controls()

    def getChannels(self) -> "list[PSUChannel]":
        return cast("list[PSUChannel]", super().getChannels())

    def estimateStorage(self) -> None:
        """Handle the no-channel bootstrap state used before PSU hardware sync."""
        channels = list(getattr(self, "channels", []) or [])
        if channels:
            base_estimate_storage = getattr(super(), "estimateStorage", None)
            if callable(base_estimate_storage):
                base_estimate_storage()
            return

        self.maxDataPoints = 0
        plugin_manager = getattr(self, "pluginManager", None)
        settings_plugin = getattr(plugin_manager, "Settings", None)
        settings = getattr(settings_plugin, "settings", None)
        if not isinstance(settings, dict):
            return
        max_points_setting = settings.get(f"{self.name}/{self.MAXDATAPOINTS}")
        widget = (
            max_points_setting.getWidget()
            if max_points_setting is not None and hasattr(max_points_setting, "getWidget")
            else None
        )
        if widget is not None and hasattr(widget, "setToolTip"):
            widget.setToolTip(
                "Storage estimate unavailable until PSU channels are synchronized with hardware."
            )

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
    available_configs_text: str
    available_configs: list[dict[str, Any]]

    def _current_channel_items(self) -> list[dict[str, Any]]:
        return [channel.asDict() for channel in self.getChannels()]

    def _default_channel_item(self) -> dict[str, Any]:
        return self.channelType(channelParent=self, tree=None).asDict()

    def _default_channel_template(self) -> dict[str, dict[str, Any]]:
        return self.channelType(channelParent=self, tree=None).getSortedDefaultChannel()

    def _bootstrap_channel_items(self) -> list[dict[str, Any]]:
        default_item = self._default_channel_item()
        return [
            _build_generic_channel_item(
                self.name,
                channel_id,
                default_item=default_item,
            )
            for channel_id in _PSU_CHANNEL_IDS
        ]

    def _ensure_local_on_action(self) -> None:
        """Expose the global PSU ON/OFF control directly in the plugin toolbar."""
        if (
            not self.useOnOffLogic
            or hasattr(self, "deviceOnAction")
            or not hasattr(self, "closeCommunicationAction")
        ):
            return

        self.deviceOnAction = self.addStateAction(
            event=lambda checked=False: self.setOn(on=checked),
            toolTipFalse=f"Turn {self.name} ON.",
            iconFalse=self.makeIcon(_PSU_POWER_ON_ICON),
            toolTipTrue=f"Turn {self.name} OFF and disconnect.",
            iconTrue=self.makeIcon(_PSU_POWER_OFF_ICON),
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
        raw_state = _normalize_runtime_state(getattr(self, "main_state", "Disconnected"))
        is_on = getattr(self, "isOn", None)
        if (
            raw_state != "Disconnected"
            and not _status_requires_operator_attention(raw_state)
            and callable(is_on)
            and not bool(is_on())
        ):
            return "OFF"
        return raw_state

    def _ensure_status_widgets(self) -> None:
        """Add compact global PSU status labels to the plugin toolbar."""
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
            self.statusBadgeAction = self.titleBar.insertWidget(
                insert_before,
                self.statusBadgeLabel,
            )
            self.statusSummaryAction = self.titleBar.insertWidget(
                insert_before,
                self.statusSummaryLabel,
            )
        elif hasattr(self.titleBar, "addWidget"):
            self.statusBadgeAction = self.titleBar.addWidget(self.statusBadgeLabel)
            self.statusSummaryAction = self.titleBar.addWidget(self.statusSummaryLabel)
        else:
            self.statusBadgeAction = None
            self.statusSummaryAction = None

        self._update_status_widgets()

    def _config_list_text(self) -> str:
        return str(getattr(self, "available_configs_text", "") or "").strip() or "n/a"

    def _config_list_tooltip_text(self) -> str:
        return "\n".join(
            (
                "Configs reported by the PSU controller:",
                self._config_list_text(),
            )
        )

    def _ensure_config_selectors(self) -> None:
        if (
            getattr(self, "titleBar", None) is None
            or getattr(self, "titleBarLabel", None) is None
            or hasattr(self, "availableConfigsValueLabel")
        ):
            return

        label_type = type(self.titleBarLabel)
        insert_before = getattr(
            self,
            "stretchAction",
            None,
        )
        self.availableConfigsLabel = label_type("Configs:")
        self.availableConfigsValueLabel = label_type("")
        if hasattr(self.availableConfigsLabel, "setObjectName"):
            self.availableConfigsLabel.setObjectName(f"{self.name}ConfigsLabel")
        if hasattr(self.availableConfigsValueLabel, "setObjectName"):
            self.availableConfigsValueLabel.setObjectName(f"{self.name}ConfigsValue")
        if hasattr(self.availableConfigsValueLabel, "setStyleSheet"):
            self.availableConfigsValueLabel.setStyleSheet("QLabel { padding-left: 4px; }")
        if insert_before is not None and hasattr(self.titleBar, "insertWidget"):
            self.availableConfigsAction = self.titleBar.insertWidget(
                insert_before,
                self.availableConfigsLabel,
            )
            self.availableConfigsValueAction = self.titleBar.insertWidget(
                insert_before,
                self.availableConfigsValueLabel,
            )
        elif hasattr(self.titleBar, "addWidget"):
            self.availableConfigsAction = self.titleBar.addWidget(self.availableConfigsLabel)
            self.availableConfigsValueAction = self.titleBar.addWidget(
                self.availableConfigsValueLabel
            )
        else:
            self.availableConfigsAction = None
            self.availableConfigsValueAction = None

        self._update_config_selectors()

    def _update_config_selectors(self) -> None:
        label = getattr(self, "availableConfigsLabel", None)
        value_label = getattr(self, "availableConfigsValueLabel", None)
        if value_label is None:
            return
        text = self._config_list_text()
        tooltip = self._config_list_tooltip_text()
        if hasattr(value_label, "setText"):
            value_label.setText(text)
        if hasattr(value_label, "setToolTip"):
            value_label.setToolTip(tooltip)
        if label is not None and hasattr(label, "setToolTip"):
            label.setToolTip(tooltip)

    def _status_badge_style(self) -> str:
        """Return a compact badge style that reflects the PSU main state."""
        state = self._display_main_state()
        normalized = state.lower()
        is_on = bool(getattr(self, "isOn", lambda: False)())
        if state == "Disconnected":
            background = "#718096"
        elif state == "OFF":
            background = "#4a5568"
        elif _status_requires_operator_attention(state):
            background = "#c53030"
        elif "stby" in normalized or "standby" in normalized:
            background = "#b7791f"
        elif is_on:
            background = "#2f855a"
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
        """Return the compact PSU runtime summary displayed in the toolbar."""
        controller = getattr(self, "controller", None)
        outputs = _compact_status_text(
            getattr(self, "output_summary", None),
            default="n/a",
        )
        faults = _compact_status_text(
            getattr(controller, "device_state_summary", None),
            default="n/a",
        )
        return f"Outputs: {outputs} | Faults: {faults}"

    def _status_tooltip_text(self) -> str:
        """Return the full PSU status tooltip for the toolbar widgets."""
        controller = getattr(self, "controller", None)
        display_state = self._display_main_state()
        hardware_state = _normalize_runtime_state(getattr(self, "main_state", "Disconnected"))
        lines = [f"State: {display_state}"]
        if display_state != hardware_state:
            lines.append(f"Hardware state: {hardware_state}")
        lines.extend(
            (
                f"Outputs: {getattr(self, 'output_summary', '') or 'n/a'}",
                f"Faults: {getattr(controller, 'device_state_summary', '') or 'n/a'}",
                f"Configs: {getattr(self, 'available_configs_text', '') or 'n/a'}",
            )
        )
        return "\n".join(lines)

    def _update_status_widgets(self) -> None:
        """Refresh the global PSU status labels in the toolbar."""
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
        if self.tree is None:
            return
        self.tree.setHeaderLabels(
            [
                parameter_dict.get(Parameter.HEADER, "") or name.title()
                for name, parameter_dict in self._default_channel_template().items()
            ]
        )

    def _update_channel_column_visibility(self) -> None:
        """Hide framework-only PSU columns and keep key readbacks resizable."""
        if self.tree is None or not self.channels:
            return

        parameter_names = list(self.channels[0].getSortedDefaultChannel())
        for hidden_name in (
            getattr(Channel, "COLLAPSE", "Collapse"),
            getattr(Channel, "REAL", "Real"),
            getattr(Channel, "ACTIVE", "Active"),
            getattr(Channel, "ENABLED", "Enabled"),
            getattr(Channel, "VALUE", "Value"),
            getattr(Channel, "EQUATION", "Equation"),
            getattr(Channel, "MIN", "Min"),
            getattr(Channel, "MAX", "Max"),
        ):
            if hidden_name in parameter_names:
                self.tree.setColumnHidden(parameter_names.index(hidden_name), True)

        header = self.tree.header()
        if header is None:
            return

        for parameter_name, default_width in (
            (getattr(Channel, "MONITOR", "Monitor"), 88),
            (self.channelType.ID, 44),
            (self.channelType.OUTPUT_STATE, 58),
            (self.channelType.VOLTAGE_SET, 90),
            (self.channelType.CURRENT_SET, 90),
            (self.channelType.CURRENT_MONITOR, 92),
        ):
            if parameter_name not in parameter_names:
                continue
            column_index = parameter_names.index(parameter_name)
            header.setSectionResizeMode(
                column_index,
                type(header).ResizeMode.Interactive,
            )
            header.resizeSection(column_index, default_width)

    def _apply_channel_items(
        self,
        items: list[dict[str, Any]],
        *,
        persist: bool = True,
    ) -> None:
        update_channel_config = getattr(self, "updateChannelConfig", None)
        export_config = getattr(self, "exportConfiguration", None)
        custom_config_file = getattr(self, "customConfigFile", None)
        config_name = getattr(self, "confINI", None)
        if not callable(update_channel_config) or not callable(custom_config_file):
            return

        config_file = custom_config_file(config_name)
        self.loading = True
        if self.tree is not None:
            self.tree.setUpdatesEnabled(False)
        try:
            update_channel_config(items, config_file)
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
                    collapse_changed = getattr(channel, "collapseChanged", None)
                    if callable(collapse_changed):
                        collapse_changed(toggle=False)
                self.tree.scheduleDelayedItemsLayout()
            toggle_advanced = getattr(self, "toggleAdvanced", None)
            if callable(toggle_advanced) and hasattr(self, "advancedAction"):
                toggle_advanced(advanced=self.advancedAction.state)
            self._update_channel_column_visibility()
            estimate_storage = getattr(self, "estimateStorage", None)
            if callable(estimate_storage):
                estimate_storage()
        finally:
            if self.tree is not None:
                self.tree.setUpdatesEnabled(True)
                self.tree.scheduleDelayedItemsLayout()
                viewport = getattr(self.tree, "viewport", lambda: None)()
                if viewport is not None and hasattr(viewport, "update"):
                    viewport.update()
            process_events = getattr(self, "processEvents", None)
            if callable(process_events):
                process_events()
            self.loading = False
        if persist and callable(export_config):
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

    def _normalize_channel_scaling(self, persist: bool = False) -> bool:
        """Migrate legacy oversized PSU row scaling back to the harmonized table size."""
        channels = list(self.getChannels()) if getattr(self, "channels", None) else []
        if not channels:
            return False

        normalized_channels: list[str] = []
        for channel in channels:
            scaling = str(
                getattr(channel, "scaling", _PSU_TABLE_SCALING) or _PSU_TABLE_SCALING
            ).strip().lower()
            if scaling not in _PSU_LEGACY_OVERSIZED_SCALINGS:
                continue

            set_without_events = getattr(channel, "_set_parameter_value_without_events", None)
            if callable(set_without_events):
                set_without_events(channel.SCALING, _PSU_TABLE_SCALING)
            else:
                getter = getattr(channel, "getParameterByName", None)
                parameter = getter(channel.SCALING) if callable(getter) else None
                if parameter is not None:
                    setter = getattr(parameter, "setValueWithoutEvents", None)
                    if callable(setter):
                        setter(_PSU_TABLE_SCALING)
                    else:
                        parameter.value = _PSU_TABLE_SCALING

            channel.scaling = _PSU_TABLE_SCALING
            scaling_changed = getattr(channel, "scalingChanged", None)
            previous_loading = getattr(channel, "loading", False)
            try:
                channel.loading = True
                if callable(scaling_changed):
                    scaling_changed()
            finally:
                channel.loading = previous_loading
            normalized_channels.append(str(getattr(channel, "name", "Unknown")))

        if not normalized_channels:
            return False

        if self.tree is not None:
            self.tree.scheduleDelayedItemsLayout()
            viewport = getattr(self.tree, "viewport", lambda: None)()
            if viewport is not None and hasattr(viewport, "update"):
                viewport.update()

        self.print(
            "Normalized legacy PSU table scaling to 'normal' for "
            + ", ".join(normalized_channels)
            + "."
        )
        export_config = getattr(self, "exportConfiguration", None)
        if persist and callable(export_config):
            export_config(useDefaultFile=True)
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
            event=self._update_config_selectors,
        )
        settings[f"{self.name}/{self.OPERATING_CONFIG}"] = parameterDict(
            value=-1,
            minimum=_PSU_FLOAT_SENTINEL,
            maximum=255,
            toolTip="Optional operating config index applied after standby. Use -1 to skip.",
            parameterType=PARAMETERTYPE.INT,
            attr="operating_config",
            event=self._update_config_selectors,
        )
        settings[f"{self.name}/{self.SHUTDOWN_CONFIG}"] = parameterDict(
            value=-1,
            minimum=_PSU_FLOAT_SENTINEL,
            maximum=255,
            toolTip="Optional shutdown config index. Use -1 to disable config-based shutdown.",
            parameterType=PARAMETERTYPE.INT,
            attr="shutdown_config",
            event=self._update_config_selectors,
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
        settings[f"{self.name}/{self.AVAILABLE_CONFIGS}"] = parameterDict(
            value="n/a",
            toolTip=(
                "PSU configuration slots reported by the controller after connect. "
                "Use these indices for standby, operating, and shutdown config settings."
            ),
            parameterType=PARAMETERTYPE.LABEL,
            attr="available_configs_text",
            indicator=True,
            internal=True,
            restore=False,
        )
        settings[f"{self.name}/Interval"][Parameter.VALUE] = 1000
        settings[f"{self.name}/{self.MAXDATAPOINTS}"][Parameter.VALUE] = 100000
        return settings

    def _set_on_ui_state(self, on: bool) -> None:
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
        """Disable manual acquisition controls until the PSU is actually ready."""
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
        """Only allow data recording when the PSU is initialized and in ST_ON."""
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
        controller = getattr(self, "controller", None)
        if self.useOnOffLogic and not hasattr(self, "onAction"):
            if controller:
                controller.closeCommunication()
            self._update_status_widgets()
            return
        if controller and getattr(controller, "initialized", False):
            self.shutdownCommunication()
            return
        if hasattr(self, "onAction"):
            self.onAction.state = False
            self._sync_local_on_action()
        if controller:
            controller.closeCommunication()
        self._update_status_widgets()

    def shutdownCommunication(self) -> None:
        if self.useOnOffLogic and hasattr(self, "onAction"):
            self.onAction.state = False
            self._sync_local_on_action()
        controller = getattr(self, "controller", None)
        if controller:
            controller.shutdownCommunication()
        self._update_status_widgets()

    def setOn(self, on: "bool | None" = None) -> None:
        controller = getattr(self, "controller", None)
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

        if controller and getattr(controller, "initialized", False):
            begin_transition = getattr(controller, "_begin_transition", None)
            can_start = not callable(begin_transition) or begin_transition(self.isOn())
            if can_start:
                toggle_thread = getattr(controller, "toggleOnFromThread", None)
                if callable(toggle_thread):
                    toggle_thread(parallel=True)
                else:
                    controller.toggleOn()
        elif hasattr(self, "onAction") and self.isOn():
            initialize_communication = getattr(self, "initializeCommunication", None)
            if callable(initialize_communication):
                initialize_communication()

    def loadConfiguration(
        self,
        file: "Path | None" = None,
        useDefaultFile: bool = False,
        append: bool = False,
    ) -> None:
        if useDefaultFile:
            file = self.customConfigFile(self.confINI)

        if (
            useDefaultFile
            and file not in {None, Path()}
            and cast(Path, file).suffix.lower() == ".ini"
            and not cast(Path, file).exists()
            and not self.channels
        ):
            self.print(
                f"PSU config file {file} not found. "
                "Bootstrapping transient CH0/CH1 channels until hardware initialization."
            )
            self._apply_channel_items(self._bootstrap_channel_items(), persist=False)
            plugin_manager = getattr(self, "pluginManager", None)
            device_manager = getattr(plugin_manager, "DeviceManager", None)
            global_update = getattr(device_manager, "globalUpdate", None)
            if callable(global_update):
                global_update(inout=self.inout)
            return

        super().loadConfiguration(file=file, useDefaultFile=False, append=append)
        self._normalize_channel_scaling(persist=useDefaultFile)
        self._update_channel_column_visibility()

    def toggleAdvanced(self, advanced: "bool | None" = False) -> None:
        super().toggleAdvanced(advanced=advanced)
        self._update_channel_column_visibility()


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
        channel[self.SCALING][Parameter.VALUE] = "normal"
        monitor_name = getattr(self, "MONITOR", "Monitor")
        if monitor_name in channel:
            channel[monitor_name][Parameter.HEADER] = "Vmon"
            channel[monitor_name][_PARAMETER_TOOLTIP_KEY] = (
                "Measured PSU output voltage read back from the controller."
            )
        channel[self.ID] = parameterDict(
            value="0",
            parameterType=PARAMETERTYPE.LABEL,
            advanced=False,
            indicator=True,
            header="CH ",
            attr="id",
        )
        channel[self.OUTPUT_STATE] = parameterDict(
            value="OFF",
            parameterType=PARAMETERTYPE.LABEL,
            advanced=False,
            indicator=True,
            header="On",
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
        # Keep framework-only parameters instantiated for bootstrap safety, but
        # order the visible PSU readbacks explicitly instead of inheriting the
        # generic IN-channel layout.
        displayed: list[str] = [
            getattr(self, "COLLAPSE", "Collapse"),
            getattr(self, "SELECT", "Select"),
            self.NAME,
            self.OUTPUT_STATE,
            self.VOLTAGE_SET,
            getattr(self, "MONITOR", "Monitor"),
            self.CURRENT_SET,
            self.CURRENT_MONITOR,
            self.DISPLAY,
            self.ID,
            self.ENABLED,
            self.VALUE,
            getattr(self, "EQUATION", "Equation"),
            self.ACTIVE,
            self.REAL,
            getattr(self, "SMOOTH", "Smooth"),
            getattr(self, "LINEWIDTH", "Linewidth"),
            getattr(self, "LINESTYLE", "Linestyle"),
            getattr(self, "DISPLAYGROUP", "Group"),
            self.SCALING,
            getattr(self, "COLOR", "Color"),
            getattr(self, "MIN", "Min"),
            getattr(self, "MAX", "Max"),
        ]
        self.displayedParameters = list(dict.fromkeys(displayed))

    def initGUI(self, item: dict) -> None:
        # Legacy PSU channel configs may not have initialized framework flags yet.
        # Seed the attributes used by core.Channel.updateColor() before the base init runs.
        if not hasattr(self, "active"):
            self.active = _coerce_bool(item.get(getattr(self, "ACTIVE", "Active")), True)
        if not hasattr(self, "enabled"):
            self.enabled = _coerce_bool(
                item.get(getattr(self, "ENABLED", "Enabled")),
                False,
            )
        if not hasattr(self, "real"):
            self.real = _coerce_bool(item.get(getattr(self, "REAL", "Real")), True)
        super().initGUI(item)
        self._sync_output_state_widget()
        self.monitorChanged()
        self.scalingChanged()

    def scalingChanged(self) -> None:
        scaling_changed = getattr(super(), "scalingChanged", None)
        if callable(scaling_changed):
            scaling_changed()
        row_height = getattr(self, "rowHeight", 0)
        if row_height >= _PSU_MIN_ROW_HEIGHT:
            return
        if row_height <= 0:
            return
        self.rowHeight = _PSU_MIN_ROW_HEIGHT
        for parameter in getattr(self, "parameters", []):
            if hasattr(parameter, "setHeight"):
                parameter.setHeight(self.rowHeight)
        if not self.loading and self.tree:
            self.tree.scheduleDelayedItemsLayout()

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

    def _set_parameter_widget_style(self, parameter_name: str, style: str) -> None:
        getter = getattr(self, "getParameterByName", None)
        if not callable(getter):
            return
        parameter = getter(parameter_name)
        if parameter is None:
            return
        widget = getattr(parameter, "getWidget", lambda: None)()
        if widget is None:
            return
        container = getattr(widget, "container", None)
        if container is not None and hasattr(container, "setStyleSheet"):
            container.setStyleSheet(style)
        if hasattr(widget, "setStyleSheet"):
            widget.setStyleSheet(style)

    def _sync_output_state_widget(self) -> None:
        getter = getattr(self, "getParameterByName", None)
        if not callable(getter):
            return
        parameter = getter(self.OUTPUT_STATE)
        if parameter is None:
            return
        state = getattr(self, "output_state", getattr(parameter, "value", "n/a"))
        self._set_parameter_widget_style(
            self.OUTPUT_STATE,
            _psu_output_state_badge_style(state),
        )

    def displayChanged(self) -> None:
        update_display = getattr(super(), "updateDisplay", None)
        if callable(update_display):
            update_display()

    def monitorChanged(self) -> None:
        self.warningState = False
        self._set_parameter_widget_style(
            getattr(self, "MONITOR", "Monitor"),
            _PSU_NEUTRAL_WIDGET_STYLE,
        )

    def realChanged(self) -> None:
        getter = getattr(self, "getParameterByName", None)
        if callable(getter):
            for parameter_name in (
                self.ID,
                getattr(self, "MONITOR", "Monitor"),
                self.OUTPUT_STATE,
                self.VOLTAGE_SET,
                self.CURRENT_SET,
                self.CURRENT_MONITOR,
            ):
                parameter = getter(parameter_name)
                if parameter is not None and hasattr(parameter, "setVisible"):
                    parameter.setVisible(self.real)
            enabled_parameter = getter(getattr(self, "ENABLED", "Enabled"))
            if enabled_parameter is None:
                return
        real_changed = getattr(super(), "realChanged", None)
        if callable(real_changed):
            real_changed()

    def setCurrentMonitorText(self, text: str) -> None:
        self._set_parameter_value_without_events(self.CURRENT_MONITOR, text)

    def setOutputStateText(self, text: str) -> None:
        self._set_parameter_value_without_events(self.OUTPUT_STATE, text)
        self._sync_output_state_widget()

    def setVoltageSetText(self, text: str) -> None:
        self._set_parameter_value_without_events(self.VOLTAGE_SET, text)

    def setCurrentSetText(self, text: str) -> None:
        self._set_parameter_value_without_events(self.CURRENT_SET, text)

    def updateColor(self):
        """Keep PSU indicators visually aligned with DMMR/AMPR channel tables."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QBrush
        from PyQt6.QtWidgets import QCheckBox, QComboBox, QSizePolicy

        color = super().updateColor()
        if color is None:
            return color

        neutral = QBrush()
        if hasattr(self, "setBackground"):
            for i in range(len(getattr(self, "parameters", [])) + 1):
                self.setBackground(i, neutral)

        for parameter in getattr(self, "parameters", []):
            widget = getattr(parameter, "getWidget", lambda: None)()
            if not widget:
                continue
            if hasattr(widget, "container"):
                widget.container.setStyleSheet("")
            if not isinstance(widget, QComboBox) and hasattr(widget, "setStyleSheet"):
                widget.setStyleSheet("")

        getter = getattr(self, "getParameterByName", None)
        if not callable(getter):
            return color

        display_param = getter(self.DISPLAY)
        if display_param:
            display_widget = display_param.getWidget()
            if isinstance(display_widget, QCheckBox):
                display_widget.setSizePolicy(
                    QSizePolicy.Policy.Maximum,
                    display_widget.sizePolicy().verticalPolicy(),
                )
                if (
                    hasattr(display_widget, "container")
                    and display_widget.container.layout()
                ):
                    display_widget.container.layout().setAlignment(
                        display_widget, Qt.AlignmentFlag.AlignCenter
                    )

        for parameter_name in (
            getattr(self, "MONITOR", "Monitor"),
            self.DISPLAY,
            self.ID,
            self.VOLTAGE_SET,
            self.CURRENT_SET,
            self.CURRENT_MONITOR,
        ):
            self._set_parameter_widget_style(parameter_name, _PSU_NEUTRAL_WIDGET_STYLE)
        self._sync_output_state_widget()
        return color


class PSUController(DeviceController):
    """PSU hardware controller used by the ESIBD Explorer plugin."""

    controllerParent: PSUDevice

    def __init__(self, controllerParent) -> None:
        super().__init__(controllerParent=controllerParent)
        self.device: Any | None = None
        self.main_state = "Disconnected"
        self.output_state_summary = "CH0=OFF, CH1=OFF"
        self.device_state_summary = "n/a"
        self.available_configs: list[dict[str, Any]] = []
        self.available_configs_text = "n/a"
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
                logger=logging.getLogger(f"esibd.plugins.{self.controllerParent.name.lower()}"),
                allow_process_backend=False,
            )
            backend_reason = str(
                getattr(self.device, "_process_backend_disabled_reason", "")
            ).strip()
            if backend_reason:
                self.print(backend_reason, flag=PRINT.WARNING)
            self.device.connect(timeout_s=float(self.controllerParent.connect_timeout_s))
            self._refresh_available_configs()
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

    def _refresh_available_configs(self) -> None:
        device = self.device
        if device is None:
            self.available_configs = []
            self.available_configs_text = "n/a"
            return

        list_configs = getattr(device, "list_configs", None)
        if not callable(list_configs):
            self.available_configs = []
            self.available_configs_text = "Unavailable"
            return

        try:
            configs = list_configs(
                timeout_s=float(getattr(self.controllerParent, "connect_timeout_s", 5.0))
            )
        except Exception as exc:  # noqa: BLE001
            self.available_configs = []
            self.available_configs_text = "Unavailable"
            self.print(
                f"Could not read PSU config list: {self._format_exception(exc)}",
                flag=PRINT.WARNING,
            )
            return

        self.available_configs = list(configs or [])
        self.available_configs_text = _format_available_configs(configs)

    def readNumbers(self) -> None:
        if self.device is None or not getattr(self, "initialized", False):
            self.initializeValues(reset=True)
            return

        timeout_s = float(getattr(self.controllerParent, "poll_timeout_s", 5.0))
        try:
            with self._controller_lock_section(
                "Could not acquire lock to read PSU housekeeping.",
                already_acquired=True,
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

        try:
            self._apply_snapshot(snapshot)
        except Exception as exc:  # noqa: BLE001
            self.errorCount += 1
            self.print(
                f"Failed to apply PSU housekeeping snapshot: {self._format_exception(exc)}",
                flag=PRINT.ERROR,
            )
            self.initializeValues(reset=True)
            return

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
        self.available_configs = []
        self.available_configs_text = "n/a"
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
        self.controllerParent.available_configs = list(self.available_configs)
        self.controllerParent.available_configs_text = self.available_configs_text
        sync_acquisition_controls = getattr(
            self.controllerParent,
            "_sync_acquisition_controls",
            None,
        )
        if callable(sync_acquisition_controls):
            sync_acquisition_controls()
        update_config_selectors = getattr(self.controllerParent, "_update_config_selectors", None)
        if callable(update_config_selectors):
            update_config_selectors()
        update_status_widgets = getattr(self.controllerParent, "_update_status_widgets", None)
        if callable(update_status_widgets):
            update_status_widgets()

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
    def _controller_lock_section(
        self,
        timeout_message: str,
        *,
        already_acquired: bool = False,
    ):
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
