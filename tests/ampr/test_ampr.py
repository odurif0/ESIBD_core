from pathlib import Path
from unittest.mock import Mock

import pytest

from cgc.ampr import AMPR, AMPRBase, AMPRDllLoadError, AMPRPlatformError
from cgc.ampr.helpers import initialize_ampr


ERROR_CODES_PATH = Path(__file__).resolve().parents[2] / "src" / "cgc" / "error_codes.json"


def test_ampr_base_rejects_non_windows(monkeypatch):
    monkeypatch.setattr("cgc.ampr.ampr_base.sys.platform", "linux")

    with pytest.raises(AMPRPlatformError):
        AMPRBase(com=5)


def test_ampr_base_raises_clear_error_when_dll_fails(monkeypatch):
    monkeypatch.setattr("cgc.ampr.ampr_base.sys.platform", "win32")

    def raise_os_error(_path):
        raise OSError("missing dll")

    monkeypatch.setattr("cgc.ampr.ampr_base.ctypes.WinDLL", raise_os_error)

    with pytest.raises(AMPRDllLoadError):
        AMPRBase(com=5, error_codes_path=ERROR_CODES_PATH)


def test_connect_rolls_back_when_baud_rate_fails(monkeypatch):
    monkeypatch.setattr("cgc.ampr.ampr_base.sys.platform", "win32")
    dll = Mock()
    dll.COM_AMPR_12_Open.return_value = AMPRBase.NO_ERR
    dll.COM_AMPR_12_SetBaudRate.return_value = AMPRBase.ERR_RATE
    dll.COM_AMPR_12_Close.return_value = AMPRBase.NO_ERR
    monkeypatch.setattr("cgc.ampr.ampr_base.ctypes.WinDLL", lambda _path: dll)

    ampr = AMPR("ampr_test", com=5)

    assert ampr.connect() is False
    assert ampr.connected is False
    dll.COM_AMPR_12_Close.assert_called_once()


def test_disconnect_marks_instance_disconnected_even_on_close_failure(monkeypatch):
    monkeypatch.setattr("cgc.ampr.ampr_base.sys.platform", "win32")
    dll = Mock()
    dll.COM_AMPR_12_Close.return_value = AMPRBase.ERR_CLOSE
    monkeypatch.setattr("cgc.ampr.ampr_base.ctypes.WinDLL", lambda _path: dll)

    ampr = AMPR("ampr_test", com=5)
    ampr.connected = True

    assert ampr.disconnect() is False
    assert ampr.connected is False


def test_initialize_ampr_disconnects_on_failure():
    ampr = Mock()
    ampr.NO_ERR = 0
    ampr.connect.return_value = True
    ampr.get_scanned_module_state.return_value = (0, False, False)
    ampr.enable_psu.return_value = (-1, False)

    with pytest.raises(RuntimeError, match="enable_psu"):
        initialize_ampr(ampr)

    ampr.disconnect.assert_called_once()
