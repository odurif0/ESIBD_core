import time

from cgc.ampr import AMPR


def main() -> None:
    ampr = AMPR("ampr_main", com=5)
    ampr.initialize()
    try:
        print(ampr.get_status())
        modules = ampr.scan_modules()
        print(f"Detected modules: {list(modules)}")

        if not modules:
            print("No AMPR modules detected.")
            return

        module = next(iter(modules))
        channel = 1
        test_voltage = 5.0
        wait_s = 10

        print(f"Initial voltages for module {module}:")
        print(ampr.get_module_voltages(module))
        time.sleep(wait_s)

        print(f"Setting module {module} channel {channel} to {test_voltage} V")
        ampr.set_module_voltage(module, channel, test_voltage)

        print(f"Voltages after setting channel {channel}:")
        print(ampr.get_module_voltages(module))
        time.sleep(wait_s)
    finally:
        ampr.shutdown()


if __name__ == "__main__":
    main()
