"""ESIBD Explorer plugin for the CGC AMPR amplifier."""

from __future__ import annotations

import importlib
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
    parameterDict,
)
from esibd.plugins import Device, Plugin

_BUNDLED_RUNTIME_PACKAGE = "esibd_ampr_plugin_runtime"
_AMPR_DRIVER_CLASS: type[Any] | None = None
_CHANNEL_NAME_KEY = getattr(Parameter, "NAME", getattr(Channel, "NAME", "Name"))
_CHANNEL_ENABLED_KEY = getattr(Channel, "ENABLED", "Enabled")
_CHANNEL_REAL_KEY = getattr(Channel, "REAL", "Real")
_AMPR_MODULE_KEY = "Module"
_AMPR_CHANNEL_ID_KEY = "CH"
_CHANNELS_PER_MODULE = 4


def _coerce_int(value: Any, default: int) -> int:
    """Return an integer value from config-like input."""
    try:
        return int(value)
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
) -> dict[str, Any]:
    """Build a minimal channel config for a newly detected physical output."""
    return {
        _CHANNEL_NAME_KEY: _generic_channel_name(device_name, module, channel_id),
        _AMPR_MODULE_KEY: module,
        _AMPR_CHANNEL_ID_KEY: channel_id,
        _CHANNEL_REAL_KEY: True,
        _CHANNEL_ENABLED_KEY: False,
    }


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
            if item.get(key, default_value) != default_value:
                return False
    return True


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
            _build_generic_channel_item(device_name, module, channel_id)
            for module, channel_id in detected_keys
        ]
        return bootstrap_items, [
            (
                "AMPR bootstrap config replaced from hardware scan.",
                None,
            )
        ]

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
        synced_items.append(_build_generic_channel_item(device_name, module, channel_id))
        added_modules.add(module)

    log_entries: list[tuple[str, PRINT | None]] = []
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

    name = "AMPR"
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
    STATE = "State"
    DETECTED_MODULES = "Detected modules"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = AMPRChannel

    def initGUI(self) -> None:
        super().initGUI()
        self.controller = AMPRController(controllerParent=self)

    def finalizeInit(self) -> None:
        super().finalizeInit()
        self._update_channel_column_visibility()

    def getChannels(self) -> "list[AMPRChannel]":
        return cast("list[AMPRChannel]", super().getChannels())

    com: int
    baudrate: int
    connect_timeout_s: float
    main_state: str
    detected_modules: str

    def getConfiguredModules(self) -> list[int]:
        """Return sorted module addresses referenced by real channels."""
        return sorted({channel.module for channel in self.getChannels() if channel.real})

    def _current_channel_items(self) -> list[dict[str, Any]]:
        """Snapshot current channels into config dictionaries."""
        return [
            channel.asDict()
            for channel in self.getChannels()
        ]

    def _default_channel_item(self) -> dict[str, Any]:
        """Return the persisted default AMPR channel configuration."""
        return self.channelType(channelParent=self, tree=None).asDict()

    def _update_channel_column_visibility(self) -> None:
        """Hide framework columns that are not useful for the AMPR UI."""
        if self.tree is None or not self.channels:
            return

        parameter_names = list(self.channels[0].getSortedDefaultChannel())
        if Channel.COLLAPSE in parameter_names:
            self.tree.setColumnHidden(parameter_names.index(Channel.COLLAPSE), True)

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
                for channel in self.getChannels():
                    channel.collapseChanged(toggle=False)
                self.tree.scheduleDelayedItemsLayout()
            if hasattr(self, "advancedAction"):
                self.toggleAdvanced(advanced=self.advancedAction.state)
            self._update_channel_column_visibility()
            self.pluginManager.DeviceManager.globalUpdate(inout=self.inout)
        finally:
            if self.tree is not None:
                self.tree.setUpdatesEnabled(True)
            self.loading = False

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
        super().closeCommunication()


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
        channel[self.MODULE] = parameterDict(
            value=0,
            parameterType=PARAMETERTYPE.INT,
            advanced=False,
            indicator=True,
            header="Mod",
            minimum=0,
            maximum=11,
            attr="module",
        )
        channel[self.ID] = parameterDict(
            value=1,
            parameterType=PARAMETERTYPE.INT,
            advanced=False,
            indicator=True,
            header="CH ",
            minimum=1,
            maximum=4,
            attr="id",
        )
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        if self.OPTIMIZE in self.displayedParameters:
            self.displayedParameters.remove(self.OPTIMIZE)
        self.displayedParameters.append(self.MODULE)
        self.displayedParameters.append(self.ID)

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

    def realChanged(self) -> None:
        self.getParameterByName(self.MODULE).setVisible(self.real)
        self.getParameterByName(self.ID).setVisible(self.real)
        super().realChanged()


class AMPRController(DeviceController):
    """AMPR hardware controller used by the ESIBD Explorer plugin."""

    controllerParent: AMPRDevice

    def __init__(self, controllerParent) -> None:
        super().__init__(controllerParent=controllerParent)
        self.device: Any | None = None
        self.detected_module_ids: list[int] = []
        self.detected_modules_text = ""
        self.main_state = "Disconnected"

    def initializeValues(self, reset: bool = False) -> None:
        if self.values is None or reset:
            self.values = {
                (channel.module, channel.id): np.nan
                for channel in self.controllerParent.getChannels()
                if channel.real
            }

    def runInitialization(self) -> None:
        self._dispose_device()
        try:
            ampr_driver_class = _get_ampr_driver_class()
            self.device = ampr_driver_class(
                device_id=f"{self.controllerParent.name.lower()}_com{int(self.controllerParent.com)}",
                com=int(self.controllerParent.com),
                baudrate=int(self.controllerParent.baudrate),
            )
            self.device.connect(timeout_s=float(self.controllerParent.connect_timeout_s))
            self._refresh_module_scan()
            self._update_state()
            self.signalComm.initCompleteSignal.emit()
        except Exception as exc:  # noqa: BLE001
            self.print(
                f"Could not initialize AMPR on COM{int(self.controllerParent.com)}: {exc}",
                flag=PRINT.WARNING,
            )
            self._dispose_device()
        finally:
            self.initializing = False

    def initComplete(self) -> None:
        if self.device is not None and self.detected_module_ids:
            self.controllerParent._sync_channels_from_detected_modules(
                self.detected_module_ids
            )
        super().initComplete()
        self._sync_status_to_gui()

    def readNumbers(self) -> None:
        if self.device is None:
            return

        self._update_state()
        configured_modules = set(self.controllerParent.getConfiguredModules())
        detected_modules = set(self.detected_module_ids)
        if detected_modules:
            poll_modules = sorted(configured_modules & detected_modules)
        else:
            poll_modules = sorted(configured_modules)

        new_values = {
            (channel.module, channel.id): np.nan
            for channel in self.controllerParent.getChannels()
            if channel.real
        }

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
        for channel in self.controllerParent.getChannels():
            if channel.enabled and channel.real:
                base = channel.value if self.controllerParent.isOn() else 0.0
                self.values[(channel.module, channel.id)] = base + self.rng.uniform(-0.25, 0.25)

    def applyValue(self, channel: AMPRChannel) -> None:
        if self.device is None:
            return

        target_voltage = float(
            channel.value if channel.enabled and self.controllerParent.isOn() else 0.0
        )
        with self.lock.acquire_timeout(
            1,
            timeoutMessage=(
                f"Could not acquire lock to apply module {channel.module} CH{channel.id}."
            ),
        ) as lock_acquired:
            if not lock_acquired:
                return
            try:
                status = self.device.set_module_voltage(
                    channel.module, channel.id, target_voltage
                )
            except Exception as exc:  # noqa: BLE001
                self.errorCount += 1
                self.print(
                    f"Failed to apply {target_voltage:.3f} V to module "
                    f"{channel.module} CH{channel.id}: {exc}",
                    flag=PRINT.ERROR,
                )
                return

        if self.device is not None and status != self.device.NO_ERR:
            self.errorCount += 1
            self.print(
                f"AMPR rejected {target_voltage:.3f} V for module "
                f"{channel.module} CH{channel.id}: {self._format_status(status)}",
                flag=PRINT.ERROR,
            )

    def updateValues(self) -> None:
        if self.values is None:
            return

        self._sync_status_to_gui()
        for channel in self.controllerParent.getChannels():
            if channel.enabled and channel.real:
                channel.monitor = np.nan if channel.waitToStabilize else self.values.get(
                    (channel.module, channel.id),
                    np.nan,
                )

    def toggleOn(self) -> None:
        super().toggleOn()
        if self.device is None:
            return

        with self.lock.acquire_timeout(
            1,
            timeoutMessage="Could not acquire lock to toggle the AMPR PSU.",
        ) as lock_acquired:
            if not lock_acquired:
                return
            try:
                status, _enabled = self.device.enable_psu(self.controllerParent.isOn())
            except Exception as exc:  # noqa: BLE001
                self.errorCount += 1
                self.print(f"Failed to toggle AMPR PSU: {exc}", flag=PRINT.ERROR)
                return

        if status != self.device.NO_ERR:
            self.errorCount += 1
            self.print(
                f"Failed to toggle AMPR PSU: {self._format_status(status)}",
                flag=PRINT.ERROR,
            )

        self._update_state()

    def closeCommunication(self) -> None:
        super().closeCommunication()
        self.main_state = "Disconnected"
        self.detected_module_ids = []
        self.detected_modules_text = ""
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
            return

        try:
            status, _state_hex, state_name = self.device.get_state()
        except Exception as exc:  # noqa: BLE001
            self.errorCount += 1
            self.main_state = "State error"
            self.print(f"Failed to read AMPR state: {exc}", flag=PRINT.ERROR)
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

    def _sync_status_to_gui(self) -> None:
        self.controllerParent.main_state = self.main_state
        self.controllerParent.detected_modules = self.detected_modules_text

    def _dispose_device(self) -> None:
        device = self.device
        self.device = None
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

    def _format_status(self, status: int) -> str:
        if self.device is None:
            return str(status)
        try:
            return str(self.device.format_status(status))
        except Exception:  # noqa: BLE001
            return str(status)
