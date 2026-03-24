#!/usr/bin/env python3
# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

BOOTSTRAP_ROOT = Path(__file__).resolve().parents[1]
if str(BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(BOOTSTRAP_ROOT))

from invest_evolution.application.training.observability import (
    summarize_release_gate_run,
    write_release_gate_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate release-gate divergence report from a training run directory."
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        help="Training output directory that contains cycle_*.json files.",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Optional label to show in the generated report.",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    summary = summarize_release_gate_run(run_dir, label=args.label)
    json_path, markdown_path = write_release_gate_report(run_dir, summary)

    print(json.dumps(
        {
            "run_dir": str(run_dir),
            "report_json": str(json_path),
            "report_markdown": str(markdown_path),
            "window": summary.get("window"),
            "new_governance": summary.get("new_governance"),
            "divergence": summary.get("divergence"),
            "release_gate_snapshot": summary.get("release_gate_snapshot"),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
