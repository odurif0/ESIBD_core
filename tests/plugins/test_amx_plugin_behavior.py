"""Behavior checks for the standalone ESIBD Explorer AMX plugin."""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import threading
import types
from enum import Enum
from pathlib import Path

import pytest


PLUGIN_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "esibd_explorer"
    / "amx"
    / "amx_plugin.py"
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
        NAME = "Name"
        ADVANCED = "Advanced"
        EVENT = "Event"
        TOOLTIP = "Tooltip"

    class Channel:
        NAME = "Name"
        REAL = "Real"
        ENABLED = "Enabled"
        VALUE = "Value"

    class _Signal:
        def emit(self, *args, **kwargs):
            self.last_emit = (args, kwargs)

    class DeviceController:
        def __init__(self, controllerParent=None):
            self.controllerParent = controllerParent
            self.lock = threading.Lock()
            self.signalComm = types.SimpleNamespace(initCompleteSignal=_Signal())
            self.errorCount = 0
            self.initializing = False
            self.acquiring = False
            self.print = lambda *args, **kwargs: None

    class ToolButton:
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
        or name == "amx_plugin_behavior_test"
    ]:
        sys.modules.pop(name, None)


def _load_module():
    _clear_test_modules()
    _install_esibd_stubs()
    spec = importlib.util.spec_from_file_location("amx_plugin_behavior_test", PLUGIN_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_config_is_replaced_with_fixed_pulsers():
    module = _load_module()
    default_item = {
        "Pulser": "0",
        "Real": True,
        "Enabled": True,
        "Delay ticks": 0,
    }

    bootstrap_items = [
        {"Name": f"AMX{index}", "Pulser": 0, "Real": True, "Enabled": True}
        for index in range(1, 5)
    ]

    synced_items, log_entries = module._plan_channel_sync(
        current_items=bootstrap_items,
        device_name="AMX",
        default_item=default_item,
    )

    assert [item["Name"] for item in synced_items] == [
        "AMX_P0",
        "AMX_P1",
        "AMX_P2",
        "AMX_P3",
    ]
    assert all(item["Enabled"] is False for item in synced_items)
    assert all(item["Real"] is True for item in synced_items)
    assert log_entries == [("AMX bootstrap config replaced with fixed pulser channels.", None)]


def test_existing_config_is_merged_and_duplicates_are_neutralized():
    module = _load_module()

    current_items = [
        {"Name": "Keep0", "Pulser": 0, "Real": True, "Enabled": True},
        {"Name": "Duplicate0", "Pulser": "0", "Real": True, "Enabled": False},
        {"Name": "Legacy6", "Pulser": 6, "Real": True, "Enabled": True},
    ]

    synced_items, log_entries = module._plan_channel_sync(
        current_items=current_items,
        device_name="AMX",
    )

    assert synced_items[0]["Real"] is True
    assert synced_items[1]["Real"] is False
    assert synced_items[2]["Real"] is False
    added_names = {item["Name"] for item in synced_items[3:]}
    assert added_names == {"AMX_P1", "AMX_P2", "AMX_P3"}
    assert ("Added generic AMX pulser channels: P1, P2, P3", None) in log_entries
    assert (
        "Marked AMX pulser channels virtual because they do not exist on hardware: P6",
        None,
    ) in log_entries
    assert (
        "Duplicate AMX mapping detected for P0: Duplicate0",
        module.PRINT.WARNING,
    ) in log_entries


def test_controller_read_numbers_maps_pulser_snapshot():
    module = _load_module()

    class FakeDevice:
        OSC_OFFSET = 2
        PULSER_WIDTH_OFFSET = 2

        def collect_housekeeping(self, timeout_s=None):
            return {
                "device_enabled": True,
                "main_state": {"name": "ST_ON"},
                "device_state": {"flags": ["DEVST_OK"]},
                "controller_state": {"flags": ["CTRLST_OK"]},
                "oscillator": {"period": 99998},
                "pulsers": [
                    {"pulser": 0, "width_ticks": 49998, "burst": 3},
                    {"pulser": 1, "width_ticks": 24998, "burst": None},
                ],
            }

    class FakeChannel:
        def __init__(self, pulser):
            self._pulser = pulser
            self.real = True
            self.enabled = True

        def pulser_number(self):
            return self._pulser

    parent = types.SimpleNamespace(
        poll_timeout_s=2.5,
        getChannels=lambda: [FakeChannel(0), FakeChannel(1)],
        main_state="",
        device_enabled_state="",
    )

    controller = module.AMXController(parent)
    controller.device = FakeDevice()
    controller.initialized = True

    controller.readNumbers()

    assert controller.main_state == "ST_ON"
    assert controller.device_enabled_state == "ON"
    assert controller.device_state_summary == "DEVST_OK"
    assert controller.controller_state_summary == "CTRLST_OK"
    assert controller.values[0] == pytest.approx(50.0)
    assert controller.values[1] == pytest.approx(25.0)
    assert controller.width_values == {0: "49998", 1: "24998"}
    assert controller.burst_values == {0: "3", 1: "n/a"}
    assert parent.main_state == "ST_ON"
    assert parent.device_enabled_state == "ON"


def test_controller_exposes_available_amx_configs_in_gui_state():
    module = _load_module()

    class FakeDevice:
        def list_configs(self, timeout_s=None):
            assert timeout_s == 2.5
            return [
                {"index": 0, "name": "Standby", "active": True, "valid": True},
                {"index": 9, "name": "Static:Out0-3=Hi-Z", "active": True, "valid": True},
            ]

    parent = types.SimpleNamespace(
        connect_timeout_s=2.5,
        main_state="",
        device_enabled_state="",
        available_configs_text="",
    )

    controller = module.AMXController(parent)
    controller.device = FakeDevice()

    controller._refresh_available_configs()
    controller._sync_status_to_gui()

    assert controller.available_configs_text == (
        "0:Standby; 9:Static:Out0-3=Hi-Z"
    )
    assert controller.available_configs == [
        {"index": 0, "name": "Standby", "active": True, "valid": True},
        {"index": 9, "name": "Static:Out0-3=Hi-Z", "active": True, "valid": True},
    ]
    assert parent.available_configs_text == controller.available_configs_text


def test_controller_exposes_loaded_amx_config_in_gui_state():
    module = _load_module()

    class FakeDevice:
        def get_status(self):
            return {
                "memory_config": 9,
                "memory_config_name": "Static:Out0-3=Hi-Z",
                "memory_config_source": "memory",
            }

    parent = types.SimpleNamespace(
        main_state="",
        device_enabled_state="",
        available_configs_text="",
        loaded_config_text="",
    )

    controller = module.AMXController(parent)
    controller.device = FakeDevice()

    controller._refresh_loaded_config_status()
    controller._sync_status_to_gui()

    assert controller.loaded_config_text == "9:Static:Out0-3=Hi-Z [memory]"
    assert parent.loaded_config_text == controller.loaded_config_text


def test_controller_toggle_on_refreshes_loaded_config_after_initialize():
    module = _load_module()

    class FakeDevice:
        def __init__(self):
            self.initialize_calls = []
            self.frequency_calls = []
            self.enable_calls = []
            self.load_calls = []

        def initialize(self, timeout_s=None, **kwargs):
            self.initialize_calls.append((timeout_s, kwargs))

        def load_config(self, config_index, timeout_s=None):
            self.load_calls.append((config_index, timeout_s))

        def set_frequency_khz(self, value, timeout_s=None):
            self.frequency_calls.append((value, timeout_s))

        def set_device_enabled(self, enabled, timeout_s=None):
            self.enable_calls.append((enabled, timeout_s))

        def collect_housekeeping(self, timeout_s=None):
            return {
                "device_enabled": True,
                "main_state": {"name": "ST_ON"},
                "device_state": {"flags": ["DEVST_OK"]},
                "controller_state": {"flags": ["CTRLST_OK"]},
                "oscillator": {"period": 100000},
                "pulsers": [],
            }

        def get_status(self):
            return {
                "memory_config": 9,
                "memory_config_name": "Operate",
                "memory_config_source": "memory",
            }

    sync_calls = []
    parent = types.SimpleNamespace(
        name="AMX",
        startup_timeout_s=7.5,
        poll_timeout_s=2.5,
        frequency_khz=2.0,
        operating_config=9,
        standby_config=-1,
        getChannels=lambda: [],
        isOn=lambda: True,
        main_state="",
        device_enabled_state="",
        available_configs_text="",
        loaded_config_text="",
        _update_config_controls=lambda: sync_calls.append("config"),
        _update_status_widgets=lambda: sync_calls.append("status"),
    )

    controller = module.AMXController(parent)
    controller.device = FakeDevice()
    controller.initialized = True
    controller.available_configs = [
        {"index": 9, "name": "Operate", "active": True, "valid": True},
    ]

    controller.toggleOn()

    assert controller.device.initialize_calls == [(7.5, {"operating_config": 9})]
    assert controller.device.load_calls == []
    assert controller.device.frequency_calls == [(2.0, 7.5)]
    assert controller.device.enable_calls == [(True, 7.5)]
    assert controller.loaded_config_text == "9:Operate [memory]"
    assert parent.loaded_config_text == "9:Operate [memory]"
    assert controller.main_state == "ST_ON"
    assert parent.main_state == "ST_ON"
    assert sync_calls


def test_controller_toggle_on_waits_for_state_on_after_enable():
    module = _load_module()

    class FakeDevice:
        def __init__(self):
            self.initialize_calls = []
            self.load_calls = []
            self.frequency_calls = []
            self.enable_calls = []
            self.snapshot_calls = 0

        def initialize(self, timeout_s=None, **kwargs):
            self.initialize_calls.append((timeout_s, kwargs))

        def load_config(self, config_index, timeout_s=None):
            self.load_calls.append((config_index, timeout_s))

        def set_frequency_khz(self, value, timeout_s=None):
            self.frequency_calls.append((value, timeout_s))

        def set_device_enabled(self, enabled, timeout_s=None):
            self.enable_calls.append((enabled, timeout_s))

        def collect_housekeeping(self, timeout_s=None):
            self.snapshot_calls += 1
            if self.snapshot_calls == 1:
                return {
                    "device_enabled": False,
                    "main_state": {"name": "STATE_ERR_FPGA_DIS"},
                    "device_state": {"flags": ["DEVST_FPGA_DIS"]},
                    "controller_state": {"flags": []},
                    "oscillator": {"period": 100000},
                    "pulsers": [],
                }
            return {
                "device_enabled": True,
                "main_state": {"name": "STATE_ON"},
                "device_state": {"flags": ["DEVST_OK"]},
                "controller_state": {"flags": []},
                "oscillator": {"period": 100000},
                "pulsers": [],
            }

        def get_status(self):
            return {
                "memory_config": 9,
                "memory_config_name": "Operate",
                "memory_config_source": "memory",
            }

    messages = []
    parent = types.SimpleNamespace(
        name="AMX",
        startup_timeout_s=7.5,
        poll_timeout_s=2.5,
        frequency_khz=2.0,
        operating_config=9,
        standby_config=-1,
        getChannels=lambda: [],
        isOn=lambda: True,
        main_state="",
        device_enabled_state="",
        available_configs_text="",
        loaded_config_text="",
        _update_config_controls=lambda: None,
        _update_status_widgets=lambda: None,
    )

    controller = module.AMXController(parent)
    controller.device = FakeDevice()
    controller.initialized = True
    controller.available_configs = [
        {"index": 9, "name": "Operate", "active": True, "valid": True},
    ]
    controller.print = lambda message, flag=None: messages.append((message, flag))
    original_sleep = module.time.sleep
    module.time.sleep = lambda _seconds: None

    try:
        controller.toggleOn()
    finally:
        module.time.sleep = original_sleep

    assert controller.device.initialize_calls == [(7.5, {"operating_config": 9})]
    assert controller.device.load_calls == []
    assert controller.device.frequency_calls == [(2.0, 7.5)]
    assert controller.device.enable_calls == [(True, 7.5)]
    assert messages == [("AMX timing enabled.", None)]
    assert controller.main_state == "STATE_ON"
    assert controller.device_enabled_state == "ON"


def test_controller_toggle_on_requires_operating_config():
    module = _load_module()

    class FakeDevice:
        def initialize(self, timeout_s=None, **kwargs):
            raise AssertionError("initialize should not run without an operating config")

    parent = types.SimpleNamespace(
        name="AMX",
        startup_timeout_s=7.5,
        poll_timeout_s=2.5,
        frequency_khz=2.0,
        operating_config=-1,
        standby_config=-1,
        getChannels=lambda: [],
        isOn=lambda: True,
        _update_config_controls=lambda: None,
        _update_status_widgets=lambda: None,
    )

    controller = module.AMXController(parent)
    controller.device = FakeDevice()
    controller.initialized = True
    restored = []
    messages = []
    controller._restore_off_ui_state = lambda: restored.append(True)
    controller.print = lambda message, flag=None: messages.append((message, flag))

    controller.toggleOn()

    assert restored == [True]
    assert messages == [
        (
            "Cannot start AMX: select an AMX config first.",
            module.PRINT.WARNING,
        )
    ]


def test_controller_defaults_standby_to_slot_zero_when_available():
    module = _load_module()

    parent = types.SimpleNamespace(
        standby_config=-1,
        operating_config=9,
    )
    controller = module.AMXController(parent)
    controller.available_configs = [
        {"index": 0, "name": "Standby", "active": True, "valid": True},
        {"index": 9, "name": "Operate", "active": True, "valid": True},
    ]

    assert controller._startup_kwargs() == {
        "standby_config": 0,
        "operating_config": 9,
    }
    assert controller._shutdown_kwargs() == {
        "standby_config": 0,
        "disable_device": False,
    }


def test_controller_shutdown_kwargs_use_explicit_standby_slot_when_valid():
    module = _load_module()

    parent = types.SimpleNamespace(
        standby_config=0,
        operating_config=9,
    )
    controller = module.AMXController(parent)
    controller.available_configs = [
        {"index": 0, "name": "Standby", "active": True, "valid": True},
        {"index": 9, "name": "Operate", "active": True, "valid": True},
    ]

    assert controller._shutdown_kwargs() == {
        "standby_config": 0,
        "disable_device": False,
    }


def test_controller_ignores_slot_zero_when_it_is_not_a_standby_config():
    module = _load_module()

    parent = types.SimpleNamespace(
        standby_config=-1,
        operating_config=9,
    )
    controller = module.AMXController(parent)
    controller.available_configs = [
        {"index": 0, "name": "Static:Out0-3=Hi-Z", "active": True, "valid": True},
        {"index": 9, "name": "Operate", "active": True, "valid": True},
    ]

    assert controller._startup_kwargs() == {"operating_config": 9}
    assert controller._shutdown_kwargs() == {}


def test_controller_skips_implicit_slot_zero_when_unavailable():
    module = _load_module()

    parent = types.SimpleNamespace(
        standby_config=-1,
        operating_config=9,
    )
    controller = module.AMXController(parent)
    controller.available_configs = [
        {"index": 9, "name": "Operate", "active": True, "valid": True},
    ]

    assert controller._startup_kwargs() == {"operating_config": 9}
    assert controller._shutdown_kwargs() == {}


def test_controller_load_now_is_rejected_while_amx_is_off():
    module = _load_module()

    class FakeDevice:
        def __init__(self):
            self.load_calls = []

        def load_config(self, config_index, timeout_s=None):
            self.load_calls.append((config_index, timeout_s))

    parent = types.SimpleNamespace(
        name="AMX",
        startup_timeout_s=7.5,
        operating_config=9,
        isOn=lambda: False,
    )
    controller = module.AMXController(parent)
    controller.device = FakeDevice()
    controller.initialized = True
    messages = []
    controller.print = lambda message, flag=None: messages.append((message, flag))

    controller.loadOperatingConfigNow()

    assert controller.device.load_calls == []
    assert messages == [
        ("Cannot load AMX config while the AMX is OFF.", module.PRINT.WARNING)
    ]


def test_controller_load_now_requires_operating_config():
    module = _load_module()

    parent = types.SimpleNamespace(
        name="AMX",
        startup_timeout_s=7.5,
        operating_config=-1,
        isOn=lambda: True,
    )
    controller = module.AMXController(parent)
    controller.device = object()
    controller.initialized = True
    messages = []
    controller.print = lambda message, flag=None: messages.append((message, flag))

    controller.loadOperatingConfigNow()

    assert messages == [
        (
            "Cannot load AMX config: select an AMX config first.",
            module.PRINT.WARNING,
        )
    ]


def test_amx_acquisition_readiness_accepts_state_on():
    module = _load_module()

    device = object.__new__(module.AMXDevice)
    device.isOn = lambda: True
    device.controller = types.SimpleNamespace(
        device=object(),
        initializing=False,
        initialized=True,
        transitioning=False,
        main_state="STATE_ON",
    )

    assert module.AMXDevice._acquisition_readiness(device) == (True, "")


def test_controller_close_communication_syncs_after_device_is_disposed():
    module = _load_module()

    class FakeDevice:
        def disconnect(self):
            return None

        def close(self):
            return None

    sync_states = []
    parent = types.SimpleNamespace(
        _update_config_controls=lambda: sync_states.append(
            (controller.device is None, controller.initialized)
        )
    )
    controller = module.AMXController(parent)
    controller.device = FakeDevice()
    controller.initialized = True
    controller.loaded_config_text = "9:Operate [memory]"
    controller.available_configs = [{"index": 9, "name": "Operate"}]
    controller.available_configs_text = "9:Operate"

    controller.closeCommunication()

    assert controller.device is None
    assert controller.initialized is False
    assert controller.loaded_config_text == "n/a"
    assert controller.available_configs == []
    assert sync_states == [(True, False)]


def test_controller_shutdown_uses_full_software_shutdown():
    module = _load_module()

    class FakeDevice:
        def __init__(self):
            self.shutdown_calls = []

        def shutdown(self, timeout_s=None, **kwargs):
            self.shutdown_calls.append((timeout_s, kwargs))
            return True

        def disconnect(self):
            return None

        def close(self):
            return None

    parent = types.SimpleNamespace(
        startup_timeout_s=7.5,
        _update_config_controls=lambda: None,
        _update_status_widgets=lambda: None,
    )
    controller = module.AMXController(parent)
    device = FakeDevice()
    controller.device = device
    controller.initialized = True
    controller.shutdownCommunication()

    assert controller.device is None
    assert device.shutdown_calls == [(7.5, {})]


def test_controller_shutdown_parks_standby_before_disconnect_when_available():
    module = _load_module()

    class FakeDevice:
        def __init__(self):
            self.shutdown_calls = []

        def shutdown(self, timeout_s=None, **kwargs):
            self.shutdown_calls.append((timeout_s, kwargs))
            return True

        def disconnect(self):
            return None

        def close(self):
            return None

    messages = []
    parent = types.SimpleNamespace(
        name="AMX",
        startup_timeout_s=7.5,
        standby_config=-1,
        _update_config_controls=lambda: None,
        _update_status_widgets=lambda: None,
    )
    controller = module.AMXController(parent)
    controller.available_configs = [
        {"index": 0, "name": "Standby", "active": True, "valid": True},
        {"index": 9, "name": "Operate", "active": True, "valid": True},
    ]
    controller.device = FakeDevice()
    controller.initialized = True
    controller.print = lambda message, flag=None: messages.append((message, flag))

    controller.shutdownCommunication()

    assert controller.device is None
    assert messages[:2] == [
        ("Starting AMX shutdown sequence.", None),
        ("Parking AMX in standby config 0 before disconnect.", None),
    ]
    assert messages[-1] == ("AMX shutdown sequence completed.", None)
    assert controller.device is None
    assert controller.initialized is False


def test_amx_controller_lock_section_uses_raw_lock_and_propagates_errors():
    module = _load_module()

    class FakeLock:
        def __init__(self):
            self.acquire_calls = []
            self.release_calls = 0
            self.acquire_timeout_calls = []

        def acquire(self, timeout=-1):
            self.acquire_calls.append(timeout)
            return True

        def release(self):
            self.release_calls += 1

        @contextlib.contextmanager
        def acquire_timeout(self, timeout, timeoutMessage="", already_acquired=False):
            self.acquire_timeout_calls.append((timeout, timeoutMessage, already_acquired))
            yield True

    controller = module.AMXController(types.SimpleNamespace())
    controller.lock = FakeLock()

    with pytest.raises(IndexError, match="boom"):
        with controller._controller_lock_section("lock failed"):
            raise IndexError("boom")

    assert controller.lock.acquire_calls == [1]
    assert controller.lock.release_calls == 1
    assert controller.lock.acquire_timeout_calls == []


def test_amx_controller_read_numbers_marks_lock_as_already_acquired():
    module = _load_module()

    class FakeDevice:
        def collect_housekeeping(self, timeout_s=None):
            return {
                "device_enabled": True,
                "main_state": {"name": "ST_ON"},
                "device_state": {"flags": ["DEVST_OK"]},
                "controller_state": {"flags": ["CTRLST_OK"]},
                "oscillator": {"period": 100000},
                "pulsers": [],
            }

    parent = types.SimpleNamespace(
        poll_timeout_s=2.5,
        getChannels=lambda: [],
        main_state="",
        device_enabled_state="",
        available_configs_text="",
    )
    controller = module.AMXController(parent)
    controller.device = FakeDevice()
    controller.initialized = True

    lock_calls = []

    @contextlib.contextmanager
    def fake_lock_section(timeout_message, *, already_acquired=False):
        lock_calls.append((timeout_message, already_acquired))
        yield

    controller._controller_lock_section = fake_lock_section
    controller.readNumbers()

    assert lock_calls == [("Could not acquire lock to read AMX housekeeping.", True)]
