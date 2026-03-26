from cgc.ampr import AMPR


def main() -> None:
    ampr = AMPR("ampr_main", com=5)
    ampr.initialize()
    try:
        print(ampr.get_status())
    finally:
        ampr.shutdown()


if __name__ == "__main__":
    main()
