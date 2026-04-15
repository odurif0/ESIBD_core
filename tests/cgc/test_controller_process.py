import pytest

from cgc._controller_process import ControllerProcessProxy


CONTROLLER_PATH = "cgc._process_proxy_support:DummyController"


def test_controller_process_proxy_round_trips_methods_and_attributes():
    proxy = ControllerProcessProxy(
        CONTROLLER_PATH,
        {"start": 2},
        label="Dummy",
        startup_timeout_s=5.0,
    )

    try:
        assert proxy.get_attribute("value", timeout_s=1.0) == 2
        assert proxy.call_method("add", 3, rpc_timeout_s=1.0) == 5
        proxy.set_attribute("value", 11, timeout_s=1.0)
        assert proxy.call_method("get_value", rpc_timeout_s=1.0) == 11
    finally:
        proxy.close()


def test_controller_process_proxy_preserves_non_poisoning_errors():
    proxy = ControllerProcessProxy(
        CONTROLLER_PATH,
        {},
        label="Dummy",
        startup_timeout_s=5.0,
    )

    try:
        with pytest.raises(ValueError, match="boom"):
            proxy.call_method("explode", rpc_timeout_s=1.0)

        assert proxy.call_method("add", 1, rpc_timeout_s=1.0) == 1
    finally:
        proxy.close()


def test_controller_process_proxy_closes_after_transport_poison():
    proxy = ControllerProcessProxy(
        CONTROLLER_PATH,
        {},
        label="Dummy",
        startup_timeout_s=5.0,
    )

    with pytest.raises(RuntimeError, match="controller poisoned"):
        proxy.call_method("poison", rpc_timeout_s=1.0)

    with pytest.raises(RuntimeError, match="Create a new instrument instance"):
        proxy.call_method("get_value", rpc_timeout_s=1.0)


def test_controller_process_proxy_terminates_worker_after_rpc_timeout():
    proxy = ControllerProcessProxy(
        CONTROLLER_PATH,
        {},
        label="Dummy",
        startup_timeout_s=5.0,
    )

    with pytest.raises(RuntimeError, match="timed out during sleep\\(\\)"):
        proxy.call_method("sleep", 1.0, rpc_timeout_s=0.05)

    with pytest.raises(RuntimeError, match="timed out during sleep\\(\\)|no longer running|Create a new instrument instance"):
        proxy.call_method("get_value", rpc_timeout_s=1.0)


def test_controller_process_proxy_allows_backend_timeout_s_kwargs():
    proxy = ControllerProcessProxy(
        CONTROLLER_PATH,
        {},
        label="Dummy",
        startup_timeout_s=5.0,
    )

    try:
        assert proxy.call_method(
            "echo_timeout",
            rpc_timeout_s=1.0,
            timeout_s=0.25,
        ) == 0.25
    finally:
        proxy.close()


def test_controller_process_proxy_close_transport_kills_stubborn_worker():
    proxy = object.__new__(ControllerProcessProxy)

    class FakeConnection:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeProcess:
        def __init__(self):
            self.alive = True
            self.terminate_calls = 0
            self.kill_calls = 0
            self.join_timeouts = []
            self.closed = False

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminate_calls += 1

        def kill(self):
            self.kill_calls += 1
            self.alive = False

        def join(self, timeout=None):
            self.join_timeouts.append(timeout)

        def close(self):
            self.closed = True

    proxy._connection = FakeConnection()
    proxy._process = FakeProcess()

    proxy._close_transport()

    assert proxy._connection.closed is True
    assert proxy._process.terminate_calls == 1
    assert proxy._process.kill_calls == 1
    assert proxy._process.join_timeouts == [1.0, 1.0]
    assert proxy._process.closed is True
