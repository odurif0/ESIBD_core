from cgc.amx import AMX

STANDBY_CONFIG = None  # Optional: replace with a validated disabled config.
OPERATING_CONFIG = 40


def main():
    amx = AMX("amx_main", com=8, port=0)
    startup_kwargs = {"operating_config": OPERATING_CONFIG}
    if STANDBY_CONFIG is not None:
        startup_kwargs["standby_config"] = STANDBY_CONFIG

    startup_state = amx.initialize(**startup_kwargs)
    print("Startup state:", startup_state)
    try:
        amx.set_frequency_hz(2_000.0)
        amx.set_pulser_duty_cycle(0, 0.5)
        print(amx.get_frequency_hz())
    finally:
        amx.shutdown()


if __name__ == "__main__":
    main()
