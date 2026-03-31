import ctypes
from pathlib import Path
import logging
import tempfile
from unittest.mock import Mock

import pytest

from cgc.amx import AMX, AMXBase, AMXDllLoadError, AMXPlatformError


ERROR_CODES_PATH = Path(__file__).resolve().parents[2] / "src" / "cgc" / "error_codes.json"


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


def test_amx_rejects_unknown_init_kwargs():
    with pytest.raises(TypeError, match="Unexpected AMX init kwargs: unexpected"):
        AMX("amx_test", com=8, unexpected=True)


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


def test_set_frequency_hz_translates_to_oscillator_period(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    monkeypatch.setattr(AMXBase, "set_oscillator_period", Mock(return_value=AMXBase.NO_ERR))

    amx.set_frequency_hz(2_000.0)

    expected_period = round((AMXBase.CLOCK / 2_000.0) - AMXBase.OSC_OFFSET)
    AMXBase.set_oscillator_period.assert_called_once_with(amx, expected_period)


def test_set_pulser_duty_cycle_uses_current_oscillator_period(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    monkeypatch.setattr(
        AMXBase, "get_oscillator_period", lambda self: (self.NO_ERR, 99998)
    )
    monkeypatch.setattr(AMXBase, "set_pulser_width", Mock(return_value=AMXBase.NO_ERR))

    amx.set_pulser_duty_cycle(0, 0.5)

    AMXBase.set_pulser_width.assert_called_once_with(amx, 0, 49998)


def test_initialize_loads_config_without_forcing_enable(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    monkeypatch.setattr(amx, "connect", Mock(return_value=True))
    monkeypatch.setattr(amx, "load_user_config", Mock())
    monkeypatch.setattr(amx, "set_device_enabled", Mock())

    amx.initialize(config_number=40)

    amx.connect.assert_called_once()
    amx.load_user_config.assert_called_once_with(40)
    amx.set_device_enabled.assert_not_called()


def test_initialize_disconnects_on_failure(monkeypatch):
    amx, _dll = make_amx(monkeypatch)

    def fake_connect(*, timeout_s):
        amx.connected = True
        return True

    monkeypatch.setattr(amx, "connect", Mock(side_effect=fake_connect))
    monkeypatch.setattr(
        amx, "load_user_config", Mock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(amx, "disconnect", Mock(return_value=True))

    with pytest.raises(RuntimeError, match="boom"):
        amx.initialize(config_number=40)

    amx.disconnect.assert_called_once()


def test_initialize_keeps_existing_connection_on_failure(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    amx._dll_port_claimed = True
    monkeypatch.setattr(amx, "connect", Mock(return_value=True))
    monkeypatch.setattr(
        amx, "load_user_config", Mock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(amx, "disconnect", Mock(return_value=True))

    with pytest.raises(RuntimeError, match="boom"):
        amx.initialize(config_number=40)

    amx.disconnect.assert_not_called()


def test_shutdown_disables_device_by_default_and_propagates_errors(monkeypatch):
    amx, _dll = make_amx(monkeypatch)
    amx.connected = True
    monkeypatch.setattr(
        amx, "set_device_enabled", Mock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(amx, "disconnect", Mock(return_value=True))

    with pytest.raises(RuntimeError, match="boom"):
        amx.shutdown()

    amx.set_device_enabled.assert_called_once_with(False)
    amx.disconnect.assert_called_once()


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

    assert amx_a.connected is False
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
