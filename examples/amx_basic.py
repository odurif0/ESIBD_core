from cgc.amx import AMX


def main():
    amx = AMX("amx_main", com=8, port=0)
    amx.connect()
    amx.load_config(40)
    try:
        amx.set_frequency_hz(2_000.0)
        amx.set_pulser_duty_cycle(0, 0.5)
        print(amx.get_frequency_hz())
    finally:
        amx.shutdown()


if __name__ == "__main__":
    main()
