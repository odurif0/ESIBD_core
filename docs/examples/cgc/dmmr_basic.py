"""Minimal CGC DMMR example."""

from cgc.dmmr import DMMR


def main():
    dmmr = DMMR("dmmr_demo", com=8)
    # initialize() rescans modules and acknowledges the current module scan.
    dmmr.initialize()
    try:
        print("Product:", dmmr.get_product_info())
        print("Status:", dmmr.get_status())
        print("Snapshot:", dmmr.collect_housekeeping())
    finally:
        dmmr.shutdown()


if __name__ == "__main__":
    main()
