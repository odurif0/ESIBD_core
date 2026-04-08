from pathlib import Path
import logging
import tempfile
import threading
import types
from unittest.mock import Mock

import pytest

from cgc.ampr import AMPR, AMPRBase, AMPRDllLoadError, AMPRPlatformError


ERROR_CODES_PATH = Path(__file__).resolve().parents[3] / "src" / "cgc" / "error_codes.json"


def make_ampr(monkeypatch):
    monkeypatch.setattr("cgc.ampr.ampr_base.sys.platform", "win32")
    dll = Mock()
    monkeypatch.setattr("cgc.ampr.ampr_base.ctypes.WinDLL", lambda _path: dll, raising=False)
    monkeypatch.setattr(
        AMPRBase,
        "get_device_type",
        lambda self: (self.NO_ERR, self.DEVICE_TYPE),
    )
    log_dir = Path(tempfile.gettempdir()) / "esibd_core_test_logs"
    return AMPR("ampr_test", com=5, log_dir=log_dir), dll


def test_ampr_base_rejects_non_windows(monkeypatch):
    monkeypatch.setattr("cgc.ampr.ampr_base.sys.platform", "linux")

    with pytest.raises(AMPRPlatformError):
        AMPRBase(com=5)


def test_ampr_base_raises_clear_error_when_dll_fails(monkeypatch):
    monkeypatch.setattr("cgc.ampr.ampr_base.sys.platform", "win32")

    def raise_os_error(_path):
        raise OSError("missing dll")

    monkeypatch.setattr("cgc.ampr.ampr_base.ctypes.WinDLL", raise_os_error, raising=False)

    with pytest.raises(AMPRDllLoadError):
        AMPRBase(com=5, error_codes_path=ERROR_CODES_PATH)


def test_ampr_base_formats_vendor_error_codes(monkeypatch):
    ampr, _dll = make_ampr(monkeypatch)

    assert ampr.describe_error(AMPRBase.ERR_RATE) == "Error setting baud rate"
    assert ampr.format_status(AMPRBase.ERR_RATE) == "-16 (Error setting baud rate)"


def test_ampr_rejects_unknown_init_kwargs():
    with pytest.raises(TypeError, match="Unexpected AMPR init kwargs: unexpected"):
        AMPR("ampr_test", com=5, unexpected=True)


@pytest.mark.parametrize(
    ("kwargs", "exc_type", "match"),
    [
        ({"device_id": ""}, ValueError, "device_id"),
        ({"com": 0}, ValueError, "com"),
        ({"baudrate": 0}, ValueError, "baudrate"),
        ({"hk_interval_s": 0}, ValueError, "hk_interval_s"),
        ({"hk_interval": 2.5}, TypeError, "Unexpected AMPR init kwargs: hk_interval"),
    ],
)
def test_ampr_rejects_invalid_init_args(kwargs, exc_type, match):
    params = {"device_id": "ampr_test", "com": 5}
    params.update(kwargs)

    with pytest.raises(exc_type, match=match):
        AMPR(**params)


def test_ampr_external_logger_prefixes_device_id(monkeypatch, caplog):
    monkeypatch.setattr("cgc.ampr.ampr_base.sys.platform", "win32")
    monkeypatch.setattr(
        "cgc.ampr.ampr_base.ctypes.WinDLL",
        lambda _path: Mock(),
        raising=False,
    )
    logger = logging.getLogger("test_ampr_external_logger")

    ampr = AMPR("ampr_test", com=5, logger=logger)

    with caplog.at_level(logging.INFO, logger=logger.name):
        ampr.logger.info("hello")

    assert "ampr_test - hello" in caplog.messages


def test_ampr_base_get_module_product_id_decodes_vendor_string(monkeypatch):
    ampr, dll = make_ampr(monkeypatch)
    backend = object.__getattribute__(ampr, "_backend")

    def fake_get_module_product_id(address, identification):
        assert int(address.value) == 2
        identification.value = b"AMPR-1000"
        return AMPRBase.NO_ERR

    dll.COM_AMPR_12_GetModuleProductID.side_effect = fake_get_module_product_id

    status, product_id = backend.get_module_product_id(2)

    assert status == AMPRBase.NO_ERR
    assert product_id == "AMPR-1000"


def test_public_ampr_module_metadata_helpers_support_omitted_address():
    class FakeBackend:
        def scan_modules(self):
            return {2: {"state": "ST_STBY"}, 5: {"state": "ST_ON"}}

        def get_module_product_id(self, address):
            return AMPRBase.NO_ERR, f"ID-{address}"

        def get_module_product_no(self, address):
            return AMPRBase.NO_ERR, address * 100

        def get_module_hw_type(self, address):
            return AMPRBase.NO_ERR, address * 10

        def get_scanned_module_params(self, address):
            return (
                AMPRBase.NO_ERR,
                address * 1000,
                address * 1000 + 1,
                address * 1000 + 2,
                address * 1000 + 3,
            )

        def get_module_info(self, address):
            return {"address": address, "product_id": f"ID-{address}"}

    ampr = object.__new__(AMPR)
    object.__setattr__(ampr, "_backend_mode", "inline")
    object.__setattr__(ampr, "_backend", FakeBackend())
    object.__setattr__(ampr, "_process_backend_disabled_reason", "")
    object.__setattr__(ampr, "logger", types.SimpleNamespace(info=lambda *a, **k: None))

    assert ampr.get_module_product_id(2) == (AMPRBase.NO_ERR, "ID-2")
    assert ampr.get_module_product_id() == {
        2: {"status": AMPRBase.NO_ERR, "product_id": "ID-2"},
        5: {"status": AMPRBase.NO_ERR, "product_id": "ID-5"},
    }
    assert ampr.get_module_capabilities(2) == {
        "status": AMPRBase.NO_ERR,
        "product_id": "ID-2",
        "product_no": 200,
        "hw_type": 20,
        "voltage_rating": None,
        "channel_count": None,
    }
    assert ampr.get_module_capabilities() == {
        2: {
            "status": AMPRBase.NO_ERR,
            "product_id": "ID-2",
            "product_no": 200,
            "hw_type": 20,
            "voltage_rating": None,
            "channel_count": None,
        },
        5: {
            "status": AMPRBase.NO_ERR,
            "product_id": "ID-5",
            "product_no": 500,
            "hw_type": 50,
            "voltage_rating": None,
            "channel_count": None,
        },
    }
    assert ampr.get_module_voltage_rating(2) == (AMPRBase.NO_ERR, None)
    assert ampr.get_module_voltage_rating() == {
        2: {"status": AMPRBase.NO_ERR, "product_id": "ID-2", "voltage_rating": None},
        5: {"status": AMPRBase.NO_ERR, "product_id": "ID-5", "voltage_rating": None},
    }
    assert ampr.get_module_channel_count(2) == (AMPRBase.NO_ERR, None)
    assert ampr.get_module_channel_count() == {
        2: {"status": AMPRBase.NO_ERR, "product_id": "ID-2", "channel_count": None},
        5: {"status": AMPRBase.NO_ERR, "product_id": "ID-5", "channel_count": None},
    }
    assert ampr.get_module_product_no() == {
        2: {"status": AMPRBase.NO_ERR, "product_no": 200},
        5: {"status": AMPRBase.NO_ERR, "product_no": 500},
    }
    assert ampr.get_module_hw_type() == {
        2: {"status": AMPRBase.NO_ERR, "hw_type": 20},
        5: {"status": AMPRBase.NO_ERR, "hw_type": 50},
    }
    assert ampr.get_scanned_module_params() == {
        2: {
            "status": AMPRBase.NO_ERR,
            "scanned_product_no": 2000,
            "saved_product_no": 2001,
            "scanned_hw_type": 2002,
            "saved_hw_type": 2003,
        },
        5: {
            "status": AMPRBase.NO_ERR,
            "scanned_product_no": 5000,
            "saved_product_no": 5001,
            "scanned_hw_type": 5002,
            "saved_hw_type": 5003,
        },
    }
    assert ampr.get_module_info() == {
        2: {"address": 2, "product_id": "ID-2"},
        5: {"address": 5, "product_id": "ID-5"},
    }


def test_public_ampr_module_voltage_rating_is_derived_from_product_id():
    class FakeBackend:
        def scan_modules(self):
            return {2: {"state": "ST_STBY"}, 5: {"state": "ST_ON"}}

        def get_module_product_id(self, address):
            labels = {
                2: "Quadruple Voltage Source 500V",
                5: "Quadruple Voltage Source 1000V",
            }
            return AMPRBase.NO_ERR, labels[address]

    ampr = object.__new__(AMPR)
    object.__setattr__(ampr, "_backend_mode", "inline")
    object.__setattr__(ampr, "_backend", FakeBackend())
    object.__setattr__(ampr, "_process_backend_disabled_reason", "")
    object.__setattr__(ampr, "logger", types.SimpleNamespace(info=lambda *a, **k: None))

    assert ampr.get_module_voltage_rating(2) == (AMPRBase.NO_ERR, 500)
    assert ampr.get_module_voltage_rating() == {
        2: {
            "status": AMPRBase.NO_ERR,
            "product_id": "Quadruple Voltage Source 500V",
            "voltage_rating": 500,
        },
        5: {
            "status": AMPRBase.NO_ERR,
            "product_id": "Quadruple Voltage Source 1000V",
            "voltage_rating": 1000,
        },
    }
    assert ampr.get_module_channel_count() == {
        2: {
            "status": AMPRBase.NO_ERR,
            "product_id": "Quadruple Voltage Source 500V",
            "channel_count": 4,
        },
        5: {
            "status": AMPRBase.NO_ERR,
            "product_id": "Quadruple Voltage Source 1000V",
            "channel_count": 4,
        },
    }


def test_public_ampr_module_capabilities_prefer_known_ids_over_strings():
    class FakeBackend:
        def scan_modules(self):
            return {2: {"state": "ST_STBY"}}

        def get_module_product_id(self, address):
            return AMPRBase.NO_ERR, "Mystery Module"

        def get_module_product_no(self, address):
            return AMPRBase.NO_ERR, 132401

        def get_module_hw_type(self, address):
            return AMPRBase.NO_ERR, 222308

    ampr = object.__new__(AMPR)
    object.__setattr__(ampr, "_backend_mode", "inline")
    object.__setattr__(ampr, "_backend", FakeBackend())
    object.__setattr__(ampr, "_process_backend_disabled_reason", "")
    object.__setattr__(ampr, "logger", types.SimpleNamespace(info=lambda *a, **k: None))

    assert ampr.get_module_capabilities() == {
        2: {
            "status": AMPRBase.NO_ERR,
            "product_id": "Mystery Module",
            "product_no": 132401,
            "hw_type": 222308,
            "voltage_rating": 1000,
            "channel_count": 4,
        }
    }


def test_scan_modules_uses_timeout_safe_dll_wrapper(monkeypatch):
    ampr, _dll = make_ampr(monkeypatch)
    backend = object.__getattribute__(ampr, "_backend")
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        return {2: {"state": "ST_STBY"}}

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    modules = backend.scan_modules()

    assert modules == {2: {"state": "ST_STBY"}}
    assert calls == [("scan_all_modules", 5.0, "scan_all_modules", ())]


def test_module_voltage_writes_use_timeout_safe_dll_wrapper(monkeypatch):
    ampr, _dll = make_ampr(monkeypatch)
    backend = object.__getattribute__(ampr, "_backend")
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        if method.__name__ == "set_module_voltage":
            return backend.NO_ERR
        raise AssertionError(f"Unexpected method: {method.__name__}")

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    assert backend.set_module_voltage(2, 1, 10.0) == backend.NO_ERR
    assert backend.set_module_voltages(2, {1: 10.0}) == {1: backend.NO_ERR}
    assert calls == [
        ("set_module_voltage", 5.0, "set_module_voltage[2:1]", (2, 1, 10.0)),
        ("set_module_voltage", 5.0, "set_module_voltage[2:1]", (2, 1, 10.0)),
    ]


def test_timeout_safe_wrapper_methods_use_dll_timeout_guard(monkeypatch):
    ampr, _dll = make_ampr(monkeypatch)
    backend = object.__getattribute__(ampr, "_backend")
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        if method.__name__ == "enable_psu":
            return backend.NO_ERR, args[0]
        if method.__name__ == "get_state":
            return backend.NO_ERR, "0x0", "ST_STBY"
        if method.__name__ == "restart":
            return backend.NO_ERR
        if method.__name__ == "get_scanned_module_state":
            return backend.NO_ERR, False, False
        if method.__name__ == "rescan_modules":
            return backend.NO_ERR
        if method.__name__ == "set_scanned_module_state":
            return backend.NO_ERR
        raise AssertionError(f"Unexpected method: {method.__name__}")

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    assert backend.enable_psu(True) == (backend.NO_ERR, True)
    assert backend.get_state() == (backend.NO_ERR, "0x0", "ST_STBY")
    assert backend.restart() == backend.NO_ERR
    assert backend.get_scanned_module_state() == (backend.NO_ERR, False, False)
    assert backend.rescan_modules() == backend.NO_ERR
    assert backend.set_scanned_module_state() == backend.NO_ERR
    assert calls == [
        ("enable_psu", 5.0, "enable_psu", (True,)),
        ("get_state", 5.0, "get_state", ()),
        ("restart", 5.0, "restart", ()),
        ("get_scanned_module_state", 5.0, "get_scanned_module_state", ()),
        ("rescan_modules", 5.0, "rescan_modules", ()),
        ("set_scanned_module_state", 5.0, "set_scanned_module_state", ()),
    ]


def test_shutdown_respects_detected_module_channel_count(monkeypatch):
    ampr, _dll = make_ampr(monkeypatch)
    backend = object.__getattribute__(ampr, "_backend")
    set_calls = []
    enable_calls = []
    scan_calls = []

    def fake_scan_modules(timeout_s=None):
        scan_calls.append(timeout_s)
        return {
            2: {"product_no": 132401, "hw_type": 222308},
            3: {"product_id": "Dual Voltage Source 500V"},
        }

    def fake_set_module_voltage(address, channel, voltage, timeout_s=None):
        set_calls.append((address, channel, voltage, timeout_s))
        return backend.NO_ERR

    def fake_enable_psu(enable, timeout_s=None):
        enable_calls.append((enable, timeout_s))
        return backend.NO_ERR, enable

    monkeypatch.setattr(backend, "scan_modules", fake_scan_modules)
    monkeypatch.setattr(backend, "set_module_voltage", fake_set_module_voltage)
    monkeypatch.setattr(backend, "enable_psu", fake_enable_psu)
    monkeypatch.setattr(backend, "disconnect", Mock(return_value=True))

    backend.shutdown(timeout_s=1.5)

    assert scan_calls == [1.5]
    assert set_calls == [
        (2, 1, 0.0, 1.5),
        (2, 2, 0.0, 1.5),
        (2, 3, 0.0, 1.5),
        (2, 4, 0.0, 1.5),
        (3, 1, 0.0, 1.5),
        (3, 2, 0.0, 1.5),
    ]
    assert enable_calls == [(False, 1.5)]
    backend.disconnect.assert_called_once_with()


def test_connect_rolls_back_when_baud_rate_fails(monkeypatch):
    ampr, dll = make_ampr(monkeypatch)
    dll.COM_AMPR_12_Open.return_value = AMPRBase.NO_ERR
    dll.COM_AMPR_12_SetBaudRate.return_value = AMPRBase.ERR_RATE
    dll.COM_AMPR_12_Close.return_value = AMPRBase.NO_ERR

    with pytest.raises(RuntimeError, match="set_baud_rate failed"):
        ampr.connect()

    assert ampr.connected is False
    dll.COM_AMPR_12_Close.assert_called_once()


def test_connect_succeeds_when_device_type_matches(monkeypatch):
    ampr, dll = make_ampr(monkeypatch)
    dll.COM_AMPR_12_Open.return_value = AMPRBase.NO_ERR
    dll.COM_AMPR_12_SetBaudRate.return_value = AMPRBase.NO_ERR

    assert ampr.connect() is True
    assert ampr.connected is True
    dll.COM_AMPR_12_Close.assert_not_called()


def test_connect_rolls_back_when_device_type_mismatches(monkeypatch):
    ampr, dll = make_ampr(monkeypatch)
    dll.COM_AMPR_12_Open.return_value = AMPRBase.NO_ERR
    dll.COM_AMPR_12_SetBaudRate.return_value = AMPRBase.NO_ERR
    dll.COM_AMPR_12_Close.return_value = AMPRBase.NO_ERR
    monkeypatch.setattr(AMPRBase, "get_device_type", lambda self: (self.NO_ERR, 0x1234))

    with pytest.raises(RuntimeError, match="device type mismatch"):
        ampr.connect()

    assert ampr.connected is False
    dll.COM_AMPR_12_Close.assert_called_once()


def test_connect_is_noop_when_already_connected(monkeypatch):
    ampr, dll = make_ampr(monkeypatch)
    ampr.connected = True

    assert ampr.connect() is True
    dll.COM_AMPR_12_Open.assert_not_called()


def test_connect_times_out_when_open_port_blocks(monkeypatch):
    ampr, _dll = make_ampr(monkeypatch)

    def fake_timeout(_method, _timeout_s, step_name, *args, **kwargs):
        ampr._poison_transport(step_name)
        raise RuntimeError(
            f"AMPR DLL call timed out during '{step_name}'. "
            "The device may be powered off or unresponsive. "
            "The AMPR instance is now marked unusable."
        )

    ampr._call_locked_with_timeout = fake_timeout

    with pytest.raises(RuntimeError, match="timed out"):
        ampr.connect(timeout_s=0.01)

    assert ampr.connected is False
    assert ampr._transport_poisoned is True


def test_timeout_poison_keeps_lock_abandoned_and_blocks_reuse(monkeypatch):
    ampr, _dll = make_ampr(monkeypatch)
    blocker = threading.Event()

    with pytest.raises(RuntimeError, match="marked unusable"):
        ampr._call_locked_with_timeout(blocker.wait, 0.01, "blocked_call", 1.0)

    assert ampr._transport_poisoned is True
    assert ampr.thread_lock.acquire(blocking=False) is False

    with pytest.raises(RuntimeError, match="transport is unusable"):
        ampr._call_locked(lambda: None)


def test_disconnect_marks_instance_disconnected_even_on_close_failure(monkeypatch):
    ampr, dll = make_ampr(monkeypatch)
    dll.COM_AMPR_12_Close.return_value = AMPRBase.ERR_CLOSE
    ampr.connected = True

    assert ampr.disconnect() is False
    assert ampr.connected is False


def test_disconnect_skips_close_when_transport_is_poisoned(monkeypatch):
    ampr, dll = make_ampr(monkeypatch)
    ampr.connected = True
    ampr._poison_transport("open_port")

    assert ampr.disconnect() is False
    assert ampr.connected is False
    dll.COM_AMPR_12_Close.assert_not_called()


def test_initialize_disables_psu_before_disconnect_on_failure(monkeypatch):
    ampr, _dll = make_ampr(monkeypatch)
    enable_calls = []

    def fake_connect(**kwargs):
        ampr.connected = True
        return True

    monkeypatch.setattr(ampr, "connect", Mock(side_effect=fake_connect))
    monkeypatch.setattr(ampr, "disconnect", Mock(return_value=True))
    monkeypatch.setattr(
        AMPRBase,
        "get_scanned_module_state",
        lambda self: (self.NO_ERR, False, False),
    )

    def fake_enable_psu(self, enable):
        enable_calls.append(enable)
        return self.NO_ERR, enable

    def fake_get_state(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(AMPRBase, "enable_psu", fake_enable_psu)
    monkeypatch.setattr(AMPRBase, "get_state", fake_get_state)

    with pytest.raises(RuntimeError, match="boom"):
        ampr.initialize(timeout_s=0.01, poll_s=0.001)

    assert enable_calls == [True, False]
    ampr.disconnect.assert_called_once()


def test_initialize_is_noop_when_already_on(monkeypatch):
    ampr, _dll = make_ampr(monkeypatch)
    ampr.connected = True

    monkeypatch.setattr(ampr, "connect", Mock(side_effect=AssertionError("connect should not be called")))

    state_calls = []

    def fake_locked_timeout(_method, _timeout_s, step_name, *args, **kwargs):
        state_calls.append(step_name)
        if step_name == "get_state_before_initialize":
            return ampr.NO_ERR, 0, "ST_ON"
        raise AssertionError(f"Unexpected step: {step_name}")

    monkeypatch.setattr(ampr, "_call_locked_with_timeout", fake_locked_timeout)

    ampr.initialize(timeout_s=0.01, poll_s=0.001)

    assert state_calls == ["get_state_before_initialize"]


def test_initialize_disconnects_when_precheck_fails_on_existing_connection(monkeypatch):
    ampr, _dll = make_ampr(monkeypatch)
    ampr.connected = True
    monkeypatch.setattr(ampr, "disconnect", Mock(return_value=True))

    def fake_locked_timeout(_method, _timeout_s, step_name, *args, **kwargs):
        if step_name == "get_state_before_initialize":
            raise RuntimeError("boom")
        raise AssertionError(f"Unexpected step: {step_name}")

    monkeypatch.setattr(ampr, "_call_locked_with_timeout", fake_locked_timeout)

    with pytest.raises(RuntimeError, match="boom"):
        ampr.initialize(timeout_s=0.01, poll_s=0.001)

    ampr.disconnect.assert_called_once()


def test_initialize_timeout_poisoned_transport_disconnects(monkeypatch):
    ampr, _dll = make_ampr(monkeypatch)
    monkeypatch.setattr(ampr, "connect", Mock(return_value=True))
    monkeypatch.setattr(ampr, "disconnect", Mock(return_value=False))

    def fake_locked_timeout(_method, _timeout_s, step_name, *args, **kwargs):
        if step_name == "get_scanned_module_state":
            return ampr.NO_ERR, False, False
        if step_name == "enable_psu":
            return ampr.NO_ERR, True
        if step_name == "get_state":
            ampr._poison_transport(step_name)
            raise RuntimeError(
                f"AMPR DLL call timed out during '{step_name}'. "
                "The device may be powered off or unresponsive. "
                "The AMPR instance is now marked unusable."
            )
        raise AssertionError(f"Unexpected step: {step_name}")

    monkeypatch.setattr(ampr, "_call_locked_with_timeout", fake_locked_timeout)

    with pytest.raises(RuntimeError, match="timed out"):
        ampr.initialize(timeout_s=0.01, poll_s=0.001)

    assert ampr._transport_poisoned is True
    ampr.disconnect.assert_called_once()


def test_housekeeping_monitor_does_not_deadlock_on_wrapped_methods(monkeypatch):
    ampr, _dll = make_ampr(monkeypatch)
    ampr.connected = True

    monkeypatch.setattr(AMPRBase, "get_product_no", lambda self: (self.NO_ERR, 1234))
    monkeypatch.setattr(AMPRBase, "get_state", lambda self: (self.NO_ERR, "0x0", "ST_ON"))
    monkeypatch.setattr(
        AMPRBase,
        "get_device_state",
        lambda self: (self.NO_ERR, "0x0", ["DEVICE_OK"]),
    )
    monkeypatch.setattr(
        AMPRBase,
        "get_housekeeping",
        lambda self: (self.NO_ERR, *([0.0] * 14)),
    )
    monkeypatch.setattr(
        AMPRBase,
        "get_voltage_state",
        lambda self: (self.NO_ERR, "0x0", ["VOLTAGE_OK"]),
    )
    monkeypatch.setattr(
        AMPRBase,
        "get_temperature_state",
        lambda self: (self.NO_ERR, "0x0", ["TEMPERATURE_OK"]),
    )
    monkeypatch.setattr(
        AMPRBase,
        "get_interlock_state",
        lambda self: (self.NO_ERR, "0x0", []),
    )
    monkeypatch.setattr(
        AMPRBase,
        "get_fan_data",
        lambda self: (self.NO_ERR, False, 0, 0, 0, 0),
    )
    monkeypatch.setattr(
        AMPRBase,
        "get_led_data",
        lambda self: (self.NO_ERR, False, False, False),
    )
    monkeypatch.setattr(
        AMPRBase,
        "get_cpu_data",
        lambda self: (self.NO_ERR, 0.0, 0.0),
    )
    monkeypatch.setattr(
        AMPRBase,
        "get_module_presence",
        lambda self: (self.NO_ERR, True, 0, [0] * (self.MODULE_NUM + 1)),
    )

    worker = threading.Thread(target=ampr.hk_monitor, daemon=True)
    worker.start()
    worker.join(timeout=0.5)

    assert worker.is_alive() is False
    assert ampr.thread_lock.acquire(blocking=False) is True
    ampr.thread_lock.release()


def test_start_housekeeping_updates_interval_s(monkeypatch):
    ampr, _dll = make_ampr(monkeypatch)
    ampr.connected = True
    ampr.external_thread = True

    assert ampr.start_housekeeping(interval_s=1.5) is True

    assert ampr.hk_interval_s == 1.5
    assert ampr.get_status()["hk_interval_s"] == 1.5
    assert ampr.stop_housekeeping() is True


@pytest.mark.parametrize(
    ("interval_s", "exc_type", "match"),
    [
        (0, ValueError, "interval_s"),
        (-1, ValueError, "interval_s"),
        (True, TypeError, "interval_s"),
    ],
)
def test_start_housekeeping_rejects_invalid_interval_s(monkeypatch, interval_s, exc_type, match):
    ampr, _dll = make_ampr(monkeypatch)
    ampr.connected = True

    with pytest.raises(exc_type, match=match):
        ampr.start_housekeeping(interval_s=interval_s)


def test_get_all_module_voltages_reads_measured_values_once(monkeypatch):
    ampr, dll = make_ampr(monkeypatch)
    measured_call_addresses = []

    monkeypatch.setattr(
        AMPRBase,
        "get_module_voltage_setpoint",
        lambda self, address, channel: (self.NO_ERR, float(channel)),
    )

    def fake_get_measured(address, voltages):
        measured_call_addresses.append(address.value)
        for index, value in enumerate((10.0, 20.0, 30.0, 40.0)):
            voltages[index] = value
        return AMPRBase.NO_ERR

    dll.COM_AMPR_12_GetMeasuredModuleOutputVoltages.side_effect = fake_get_measured

    voltages = AMPRBase.get_all_module_voltages(ampr, 2)

    assert measured_call_addresses == [2]
    assert voltages == {
        1: {"setpoint": 1.0, "measured": 10.0},
        2: {"setpoint": 2.0, "measured": 20.0},
        3: {"setpoint": 3.0, "measured": 30.0},
        4: {"setpoint": 4.0, "measured": 40.0},
    }


@pytest.mark.parametrize("voltage", [1000.1, -1000.1, float("nan"), float("inf")])
def test_set_module_voltage_rejects_values_outside_module_rating(monkeypatch, voltage):
    ampr, dll = make_ampr(monkeypatch)

    status = AMPRBase.set_module_voltage(ampr, 0, 1, voltage)

    assert status == AMPRBase.ERR_ARGUMENT
    dll.COM_AMPR_12_SetModuleOutputVoltage.assert_not_called()
