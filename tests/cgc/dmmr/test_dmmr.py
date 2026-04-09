from pathlib import Path
import logging
import tempfile
from unittest.mock import Mock

import pytest

from cgc.dmmr import DMMR, DMMRBase, DMMRDllLoadError, DMMRPlatformError


ERROR_CODES_PATH = Path(__file__).resolve().parents[3] / "src" / "cgc" / "error_codes.json"


@pytest.fixture(autouse=True)
def clear_dmmr_connection_registry():
    with DMMR._active_connections_lock:
        DMMR._active_connections.clear()
    yield
    with DMMR._active_connections_lock:
        DMMR._active_connections.clear()


def make_dmmr(monkeypatch, *, device_id="dmmr_test", com=8):
    monkeypatch.setattr("cgc.dmmr.dmmr_base.sys.platform", "win32")
    dll = Mock()
    monkeypatch.setattr("cgc.dmmr.dmmr_base.ctypes.WinDLL", lambda _path: dll, raising=False)
    monkeypatch.setattr(
        DMMRBase,
        "get_device_type",
        lambda self: (self.NO_ERR, self.DEVICE_TYPE),
    )
    log_dir = Path(tempfile.gettempdir()) / "esibd_core_test_logs"
    return DMMR(device_id, com=com, log_dir=log_dir), dll


def test_dmmr_base_rejects_non_windows(monkeypatch):
    monkeypatch.setattr("cgc.dmmr.dmmr_base.sys.platform", "linux")

    with pytest.raises(DMMRPlatformError):
        DMMRBase(com=8)


def test_dmmr_base_raises_clear_error_when_dll_fails(monkeypatch):
    monkeypatch.setattr("cgc.dmmr.dmmr_base.sys.platform", "win32")

    def raise_os_error(_path):
        raise OSError("missing dll")

    monkeypatch.setattr("cgc.dmmr.dmmr_base.ctypes.WinDLL", raise_os_error, raising=False)

    with pytest.raises(DMMRDllLoadError):
        DMMRBase(com=8, error_codes_path=ERROR_CODES_PATH)


def test_dmmr_base_formats_vendor_error_codes(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)

    assert dmmr.describe_error(DMMRBase.ERR_RATE) == "Error setting baud rate"
    assert dmmr.format_status(DMMRBase.ERR_RATE) == "-16 (Error setting baud rate)"


@pytest.mark.parametrize(
    ("state_value", "state_name"),
    [
        (1, "ST_OVERLOAD"),
        (2, "ST_STBY"),
    ],
)
def test_dmmr_base_decodes_documented_main_states(monkeypatch, state_value, state_name):
    dmmr, dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")

    def fake_get_state(ptr):
        ptr._obj.value = state_value
        return backend.NO_ERR

    dll.COM_DMMR_8_GetState.side_effect = fake_get_state

    assert backend.get_state() == (
        backend.NO_ERR,
        f"0x{state_value:04X}",
        state_name,
    )


def test_dmmr_rejects_unknown_init_kwargs():
    with pytest.raises(TypeError, match="Unexpected DMMR init kwargs: unexpected"):
        DMMR("dmmr_test", com=8, unexpected=True)


@pytest.mark.parametrize(
    ("kwargs", "exc_type", "match"),
    [
        ({"device_id": ""}, ValueError, "device_id"),
        ({"com": 0}, ValueError, "com"),
        ({"baudrate": 0}, ValueError, "baudrate"),
        ({"hk_interval_s": 0}, ValueError, "hk_interval_s"),
        ({"hk_interval": 2.5}, TypeError, "Unexpected DMMR init kwargs: hk_interval"),
    ],
)
def test_dmmr_rejects_invalid_init_args(kwargs, exc_type, match):
    params = {"device_id": "dmmr_test", "com": 8}
    params.update(kwargs)

    with pytest.raises(exc_type, match=match):
        DMMR(**params)


def test_dmmr_external_logger_prefixes_device_id(monkeypatch, caplog):
    monkeypatch.setattr("cgc.dmmr.dmmr_base.sys.platform", "win32")
    monkeypatch.setattr(
        "cgc.dmmr.dmmr_base.ctypes.WinDLL",
        lambda _path: Mock(),
        raising=False,
    )
    logger = logging.getLogger("test_dmmr_external_logger")

    dmmr = DMMR("dmmr_test", com=8, logger=logger)

    with caplog.at_level(logging.INFO, logger=logger.name):
        dmmr.logger.info("hello")

    assert "dmmr_test - hello" in caplog.messages


def test_dmmr_uses_process_backend_when_supported(monkeypatch):
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

    monkeypatch.setattr("cgc._driver_common.RUNTIME_IS_WINDOWS", True)
    monkeypatch.setattr("cgc._driver_common.ControllerProcessProxy", FakeProxy)

    dmmr = DMMR("dmmr_process", com=8)

    assert dmmr._backend_mode == "process"
    assert created["controller_path"] == "cgc.dmmr.dmmr:_DMMRController"
    assert created["label"] == "DMMR dmmr_process"
    assert created["controller_kwargs"]["device_id"] == "dmmr_process"
    assert created["controller_kwargs"]["com"] == 8
    assert created["controller_kwargs"]["logger"] is None

    dmmr.close()

    assert dmmr._backend.closed is True


def test_connect_rolls_back_when_baud_rate_fails(monkeypatch):
    dmmr, dll = make_dmmr(monkeypatch)
    dll.COM_DMMR_8_Open.return_value = DMMRBase.NO_ERR
    dll.COM_DMMR_8_SetBaudRate.return_value = DMMRBase.ERR_RATE
    dll.COM_DMMR_8_Close.return_value = DMMRBase.NO_ERR

    with pytest.raises(RuntimeError, match="set_baud_rate failed"):
        dmmr.connect()

    assert dmmr.connected is False
    dll.COM_DMMR_8_Close.assert_called_once()


def test_connect_warns_when_reusing_the_same_dll_port(monkeypatch, caplog):
    dmmr_a, dll_a = make_dmmr(monkeypatch, device_id="dmmr_a", com=8)
    dmmr_b, dll_b = make_dmmr(monkeypatch, device_id="dmmr_b", com=9)
    dll_a.COM_DMMR_8_Open.return_value = DMMRBase.NO_ERR
    dll_b.COM_DMMR_8_Open.return_value = DMMRBase.NO_ERR
    dll_a.COM_DMMR_8_SetBaudRate.return_value = DMMRBase.NO_ERR
    dll_b.COM_DMMR_8_SetBaudRate.return_value = DMMRBase.NO_ERR

    dmmr_a.connect()
    with caplog.at_level(logging.WARNING):
        dmmr_b.connect()

    assert "same DLL port" in caplog.text
    assert "port 0" in caplog.text


def test_connect_warns_when_product_id_looks_like_another_instrument(monkeypatch, caplog):
    dmmr, dll = make_dmmr(monkeypatch)
    dll.COM_DMMR_8_Open.return_value = DMMRBase.NO_ERR
    dll.COM_DMMR_8_SetBaudRate.return_value = DMMRBase.NO_ERR
    monkeypatch.setattr(
        DMMRBase,
        "get_product_id",
        lambda self: (self.NO_ERR, "HV-AMX-CTRL-4ED, Rev.2-20"),
    )

    with caplog.at_level(logging.WARNING):
        dmmr.connect()

    assert "does not look like a DMMR controller" in caplog.text
    assert "HV-AMX-CTRL-4ED" in caplog.text


def test_scan_modules_uses_timeout_safe_dll_wrapper(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")
    backend.connected = True
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        if step_name == "get_module_presence":
            return backend.NO_ERR, True, 8, [0, 0, backend.MODULE_PRESENT, 0, 0, 0, 0, 0]
        if step_name == "scan_present_modules":
            return {2: {"state": 0}}
        raise AssertionError(f"unexpected step {step_name}")

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    modules = backend.scan_modules()

    assert modules == {2: {"state": 0}}
    assert calls == [
        ("get_module_presence", 5.0, "get_module_presence", (backend,)),
        (
            "_scan_present_modules_unlocked",
            5.0,
            "scan_present_modules",
            ([0, 0, backend.MODULE_PRESENT, 0, 0, 0, 0, 0], 8),
        ),
    ]


def test_initialize_rescans_modules_and_returns_scan(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")
    calls = []

    def fake_connect(timeout_s=5.0):
        calls.append(("connect", timeout_s))
        backend.connected = True
        return True

    def fake_rescan_modules(timeout_s=None):
        calls.append(("rescan_modules", timeout_s))
        return backend.NO_ERR

    def fake_scan_modules(timeout_s=None):
        calls.append(("scan_modules", timeout_s))
        return {3: {"product_no": 132306}}

    def fake_get_scanned_module_state(timeout_s=None):
        calls.append(("get_scanned_module_state", timeout_s))
        return backend.NO_ERR, False

    monkeypatch.setattr(backend, "connect", fake_connect)
    monkeypatch.setattr(backend, "rescan_modules", fake_rescan_modules)
    monkeypatch.setattr(backend, "scan_modules", fake_scan_modules)
    monkeypatch.setattr(
        backend,
        "get_scanned_module_state",
        fake_get_scanned_module_state,
    )

    modules = backend.initialize(timeout_s=1.5)

    assert modules == {3: {"product_no": 132306}}
    assert calls == [
        ("connect", 1.5),
        ("rescan_modules", 1.5),
        ("scan_modules", 1.5),
        ("get_scanned_module_state", 1.5),
    ]


def test_initialize_persists_scan_state_by_default(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")

    monkeypatch.setattr(backend, "connect", lambda timeout_s=5.0: True)
    monkeypatch.setattr(backend, "rescan_modules", lambda timeout_s=None: backend.NO_ERR)
    monkeypatch.setattr(backend, "scan_modules", lambda timeout_s=None: {3: {"state": 0}})
    monkeypatch.setattr(
        backend,
        "get_scanned_module_state",
        lambda timeout_s=None: (backend.NO_ERR, True),
    )
    persist = Mock(return_value=backend.NO_ERR)
    monkeypatch.setattr(backend, "set_scanned_module_state", persist)

    modules = backend.initialize(timeout_s=2.0)

    assert modules == {3: {"state": 0}}
    persist.assert_called_once_with(timeout_s=2.0)


def test_initialize_disconnects_when_rescan_fails(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")

    def fake_connect(timeout_s=5.0):
        backend.connected = True
        return True

    monkeypatch.setattr(backend, "connect", fake_connect)
    monkeypatch.setattr(
        backend, "rescan_modules", lambda timeout_s=None: backend.ERR_COMMAND_RECEIVE
    )
    disconnect = Mock(return_value=True)
    monkeypatch.setattr(backend, "disconnect", disconnect)

    with pytest.raises(RuntimeError, match="rescan_modules failed"):
        backend.initialize(timeout_s=2.0)

    disconnect.assert_called_once_with()


def test_initialize_can_skip_persisting_scan_state(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")

    monkeypatch.setattr(backend, "connect", lambda timeout_s=5.0: True)
    monkeypatch.setattr(backend, "rescan_modules", lambda timeout_s=None: backend.NO_ERR)
    monkeypatch.setattr(backend, "scan_modules", lambda timeout_s=None: {3: {"state": 0}})
    monkeypatch.setattr(
        backend,
        "get_scanned_module_state",
        lambda timeout_s=None: (backend.NO_ERR, True),
    )
    persist = Mock(return_value=backend.NO_ERR)
    monkeypatch.setattr(backend, "set_scanned_module_state", persist)

    modules = backend.initialize(timeout_s=2.0, persist_scan=False)

    assert modules == {3: {"state": 0}}
    persist.assert_not_called()


def test_scan_modules_raises_when_presence_read_fails(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")
    backend.connected = True

    monkeypatch.setattr(
        DMMRBase,
        "get_module_presence",
        lambda self: (self.ERR_NOT_CONNECTED, False, 0, [0] * self.MODULE_NUM),
    )

    with pytest.raises(RuntimeError, match="get_module_presence failed"):
        backend.scan_modules(timeout_s=1.5)


def test_get_product_info_returns_structured_metadata(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    dmmr.connected = True
    monkeypatch.setattr(DMMRBase, "get_product_no", lambda self: (self.NO_ERR, 132306))
    monkeypatch.setattr(
        DMMRBase, "get_product_id", lambda self: (self.NO_ERR, "DMMR-CTRL-8")
    )
    monkeypatch.setattr(
        DMMRBase, "get_device_type", lambda self: (self.NO_ERR, self.DEVICE_TYPE)
    )
    monkeypatch.setattr(DMMRBase, "get_fw_version", lambda self: (self.NO_ERR, 0x0104))
    monkeypatch.setattr(
        DMMRBase, "get_fw_date", lambda self: (self.NO_ERR, "2026-04-08")
    )
    monkeypatch.setattr(DMMRBase, "get_hw_type", lambda self: (self.NO_ERR, 18))
    monkeypatch.setattr(DMMRBase, "get_hw_version", lambda self: (self.NO_ERR, 2))
    monkeypatch.setattr(
        DMMRBase, "get_manuf_date", lambda self: (self.NO_ERR, 2026, 14)
    )
    monkeypatch.setattr(
        DMMRBase, "get_base_product_no", lambda self: (self.NO_ERR, 132300)
    )
    monkeypatch.setattr(
        DMMRBase, "get_base_manuf_date", lambda self: (self.NO_ERR, 2025, 50)
    )
    monkeypatch.setattr(DMMRBase, "get_base_hw_type", lambda self: (self.NO_ERR, 7))
    monkeypatch.setattr(DMMRBase, "get_base_hw_version", lambda self: (self.NO_ERR, 3))

    info = dmmr.get_product_info()

    assert info == {
        "product_no": 132306,
        "product_id": "DMMR-CTRL-8",
        "device_type": DMMRBase.DEVICE_TYPE,
        "firmware": {
            "version": 0x0104,
            "date": "2026-04-08",
        },
        "hardware": {
            "type": 18,
            "version": 2,
        },
        "manufacturing": {
            "year": 2026,
            "calendar_week": 14,
        },
        "base": {
            "product_no": 132300,
            "hardware": {
                "type": 7,
                "version": 3,
            },
            "manufacturing": {
                "year": 2025,
                "calendar_week": 50,
            },
        },
    }


def test_get_product_info_uses_timeout_safe_batch_wrapper(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")
    backend.connected = True
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        return {"product_id": "DMMR-CTRL-8"}

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    assert backend.get_product_info(timeout_s=2.0) == {"product_id": "DMMR-CTRL-8"}
    assert calls == [("_get_product_info_unlocked", 10.0, "get_product_info", ())]


def test_collect_housekeeping_returns_structured_snapshot(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    dmmr.connected = True
    monkeypatch.setattr(
        DMMRBase, "get_state", lambda self: (self.NO_ERR, "0x0000", "ST_ON")
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_device_state",
        lambda self: (self.NO_ERR, "0x1000", ["DS_MODULE_FAIL"]),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_voltage_state",
        lambda self: (self.NO_ERR, "0x0007", ["VS_3V3_OK", "VS_5V0_OK", "VS_12V_OK"]),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_temperature_state",
        lambda self: (self.NO_ERR, "0x0000", ["TEMPERATURE_OK"]),
    )
    monkeypatch.setattr(DMMRBase, "get_enable", lambda self: (self.NO_ERR, True))
    monkeypatch.setattr(
        DMMRBase, "get_automatic_current", lambda self: (self.NO_ERR, False)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_housekeeping",
        lambda self: (self.NO_ERR, 12.0, 5.0, 3.3, 41.5),
    )
    monkeypatch.setattr(
        DMMRBase, "get_cpu_data", lambda self: (self.NO_ERR, 0.2, 180_000_000.0)
    )
    monkeypatch.setattr(
        DMMRBase, "get_uptime_int", lambda self: (self.NO_ERR, 10, 20, 100, 200)
    )
    monkeypatch.setattr(
        DMMRBase, "get_optime_int", lambda self: (self.NO_ERR, 3, 4, 30, 40)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_base_state",
        lambda self: (self.NO_ERR, "0x4e80", ["BS_ENABLE", "BS_ON_TEMP"]),
    )
    monkeypatch.setattr(DMMRBase, "get_base_temp", lambda self: (self.NO_ERR, 37.5))
    monkeypatch.setattr(
        DMMRBase,
        "get_base_fan_pwm",
        lambda self: (self.NO_ERR, 1200, "0x1600", ["FAN_OK", "FAN_INSTAL"]),
    )
    monkeypatch.setattr(DMMRBase, "get_base_fan_rpm", lambda self: (self.NO_ERR, 950.0))
    monkeypatch.setattr(
        DMMRBase, "get_base_led_data", lambda self: (self.NO_ERR, False, True, False)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_presence",
        lambda self: (self.NO_ERR, True, 8, [0, 0, 0, self.MODULE_PRESENT, 0, 0, 0, 0]),
    )
    monkeypatch.setattr(
        DMMRBase, "get_scanned_module_state", lambda self: (self.NO_ERR, True)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_product_id",
        lambda self, address: (self.NO_ERR, f"DPA-{address}"),
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_product_no", lambda self, address: (self.NO_ERR, 4500 + address)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_device_type",
        lambda self, address: (self.NO_ERR, self.MODULE_TYPE),
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_fw_version", lambda self, address: (self.NO_ERR, 0x0201)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_fw_date", lambda self, address: (self.NO_ERR, "2026-04-07")
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_hw_type", lambda self, address: (self.NO_ERR, 91)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_hw_version", lambda self, address: (self.NO_ERR, 5)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_manuf_date", lambda self, address: (self.NO_ERR, 2026, 12)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_uptime_int", lambda self, address: (self.NO_ERR, 1, 2, 10, 20)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_optime_int", lambda self, address: (self.NO_ERR, 3, 4, 30, 40)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_cpu_data", lambda self, address: (self.NO_ERR, 0.15)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_housekeeping",
        lambda self, address: (
            self.NO_ERR,
            3.3,
            44.0,
            5.0,
            12.0,
            3.31,
            45.0,
            2.5,
            -36.0,
            20.0,
            -20.0,
            15.0,
            -15.0,
            1.8,
            -1.8,
            2.048,
            -2.048,
        ),
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_state", lambda self, address: (self.NO_ERR, 7)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_buffer_state", lambda self, address: (self.NO_ERR, False)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_ready_flags",
        lambda self, address: (
            self.NO_ERR,
            self.MEAS_CUR_RDY | self.HK_MOD_DATA_RDY,
        ),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_meas_range",
        lambda self, address: (self.ERR_COMMAND_RECEIVE, 0, False),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_current",
        lambda self, address: (self.NO_ERR, 2.5e-12, 3),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_scanned_module_params",
        lambda self, address: (self.NO_ERR, 4503, 4503, 91, 91),
    )

    snapshot = dmmr.collect_housekeeping()

    assert snapshot["device_enabled"] is True
    assert snapshot["automatic_current"] is False
    assert snapshot["main_state"] == {"hex": "0x0000", "name": "ST_ON"}
    assert snapshot["device_state"] == {
        "hex": "0x1000",
        "flags": ["DS_MODULE_FAIL"],
    }
    assert snapshot["voltage_state"] == {
        "hex": "0x0007",
        "flags": ["VS_3V3_OK", "VS_5V0_OK", "VS_12V_OK"],
    }
    assert snapshot["temperature_state"] == {
        "hex": "0x0000",
        "flags": ["TEMPERATURE_OK"],
    }
    assert snapshot["housekeeping"] == {
        "volt_12v_v": 12.0,
        "volt_5v0_v": 5.0,
        "volt_3v3_v": 3.3,
        "temp_cpu_c": 41.5,
    }
    assert snapshot["cpu"] == {"load": 0.2, "frequency_hz": 180_000_000.0}
    assert snapshot["uptime"] == {
        "seconds": 10,
        "milliseconds": 20,
        "operation_seconds": 3,
        "operation_milliseconds": 4,
        "total_uptime_seconds": 100,
        "total_uptime_milliseconds": 200,
        "total_operation_seconds": 30,
        "total_operation_milliseconds": 40,
    }
    assert snapshot["base"] == {
        "state": {
            "hex": "0x4e80",
            "flags": ["BS_ENABLE", "BS_ON_TEMP"],
        },
        "temperature_c": 37.5,
        "fan": {
            "pwm": 1200,
            "rpm": 950.0,
            "state": {
                "hex": "0x1600",
                "flags": ["FAN_OK", "FAN_INSTAL"],
            },
        },
        "led": {
            "red": False,
            "green": True,
            "blue": False,
        },
    }
    assert snapshot["module_presence"] == {
        "valid": True,
        "max_module": 8,
        "present": [3],
        "raw": [0, 0, 0, DMMRBase.MODULE_PRESENT, 0, 0, 0, 0],
    }
    assert snapshot["scanned_module_state"] == {"module_mismatch": True}
    assert snapshot["modules"][3]["product_id"] == "DPA-3"
    assert snapshot["modules"][3]["product_no"] == 4503
    assert snapshot["modules"][3]["device_type"] == DMMRBase.MODULE_TYPE
    assert snapshot["modules"][3]["firmware"] == {
        "version": 0x0201,
        "date": "2026-04-07",
    }
    assert snapshot["modules"][3]["hardware"] == {"type": 91, "version": 5}
    assert snapshot["modules"][3]["manufacturing"] == {
        "year": 2026,
        "calendar_week": 12,
    }
    assert snapshot["modules"][3]["uptime"] == {
        "seconds": 1,
        "milliseconds": 2,
        "total_seconds": 10,
        "total_milliseconds": 20,
        "operation_seconds": 3,
        "operation_milliseconds": 4,
        "total_operation_seconds": 30,
        "total_operation_milliseconds": 40,
    }
    assert snapshot["modules"][3]["cpu"] == {"load": 0.15}
    assert snapshot["modules"][3]["state"] == 7
    assert snapshot["modules"][3]["buffer"] == {"empty": False}
    assert snapshot["modules"][3]["ready_flags"] == {
        "raw": DMMRBase.MEAS_CUR_RDY | DMMRBase.HK_MOD_DATA_RDY,
        "measurement_current_ready": True,
        "measurement_housekeeping_ready": False,
        "module_housekeeping_ready": True,
    }
    assert snapshot["modules"][3]["current"] == {"value": 2.5e-12, "range": 3}
    assert snapshot["modules"][3]["scanned_params"] == {
        "scanned_product_no": 4503,
        "saved_product_no": 4503,
        "scanned_hw_type": 91,
        "saved_hw_type": 91,
    }
    assert "measurement_range" not in snapshot["modules"][3]


def test_collect_housekeeping_uses_timeout_safe_batch_wrapper(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")
    backend.connected = True
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        return {"main_state": {"name": "ST_ON"}}

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    assert backend.collect_housekeeping(timeout_s=3.0) == {
        "main_state": {"name": "ST_ON"}
    }
    assert calls == [("_collect_housekeeping_unlocked", 20.0, "collect_housekeeping", ())]


def test_collect_housekeeping_caches_optional_module_commands(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")
    backend.connected = True
    calls = {"optime": 0, "meas_range": 0}

    monkeypatch.setattr(
        DMMRBase, "get_state", lambda self: (self.NO_ERR, "0x0000", "ST_ON")
    )
    monkeypatch.setattr(
        DMMRBase, "get_device_state", lambda self: (self.NO_ERR, "0x0000", ["DEVICE_OK"])
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_voltage_state",
        lambda self: (self.NO_ERR, "0x0007", ["VS_3V3_OK", "VS_5V0_OK", "VS_12V_OK"]),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_temperature_state",
        lambda self: (self.NO_ERR, "0x0000", ["TEMPERATURE_OK"]),
    )
    monkeypatch.setattr(DMMRBase, "get_enable", lambda self: (self.NO_ERR, True))
    monkeypatch.setattr(
        DMMRBase, "get_automatic_current", lambda self: (self.NO_ERR, False)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_housekeeping",
        lambda self: (self.NO_ERR, 12.0, 5.0, 3.3, 41.5),
    )
    monkeypatch.setattr(
        DMMRBase, "get_cpu_data", lambda self: (self.NO_ERR, 0.2, 180_000_000.0)
    )
    monkeypatch.setattr(
        DMMRBase, "get_uptime_int", lambda self: (self.NO_ERR, 10, 20, 100, 200)
    )
    monkeypatch.setattr(
        DMMRBase, "get_optime_int", lambda self: (self.NO_ERR, 3, 4, 30, 40)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_base_state",
        lambda self: (self.NO_ERR, "0x0000", ["BASE_OK"]),
    )
    monkeypatch.setattr(DMMRBase, "get_base_temp", lambda self: (self.NO_ERR, 37.5))
    monkeypatch.setattr(
        DMMRBase,
        "get_base_fan_pwm",
        lambda self: (self.NO_ERR, 1200, "0x1600", ["FAN_OK"]),
    )
    monkeypatch.setattr(DMMRBase, "get_base_fan_rpm", lambda self: (self.NO_ERR, 950.0))
    monkeypatch.setattr(
        DMMRBase, "get_base_led_data", lambda self: (self.NO_ERR, False, True, False)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_presence",
        lambda self: (self.NO_ERR, True, 8, [0, self.MODULE_PRESENT, 0, 0, 0, 0, 0, 0]),
    )
    monkeypatch.setattr(
        DMMRBase, "get_scanned_module_state", lambda self: (self.NO_ERR, False)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_product_id",
        lambda self, address: (self.NO_ERR, f"DPA-{address}"),
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_product_no", lambda self, address: (self.NO_ERR, 4500 + address)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_device_type",
        lambda self, address: (self.NO_ERR, self.MODULE_TYPE),
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_fw_version", lambda self, address: (self.NO_ERR, 0x0201)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_fw_date", lambda self, address: (self.NO_ERR, "2026-04-07")
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_hw_type", lambda self, address: (self.NO_ERR, 91)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_hw_version", lambda self, address: (self.NO_ERR, 5)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_manuf_date", lambda self, address: (self.NO_ERR, 2026, 12)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_uptime_int", lambda self, address: (self.NO_ERR, 1, 2, 10, 20)
    )

    def fake_get_module_optime_int(self, address):
        calls["optime"] += 1
        return self.ERR_COMMAND_RECEIVE, 0, 0, 0, 0

    def fake_get_module_meas_range(self, address):
        calls["meas_range"] += 1
        return self.ERR_DATA_RECEIVE, 0, False

    monkeypatch.setattr(DMMRBase, "get_module_optime_int", fake_get_module_optime_int)
    monkeypatch.setattr(DMMRBase, "get_module_cpu_data", lambda self, address: (self.NO_ERR, 0.15))
    monkeypatch.setattr(
        DMMRBase,
        "get_module_housekeeping",
        lambda self, address: (
            self.NO_ERR,
            3.3,
            44.0,
            5.0,
            12.0,
            3.31,
            45.0,
            2.5,
            -36.0,
            20.0,
            -20.0,
            15.0,
            -15.0,
            1.8,
            -1.8,
            2.048,
            -2.048,
        ),
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_state", lambda self, address: (self.NO_ERR, 7)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_buffer_state", lambda self, address: (self.NO_ERR, False)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_ready_flags",
        lambda self, address: (self.NO_ERR, self.MEAS_CUR_RDY),
    )
    monkeypatch.setattr(DMMRBase, "get_module_meas_range", fake_get_module_meas_range)
    monkeypatch.setattr(
        DMMRBase,
        "get_module_current",
        lambda self, address: (self.NO_ERR, 2.5e-12, 3),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_scanned_module_params",
        lambda self, address: (self.NO_ERR, 4501, 4501, 91, 91),
    )

    first_snapshot = backend.collect_housekeeping()
    second_snapshot = backend.collect_housekeeping()

    assert "measurement_range" not in first_snapshot["modules"][1]
    assert "measurement_range" not in second_snapshot["modules"][1]
    assert first_snapshot["modules"][1]["uptime"] == {
        "seconds": 1,
        "milliseconds": 2,
        "total_seconds": 10,
        "total_milliseconds": 20,
    }
    assert second_snapshot["modules"][1]["uptime"] == {
        "seconds": 1,
        "milliseconds": 2,
        "total_seconds": 10,
        "total_milliseconds": 20,
    }
    assert calls == {"optime": 1, "meas_range": 1}


def test_list_configs_filters_empty_slots(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")

    monkeypatch.setattr(
        backend,
        "_call_locked_with_timeout",
        lambda method, timeout_s, step_name, *args, **kwargs: method(*args, **kwargs),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_config_list",
        lambda self: (self.NO_ERR, [True, False, False], [True, True, False]),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_config_name",
        lambda self, index: (self.NO_ERR, f"config-{index}"),
    )

    assert backend.list_configs() == [
        {"index": 0, "name": "config-0", "active": True, "valid": True},
        {"index": 1, "name": "config-1", "active": False, "valid": True},
    ]


def test_get_config_list_falls_back_to_per_slot_flags(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")
    backend.connected = True

    monkeypatch.setattr(
        backend,
        "_call_locked_with_timeout",
        lambda method, timeout_s, step_name, *args, **kwargs: method(*args, **kwargs),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_config_list",
        lambda self: (self.ERR_DATA_RECEIVE, [], []),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_config_flags",
        lambda self, index: (
            self.NO_ERR,
            index == 0,
            index in {0, 1},
        ),
    )

    status, active_list, valid_list = backend.get_config_list(timeout_s=1.0)

    assert status == backend.NO_ERR
    assert active_list[:3] == [True, False, False]
    assert valid_list[:3] == [True, True, False]
    assert len(active_list) == backend.MAX_CONFIG
    assert len(valid_list) == backend.MAX_CONFIG


def test_get_config_list_caches_optional_direct_query_failure(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")
    backend.connected = True
    calls = {"config_list": 0}

    monkeypatch.setattr(
        backend,
        "_call_locked_with_timeout",
        lambda method, timeout_s, step_name, *args, **kwargs: method(*args, **kwargs),
    )

    def fake_get_config_list(self):
        calls["config_list"] += 1
        return self.ERR_DATA_RECEIVE, [], []

    monkeypatch.setattr(DMMRBase, "get_config_list", fake_get_config_list)
    monkeypatch.setattr(
        DMMRBase,
        "get_config_flags",
        lambda self, index: (self.NO_ERR, False, index == 0),
    )

    backend.get_config_list(timeout_s=1.0)
    backend.get_config_list(timeout_s=1.0)

    assert calls["config_list"] == 1


def test_shutdown_disables_measurement_before_disconnect(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")
    backend.connected = True
    monkeypatch.setattr(backend, "set_automatic_current", Mock(return_value=backend.NO_ERR))
    monkeypatch.setattr(backend, "set_enable", Mock(return_value=backend.NO_ERR))
    monkeypatch.setattr(backend, "disconnect", Mock(return_value=True))

    assert backend.shutdown() is True

    backend.set_automatic_current.assert_called_once_with(False, timeout_s=5.0)
    backend.set_enable.assert_called_once_with(False, timeout_s=5.0)
    backend.disconnect.assert_called_once_with()


def test_disconnect_keeps_connected_true_when_close_port_fails(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")
    backend.connected = True
    monkeypatch.setattr(backend, "stop_housekeeping", Mock(return_value=True))
    monkeypatch.setattr(backend, "_call_locked", Mock(return_value=backend.ERR_CLOSE))

    assert backend.disconnect() is False
    assert backend.connected is True


def test_dmmr_base_rejects_invalid_config_number(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")

    with pytest.raises(ValueError, match="config_number"):
        backend.get_config_name(backend.MAX_CONFIG)


def test_dmmr_base_rejects_wrong_config_data_length(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")

    with pytest.raises(ValueError, match=f"{backend.MAX_REG} register values"):
        backend.set_current_config([0] * (backend.MAX_REG - 1))


def test_dmmr_base_rejects_non_integer_config_data(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")
    config_data = [0] * backend.MAX_REG
    config_data[5] = 1.5

    with pytest.raises(TypeError, match="config_data\\[5\\]"):
        backend.set_config_data(0, config_data)


def test_dmmr_base_rejects_overlong_config_name(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")

    with pytest.raises(ValueError, match="shorter than"):
        backend.set_config_name(0, "x" * backend.CONFIG_NAME_SIZE)


def test_get_module_info_returns_normalized_module_snapshot(monkeypatch):
    dmmr, _dll = make_dmmr(monkeypatch)
    backend = object.__getattribute__(dmmr, "_backend")

    monkeypatch.setattr(
        DMMRBase,
        "get_module_product_id",
        lambda self, address: (self.NO_ERR, f"DPA-{address}"),
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_product_no", lambda self, address: (self.NO_ERR, 4500 + address)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_device_type",
        lambda self, address: (self.NO_ERR, self.MODULE_TYPE),
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_fw_version", lambda self, address: (self.NO_ERR, 0x0201)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_fw_date", lambda self, address: (self.NO_ERR, "2026-04-07")
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_hw_type", lambda self, address: (self.NO_ERR, 91)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_hw_version", lambda self, address: (self.NO_ERR, 5)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_manuf_date", lambda self, address: (self.NO_ERR, 2026, 12)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_uptime_int", lambda self, address: (self.NO_ERR, 1, 2, 10, 20)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_optime_int", lambda self, address: (self.NO_ERR, 3, 4, 30, 40)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_cpu_data", lambda self, address: (self.NO_ERR, 0.15)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_housekeeping",
        lambda self, address: (
            self.NO_ERR,
            3.3,
            44.0,
            5.0,
            12.0,
            3.31,
            45.0,
            2.5,
            -36.0,
            20.0,
            -20.0,
            15.0,
            -15.0,
            1.8,
            -1.8,
            2.048,
            -2.048,
        ),
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_state", lambda self, address: (self.NO_ERR, 7)
    )
    monkeypatch.setattr(
        DMMRBase, "get_module_buffer_state", lambda self, address: (self.NO_ERR, False)
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_ready_flags",
        lambda self, address: (
            self.NO_ERR,
            self.MEAS_CUR_RDY | self.HK_MOD_DATA_RDY,
        ),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_meas_range",
        lambda self, address: (self.NO_ERR, 4, True),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_module_current",
        lambda self, address: (self.NO_ERR, 2.5e-12, 3),
    )
    monkeypatch.setattr(
        DMMRBase,
        "get_scanned_module_params",
        lambda self, address: (self.NO_ERR, 4503, 4503, 91, 91),
    )

    snapshot = backend.get_module_info(3)

    assert snapshot == {
        "address": 3,
        "product_id": "DPA-3",
        "product_no": 4503,
        "device_type": DMMRBase.MODULE_TYPE,
        "firmware": {
            "version": 0x0201,
            "date": "2026-04-07",
        },
        "hardware": {
            "type": 91,
            "version": 5,
        },
        "manufacturing": {
            "year": 2026,
            "calendar_week": 12,
        },
        "uptime": {
            "seconds": 1,
            "milliseconds": 2,
            "total_seconds": 10,
            "total_milliseconds": 20,
            "operation_seconds": 3,
            "operation_milliseconds": 4,
            "total_operation_seconds": 30,
            "total_operation_milliseconds": 40,
        },
        "cpu": {
            "load": 0.15,
        },
        "housekeeping": {
            "volt_3v3_v": 3.3,
            "temp_cpu_c": 44.0,
            "volt_5v0_v": 5.0,
            "volt_12v_v": 12.0,
            "volt_3v3i_v": 3.31,
            "temp_cpui_c": 45.0,
            "volt_2v5i_v": 2.5,
            "volt_36vn_v": -36.0,
            "volt_20vp_v": 20.0,
            "volt_20vn_v": -20.0,
            "volt_15vp_v": 15.0,
            "volt_15vn_v": -15.0,
            "volt_1v8p_v": 1.8,
            "volt_1v8n_v": -1.8,
            "volt_vrefp_v": 2.048,
            "volt_vrefn_v": -2.048,
        },
        "state": 7,
        "buffer": {
            "empty": False,
        },
        "ready_flags": {
            "raw": DMMRBase.MEAS_CUR_RDY | DMMRBase.HK_MOD_DATA_RDY,
            "measurement_current_ready": True,
            "measurement_housekeeping_ready": False,
            "module_housekeeping_ready": True,
        },
        "measurement_range": {
            "range": 4,
            "auto_range": True,
        },
        "current": {
            "value": 2.5e-12,
            "range": 3,
        },
        "scanned_params": {
            "scanned_product_no": 4503,
            "saved_product_no": 4503,
            "scanned_hw_type": 91,
            "saved_hw_type": 91,
        },
    }


def test_public_get_module_info_supports_omitted_address():
    class FakeBackend:
        def scan_modules(self, timeout_s=None):
            return {2: {"state": 0}, 5: {"state": 0}}

        def get_module_info(self, address, **kwargs):
            return {"address": address, "product_id": f"ID-{address}"}

    dmmr = object.__new__(DMMR)
    object.__setattr__(dmmr, "_backend_mode", "inline")
    object.__setattr__(dmmr, "_backend", FakeBackend())
    object.__setattr__(dmmr, "_process_backend_disabled_reason", "")

    assert dmmr.get_module_info(2) == {"address": 2, "product_id": "ID-2"}
    assert dmmr.get_module_info() == {
        2: {"address": 2, "product_id": "ID-2"},
        5: {"address": 5, "product_id": "ID-5"},
    }


def test_public_get_module_info_forwards_timeout_to_scan():
    calls = []

    class FakeBackend:
        def scan_modules(self, timeout_s=None):
            calls.append(("scan_modules", timeout_s))
            return {2: {"state": 0}, 5: {"state": 0}}

        def get_module_info(self, address, **kwargs):
            calls.append(("get_module_info", address, kwargs.get("timeout_s")))
            return {"address": address, "product_id": f"ID-{address}"}

    dmmr = object.__new__(DMMR)
    object.__setattr__(dmmr, "_backend_mode", "inline")
    object.__setattr__(dmmr, "_backend", FakeBackend())
    object.__setattr__(dmmr, "_process_backend_disabled_reason", "")

    assert dmmr.get_module_info(timeout_s=2.5) == {
        2: {"address": 2, "product_id": "ID-2"},
        5: {"address": 5, "product_id": "ID-5"},
    }
    assert calls == [
        ("scan_modules", 2.5),
        ("get_module_info", 2, 2.5),
        ("get_module_info", 5, 2.5),
    ]


def test_public_get_module_current_supports_omitted_address():
    class FakeBackend:
        def scan_modules(self, timeout_s=None):
            return {2: {"state": 0}, 5: {"state": 0}}

        def get_module_current(self, address, timeout_s=None):
            return DMMRBase.NO_ERR, address * 1e-12, address

    dmmr = object.__new__(DMMR)
    object.__setattr__(dmmr, "_backend_mode", "inline")
    object.__setattr__(dmmr, "_backend", FakeBackend())
    object.__setattr__(dmmr, "_process_backend_disabled_reason", "")

    assert dmmr.get_module_current(2) == (DMMRBase.NO_ERR, 2e-12, 2)
    assert dmmr.get_module_current() == {
        2: {"status": DMMRBase.NO_ERR, "current": 2e-12, "meas_range": 2},
        5: {"status": DMMRBase.NO_ERR, "current": 5e-12, "meas_range": 5},
    }


def test_public_get_module_current_forwards_timeout_to_scan():
    calls = []

    class FakeBackend:
        def scan_modules(self, timeout_s=None):
            calls.append(("scan_modules", timeout_s))
            return {2: {"state": 0}, 5: {"state": 0}}

        def get_module_current(self, address, timeout_s=None):
            calls.append(("get_module_current", address, timeout_s))
            return DMMRBase.NO_ERR, address * 1e-12, address

    dmmr = object.__new__(DMMR)
    object.__setattr__(dmmr, "_backend_mode", "inline")
    object.__setattr__(dmmr, "_backend", FakeBackend())
    object.__setattr__(dmmr, "_process_backend_disabled_reason", "")

    assert dmmr.get_module_current(timeout_s=3.0) == {
        2: {"status": DMMRBase.NO_ERR, "current": 2e-12, "meas_range": 2},
        5: {"status": DMMRBase.NO_ERR, "current": 5e-12, "meas_range": 5},
    }
    assert calls == [
        ("scan_modules", 3.0),
        ("get_module_current", 2, 3.0),
        ("get_module_current", 5, 3.0),
    ]
