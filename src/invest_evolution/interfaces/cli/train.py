"""`python -m invest_evolution.interfaces.cli.train` entrypoint."""

from __future__ import annotations

import argparse
import sys


def _wants_help(argv: list[str]) -> bool:
    return any(arg in {"-h", "--help"} for arg in argv)


def _print_fallback_help(exc: ModuleNotFoundError) -> None:
    parser = argparse.ArgumentParser(
        prog="invest-train",
        description=(
            "Invest Evolution batch/compat training entrypoint "
            "(humans should prefer Commander; backend unavailable in current env)."
        ),
    )
    parser.add_argument("args", nargs="*", help="Arguments forwarded to training runtime.")
    parser.print_help()
    print(f"\nbackend import failed: {exc}", file=sys.stderr)


def main() -> int:
    argv = list(sys.argv[1:])
    try:
        from invest_evolution.application.train import train_main as _main
    except ModuleNotFoundError as exc:
        if _wants_help(argv):
            _print_fallback_help(exc)
            return 1
        raise
    result = _main()
    return int(result) if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
