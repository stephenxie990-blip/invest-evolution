"""`python -m invest_evolution.interfaces.cli.market_data` entrypoint."""

from __future__ import annotations

import argparse
import sys


def _wants_help(argv: list[str]) -> bool:
    return any(arg in {"-h", "--help"} for arg in argv)


def _print_fallback_help(exc: ModuleNotFoundError) -> None:
    parser = argparse.ArgumentParser(
        prog="invest-data",
        description="Invest Evolution market data entrypoint (backend unavailable in current env).",
    )
    parser.add_argument("args", nargs="*", help="Arguments forwarded to market data CLI.")
    parser.print_help()
    print(f"\nbackend import failed: {exc}", file=sys.stderr)


def main() -> int:
    argv = list(sys.argv[1:])
    try:
        from invest_evolution.market_data.__main__ import main as _main
    except ModuleNotFoundError as exc:
        if _wants_help(argv):
            _print_fallback_help(exc)
            return 1
        raise
    _main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
