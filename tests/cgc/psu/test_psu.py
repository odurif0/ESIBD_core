import ctypes
from pathlib import Path
import logging
import tempfile
from unittest.mock import Mock

import pytest

from cgc.psu import PSU, PSUBase, PSUDllLoadError, PSUPlatformError


ERROR_CODES_PATH = Path(__file__).resolve().parents[3] / "src" / "cgc" / "error_codes.json"


@pytest.fixture(autouse=True)
def clear_psu_connection_registry():
    with PSU._active_connections_lock:
        PSU._active_connections.clear()
    yield
    with PSU._active_connections_lock:
        PSU._active_connections.clear()


def make_psu(monkeypatch, *, device_id="psu_test", com=6, port=0):
    monkeypatch.setattr("cgc.psu.psu_base.sys.platform", "win32")
    dll = Mock()
    monkeypatch.setattr("cgc.psu.psu_base.ctypes.WinDLL", lambda _path: dll, raising=False)
    log_dir = Path(tempfile.gettempdir()) / "esibd_core_test_logs"
    return PSU(device_id, com=com, port=port, log_dir=log_dir), dll


def test_psu_base_rejects_non_windows(monkeypatch):
    monkeypatch.setattr("cgc.psu.psu_base.sys.platform", "linux")

    with pytest.raises(PSUPlatformError):
        PSUBase(com=6)


def test_psu_base_raises_clear_error_when_dll_fails(monkeypatch):
    monkeypatch.setattr("cgc.psu.psu_base.sys.platform", "win32")

    def raise_os_error(_path):
        raise OSError("missing dll")

    monkeypatch.setattr("cgc.psu.psu_base.ctypes.WinDLL", raise_os_error, raising=False)

    with pytest.raises(PSUDllLoadError):
        PSUBase(com=6, error_codes_path=ERROR_CODES_PATH)


def test_psu_base_dll_error_mentions_64bit_python_for_x64_bundle(monkeypatch):
    monkeypatch.setattr("cgc.psu.psu_base.sys.platform", "win32")
    monkeypatch.setattr("cgc.psu.psu_base._python_is_64bit", lambda: False)

    def raise_os_error(_path):
        raise OSError("bad image format")

    monkeypatch.setattr("cgc.psu.psu_base.ctypes.WinDLL", raise_os_error, raising=False)

    with pytest.raises(PSUDllLoadError, match="64-bit Python"):
        PSUBase(com=6, error_codes_path=ERROR_CODES_PATH)


def test_psu_base_get_psu_enable_uses_windows_bool(monkeypatch):
    monkeypatch.setattr("cgc.psu.psu_base.sys.platform", "win32")
    dll = Mock()

    def fake_get_psu_enable(port, psu0_ptr, psu1_ptr):
        assert port == 0
        assert ctypes.sizeof(psu0_ptr._obj) == ctypes.sizeof(PSUBase.WIN_BOOL)
        assert ctypes.sizeof(psu1_ptr._obj) == ctypes.sizeof(PSUBase.WIN_BOOL)
        psu0_ptr._obj.value = 1
        psu1_ptr._obj.value = 0
        return PSUBase.NO_ERR

    dll.COM_HVPSU2D_GetPSUEnable.side_effect = fake_get_psu_enable
    monkeypatch.setattr("cgc.psu.psu_base.ctypes.WinDLL", lambda _path: dll, raising=False)

    base = PSUBase(com=6, error_codes_path=ERROR_CODES_PATH)

    assert base.get_psu_enable() == (PSUBase.NO_ERR, True, False)


def test_psu_base_decodes_config_name_with_replacement(monkeypatch):
    monkeypatch.setattr("cgc.psu.psu_base.sys.platform", "win32")
    dll = Mock()

    def fake_get_config_name(_port, _config_number, name):
        name.value = b"psu-\xffcfg"
        return PSUBase.NO_ERR

    dll.COM_HVPSU2D_GetConfigName.side_effect = fake_get_config_name
    monkeypatch.setattr("cgc.psu.psu_base.ctypes.WinDLL", lambda _path: dll, raising=False)

    base = PSUBase(com=6, error_codes_path=ERROR_CODES_PATH)

    assert base.get_config_name(0) == (PSUBase.NO_ERR, "psu-\ufffdcfg")


def test_psu_rejects_unknown_init_kwargs():
    with pytest.raises(TypeError, match="Unexpected PSU init kwargs: unexpected"):
        PSU("psu_test", com=6, unexpected=True)


@pytest.mark.parametrize("baudrate", [0, -1])
def test_psu_rejects_non_positive_baudrate(baudrate):
    with pytest.raises(ValueError, match="baudrate must be > 0"):
        PSU("psu_test", com=6, baudrate=baudrate)


def test_psu_external_logger_prefixes_device_id(monkeypatch, caplog):
    monkeypatch.setattr("cgc.psu.psu_base.sys.platform", "win32")
    monkeypatch.setattr(
        "cgc.psu.psu_base.ctypes.WinDLL",
        lambda _path: Mock(),
        raising=False,
    )
    logger = logging.getLogger("test_psu_external_logger")

    psu = PSU("psu_test", com=6, logger=logger)

    with caplog.at_level(logging.INFO, logger=logger.name):
        psu.logger.info("hello")

    assert "psu_test - hello" in caplog.messages


def test_psu_uses_process_backend_when_supported(monkeypatch):
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

    psu = PSU("psu_process", com=6, port=1)

    assert psu._backend_mode == "process"
    assert created["controller_path"] == "cgc.psu.psu:_PSUController"
    assert created["label"] == "PSU psu_process"
    assert created["controller_kwargs"]["device_id"] == "psu_process"
    assert created["controller_kwargs"]["com"] == 6
    assert created["controller_kwargs"]["port"] == 1
    assert created["controller_kwargs"]["logger"] is None

    psu.close()

    assert psu._backend.closed is True


def test_connect_rolls_back_when_baud_rate_fails(monkeypatch):
    psu, dll = make_psu(monkeypatch)
    dll.COM_HVPSU2D_Open.return_value = PSUBase.NO_ERR
    dll.COM_HVPSU2D_SetBaudRate.return_value = PSUBase.ERR_RATE
    dll.COM_HVPSU2D_Close.return_value = PSUBase.NO_ERR

    with pytest.raises(RuntimeError, match="set_baud_rate failed"):
        psu.connect()

    assert psu.connected is False
    dll.COM_HVPSU2D_Close.assert_called_once()


def test_connect_warns_when_reusing_the_same_dll_port(monkeypatch, caplog):
    psu_a, dll_a = make_psu(monkeypatch, device_id="psu_a", com=6, port=0)
    psu_b, dll_b = make_psu(monkeypatch, device_id="psu_b", com=7, port=0)
    dll_a.COM_HVPSU2D_Open.return_value = PSUBase.NO_ERR
    dll_b.COM_HVPSU2D_Open.return_value = PSUBase.NO_ERR
    dll_a.COM_HVPSU2D_SetBaudRate.return_value = PSUBase.NO_ERR
    dll_b.COM_HVPSU2D_SetBaudRate.return_value = PSUBase.NO_ERR

    psu_a.connect()
    with caplog.at_level(logging.WARNING):
        psu_b.connect()

    assert "same DLL port" in caplog.text
    assert "port 0" in caplog.text


def test_connect_warns_when_multiple_dll_ports_are_active(monkeypatch, caplog):
    psu_a, dll_a = make_psu(monkeypatch, device_id="psu_a", com=6, port=0)
    psu_b, dll_b = make_psu(monkeypatch, device_id="psu_b", com=7, port=1)
    dll_a.COM_HVPSU2D_Open.return_value = PSUBase.NO_ERR
    dll_b.COM_HVPSU2D_Open.return_value = PSUBase.NO_ERR
    dll_a.COM_HVPSU2D_SetBaudRate.return_value = PSUBase.NO_ERR
    dll_b.COM_HVPSU2D_SetBaudRate.return_value = PSUBase.NO_ERR
    monkeypatch.setattr(
        PSUBase, "get_product_id", lambda self: (self.NO_ERR, "PSU-CTRL-2D")
    )

    psu_a.connect()
    with caplog.at_level(logging.WARNING):
        psu_b.connect()

    assert "Multiple PSU instances in this process currently claim DLL ports" in caplog.text
    assert "[0, 1]" in caplog.text


def test_connect_warns_when_product_id_looks_like_another_instrument(monkeypatch, caplog):
    psu, dll = make_psu(monkeypatch)
    dll.COM_HVPSU2D_Open.return_value = PSUBase.NO_ERR
    dll.COM_HVPSU2D_SetBaudRate.return_value = PSUBase.NO_ERR
    monkeypatch.setattr(
        PSUBase,
        "get_product_id",
        lambda self: (self.NO_ERR, "HV-AMX-CTRL-4ED, Rev.2-20"),
    )

    with caplog.at_level(logging.WARNING):
        psu.connect()

    assert "does not look like a PSU controller" in caplog.text
    assert "HV-AMX-CTRL-4ED" in caplog.text


def test_connect_identity_probe_uses_timeout_safe_wrapper(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    backend = object.__getattribute__(psu, "_backend")
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        if method.__name__ == "open_port":
            return backend.NO_ERR
        if method.__name__ == "set_baud_rate":
            return backend.NO_ERR, backend.baudrate
        if method.__name__ == "get_product_id":
            return backend.NO_ERR, "PSU-CTRL-2D"
        raise AssertionError(f"Unexpected method: {method.__name__}")

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    assert backend.connect(timeout_s=2.5) is True

    assert calls == [
        ("open_port", 2.5, "open_port", (backend.com, backend.port_num)),
        ("set_baud_rate", 2.5, "set_baud_rate", (backend.baudrate,)),
        ("get_product_id", 2.5, "get_product_id", (backend,)),
    ]


def test_load_config_calls_vendor_wrapper(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    monkeypatch.setattr(PSUBase, "load_current_config", lambda self, index: self.NO_ERR)

    psu.load_config(19)


def test_initialize_runs_routine_sequence_and_returns_standby_state(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    backend = object.__getattribute__(psu, "_backend")
    calls = []

    def fake_connect(timeout_s=5.0):
        backend.connected = True
        calls.append(("connect", timeout_s))
        return True

    monkeypatch.setattr(backend, "connect", fake_connect)
    monkeypatch.setattr(
        backend,
        "load_config",
        lambda config_number, timeout_s=None: calls.append(
            ("load_config", config_number, timeout_s)
        ),
    )
    monkeypatch.setattr(
        backend,
        "get_device_enabled",
        lambda timeout_s=None: calls.append(("get_device_enabled", timeout_s)) or False,
    )
    monkeypatch.setattr(
        backend,
        "get_output_enabled",
        lambda timeout_s=None: calls.append(("get_output_enabled", timeout_s))
        or (False, False),
    )

    result = backend.initialize(
        timeout_s=2.5,
        standby_config=1,
        operating_config=7,
    )

    assert result == {
        "standby_config": 1,
        "device_enabled": False,
        "output_enabled": (False, False),
        "operating_config": 7,
    }
    assert calls == [
        ("connect", 2.5),
        ("load_config", 1, 2.5),
        ("get_device_enabled", 2.5),
        ("get_output_enabled", 2.5),
        ("load_config", 7, 2.5),
    ]


def test_initialize_can_stop_after_standby_checks(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    backend = object.__getattribute__(psu, "_backend")
    calls = []

    def fake_connect(timeout_s=5.0):
        backend.connected = True
        calls.append(("connect", timeout_s))
        return True

    monkeypatch.setattr(backend, "connect", fake_connect)
    monkeypatch.setattr(
        backend,
        "load_config",
        lambda config_number, timeout_s=None: calls.append(
            ("load_config", config_number, timeout_s)
        ),
    )
    monkeypatch.setattr(backend, "get_device_enabled", lambda timeout_s=None: True)
    monkeypatch.setattr(
        backend, "get_output_enabled", lambda timeout_s=None: (False, False)
    )

    result = backend.initialize(timeout_s=2.0, standby_config=3)

    assert result == {
        "standby_config": 3,
        "device_enabled": True,
        "output_enabled": (False, False),
    }
    assert calls == [
        ("connect", 2.0),
        ("load_config", 3, 2.0),
    ]


def test_initialize_rejects_standby_configs_that_leave_outputs_enabled(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    backend = object.__getattribute__(psu, "_backend")

    def fake_connect(timeout_s=5.0):
        backend.connected = True
        return True

    monkeypatch.setattr(backend, "connect", fake_connect)
    monkeypatch.setattr(backend, "load_config", lambda config_number, timeout_s=None: None)
    monkeypatch.setattr(backend, "get_device_enabled", lambda timeout_s=None: True)
    monkeypatch.setattr(
        backend, "get_output_enabled", lambda timeout_s=None: (True, False)
    )
    shutdown = Mock(return_value=True)
    monkeypatch.setattr(backend, "shutdown", shutdown)

    with pytest.raises(RuntimeError, match="left outputs enabled"):
        backend.initialize(timeout_s=2.0, standby_config=1)

    shutdown.assert_called_once_with(timeout_s=2.0)


def test_initialize_uses_disconnect_cleanup_when_transport_is_poisoned(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    backend = object.__getattribute__(psu, "_backend")

    def fake_connect(timeout_s=5.0):
        backend.connected = True
        return True

    monkeypatch.setattr(backend, "connect", fake_connect)
    monkeypatch.setattr(backend, "load_config", lambda config_number, timeout_s=None: None)
    monkeypatch.setattr(backend, "get_device_enabled", lambda timeout_s=None: False)

    def fake_get_output_enabled(timeout_s=None):
        backend._poison_transport("get_output_enabled")
        raise RuntimeError("timed out during get_output_enabled")

    monkeypatch.setattr(backend, "get_output_enabled", fake_get_output_enabled)
    disconnect = Mock(return_value=False)
    monkeypatch.setattr(backend, "disconnect", disconnect)
    monkeypatch.setattr(
        backend,
        "shutdown",
        Mock(side_effect=AssertionError("shutdown should not be used when poisoned")),
    )

    with pytest.raises(RuntimeError, match="timed out during get_output_enabled"):
        backend.initialize(timeout_s=2.0, standby_config=1)

    disconnect.assert_called_once_with(timeout_s=2.0)


def test_shutdown_disables_outputs_and_device_by_default_and_propagates_errors(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    monkeypatch.setattr(psu, "set_channel_current", Mock())
    monkeypatch.setattr(psu, "set_channel_voltage", Mock())
    monkeypatch.setattr(
        psu, "set_output_enabled", Mock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(psu, "set_device_enabled", Mock())
    monkeypatch.setattr(psu, "disconnect", Mock(return_value=True))

    with pytest.raises(
        RuntimeError,
        match="set_output_enabled\\(False, False\\): boom",
    ):
        psu.shutdown()

    psu.set_channel_current.assert_any_call(0, 0.0)
    psu.set_channel_current.assert_any_call(1, 0.0)
    psu.set_channel_voltage.assert_any_call(0, 0.0)
    psu.set_channel_voltage.assert_any_call(1, 0.0)
    psu.set_output_enabled.assert_called_once_with(False, False)
    psu.set_device_enabled.assert_called_once_with(False)
    psu.disconnect.assert_called_once()


def test_shutdown_zeros_setpoints_before_disabling_outputs_and_device(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    calls = []

    monkeypatch.setattr(
        psu,
        "set_channel_current",
        lambda channel, current: calls.append(("current", channel, current)),
    )
    monkeypatch.setattr(
        psu,
        "set_channel_voltage",
        lambda channel, voltage: calls.append(("voltage", channel, voltage)),
    )
    monkeypatch.setattr(
        psu,
        "set_output_enabled",
        lambda psu0, psu1: calls.append(("outputs", psu0, psu1)),
    )
    monkeypatch.setattr(
        psu,
        "set_device_enabled",
        lambda enabled: calls.append(("device", enabled)),
    )
    monkeypatch.setattr(
        psu,
        "disconnect",
        lambda: calls.append(("disconnect",)) or True,
    )

    assert psu.shutdown() is True

    assert calls == [
        ("current", 0, 0.0),
        ("current", 1, 0.0),
        ("voltage", 0, 0.0),
        ("voltage", 1, 0.0),
        ("outputs", False, False),
        ("device", False),
        ("disconnect",),
    ]


@pytest.mark.parametrize("voltage_v", [float("nan"), float("inf"), -float("inf")])
def test_set_channel_voltage_rejects_non_finite_values(monkeypatch, voltage_v):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    call_locked = Mock()
    monkeypatch.setattr(psu, "_call_locked", call_locked)

    with pytest.raises(ValueError, match="voltage must be finite"):
        psu.set_channel_voltage(0, voltage_v)

    call_locked.assert_not_called()


@pytest.mark.parametrize("current_a", [float("nan"), float("inf"), -float("inf")])
def test_set_channel_current_rejects_non_finite_values(monkeypatch, current_a):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    call_locked = Mock()
    monkeypatch.setattr(psu, "_call_locked", call_locked)

    with pytest.raises(ValueError, match="current must be finite"):
        psu.set_channel_current(0, current_a)

    call_locked.assert_not_called()


def test_shutdown_rejects_standby_config_when_disable_flags_are_enabled(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    monkeypatch.setattr(psu, "load_config", Mock())
    monkeypatch.setattr(psu, "disconnect", Mock(return_value=True))

    with pytest.raises(ValueError, match="standby_config"):
        psu.shutdown(standby_config=3)

    psu.load_config.assert_not_called()
    psu.disconnect.assert_not_called()


def test_shutdown_can_load_explicit_standby_config_without_disable_steps(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    monkeypatch.setattr(psu, "load_config", Mock())
    monkeypatch.setattr(psu, "set_output_enabled", Mock())
    monkeypatch.setattr(psu, "set_device_enabled", Mock())
    monkeypatch.setattr(psu, "disconnect", Mock(return_value=True))

    assert (
        psu.shutdown(
            standby_config=3,
            disable_outputs=False,
            disable_device=False,
        )
        is True
    )

    psu.load_config.assert_called_once_with(3)
    psu.set_output_enabled.assert_not_called()
    psu.set_device_enabled.assert_not_called()
    psu.disconnect.assert_called_once()


def test_failed_disconnect_keeps_dll_port_claim_warning(monkeypatch, caplog):
    psu_a, dll_a = make_psu(monkeypatch, device_id="psu_a", com=6, port=0)
    psu_b, dll_b = make_psu(monkeypatch, device_id="psu_b", com=7, port=0)
    dll_a.COM_HVPSU2D_Open.return_value = PSUBase.NO_ERR
    dll_b.COM_HVPSU2D_Open.return_value = PSUBase.NO_ERR
    dll_a.COM_HVPSU2D_SetBaudRate.return_value = PSUBase.NO_ERR
    dll_b.COM_HVPSU2D_SetBaudRate.return_value = PSUBase.NO_ERR
    dll_a.COM_HVPSU2D_Close.return_value = PSUBase.ERR_CLOSE

    psu_a.connect()
    assert psu_a.disconnect() is False
    caplog.clear()

    with caplog.at_level(logging.WARNING):
        psu_b.connect()

    assert psu_a.connected is True
    assert psu_a._dll_port_claimed is True
    assert "same DLL port" in caplog.text


def test_list_configs_filters_empty_entries(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    monkeypatch.setattr(
        PSUBase,
        "get_config_list",
        lambda self: (self.NO_ERR, [True, False, False], [True, False, True]),
    )
    names = {0: (PSUBase.NO_ERR, "standby"), 2: (PSUBase.NO_ERR, "test")}
    monkeypatch.setattr(PSUBase, "get_config_name", lambda self, index: names[index])

    configs = psu.list_configs()

    assert configs == [
        {"index": 0, "name": "standby", "active": True, "valid": True},
        {"index": 2, "name": "test", "active": False, "valid": True},
    ]


def test_list_configs_falls_back_to_per_config_flags(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    monkeypatch.setattr(
        PSUBase,
        "get_config_list",
        lambda self: (self.ERR_DATA_RECEIVE, [], []),
    )
    flags = {
        0: (PSUBase.NO_ERR, True, True),
        1: (PSUBase.NO_ERR, False, False),
        2: (PSUBase.NO_ERR, False, True),
    }
    monkeypatch.setattr(
        PSUBase,
        "get_config_flags",
        lambda self, index: flags.get(index, (self.NO_ERR, False, False)),
    )
    names = {0: (PSUBase.NO_ERR, "standby"), 2: (PSUBase.NO_ERR, "test")}
    monkeypatch.setattr(PSUBase, "get_config_name", lambda self, index: names[index])

    configs = psu.list_configs()

    assert configs == [
        {"index": 0, "name": "standby", "active": True, "valid": True},
        {"index": 2, "name": "test", "active": False, "valid": True},
    ]


def test_list_configs_uses_timeout_safe_wrapper(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    backend = object.__getattribute__(psu, "_backend")
    backend.connected = True
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        if method.__name__ == "_list_configs_unlocked":
            return [{"index": 0, "name": "standby", "active": True, "valid": True}]
        raise AssertionError(f"Unexpected method: {method.__name__}")

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    assert backend.list_configs(timeout_s=3.0) == [
        {"index": 0, "name": "standby", "active": True, "valid": True}
    ]
    assert calls == [
        ("_list_configs_unlocked", 11.0, "list_configs", (False,)),
    ]


def test_get_product_info_returns_structured_metadata(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    monkeypatch.setattr(PSUBase, "get_product_no", lambda self: (self.NO_ERR, 350))
    monkeypatch.setattr(
        PSUBase, "get_product_id", lambda self: (self.NO_ERR, "PSU-CTRL-2D")
    )
    monkeypatch.setattr(PSUBase, "get_fw_version", lambda self: (self.NO_ERR, 0x0102))
    monkeypatch.setattr(
        PSUBase, "get_fw_date", lambda self: (self.NO_ERR, "2026-03-31")
    )
    monkeypatch.setattr(PSUBase, "get_hw_type", lambda self: (self.NO_ERR, 17))
    monkeypatch.setattr(PSUBase, "get_hw_version", lambda self: (self.NO_ERR, 3))

    info = psu.get_product_info()

    assert info == {
        "product_no": 350,
        "product_id": "PSU-CTRL-2D",
        "firmware": {
            "version": 0x0102,
            "date": "2026-03-31",
        },
        "hardware": {
            "type": 17,
            "version": 3,
        },
    }


def test_get_product_info_tolerates_optional_metadata_failures(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    monkeypatch.setattr(PSUBase, "get_product_no", lambda self: (self.NO_ERR, 350))
    monkeypatch.setattr(
        PSUBase, "get_product_id", lambda self: (self.NO_ERR, "PSU-CTRL-2D")
    )
    monkeypatch.setattr(PSUBase, "get_fw_version", lambda self: (self.NO_ERR, 0x0102))
    monkeypatch.setattr(
        PSUBase, "get_fw_date", lambda self: (self.NO_ERR, "2026-03-31")
    )
    monkeypatch.setattr(
        PSUBase, "get_hw_type", lambda self: (self.ERR_DATA_RECEIVE, 0)
    )
    monkeypatch.setattr(
        PSUBase, "get_hw_version", lambda self: (self.ERR_COMMAND_RECEIVE, 0)
    )

    info = psu.get_product_info()

    assert info == {
        "product_no": 350,
        "product_id": "PSU-CTRL-2D",
        "firmware": {
            "version": 0x0102,
            "date": "2026-03-31",
        },
        "hardware": {
            "type": None,
            "version": None,
        },
    }


def test_get_output_enabled_falls_back_to_psu_state(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    monkeypatch.setattr(
        PSUBase,
        "get_psu_enable",
        lambda self: (self.ERR_COMMAND_RECEIVE, False, False),
    )
    monkeypatch.setattr(
        PSUBase,
        "get_psu_state",
        lambda self: (
            self.NO_ERR,
            self.PSU_STATE_PSU0_ENB_CTRL | self.PSU_STATE_PSU1_ENB_CTRL,
        ),
    )

    assert psu.get_output_enabled() == (True, True)


def test_collect_housekeeping_returns_structured_snapshot(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    monkeypatch.setattr(
        PSUBase, "get_main_state", lambda self: (self.NO_ERR, "0x0000", "STATE_ON")
    )
    monkeypatch.setattr(
        PSUBase,
        "get_device_state",
        lambda self: (self.NO_ERR, "0x0001", ["DEVST_VCPU_FAIL"]),
    )
    monkeypatch.setattr(PSUBase, "get_device_enable", lambda self: (self.NO_ERR, True))
    monkeypatch.setattr(PSUBase, "get_psu_enable", lambda self: (self.NO_ERR, True, False))
    monkeypatch.setattr(
        PSUBase,
        "get_housekeeping",
        lambda self: (self.NO_ERR, 24.0, 5.0, 3.3, 41.5),
    )
    monkeypatch.setattr(
        PSUBase,
        "get_sensor_data",
        lambda self: (self.NO_ERR, [12.0, 13.0, 14.0]),
    )
    monkeypatch.setattr(
        PSUBase,
        "get_fan_data",
        lambda self: (
            self.NO_ERR,
            [True, True, False],
            [False, False, True],
            [1000, 1100, 1200],
            [990, 1090, 1190],
            [100, 110, 120],
        ),
    )
    monkeypatch.setattr(
        PSUBase, "get_led_data", lambda self: (self.NO_ERR, True, False, True)
    )
    monkeypatch.setattr(
        PSUBase, "get_cpu_data", lambda self: (self.NO_ERR, 0.25, 250_000_000.0)
    )
    monkeypatch.setattr(PSUBase, "get_uptime", lambda self: (self.NO_ERR, 12, 34, 56))
    monkeypatch.setattr(
        PSUBase, "get_total_time", lambda self: (self.NO_ERR, 120, 560)
    )
    monkeypatch.setattr(
        PSUBase,
        "get_psu_data",
        lambda self, channel: (
            self.NO_ERR,
            10.0 + channel,
            0.1 + channel,
            1.0 + channel,
        ),
    )
    monkeypatch.setattr(
        PSUBase,
        "get_psu_set_output_voltage",
        lambda self, channel: (
            self.NO_ERR,
            20.0 + channel,
            30.0 + channel,
        ),
    )
    monkeypatch.setattr(
        PSUBase,
        "get_psu_set_output_current",
        lambda self, channel: (
            self.NO_ERR,
            0.2 + channel,
            0.3 + channel,
        ),
    )
    monkeypatch.setattr(
        PSUBase,
        "get_adc_housekeeping",
        lambda self, channel: (
            self.NO_ERR,
            1.1 + channel,
            1.2 + channel,
            1.3 + channel,
            1.4 + channel,
            1.5 + channel,
            40.0 + channel,
        ),
    )
    monkeypatch.setattr(
        PSUBase,
        "get_psu_housekeeping",
        lambda self, channel: (
            self.NO_ERR,
            24.0 + channel,
            12.0 + channel,
            -12.0 - channel,
            2.5 + channel,
        ),
    )

    snapshot = psu.collect_housekeeping()

    assert snapshot["device_enabled"] is True
    assert snapshot["output_enabled"] == (True, False)
    assert snapshot["main_state"] == {"hex": "0x0000", "name": "STATE_ON"}
    assert snapshot["device_state"] == {
        "hex": "0x0001",
        "flags": ["DEVST_VCPU_FAIL"],
    }
    assert snapshot["housekeeping"] == {
        "volt_rect_v": 24.0,
        "volt_5v0_v": 5.0,
        "volt_3v3_v": 3.3,
        "temp_cpu_c": 41.5,
    }
    assert snapshot["sensors_c"] == [12.0, 13.0, 14.0]
    assert snapshot["led"] == {"red": True, "green": False, "blue": True}
    assert snapshot["cpu"] == {"load": 0.25, "frequency_hz": 250_000_000.0}
    assert snapshot["uptime"] == {
        "seconds": 12,
        "milliseconds": 34,
        "operation_seconds": 56,
        "total_uptime_seconds": 120,
        "total_operation_seconds": 560,
    }
    assert snapshot["channels"][0] == {
        "channel": 0,
        "label": "positive",
        "enabled": True,
        "voltage": {
            "measured_v": 10.0,
            "set_v": 20.0,
            "limit_v": 30.0,
        },
        "current": {
            "measured_a": 0.1,
            "set_a": 0.2,
            "limit_a": 0.3,
        },
        "dropout_v": 1.0,
        "adc": {
            "volt_avdd_v": 1.1,
            "volt_dvdd_v": 1.2,
            "volt_aldo_v": 1.3,
            "volt_dldo_v": 1.4,
            "volt_ref_v": 1.5,
            "temp_adc_c": 40.0,
        },
        "rails": {
            "volt_24vp_v": 24.0,
            "volt_12vp_v": 12.0,
            "volt_12vn_v": -12.0,
            "volt_ref_v": 2.5,
        },
    }
    assert snapshot["channels"][1]["label"] == "negative"
    assert snapshot["channels"][1]["enabled"] is False


def test_collect_housekeeping_tolerates_short_driver_sequences(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    backend = object.__getattribute__(psu, "_backend")
    backend.connected = True
    monkeypatch.setattr(
        PSUBase, "get_main_state", lambda self: (self.NO_ERR, "0x0000", "STATE_ON")
    )
    monkeypatch.setattr(
        PSUBase,
        "get_device_state",
        lambda self: (self.NO_ERR, "0x0000", ["DEVST_OK"]),
    )
    monkeypatch.setattr(PSUBase, "get_device_enable", lambda self: (self.NO_ERR, True))
    monkeypatch.setattr(backend, "_get_output_enabled_unlocked", lambda: (True,))
    monkeypatch.setattr(
        PSUBase,
        "get_housekeeping",
        lambda self: (self.NO_ERR, 24.0, 5.0, 3.3, 41.5),
    )
    monkeypatch.setattr(
        PSUBase,
        "get_sensor_data",
        lambda self: (self.NO_ERR, [12.0]),
    )
    monkeypatch.setattr(
        PSUBase,
        "get_fan_data",
        lambda self: (
            self.NO_ERR,
            [True],
            [False],
            [1000],
            [990],
            [100],
        ),
    )
    monkeypatch.setattr(
        PSUBase, "get_led_data", lambda self: (self.NO_ERR, True, False, True)
    )
    monkeypatch.setattr(
        PSUBase, "get_cpu_data", lambda self: (self.NO_ERR, 0.25, 250_000_000.0)
    )
    monkeypatch.setattr(PSUBase, "get_uptime", lambda self: (self.NO_ERR, 12, 34, 56))
    monkeypatch.setattr(
        PSUBase, "get_total_time", lambda self: (self.NO_ERR, 120, 560)
    )
    monkeypatch.setattr(
        PSUBase,
        "get_psu_data",
        lambda self, channel: (
            self.NO_ERR,
            10.0 + channel,
            0.1 + channel,
            1.0 + channel,
        ),
    )
    monkeypatch.setattr(
        PSUBase,
        "get_psu_set_output_voltage",
        lambda self, channel: (
            self.NO_ERR,
            20.0 + channel,
            30.0 + channel,
        ),
    )
    monkeypatch.setattr(
        PSUBase,
        "get_psu_set_output_current",
        lambda self, channel: (
            self.NO_ERR,
            0.2 + channel,
            0.3 + channel,
        ),
    )
    monkeypatch.setattr(
        PSUBase,
        "get_adc_housekeeping",
        lambda self, channel: (
            self.NO_ERR,
            1.1 + channel,
            1.2 + channel,
            1.3 + channel,
            1.4 + channel,
            1.5 + channel,
            40.0 + channel,
        ),
    )
    monkeypatch.setattr(
        PSUBase,
        "get_psu_housekeeping",
        lambda self, channel: (
            self.NO_ERR,
            24.0 + channel,
            12.0 + channel,
            -12.0 - channel,
            2.5 + channel,
        ),
    )

    snapshot = backend.collect_housekeeping()

    assert snapshot["output_enabled"] == (True, False)
    assert snapshot["sensors_c"][0] == 12.0
    assert snapshot["sensors_c"][1] != snapshot["sensors_c"][1]
    assert snapshot["sensors_c"][2] != snapshot["sensors_c"][2]
    assert snapshot["fans"][0] == {
        "fan": 0,
        "enabled": True,
        "failed": False,
        "set_rpm": 1000,
        "measured_rpm": 990,
        "pwm": 100,
    }
    assert snapshot["fans"][1] == {
        "fan": 1,
        "enabled": False,
        "failed": False,
        "set_rpm": 0,
        "measured_rpm": 0,
        "pwm": 0,
    }
    assert snapshot["fans"][2] == {
        "fan": 2,
        "enabled": False,
        "failed": False,
        "set_rpm": 0,
        "measured_rpm": 0,
        "pwm": 0,
    }
    assert snapshot["channels"][1]["enabled"] is False


def test_psu_critical_operations_use_timeout_safe_wrapper(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    backend = object.__getattribute__(psu, "_backend")
    backend.connected = True
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        if method.__name__ == "set_psu_output_voltage":
            return backend.NO_ERR
        if method.__name__ == "set_psu_output_current":
            return backend.NO_ERR
        if method.__name__ == "set_psu_enable":
            return backend.NO_ERR
        if method.__name__ == "set_device_enable":
            return backend.NO_ERR
        if method.__name__ == "close_port":
            return backend.NO_ERR
        raise AssertionError(f"Unexpected method: {method.__name__}")

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    backend.set_channel_voltage(0, 25.0, timeout_s=3.0)
    backend.set_channel_current(1, 0.5, timeout_s=4.0)
    backend.set_output_enabled(True, False, timeout_s=6.0)
    backend.set_device_enabled(True, timeout_s=7.0)
    assert backend.disconnect(timeout_s=8.0) is True
    assert backend.connected is False

    assert calls == [
        ("set_psu_output_voltage", 3.0, "set_psu_output_voltage[0]", (backend, 0, 25.0)),
        ("set_psu_output_current", 4.0, "set_psu_output_current[1]", (backend, 1, 0.5)),
        ("set_psu_enable", 6.0, "set_psu_enable", (backend, True, False)),
        ("set_device_enable", 7.0, "set_device_enable", (backend, True)),
        ("close_port", 8.0, "close_port", ()),
    ]


def test_psu_product_info_and_housekeeping_use_batch_timeout_wrapper(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    backend = object.__getattribute__(psu, "_backend")
    backend.connected = True
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        if method.__name__ == "_get_product_info_unlocked":
            return {"product_no": 350}
        if method.__name__ == "_collect_housekeeping_unlocked":
            return {"device_enabled": True}
        raise AssertionError(f"Unexpected method: {method.__name__}")

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    assert backend.get_product_info(timeout_s=3.0) == {"product_no": 350}
    assert backend.collect_housekeeping(timeout_s=3.0) == {"device_enabled": True}
    assert calls == [
        ("_get_product_info_unlocked", 11.0, "get_product_info", ()),
        ("_collect_housekeeping_unlocked", 20.0, "collect_housekeeping", ()),
    ]


def test_psu_interlock_access_uses_timeout_safe_wrapper(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    backend = object.__getattribute__(psu, "_backend")
    backend.connected = True
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        if method.__name__ == "get_interlock_enable":
            return backend.NO_ERR, True, False
        if method.__name__ == "set_interlock_enable":
            return backend.NO_ERR
        raise AssertionError(f"Unexpected method: {method.__name__}")

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    assert backend.get_interlock_enabled(timeout_s=2.5) == (True, False)
    backend.set_interlock_enabled(True, False, timeout_s=2.5)

    assert calls == [
        ("get_interlock_enable", 2.5, "get_interlock_enable", (backend,)),
        ("set_interlock_enable", 2.5, "set_interlock_enable", (backend, True, False)),
    ]


def test_shutdown_forwards_explicit_timeout_to_each_best_effort_step(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    backend = object.__getattribute__(psu, "_backend")
    backend.connected = True
    calls = []

    monkeypatch.setattr(
        backend,
        "set_channel_current",
        lambda channel, current, timeout_s=None: calls.append(
            ("current", channel, current, timeout_s)
        ),
    )
    monkeypatch.setattr(
        backend,
        "set_channel_voltage",
        lambda channel, voltage, timeout_s=None: calls.append(
            ("voltage", channel, voltage, timeout_s)
        ),
    )
    monkeypatch.setattr(
        backend,
        "set_output_enabled",
        lambda psu0, psu1, timeout_s=None: calls.append(
            ("outputs", psu0, psu1, timeout_s)
        ),
    )
    monkeypatch.setattr(
        backend,
        "set_device_enabled",
        lambda enabled, timeout_s=None: calls.append(("device", enabled, timeout_s)),
    )
    monkeypatch.setattr(
        backend,
        "disconnect",
        lambda timeout_s=None: calls.append(("disconnect", timeout_s)) or True,
    )

    assert backend.shutdown(timeout_s=7.0) is True

    assert calls == [
        ("current", 0, 0.0, 7.0),
        ("current", 1, 0.0, 7.0),
        ("voltage", 0, 0.0, 7.0),
        ("voltage", 1, 0.0, 7.0),
        ("outputs", False, False, 7.0),
        ("device", False, 7.0),
        ("disconnect", 7.0),
    ]
