from cgc.psu import PSU


def main():
    psu = PSU("psu_main", com=6, port=0)
    psu.connect()
    psu.load_config(19)
    try:
        psu.set_channel_voltage(0, 25.0)
        psu.set_channel_current(0, 0.5)
        print(psu.get_channel_voltage(0))
        print(psu.get_channel_current(0))
    finally:
        psu.shutdown()


if __name__ == "__main__":
    main()
