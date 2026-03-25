from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from invest_evolution.application.verification_report import (  # noqa: E402
    REPORT_PATH,
    build_report,
)


def main() -> int:
    report = build_report()
    results = report.get("results")
    errors = []
    if isinstance(results, list):
        errors = [result for result in results if result.get("returncode", 0) != 0]
    print(f"Verification report written to {REPORT_PATH.resolve()}")
    if errors:
        print("One or more verification steps failed.")
        return 1
    print("All verification steps succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
