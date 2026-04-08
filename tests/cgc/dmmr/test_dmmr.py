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
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        return {2: {"state": 0}}

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    modules = backend.scan_modules()

    assert modules == {2: {"state": 0}}
    assert calls == [("scan_all_modules", 5.0, "scan_all_modules", ())]


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


def test_public_get_module_info_supports_omitted_address():
    class FakeBackend:
        def scan_modules(self):
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


def test_public_get_module_current_supports_omitted_address():
    class FakeBackend:
        def scan_modules(self):
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
