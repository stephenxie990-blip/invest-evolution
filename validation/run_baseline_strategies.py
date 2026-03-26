from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.validation.phase0 import load_cutoff_dates_from_run, run_bare_validation  # noqa: E402
from invest.models import list_models  # noqa: E402


def _default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("outputs") / f"phase0_bare_strategy_validation_{stamp}"


def _resolve_cutoffs(args: argparse.Namespace) -> list[str]:
    if args.cutoff_run:
        return load_cutoff_dates_from_run(args.cutoff_run)
    return [str(item) for item in list(args.cutoff_dates or []) if str(item).strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run independent Phase 0 bare-strategy validation.")
    parser.add_argument("--models", nargs="+", default=list_models(), help="Model names to validate.")
    parser.add_argument("--config-path", type=str, default=None, help="Optional custom config path. Use only with a single model.")
    parser.add_argument("--cutoff-run", type=str, default=None, help="Existing run directory used to source cutoff dates.")
    parser.add_argument("--cutoff-dates", nargs="*", default=None, help="Explicit cutoff dates in YYYYMMDD/ISO form.")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory for validation artifacts.")
    parser.add_argument("--stock-count", type=int, default=None, help="Universe size override.")
    parser.add_argument("--min-history-days", type=int, default=None, help="History window override.")
    parser.add_argument("--simulation-days", type=int, default=None, help="Simulation horizon override.")
    args = parser.parse_args()

    cutoff_dates = _resolve_cutoffs(args)
    if not cutoff_dates:
        raise SystemExit("No cutoff dates provided. Use --cutoff-run or --cutoff-dates.")
    if args.config_path and len(args.models) != 1:
        raise SystemExit("--config-path can only be used with a single model.")

    output_dir = Path(args.output_dir or _default_output_dir()).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    index_payload = {
        "cutoff_dates": cutoff_dates,
        "models": [],
    }
    for model_name in args.models:
        payload = run_bare_validation(
            model_name=model_name,
            config_path=args.config_path,
            cutoff_dates=cutoff_dates,
            stock_count=args.stock_count,
            min_history_days=args.min_history_days,
            simulation_days=args.simulation_days,
        )
        output_path = output_dir / f"{model_name}_bare_validation.json"
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        index_payload["models"].append(
            {
                "model_name": model_name,
                "result_path": str(output_path),
                "summary": dict(payload.get("summary") or {}),
            }
        )
        print(f"[phase0] saved {model_name} bare validation -> {output_path}")

    index_path = output_dir / "index.json"
    index_path.write_text(json.dumps(index_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[phase0] index -> {index_path}")


if __name__ == "__main__":
    main()
