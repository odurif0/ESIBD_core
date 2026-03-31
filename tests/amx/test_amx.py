from pathlib import Path
import logging
import tempfile
from unittest.mock import Mock

import pytest

from cgc.amx import AMX, AMXBase, AMXDllLoadError, AMXPlatformError


ERROR_CODES_PATH = Path(__file__).resolve().parents[2] / "src" / "cgc" / "error_codes.json"


def make_amx(monkeypatch):
    monkeypatch.setattr("cgc.amx.amx_base.sys.platform", "win32")
    dll = Mock()
    monkeypatch.setattr("cgc.amx.amx_base.ctypes.WinDLL", lambda _path: dll, raising=False)
    log_dir = Path(tempfile.gettempdir()) / "esibd_core_test_logs"
    return AMX("amx_test", com=8, port=0, log_dir=log_dir), dll


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
