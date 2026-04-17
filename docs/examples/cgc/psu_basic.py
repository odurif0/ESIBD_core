from cgc.psu import PSU

STANDBY_CONFIG = 1
OPERATING_CONFIG = 2  # Example only: replace with your saved operating config.


def main():
    psu = PSU("psu_main", com=6, port=0)
    startup_state = psu.initialize(
        standby_config=STANDBY_CONFIG,
        operating_config=OPERATING_CONFIG,
    )
    print("Startup state:", startup_state)
    # initialize() now forces both HV outputs back OFF if the saved standby
    # slot briefly leaves one enabled, then verifies the readback before it
    # continues to the operating slot.
    # Optional deeper startup check for maintenance or troubleshooting:
    # print("Snapshot:", psu.collect_housekeeping())
    try:
        psu.set_channel_voltage(0, 25.0)
        psu.set_channel_current(0, 0.5)
        print(psu.get_channel_voltage(0))
        print(psu.get_channel_current(0))
    finally:
        psu.shutdown()


if __name__ == "__main__":
    main()
