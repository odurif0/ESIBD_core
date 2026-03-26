from pathlib import Path
import tempfile
from unittest.mock import Mock

import pytest

from cgc.ampr import AMPR, AMPRBase, AMPRDllLoadError, AMPRPlatformError


ERROR_CODES_PATH = Path(__file__).resolve().parents[2] / "src" / "cgc" / "error_codes.json"


def make_ampr(monkeypatch):
    monkeypatch.setattr("cgc.ampr.ampr_base.sys.platform", "win32")
    dll = Mock()
    monkeypatch.setattr("cgc.ampr.ampr_base.ctypes.WinDLL", lambda _path: dll, raising=False)
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


def test_connect_rolls_back_when_baud_rate_fails(monkeypatch):
    ampr, dll = make_ampr(monkeypatch)
    dll.COM_AMPR_12_Open.return_value = AMPRBase.NO_ERR
    dll.COM_AMPR_12_SetBaudRate.return_value = AMPRBase.ERR_RATE
    dll.COM_AMPR_12_Close.return_value = AMPRBase.NO_ERR

    with pytest.raises(RuntimeError, match="set_baud_rate failed"):
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

    monkeypatch.setattr(ampr, "connect", Mock(return_value=True))
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
