from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.validation.phase0 import (  # noqa: E402
    compare_validation_runs,
    load_cutoff_dates_from_run,
    run_bare_validation,
    run_controller_calibration,
)


def _default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("outputs") / f"phase0_known_good_calibration_{stamp}"


def _resolve_cutoffs(args: argparse.Namespace) -> list[str]:
    if args.cutoff_run:
        return load_cutoff_dates_from_run(args.cutoff_run)
    return [str(item) for item in list(args.cutoff_dates or []) if str(item).strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 0 known-good calibration against the full training pipeline.")
    parser.add_argument("--model-name", type=str, default="momentum", help="Registered model name used to load the config.")
    parser.add_argument(
        "--config-path",
        type=str,
        default="invest/models/configs/known_good_baseline.yaml",
        help="Config path for the calibration strategy.",
    )
    parser.add_argument("--cutoff-run", type=str, default=None, help="Existing run directory used to source cutoff dates.")
    parser.add_argument("--cutoff-dates", nargs="*", default=None, help="Explicit cutoff dates in YYYYMMDD/ISO form.")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory for calibration artifacts.")
    parser.add_argument("--min-history-days", type=int, default=None, help="History window override.")
    parser.add_argument("--simulation-days", type=int, default=None, help="Simulation horizon override.")
    parser.add_argument("--dry-run-llm", action="store_true", help="Run the controller calibration with dry-run LLM mode.")
    args = parser.parse_args()

    cutoff_dates = _resolve_cutoffs(args)
    if not cutoff_dates:
        raise SystemExit("No cutoff dates provided. Use --cutoff-run or --cutoff-dates.")

    output_dir = Path(args.output_dir or _default_output_dir()).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    bare_payload = run_bare_validation(
        model_name=args.model_name,
        config_path=args.config_path,
        cutoff_dates=cutoff_dates,
        min_history_days=args.min_history_days,
        simulation_days=args.simulation_days,
    )
    bare_path = output_dir / "bare_validation.json"
    bare_path.write_text(json.dumps(bare_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[phase0] bare validation -> {bare_path}")

    system_run_dir = output_dir / "system_run"
    system_payload = run_controller_calibration(
        model_name=args.model_name,
        config_path=args.config_path,
        cutoff_dates=cutoff_dates,
        output_dir=system_run_dir,
        min_history_days=int(args.min_history_days or bare_payload.get("min_history_days") or 0),
        simulation_days=int(args.simulation_days or bare_payload.get("simulation_days") or 0),
        dry_run_llm=bool(args.dry_run_llm),
    )
    system_summary_path = output_dir / "system_summary.json"
    system_summary_path.write_text(json.dumps(system_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[phase0] system summary -> {system_summary_path}")

    comparison = compare_validation_runs(
        bare_summary=bare_payload,
        system_summary=system_payload,
    )
    comparison_path = output_dir / "comparison.json"
    comparison_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[phase0] comparison -> {comparison_path}")


if __name__ == "__main__":
    main()
