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
        assert proxy.call_method("add", 3, timeout_s=1.0) == 5
        proxy.set_attribute("value", 11, timeout_s=1.0)
        assert proxy.call_method("get_value", timeout_s=1.0) == 11
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
            proxy.call_method("explode", timeout_s=1.0)

        assert proxy.call_method("add", 1, timeout_s=1.0) == 1
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
        proxy.call_method("poison", timeout_s=1.0)

    with pytest.raises(RuntimeError, match="Create a new instrument instance"):
        proxy.call_method("get_value", timeout_s=1.0)


def test_controller_process_proxy_terminates_worker_after_rpc_timeout():
    proxy = ControllerProcessProxy(
        CONTROLLER_PATH,
        {},
        label="Dummy",
        startup_timeout_s=5.0,
    )

    with pytest.raises(RuntimeError, match="timed out during sleep\\(\\)"):
        proxy.call_method("sleep", 1.0, timeout_s=0.05)

    with pytest.raises(RuntimeError, match="timed out during sleep\\(\\)|no longer running|Create a new instrument instance"):
        proxy.call_method("get_value", timeout_s=1.0)
