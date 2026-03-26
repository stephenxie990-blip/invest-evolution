from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.validation.phase0 import build_trade_trace_records  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Phase 0 trade-trace audit records from a validation result.")
    parser.add_argument("--result-file", type=str, required=True, help="Bare validation JSON result file.")
    parser.add_argument("--limit", type=int, default=5, help="Number of trade traces to export.")
    parser.add_argument(
        "--selection",
        type=str,
        default="top_abs",
        choices=["top_abs", "mixed"],
        help="Trace sampling mode.",
    )
    parser.add_argument("--output", type=str, default=None, help="Output file path.")
    args = parser.parse_args()

    result_path = Path(args.result_file).expanduser().resolve()
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    traces = build_trade_trace_records(payload, limit=args.limit, selection=args.selection)
    output_path = Path(args.output).expanduser().resolve() if args.output else result_path.with_name(
        f"{result_path.stem}_trade_trace.json"
    )
    output_path.write_text(json.dumps({"traces": traces}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[phase0] trade trace -> {output_path}")


if __name__ == "__main__":
    main()
