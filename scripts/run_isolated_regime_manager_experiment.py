#!/usr/bin/env python3
# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Any, Sequence

BOOTSTRAP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = BOOTSTRAP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from invest_evolution.application.train import SelfLearningController, _build_mock_provider
from invest_evolution.application.training.execution import session_cycle_history
from invest_evolution.application.training.isolated_experiments import (
    build_isolated_experiment_spec,
    discover_isolated_regime_dates,
    resolve_isolated_experiment_preset,
)
from invest_evolution.application.training.observability import (
    summarize_release_gate_run,
    write_release_gate_report,
)
from invest_evolution.config import PROJECT_ROOT, config
from invest_evolution.config.control_plane import RuntimePathConfigService

logger = logging.getLogger(__name__)


def _default_output_dir(preset_name: str) -> Path:
    safe_name = preset_name.replace("@", "_at_")
    return PROJECT_ROOT / "outputs" / f"isolated_{safe_name}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_run_report(
    *,
    controller: Any,
    successful_cycles_target: int,
    status: str,
    error: BaseException | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "attempted_cycles": int(getattr(controller, "total_cycle_attempts", 0) or 0),
        "successful_cycles": len(session_cycle_history(controller)),
        "successful_cycles_target": int(successful_cycles_target or 0) or None,
        "target_met": False,
    }
    if error is not None:
        payload["error_type"] = type(error).__name__
        payload["error_message"] = str(error)
    return payload


def _build_cycle_realization_summary(
    controller: Any,
    *,
    target_regime: str,
    expected_cutoff_dates: list[str],
    manager_id: str,
) -> dict[str, Any]:
    realized_cycles: list[dict[str, Any]] = []
    drifted_cycles: list[dict[str, Any]] = []
    for item in list(session_cycle_history(controller) or []):
        governance_decision = dict(getattr(item, "governance_decision", {}) or {})
        realized = {
            "cycle_id": int(getattr(item, "cycle_id", 0) or 0),
            "cutoff_date": str(getattr(item, "cutoff_date", "") or ""),
            "regime": str(governance_decision.get("regime") or "unknown").strip().lower(),
            "dominant_manager_id": str(governance_decision.get("dominant_manager_id") or "").strip(),
            "manager_config_ref": str(getattr(item, "manager_config_ref", "") or ""),
        }
        realized["cutoff_in_expected_sequence"] = realized["cutoff_date"] in expected_cutoff_dates
        realized["regime_match"] = realized["regime"] == str(target_regime or "").strip().lower()
        realized["manager_match"] = realized["dominant_manager_id"] == str(manager_id or "").strip()
        realized_cycles.append(realized)
        if not (realized["cutoff_in_expected_sequence"] and realized["regime_match"] and realized["manager_match"]):
            drifted_cycles.append(realized)
    return {
        "schema_version": "training.isolated_cycle_realization_summary.v1",
        "target_regime": str(target_regime or "").strip().lower(),
        "manager_id": str(manager_id or "").strip(),
        "expected_cutoff_dates": list(expected_cutoff_dates or []),
        "realized_cycles": realized_cycles,
        "drifted_cycles": drifted_cycles,
        "drift_count": len(drifted_cycles),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a fixed regime x fixed manager isolated strict-training experiment without relaxing gates."
    )
    parser.add_argument(
        "--preset",
        required=True,
        choices=sorted(["defensive_low_vol@bear", "mean_reversion@oscillation"]),
        help="Named isolated experiment line to run.",
    )
    parser.add_argument("--cycles", type=int, default=12, help="Maximum attempt cycles.")
    parser.add_argument(
        "--successful-cycles-target",
        type=int,
        default=10,
        help="Successful cycle target before stopping.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Training output directory for this isolated experiment run.",
    )
    parser.add_argument("--artifact-log-dir", type=str, default=None)
    parser.add_argument("--config-audit-log-path", type=str, default=None)
    parser.add_argument("--config-snapshot-dir", type=str, default=None)
    parser.add_argument("--freeze-n", type=int, default=10)
    parser.add_argument("--freeze-m", type=int, default=7)
    parser.add_argument("--step-days", type=int, default=30)
    parser.add_argument("--warmup-windows", type=int, default=3)
    parser.add_argument("--max-discovered-dates", type=int, default=12)
    parser.add_argument("--min-discovered-dates", type=int, default=5)
    parser.add_argument("--min-date", type=str, default="")
    parser.add_argument("--max-date", type=str, default="")
    parser.add_argument("--log-level", type=str, default="INFO")
    parser.add_argument("--use-allocator", action="store_true")
    parser.add_argument("--allocator-top-n", type=int, default=None)
    parser.add_argument("--mock", action="store_true", help="Use mock market data for smoke/debug runs.")
    parser.add_argument(
        "--llm-dry-run",
        action="store_true",
        help="Avoid real LLM calls; only for local smoke/debug runs.",
    )
    parser.add_argument(
        "--force-full-cycles",
        action="store_true",
        help="Do not stop early when freeze gate passes.",
    )
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="Only discover fixed cutoff windows and write metadata without running cycles.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    preset = resolve_isolated_experiment_preset(args.preset)
    runtime_paths = RuntimePathConfigService(project_root=PROJECT_ROOT).get_payload()
    output_dir = Path(args.output or _default_output_dir(preset.name)).expanduser().resolve()
    artifact_log_dir = Path(args.artifact_log_dir or runtime_paths["artifact_log_dir"]).expanduser().resolve()
    config_audit_log_path = Path(
        args.config_audit_log_path or runtime_paths["config_audit_log_path"]
    ).expanduser().resolve()
    config_snapshot_dir = Path(
        args.config_snapshot_dir or runtime_paths["config_snapshot_dir"]
    ).expanduser().resolve()

    data_provider = _build_mock_provider() if args.mock else None
    controller = SelfLearningController(
        output_dir=str(output_dir),
        artifact_log_dir=str(artifact_log_dir),
        config_audit_log_path=str(config_audit_log_path),
        config_snapshot_dir=str(config_snapshot_dir),
        freeze_total_cycles=args.freeze_n,
        freeze_profit_required=args.freeze_m,
        data_provider=data_provider,
    )
    controller.aggregate_leaderboard_enabled = False

    if args.mock:
        controller.set_llm_dry_run(True)

    discovery = discover_isolated_regime_dates(
        controller,
        manager_id=preset.manager_id,
        target_regime=preset.target_regime,
        step_days=args.step_days,
        warmup_windows=args.warmup_windows,
        min_date=args.min_date or None,
        max_date=args.max_date or None,
        max_dates=args.max_discovered_dates,
    )
    discovery["preset"] = preset.name
    discovery["label"] = preset.label
    discovery_path = output_dir / "isolated_discovery.json"
    _write_json(discovery_path, discovery)
    matched_dates = list(discovery.get("matched_dates") or [])
    if len(matched_dates) < max(1, int(args.min_discovered_dates or 1)):
        logger.error(
            "discovered only %s matching cutoff dates for %s; require at least %s",
            len(matched_dates),
            preset.name,
            args.min_discovered_dates,
        )
        return 2

    experiment_spec = build_isolated_experiment_spec(
        manager_id=preset.manager_id,
        cutoff_dates=matched_dates,
        llm_dry_run=bool(args.llm_dry_run or args.mock),
    )
    experiment_spec_path = output_dir / "isolated_experiment_spec.json"
    _write_json(experiment_spec_path, experiment_spec)

    if args.discover_only:
        print(
            json.dumps(
                {
                    "preset": preset.name,
                    "discovery_path": str(discovery_path),
                    "experiment_spec_path": str(experiment_spec_path),
                    "matched_dates": matched_dates,
                    "matched_count": len(matched_dates),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    controller.configure_experiment(experiment_spec)
    if args.use_allocator:
        config.allocator_enabled = True
        config.governance_enabled = True
        config.governance_mode = "rule"
    if args.allocator_top_n is not None:
        config.allocator_top_n = max(1, int(args.allocator_top_n))

    original_stop_on_freeze = bool(getattr(config, "stop_on_freeze", True))
    if args.force_full_cycles:
        config.stop_on_freeze = False

    exit_code = 0
    try:
        try:
            report = controller.run_continuous(
                max_cycles=args.cycles,
                successful_cycles_target=args.successful_cycles_target,
            )
        except KeyboardInterrupt as exc:
            exit_code = 130
            report = _build_run_report(
                controller=controller,
                successful_cycles_target=args.successful_cycles_target,
                status="interrupted",
                error=exc,
            )
            logger.warning("isolated experiment interrupted")
        except Exception as exc:
            exit_code = 1
            report = _build_run_report(
                controller=controller,
                successful_cycles_target=args.successful_cycles_target,
                status="failed",
                error=exc,
            )
            logger.exception("isolated experiment failed")
    finally:
        config.stop_on_freeze = original_stop_on_freeze

    run_report_path = output_dir / "run_report.json"
    _write_json(run_report_path, report)

    realization_summary = _build_cycle_realization_summary(
        controller,
        target_regime=preset.target_regime,
        expected_cutoff_dates=matched_dates,
        manager_id=preset.manager_id,
    )
    realization_summary_path = output_dir / "isolated_cycle_realization_summary.json"
    _write_json(realization_summary_path, realization_summary)

    summary = summarize_release_gate_run(
        output_dir,
        run_report=report,
        label=preset.label,
    )
    report_json_path, report_markdown_path = write_release_gate_report(output_dir, summary)

    print(
        json.dumps(
            {
                "preset": preset.name,
                "discovery_path": str(discovery_path),
                "experiment_spec_path": str(experiment_spec_path),
                "realization_summary_path": str(realization_summary_path),
                "run_report_path": str(run_report_path),
                "release_gate_report_json": str(report_json_path),
                "release_gate_report_markdown": str(report_markdown_path),
                "matched_dates": matched_dates,
                "run_report": report,
                "release_gate_snapshot": summary.get("release_gate_snapshot"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
