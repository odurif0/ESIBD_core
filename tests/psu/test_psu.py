from pathlib import Path
import logging
import tempfile
from unittest.mock import Mock

import pytest

from cgc.psu import PSU, PSUBase, PSUDllLoadError, PSUPlatformError


ERROR_CODES_PATH = Path(__file__).resolve().parents[2] / "src" / "cgc" / "error_codes.json"


def make_psu(monkeypatch):
    monkeypatch.setattr("cgc.psu.psu_base.sys.platform", "win32")
    dll = Mock()
    monkeypatch.setattr("cgc.psu.psu_base.ctypes.WinDLL", lambda _path: dll, raising=False)
    log_dir = Path(tempfile.gettempdir()) / "esibd_core_test_logs"
    return PSU("psu_test", com=6, port=0, log_dir=log_dir), dll


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


def test_psu_rejects_unknown_init_kwargs():
    with pytest.raises(TypeError, match="Unexpected PSU init kwargs: unexpected"):
        PSU("psu_test", com=6, unexpected=True)


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


def test_connect_rolls_back_when_baud_rate_fails(monkeypatch):
    psu, dll = make_psu(monkeypatch)
    dll.COM_HVPSU2D_Open.return_value = PSUBase.NO_ERR
    dll.COM_HVPSU2D_SetBaudRate.return_value = PSUBase.ERR_RATE
    dll.COM_HVPSU2D_Close.return_value = PSUBase.NO_ERR

    with pytest.raises(RuntimeError, match="set_baud_rate failed"):
        psu.connect()

    assert psu.connected is False
    dll.COM_HVPSU2D_Close.assert_called_once()


def test_initialize_loads_config_without_overriding_enable_flags(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    monkeypatch.setattr(psu, "connect", Mock(return_value=True))
    monkeypatch.setattr(psu, "load_user_config", Mock())
    monkeypatch.setattr(psu, "set_device_enabled", Mock())
    monkeypatch.setattr(psu, "set_output_enabled", Mock())

    psu.initialize(config_number=19)

    psu.connect.assert_called_once()
    psu.load_user_config.assert_called_once_with(19)
    psu.set_device_enabled.assert_not_called()
    psu.set_output_enabled.assert_not_called()


def test_initialize_disconnects_on_failure(monkeypatch):
    psu, _dll = make_psu(monkeypatch)

    def fake_connect(*, timeout_s):
        psu.connected = True
        return True

    monkeypatch.setattr(psu, "connect", Mock(side_effect=fake_connect))
    monkeypatch.setattr(
        psu, "load_user_config", Mock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(psu, "disconnect", Mock(return_value=True))

    with pytest.raises(RuntimeError, match="boom"):
        psu.initialize(config_number=19)

    psu.disconnect.assert_called_once()


def test_shutdown_disables_outputs_and_device_by_default_and_propagates_errors(monkeypatch):
    psu, _dll = make_psu(monkeypatch)
    psu.connected = True
    monkeypatch.setattr(
        psu, "set_output_enabled", Mock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(psu, "set_device_enabled", Mock())
    monkeypatch.setattr(psu, "disconnect", Mock(return_value=True))

    with pytest.raises(RuntimeError, match="boom"):
        psu.shutdown()

    psu.set_output_enabled.assert_called_once_with(False, False)
    psu.set_device_enabled.assert_not_called()
    psu.disconnect.assert_called_once()


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
