"""Minimal CGC DMMR example."""

from cgc.dmmr import DMMR


def main():
    dmmr = DMMR("dmmr_demo", com=8)
    dmmr.connect()
    try:
        print("Status:", dmmr.get_status())
        print("Modules:", dmmr.scan_modules())
    finally:
        dmmr.shutdown()


if __name__ == "__main__":
    main()
