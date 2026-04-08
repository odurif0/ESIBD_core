"""Tests for shared CGC driver runtime helpers."""

from __future__ import annotations

import warnings

import cgc._driver_common as driver_common


class _FakeBackend:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeClient(driver_common.ProcessIsolatedClientMixin):
    _INSTRUMENT_NAME = "AMPR"
    _PROCESS_CONTROLLER_CLASS = _FakeBackend
    _PROCESS_CONTROLLER_PATH = "fake.module:Controller"


def test_process_backend_falls_back_to_inline_when_worker_startup_fails():
    original_supports = driver_common.supports_process_backend
    original_proxy = driver_common.ControllerProcessProxy
    driver_common.supports_process_backend = lambda *args: True

    def _raise_startup_error(*args, **kwargs):
        raise RuntimeError("worker timed out during worker startup")

    driver_common.ControllerProcessProxy = _raise_startup_error
    try:
        client = _FakeClient()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client._initialize_process_backend(
                backend_kwargs={"device_id": "demo"},
                incompatible_objects={"logger": None},
            )
    finally:
        driver_common.supports_process_backend = original_supports
        driver_common.ControllerProcessProxy = original_proxy

    assert client._backend_mode == "inline"
    assert isinstance(client._backend, _FakeBackend)
    assert (
        client._process_backend_disabled_reason
        == "AMPR process isolation startup failed; "
        "falling back to inline controller: worker timed out during worker startup"
    )
    assert [str(w.message) for w in caught] == [client._process_backend_disabled_reason]
