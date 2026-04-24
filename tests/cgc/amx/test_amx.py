import ctypes
from pathlib import Path
import logging
import tempfile
from unittest.mock import Mock

import pytest

from cgc.amx import AMX, AMXBase, AMXDllLoadError, AMXPlatformError


ERROR_CODES_PATH = Path(__file__).resolve().parents[3] / "src" / "cgc" / "error_codes.json"


@pytest.fixture(autouse=True)
def clear_amx_connection_registry():
    with AMX._active_connections_lock:
        AMX._active_connections.clear()
    yield
    with AMX._active_connections_lock:
        AMX._active_connections.clear()


def make_amx(monkeypatch, *, device_id="amx_test", com=8, port=0):
    monkeypatch.setattr("cgc.amx.amx_base.sys.platform", "win32")
    dll = Mock()
    monkeypatch.setattr("cgc.amx.amx_base.ctypes.WinDLL", lambda _path: dll, raising=False)
    log_dir = Path(tempfile.gettempdir()) / "esibd_core_test_logs"
    return AMX(device_id, com=com, port=port, log_dir=log_dir), dll


def test_amx_base_rejects_non_windows(monkeypatch):
    monkeypatch.setattr("cgc.amx.amx_base.sys.platform", "linux")

    with pytest.raises(AMXPlatformError):
        AMXBase(com=8)


def test_amx_base_raises_clear_error_when_dll_fails(monkeypatch):
    monkeypatch.setattr("cgc.amx.amx_base.sys.platform", "win32")

    def raise_os_error(_path):
        raise OSError("missing dll")

    monkeypatch.setattr("cgc.amx.amx_base.ctypes.WinDLL", raise_os_error, raising=False)

    with pytest.raises(AMXDllLoadError):
        AMXBase(com=8, error_codes_path=ERROR_CODES_PATH)


def test_amx_base_dll_error_mentions_64bit_python_for_x64_bundle(monkeypatch):
    monkeypatch.setattr("cgc.amx.amx_base.sys.platform", "win32")
    monkeypatch.setattr("cgc.amx.amx_base._python_is_64bit", lambda: False)

    def raise_os_error(_path):
        raise OSError("bad image format")

    monkeypatch.setattr("cgc.amx.amx_base.ctypes.WinDLL", raise_os_error, raising=False)

    with pytest.raises(AMXDllLoadError, match="64-bit Python"):
        AMXBase(com=8, error_codes_path=ERROR_CODES_PATH)


def test_amx_base_get_device_enable_uses_windows_bool(monkeypatch):
    monkeypatch.setattr("cgc.amx.amx_base.sys.platform", "win32")
    dll = Mock()

    def fake_get_device_enable(port, enable_ptr):
        assert port == 0
        assert ctypes.sizeof(enable_ptr._obj) == ctypes.sizeof(AMXBase.WIN_BOOL)
        enable_ptr._obj.value = 1
        return AMXBase.NO_ERR

    dll.COM_HVAMX4ED_GetDeviceEnable.side_effect = fake_get_device_enable
    monkeypatch.setattr("cgc.amx.amx_base.ctypes.WinDLL", lambda _path: dll, raising=False)

    base = AMXBase(com=8, error_codes_path=ERROR_CODES_PATH)

    assert base.get_device_enable() == (AMXBase.NO_ERR, True)


def test_amx_base_decodes_config_name_with_replacement(monkeypatch):
    monkeypatch.setattr("cgc.amx.amx_base.sys.platform", "win32")
    dll = Mock()

    def fake_get_config_name(_port, _config_number, name):
        name.value = b"amx-\xffcfg"
        return AMXBase.NO_ERR

    dll.COM_HVAMX4ED_GetConfigName.side_effect = fake_get_config_name
    monkeypatch.setattr("cgc.amx.amx_base.ctypes.WinDLL", lambda _path: dll, raising=False)

    base = AMXBase(com=8, error_codes_path=ERROR_CODES_PATH)

    assert base.get_config_name(0) == (AMXBase.NO_ERR, "amx-\ufffdcfg")


def test_amx_rejects_unknown_init_kwargs():
    with pytest.raises(TypeError, match="Unexpected AMX init kwargs: unexpected"):
        AMX("amx_test", com=8, unexpected=True)


@pytest.mark.parametrize("baudrate", [0, -1])
def test_amx_rejects_non_positive_baudrate(baudrate):
    with pytest.raises(ValueError, match="baudrate must be > 0"):
        AMX("amx_test", com=8, baudrate=baudrate)


def test_amx_external_logger_prefixes_device_id(monkeypatch, caplog):
    monkeypatch.setattr("cgc.amx.amx_base.sys.platform", "win32")
    monkeypatch.setattr(
        "cgc.amx.amx_base.ctypes.WinDLL",
        lambda _path: Mock(),
        raising=False,
    )
    logger = logging.getLogger("test_amx_external_logger")

    amx = AMX("amx_test", com=8, logger=logger)

    with caplog.at_level(logging.INFO, logger=logger.name):
        amx.logger.info("hello")

    assert "amx_test - hello" in caplog.messages


def test_amx_uses_process_backend_when_explicitly_requested(monkeypatch):
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

    amx = AMX("amx_process", com=8, port=1, process_backend=True)

    assert amx._backend_mode == "process"
    assert created["controller_path"] == "cgc.amx.amx:_AMXController"
    assert created["label"] == "AMX amx_process"
    assert created["controller_kwargs"]["device_id"] == "amx_process"
    assert created["controller_kwargs"]["com"] == 8
    assert created["controller_kwargs"]["port"] == 1


def test_amx_defaults_to_inline_backend(monkeypatch):
    created = {}

    class FakeProxy:
        def __init__(self, *args, **kwargs):  # pragma: no cover - should stay unused
            created["proxy_called"] = True

    class FakeController:
        def __init__(self, **kwargs):
            created["inline_kwargs"] = kwargs

    monkeypatch.setattr("cgc._driver_common.RUNTIME_IS_WINDOWS", True)
    monkeypatch.setattr("cgc._driver_common.ControllerProcessProxy", FakeProxy)
    monkeypatch.setattr(AMX, "_PROCESS_CONTROLLER_CLASS", FakeController)

    amx = AMX("amx_inline", com=8, port=1)

    assert amx._backend_mode == "inline"
    assert created["inline_kwargs"]["device_id"] == "amx_inline"
    assert created["inline_kwargs"]["com"] == 8
    assert created["inline_kwargs"]["port"] == 1
    assert "proxy_called" not in created
    assert amx._process_backend_disabled_reason == ""


def test_connect_rolls_back_when_baud_rate_fails(monkeypatch):
    amx, dll = make_amx(monkeypatch)
    dll.COM_HVAMX4ED_Open.return_value = AMXBase.NO_ERR
    dll.COM_HVAMX4ED_SetBaudRate.return_value = AMXBase.ERR_RATE
    dll.COM_HVAMX4ED_Close.return_value = AMXBase.NO_ERR

    with pytest.raises(RuntimeError, match="set_baud_rate failed"):
        amx.connect()

    assert amx.connected is False
    dll.COM_HVAMX4ED_Close.assert_called_once()


def test_connect_warns_when_reusing_the_same_dll_port(monkeypatch, caplog):
    amx_a, dll_a = make_amx(monkeypatch, device_id="amx_a", com=8, port=0)
    amx_b, dll_b = make_amx(monkeypatch, device_id="amx_b", com=9, port=0)
    dll_a.COM_HVAMX4ED_Open.return_value = AMXBase.NO_ERR
    dll_b.COM_HVAMX4ED_Open.return_value = AMXBase.NO_ERR
    dll_a.COM_HVAMX4ED_SetBaudRate.return_value = AMXBase.NO_ERR
    dll_b.COM_HVAMX4ED_SetBaudRate.return_value = AMXBase.NO_ERR

    amx_a.connect()
    with caplog.at_level(logging.WARNING):
        amx_b.connect()

    assert "same DLL port" in caplog.text
    assert "port 0" in caplog.text


def test_connect_warns_when_multiple_dll_ports_are_active(monkeypatch, caplog):
    amx_a, dll_a = make_amx(monkeypatch, device_id="amx_a", com=8, port=0)
    amx_b, dll_b = make_amx(monkeypatch, device_id="amx_b", com=9, port=1)
    dll_a.COM_HVAMX4ED_Open.return_value = AMXBase.NO_ERR
    dll_b.COM_HVAMX4ED_Open.return_value = AMXBase.NO_ERR
    dll_a.COM_HVAMX4ED_SetBaudRate.return_value = AMXBase.NO_ERR
    dll_b.COM_HVAMX4ED_SetBaudRate.return_value = AMXBase.NO_ERR
    monkeypatch.setattr(
        AMXBase, "get_product_id", lambda self: (self.NO_ERR, "AMX-CTRL-4ED")
    )

    amx_a.connect()
    with caplog.at_level(logging.WARNING):
        amx_b.connect()

    assert "Multiple AMX instances in this process currently claim DLL ports" in caplog.text
    assert "[0, 1]" in caplog.text


def test_connect_warns_when_product_id_looks_like_another_instrument(monkeypatch, caplog):
    amx, dll = make_amx(monkeypatch)
    dll.COM_HVAMX4ED_Open.return_value = AMXBase.NO_ERR
    dll.COM_HVAMX4ED_SetBaudRate.return_value = AMXBase.NO_ERR
    monkeypatch.setattr(
        AMXBase,
        "get_product_id",
        lambda self: (self.NO_ERR, "HV-PSU-CTRL-2D, Rev.1-01"),
    )

    with caplog.at_level(logging.WARNING):
        amx.connect()

    assert "does not look like an AMX controller" in caplog.text
    assert "HV-PSU-CTRL-2D" in caplog.text


def test_connect_fails_and_closes_when_identity_probe_gets_wrong_command(monkeypatch):
    amx, dll = make_amx(monkeypatch)
    dll.COM_HVAMX4ED_Open.return_value = AMXBase.NO_ERR
    dll.COM_HVAMX4ED_SetBaudRate.return_value = AMXBase.NO_ERR
    dll.COM_HVAMX4ED_Close.return_value = AMXBase.NO_ERR
    monkeypatch.setattr(
        AMXBase,
        "get_product_id",
        lambda self: (self.ERR_COMMAND_WRONG, ""),
    )

    with pytest.raises(RuntimeError, match="did not respond to the AMX identity probe"):
        amx.connect()

    assert amx.connected is False
    dll.COM_HVAMX4ED_Close.assert_called_once()


def test_connect_identity_probe_uses_timeout_safe_wrapper(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    backend = object.__getattribute__(amx, "_backend")
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        if method.__name__ == "open_port":
            return backend.NO_ERR
        if method.__name__ == "set_baud_rate":
            return backend.NO_ERR, backend.baudrate
        if method.__name__ == "get_product_id":
            return backend.NO_ERR, "AMX-CTRL-4ED"
        raise AssertionError(f"Unexpected method: {method.__name__}")

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    assert backend.connect(timeout_s=2.5) is True

    assert calls == [
        ("open_port", 2.5, "open_port", (backend.com, backend.port_num)),
        ("set_baud_rate", 2.5, "set_baud_rate", (backend.baudrate,)),
        ("get_product_id", 2.5, "get_product_id", (backend,)),
    ]


def test_connect_fails_when_identity_probe_times_out(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    backend = object.__getattribute__(amx, "_backend")

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        if method.__name__ == "open_port":
            return backend.NO_ERR
        if method.__name__ == "set_baud_rate":
            return backend.NO_ERR, backend.baudrate
        if method.__name__ == "get_product_id":
            backend._poison_transport(step_name)
            raise RuntimeError("timed out during get_product_id")
        raise AssertionError(f"Unexpected method: {method.__name__}")

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    with pytest.raises(RuntimeError, match="timed out during get_product_id"):
        backend.connect(timeout_s=2.0)

    assert backend.connected is False
    assert backend._transport_poisoned is True


def test_set_frequency_hz_translates_to_oscillator_period(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    monkeypatch.setattr(AMXBase, "set_oscillator_period", Mock(return_value=AMXBase.NO_ERR))

    amx.set_frequency_hz(2_000.0)

    expected_period = round((AMXBase.CLOCK / 2_000.0) - AMXBase.OSC_OFFSET)
    called_self, called_period = AMXBase.set_oscillator_period.call_args.args
    assert called_period == expected_period
    assert called_self.device_id == amx.device_id
    assert called_self.com == amx.com


def test_get_frequency_khz_returns_scaled_frequency(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    monkeypatch.setattr(
        AMXBase,
        "get_oscillator_period",
        Mock(
            return_value=(
                AMXBase.NO_ERR,
                round((AMXBase.CLOCK / 2_000.0) - AMXBase.OSC_OFFSET),
            )
        ),
    )

    assert amx.get_frequency_khz() == pytest.approx(2.0)


def test_set_pulser_duty_cycle_uses_current_oscillator_period(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    monkeypatch.setattr(
        AMXBase, "get_oscillator_period", lambda self: (self.NO_ERR, 99998)
    )
    monkeypatch.setattr(AMXBase, "set_pulser_width", Mock(return_value=AMXBase.NO_ERR))

    amx.set_pulser_duty_cycle(0, 0.5)

    called_self, called_pulser, called_width = AMXBase.set_pulser_width.call_args.args
    assert (called_pulser, called_width) == (0, 49998)
    assert called_self.device_id == amx.device_id
    assert called_self.com == amx.com


def test_set_pulser_duty_cycle_runs_in_single_locked_section(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    calls = []
    monkeypatch.setattr(
        AMXBase, "get_oscillator_period", lambda self: (self.NO_ERR, 99998)
    )
    monkeypatch.setattr(AMXBase, "set_pulser_width", Mock(return_value=AMXBase.NO_ERR))

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        with amx.thread_lock:
            return method(*args, **kwargs)

    monkeypatch.setattr(amx, "_call_locked_with_timeout", fake_locked_timeout)

    amx.set_pulser_duty_cycle(1, 0.25, timeout_s=2.5)

    assert calls == [
        ("_set_pulser_duty_cycle_unlocked", 2.5, "set_pulser_duty_cycle[1]", (1, 0.25))
    ]
    called_self, called_pulser, called_width = AMXBase.set_pulser_width.call_args.args
    assert called_self.device_id == amx.device_id
    assert called_self.com == amx.com
    assert called_pulser == 1
    assert called_width == 24998


def test_load_config_calls_vendor_wrapper(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    monkeypatch.setattr(AMXBase, "load_current_config", lambda self, index: self.NO_ERR)
    monkeypatch.setattr(AMXBase, "get_config_name", lambda self, index: (self.NO_ERR, "Standby"))

    amx.load_config(40)

    assert amx.get_status()["memory_config"] == 40
    assert amx.get_status()["memory_config_name"] == "Standby"
    assert amx.get_status()["memory_config_source"] == "explicit"


def test_initialize_can_connect_without_loading_any_config(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    backend = object.__getattribute__(amx, "_backend")
    calls = []

    def fake_connect(timeout_s=5.0):
        backend.connected = True
        calls.append(("connect", timeout_s))
        return True

    monkeypatch.setattr(backend, "connect", fake_connect)
    monkeypatch.setattr(backend, "_find_auto_standby_config", lambda timeout_s=None: None)
    monkeypatch.setattr(
        backend,
        "load_config",
        Mock(side_effect=AssertionError("initialize() should not load a config by default")),
    )

    assert backend.initialize(timeout_s=2.0) == {}
    assert calls == [("connect", 2.0)]


def test_initialize_auto_loads_valid_standby_config_into_memory(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    backend = object.__getattribute__(amx, "_backend")
    calls = []

    def fake_connect(timeout_s=5.0):
        backend.connected = True
        calls.append(("connect", timeout_s))
        return True

    monkeypatch.setattr(backend, "connect", fake_connect)
    monkeypatch.setattr(
        backend,
        "_find_auto_standby_config",
        lambda timeout_s=None: {"index": 0, "name": "Standby", "valid": True},
    )
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

    result = backend.initialize(timeout_s=2.5)

    assert result == {
        "standby_config": 0,
        "device_enabled": False,
        "memory_config": 0,
        "memory_config_name": "Standby",
        "memory_config_source": "auto-standby",
    }
    assert calls == [
        ("connect", 2.5),
        ("load_config", 0, 2.5),
        ("get_device_enabled", 2.5),
    ]


def test_initialize_runs_standby_then_operating_sequence_and_returns_state(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    backend = object.__getattribute__(amx, "_backend")
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

    result = backend.initialize(
        timeout_s=2.5,
        standby_config=3,
        operating_config=40,
    )

    assert result == {
        "standby_config": 3,
        "device_enabled": False,
        "operating_config": 40,
        "memory_config": 40,
        "memory_config_name": None,
        "memory_config_source": "operating",
    }
    assert calls == [
        ("connect", 2.5),
        ("load_config", 3, 2.5),
        ("get_device_enabled", 2.5),
        ("load_config", 40, 2.5),
    ]


def test_initialize_can_load_only_an_operating_config(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    backend = object.__getattribute__(amx, "_backend")
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
        Mock(side_effect=AssertionError("standby check should not run")),
    )

    result = backend.initialize(timeout_s=2.0, operating_config=40)

    assert result == {
        "operating_config": 40,
        "memory_config": 40,
        "memory_config_name": None,
        "memory_config_source": "operating",
    }
    assert calls == [
        ("connect", 2.0),
        ("load_config", 40, 2.0),
    ]


def test_initialize_rejects_standby_config_when_device_stays_enabled(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    backend = object.__getattribute__(amx, "_backend")

    def fake_connect(timeout_s=5.0):
        backend.connected = True
        return True

    monkeypatch.setattr(backend, "connect", fake_connect)
    monkeypatch.setattr(backend, "load_config", lambda config_number, timeout_s=None: None)
    monkeypatch.setattr(backend, "get_device_enabled", lambda timeout_s=None: True)
    shutdown = Mock(return_value=True)
    monkeypatch.setattr(backend, "shutdown", shutdown)

    with pytest.raises(RuntimeError, match="left the device enabled"):
        backend.initialize(timeout_s=2.0, standby_config=1)

    shutdown.assert_called_once_with(timeout_s=2.0)


def test_initialize_uses_disconnect_cleanup_when_transport_is_poisoned(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    backend = object.__getattribute__(amx, "_backend")

    def fake_connect(timeout_s=5.0):
        backend.connected = True
        return True

    monkeypatch.setattr(backend, "connect", fake_connect)
    monkeypatch.setattr(backend, "load_config", lambda config_number, timeout_s=None: None)

    def fake_get_device_enabled(timeout_s=None):
        backend._transport_poisoned = True
        raise RuntimeError("poisoned")

    monkeypatch.setattr(backend, "get_device_enabled", fake_get_device_enabled)
    disconnect = Mock(return_value=False)
    shutdown = Mock(side_effect=AssertionError("shutdown should not be called"))
    monkeypatch.setattr(backend, "disconnect", disconnect)
    monkeypatch.setattr(backend, "shutdown", shutdown)

    with pytest.raises(RuntimeError, match="poisoned"):
        backend.initialize(timeout_s=2.0, standby_config=1)

    disconnect.assert_called_once_with(timeout_s=2.0)


def test_shutdown_disables_device_by_default_and_propagates_errors(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    monkeypatch.setattr(
        amx, "set_device_enabled", Mock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(amx, "disconnect", Mock(return_value=True))

    with pytest.raises(RuntimeError, match="set_device_enabled\\(False\\): boom"):
        amx.shutdown()

    amx.set_device_enabled.assert_called_once_with(False)
    amx.disconnect.assert_called_once()


def test_shutdown_rejects_standby_config_when_disable_device_is_enabled(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    monkeypatch.setattr(amx, "load_config", Mock())
    monkeypatch.setattr(amx, "disconnect", Mock(return_value=True))

    with pytest.raises(ValueError, match="standby_config"):
        amx.shutdown(standby_config=5)

    amx.load_config.assert_not_called()
    amx.disconnect.assert_not_called()


def test_shutdown_can_load_explicit_standby_config_without_disable_step(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    monkeypatch.setattr(amx, "load_config", Mock())
    monkeypatch.setattr(amx, "set_device_enabled", Mock())
    monkeypatch.setattr(amx, "disconnect", Mock(return_value=True))

    assert amx.shutdown(standby_config=5, disable_device=False) is True

    amx.load_config.assert_called_once_with(5)
    amx.set_device_enabled.assert_not_called()
    amx.disconnect.assert_called_once()


def test_shutdown_falls_back_to_disable_device_when_standby_load_fails(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    monkeypatch.setattr(amx, "load_config", Mock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(amx, "set_device_enabled", Mock())
    monkeypatch.setattr(amx, "disconnect", Mock(return_value=True))

    with pytest.raises(RuntimeError, match="load_config\\(5\\): boom"):
        amx.shutdown(standby_config=5, disable_device=False)

    amx.load_config.assert_called_once_with(5)
    amx.set_device_enabled.assert_called_once_with(False)
    amx.disconnect.assert_called_once()


def test_amx_critical_operations_use_timeout_safe_wrapper(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    backend = object.__getattribute__(amx, "_backend")
    backend.connected = True
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        if method.__name__ == "load_current_config":
            return backend.NO_ERR
        if method.__name__ == "set_device_enable":
            return backend.NO_ERR
        if method.__name__ == "get_device_enable":
            return backend.NO_ERR, True
        if method.__name__ == "set_oscillator_period":
            return backend.NO_ERR
        if method.__name__ == "get_oscillator_period":
            return backend.NO_ERR, 99998
        if method.__name__ == "set_pulser_delay":
            return backend.NO_ERR
        if method.__name__ == "get_pulser_delay":
            return backend.NO_ERR, 100
        if method.__name__ == "set_pulser_width":
            return backend.NO_ERR
        if method.__name__ == "get_pulser_width":
            return backend.NO_ERR, 200
        if method.__name__ == "set_switch_trigger_delay":
            return backend.NO_ERR
        if method.__name__ == "get_switch_trigger_delay":
            return backend.NO_ERR, 3, 4
        if method.__name__ == "set_switch_enable_delay":
            return backend.NO_ERR
        if method.__name__ == "get_switch_enable_delay":
            return backend.NO_ERR, 5
        if method.__name__ == "close_port":
            return backend.NO_ERR
        raise AssertionError(f"Unexpected method: {method.__name__}")

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    backend.load_config(40, timeout_s=2.0)
    backend.set_device_enabled(True, timeout_s=2.1)
    assert backend.get_device_enabled(timeout_s=2.2) is True
    backend.set_frequency_hz(2_000.0, timeout_s=2.3)
    assert backend.get_frequency_hz(timeout_s=2.4) == 1000.0
    backend.set_pulser_delay_ticks(0, 7, timeout_s=2.5)
    assert backend.get_pulser_delay_ticks(0, timeout_s=2.6) == 100
    backend.set_pulser_width_ticks(0, 8, timeout_s=2.7)
    assert backend.get_pulser_width_ticks(0, timeout_s=2.8) == 200
    backend.set_switch_trigger_delay(0, 1, 2, timeout_s=2.9)
    assert backend.get_switch_trigger_delay(0, timeout_s=3.0) == (3, 4)
    backend.set_switch_enable_delay(0, 6, timeout_s=3.1)
    assert backend.get_switch_enable_delay(0, timeout_s=3.2) == 5
    assert backend.disconnect(timeout_s=3.3) is True
    assert backend.connected is False

    assert calls == [
        ("load_current_config", 2.0, "load_current_config", (backend, 40)),
        ("get_config_name", 2.0, "get_config_name[40]", (backend, 40)),
        ("set_device_enable", 2.1, "set_device_enable", (backend, True)),
        ("get_device_enable", 2.2, "get_device_enable", (backend,)),
        ("set_oscillator_period", 2.3, "set_oscillator_period", (backend, 49998)),
        ("get_oscillator_period", 2.4, "get_oscillator_period", (backend,)),
        ("set_pulser_delay", 2.5, "set_pulser_delay[0]", (backend, 0, 7)),
        ("get_pulser_delay", 2.6, "get_pulser_delay[0]", (backend, 0)),
        ("set_pulser_width", 2.7, "set_pulser_width[0]", (backend, 0, 8)),
        ("get_pulser_width", 2.8, "get_pulser_width[0]", (backend, 0)),
        ("set_switch_trigger_delay", 2.9, "set_switch_trigger_delay[0]", (backend, 0, 1, 2)),
        ("get_switch_trigger_delay", 3.0, "get_switch_trigger_delay[0]", (backend, 0)),
        ("set_switch_enable_delay", 3.1, "set_switch_enable_delay[0]", (backend, 0, 6)),
        ("get_switch_enable_delay", 3.2, "get_switch_enable_delay[0]", (backend, 0)),
        ("close_port", 3.3, "close_port", ()),
    ]


def test_amx_batch_operations_use_timeout_safe_wrapper(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    backend = object.__getattribute__(amx, "_backend")
    backend.connected = True
    calls = []

    def fake_locked_timeout(method, timeout_s, step_name, *args, **kwargs):
        calls.append((method.__name__, timeout_s, step_name, args))
        if method.__name__ == "_list_configs_unlocked":
            return [{"index": 0, "name": "cfg0", "active": True, "valid": True}]
        if method.__name__ == "_get_product_info_unlocked":
            return {"product_no": 404}
        if method.__name__ == "_collect_housekeeping_unlocked":
            return {"device_enabled": True}
        raise AssertionError(f"Unexpected method: {method.__name__}")

    monkeypatch.setattr(backend, "_call_locked_with_timeout", fake_locked_timeout)

    assert backend.list_configs(timeout_s=3.0) == [
        {"index": 0, "name": "cfg0", "active": True, "valid": True}
    ]
    assert backend.get_product_info(timeout_s=3.0) == {"product_no": 404}
    assert backend.collect_housekeeping(timeout_s=3.0) == {"device_enabled": True}

    assert calls == [
        ("_list_configs_unlocked", 11.0, "list_configs", (False,)),
        ("_get_product_info_unlocked", 11.0, "get_product_info", ()),
        ("_collect_housekeeping_unlocked", 20.0, "collect_housekeeping", ()),
    ]


def test_amx_shutdown_forwards_explicit_timeout_to_each_best_effort_step(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    backend = object.__getattribute__(amx, "_backend")
    backend.connected = True
    calls = []

    monkeypatch.setattr(
        backend,
        "load_config",
        lambda config_number, timeout_s=None: calls.append(
            ("load_config", config_number, timeout_s)
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

    assert backend.shutdown(standby_config=5, disable_device=False, timeout_s=7.0) is True
    assert calls == [
        ("load_config", 5, 7.0),
        ("disconnect", 7.0),
    ]

    calls.clear()
    assert backend.shutdown(timeout_s=8.0) is True
    assert calls == [
        ("device", False, 8.0),
        ("disconnect", 8.0),
    ]


def test_failed_disconnect_keeps_dll_port_claim_warning(monkeypatch, caplog):
    amx_a, dll_a = make_amx(monkeypatch, device_id="amx_a", com=8, port=0)
    amx_b, dll_b = make_amx(monkeypatch, device_id="amx_b", com=9, port=0)
    dll_a.COM_HVAMX4ED_Open.return_value = AMXBase.NO_ERR
    dll_b.COM_HVAMX4ED_Open.return_value = AMXBase.NO_ERR
    dll_a.COM_HVAMX4ED_SetBaudRate.return_value = AMXBase.NO_ERR
    dll_b.COM_HVAMX4ED_SetBaudRate.return_value = AMXBase.NO_ERR
    dll_a.COM_HVAMX4ED_Close.return_value = AMXBase.ERR_CLOSE

    amx_a.connect()
    assert amx_a.disconnect() is False
    caplog.clear()

    with caplog.at_level(logging.WARNING):
        amx_b.connect()

    assert amx_a.connected is True
    assert amx_a._dll_port_claimed is True
    assert "same DLL port" in caplog.text


def test_set_switch_enable_delay_rejects_out_of_range(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True

    with pytest.raises(ValueError, match="Expected 0 <= delay < 16"):
        amx.set_switch_enable_delay(0, 16)


def test_set_pulser_width_ticks_rejects_uint32_overflow(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True

    with pytest.raises(ValueError, match="width"):
        amx.set_pulser_width_ticks(0, 1 << 32)


def test_get_product_info_returns_structured_metadata(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    monkeypatch.setattr(AMXBase, "get_product_no", lambda self: (self.NO_ERR, 404))
    monkeypatch.setattr(
        AMXBase, "get_product_id", lambda self: (self.NO_ERR, "AMX-CTRL-4ED")
    )
    monkeypatch.setattr(AMXBase, "get_fw_version", lambda self: (self.NO_ERR, 0x0103))
    monkeypatch.setattr(
        AMXBase, "get_fw_date", lambda self: (self.NO_ERR, "2026-03-31")
    )
    monkeypatch.setattr(AMXBase, "get_hw_type", lambda self: (self.NO_ERR, 7))
    monkeypatch.setattr(AMXBase, "get_hw_version", lambda self: (self.NO_ERR, 2))

    info = amx.get_product_info()

    assert info == {
        "product_no": 404,
        "product_id": "AMX-CTRL-4ED",
        "firmware": {
            "version": 0x0103,
            "date": "2026-03-31",
        },
        "hardware": {
            "type": 7,
            "version": 2,
        },
    }


def test_collect_housekeeping_returns_structured_snapshot(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    monkeypatch.setattr(
        AMXBase, "get_main_state", lambda self: (self.NO_ERR, "0x0000", "STATE_ON")
    )
    monkeypatch.setattr(
        AMXBase,
        "get_device_state",
        lambda self: (self.NO_ERR, "0x0001", ["DEVST_VCPU_FAIL"]),
    )
    monkeypatch.setattr(
        AMXBase,
        "get_controller_state",
        lambda self: (self.NO_ERR, "0x0003", ["ENB", "ENB_OSC"]),
    )
    monkeypatch.setattr(AMXBase, "get_device_enable", lambda self: (self.NO_ERR, True))
    monkeypatch.setattr(
        AMXBase,
        "get_housekeeping",
        lambda self: (self.NO_ERR, 12.0, 5.0, 3.3, 40.5),
    )
    monkeypatch.setattr(
        AMXBase,
        "get_sensor_data",
        lambda self: (self.NO_ERR, [11.0, 12.0, 13.0]),
    )
    monkeypatch.setattr(
        AMXBase,
        "get_fan_data",
        lambda self: (
            self.NO_ERR,
            [True, False, True],
            [False, True, False],
            [1200, 1300, 1400],
            [1190, 1290, 1390],
            [100, 200, 300],
        ),
    )
    monkeypatch.setattr(
        AMXBase, "get_led_data", lambda self: (self.NO_ERR, False, True, True)
    )
    monkeypatch.setattr(
        AMXBase, "get_cpu_data", lambda self: (self.NO_ERR, 0.5, 200_000_000.0)
    )
    monkeypatch.setattr(AMXBase, "get_uptime", lambda self: (self.NO_ERR, 1, 2, 3))
    monkeypatch.setattr(
        AMXBase, "get_total_time", lambda self: (self.NO_ERR, 10, 20)
    )
    monkeypatch.setattr(
        AMXBase, "get_oscillator_period", lambda self: (self.NO_ERR, 99998)
    )
    monkeypatch.setattr(
        AMXBase,
        "get_pulser_delay",
        lambda self, pulser: (self.NO_ERR, 100 + pulser),
    )
    monkeypatch.setattr(
        AMXBase,
        "get_pulser_width",
        lambda self, pulser: (self.NO_ERR, 200 + pulser),
    )
    monkeypatch.setattr(
        AMXBase,
        "get_pulser_burst",
        lambda self, pulser: (self.NO_ERR, 300 + pulser),
    )
    monkeypatch.setattr(
        AMXBase,
        "get_switch_trigger_config",
        lambda self, switch: (self.NO_ERR, 10 + switch),
    )
    monkeypatch.setattr(
        AMXBase,
        "get_switch_enable_config",
        lambda self, switch: (self.NO_ERR, 20 + switch),
    )
    monkeypatch.setattr(
        AMXBase,
        "get_switch_trigger_delay",
        lambda self, switch: (self.NO_ERR, 30 + switch, 40 + switch),
    )
    monkeypatch.setattr(
        AMXBase,
        "get_switch_enable_delay",
        lambda self, switch: (self.NO_ERR, 50 + switch),
    )

    snapshot = amx.collect_housekeeping()

    assert snapshot["device_enabled"] is True
    assert snapshot["main_state"] == {"hex": "0x0000", "name": "STATE_ON"}
    assert snapshot["device_state"] == {
        "hex": "0x0001",
        "flags": ["DEVST_VCPU_FAIL"],
    }
    assert snapshot["controller_state"] == {
        "hex": "0x0003",
        "flags": ["ENB", "ENB_OSC"],
    }
    assert snapshot["housekeeping"] == {
        "volt_12v_v": 12.0,
        "volt_5v0_v": 5.0,
        "volt_3v3_v": 3.3,
        "temp_cpu_c": 40.5,
    }
    assert snapshot["sensors_c"] == [11.0, 12.0, 13.0]
    assert snapshot["led"] == {"red": False, "green": True, "blue": True}
    assert snapshot["cpu"] == {"load": 0.5, "frequency_hz": 200_000_000.0}
    assert snapshot["uptime"] == {
        "seconds": 1,
        "milliseconds": 2,
        "operation_seconds": 3,
        "total_uptime_seconds": 10,
        "total_operation_seconds": 20,
    }
    assert snapshot["oscillator"]["period"] == 99998
    assert snapshot["oscillator"]["frequency_hz"] == 1000.0
    assert snapshot["pulsers"][0] == {
        "pulser": 0,
        "label": "pulser_0",
        "delay_ticks": 100,
        "width_ticks": 200,
        "burst": 300,
    }
    assert snapshot["pulsers"][2]["burst"] is None
    assert snapshot["switches"][0] == {
        "switch": 0,
        "trigger_config": 10,
        "enable_config": 20,
        "trigger_delay": {
            "rise": 30,
            "fall": 40,
        },
        "enable_delay": 50,
    }
