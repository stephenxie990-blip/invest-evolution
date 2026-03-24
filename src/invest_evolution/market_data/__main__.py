import logging

from .manager import _cli_main


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _cli_main()


if __name__ == "__main__":
    main()
