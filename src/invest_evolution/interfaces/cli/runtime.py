"""`python -m invest_evolution.interfaces.cli.runtime` entrypoint."""

from __future__ import annotations

from invest_evolution.application.runtime_service import main as _main


def main() -> int:
    result = _main()
    return int(result) if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
