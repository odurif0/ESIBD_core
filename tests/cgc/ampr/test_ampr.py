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
