#!/usr/bin/env python3
# ruff: noqa: E402

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import sys
from typing import Any, Sequence

BOOTSTRAP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = BOOTSTRAP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from invest_evolution.config import PROJECT_ROOT, config, normalize_date
from invest_evolution.config.control_plane import RuntimePathConfigService
from invest_evolution.application.train import SelfLearningController, _build_mock_provider
from invest_evolution.application.training.observability import (
    summarize_release_gate_run,
    write_release_gate_report,
)
from invest_evolution.application.training.execution import session_cycle_history

logger = logging.getLogger(__name__)

DEFAULT_STAGE4_SHADOW_WARMUP_WINDOWS = 3


def _default_output_dir() -> Path:
    return PROJECT_ROOT / "outputs" / "p0_release_gate_stage1_20260317"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_stage4_run_report(
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


def resolve_stage4_shadow_anchor_date(
    controller: Any,
    *,
    step_days: int = 30,
    warmup_windows: int = 0,
    min_date: str | None = None,
    max_date: str | None = None,
) -> str | None:
    normalized_min_date = normalize_date(
        str(min_date or getattr(controller, "experiment_min_date", None) or "20180101")
    )
    normalized_max_date = normalize_date(str(max_date)) if str(max_date or "").strip() else ""
    target_stock_count = max(1, int(getattr(config, "max_stocks", 50) or 50))
    min_history_days = max(
        30,
        int(
            getattr(controller, "experiment_min_history_days", None)
            or getattr(config, "min_history_days", 200)
            or 200
        ),
    )
    probe_days = max(1, int(step_days or 30))
    data_manager = getattr(controller, "data_manager", None)
    readiness = getattr(data_manager, "check_training_readiness", None)
    if not callable(readiness):
        return None

    try:
        initial = readiness(
            normalized_min_date,
            stock_count=target_stock_count,
            min_history_days=min_history_days,
        )
    except Exception:
        logger.warning("failed to probe stage4 shadow anchor readiness at %s", normalized_min_date, exc_info=True)
        return None
    initial_payload = dict(initial) if isinstance(initial, dict) else {}

    normalized_warmup_windows = max(0, int(warmup_windows or 0))
    if bool(initial_payload.get("ready")) and normalized_warmup_windows == 0:
        return normalized_min_date

    if not normalized_max_date:
        normalized_max_date = normalize_date(
            str(dict(initial_payload.get("date_range") or {}).get("max") or "")
        )
    if not normalized_max_date:
        return None

    start_dt = datetime.strptime(normalized_min_date, "%Y%m%d") + timedelta(
        days=probe_days * normalized_warmup_windows
    )
    if start_dt < datetime.strptime(normalized_min_date, "%Y%m%d"):
        start_dt = datetime.strptime(normalized_min_date, "%Y%m%d")
    cursor = start_dt
    end_dt = datetime.strptime(normalized_max_date, "%Y%m%d")
    while cursor <= end_dt:
        candidate = cursor.strftime("%Y%m%d")
        try:
            diagnostics = readiness(
                candidate,
                stock_count=target_stock_count,
                min_history_days=min_history_days,
            )
        except Exception:
            logger.debug("stage4 shadow anchor probe failed at %s", candidate, exc_info=True)
            cursor += timedelta(days=probe_days)
            continue
        diagnostics_payload = dict(diagnostics) if isinstance(diagnostics, dict) else {}
        if bool(diagnostics_payload.get("ready")):
            return candidate
        cursor += timedelta(days=probe_days)
    return None


def build_stage4_shadow_experiment_spec(
    *,
    mock: bool = False,
    llm_dry_run: bool = False,
    anchor_date: str | None = None,
) -> dict[str, Any]:
    protocol: dict[str, Any] = {
        "shadow_mode": True,
        # Stage 4 needs monotonic evidence accumulation; random cutoffs and single-cycle review
        # keep research feedback sample_count at zero for too many runs.
        "review_window": {"mode": "rolling", "size": 5},
        "cutoff_policy": {
            "mode": "rolling",
            "step_days": 30,
            "anchor_date": normalize_date(str(anchor_date)) if str(anchor_date or "").strip() else "",
        },
    }
    spec: dict[str, Any] = {"protocol": protocol}
    if mock or llm_dry_run:
        spec["llm"] = {"dry_run": True}
    return spec


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run P0 release-gate Stage 1 shadow/advisory observation and emit divergence reports."
    )
    parser.add_argument("--cycles", type=int, default=120, help="Maximum attempt cycles.")
    parser.add_argument(
        "--successful-cycles-target",
        type=int,
        default=30,
        help="Successful cycle target before stopping.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(_default_output_dir()),
        help="Training output directory for this release-gate observation run.",
    )
    parser.add_argument("--artifact-log-dir", type=str, default=None)
    parser.add_argument("--config-audit-log-path", type=str, default=None)
    parser.add_argument("--config-snapshot-dir", type=str, default=None)
    parser.add_argument("--freeze-n", type=int, default=10)
    parser.add_argument("--freeze-m", type=int, default=7)
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
        "--label",
        type=str,
        default="p0_release_gate_stage1",
        help="Human label written into the divergence report.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    runtime_paths = RuntimePathConfigService(project_root=PROJECT_ROOT).get_payload()
    output_dir = Path(args.output).expanduser().resolve()
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
    anchor_date = resolve_stage4_shadow_anchor_date(
        controller,
        step_days=30,
        warmup_windows=DEFAULT_STAGE4_SHADOW_WARMUP_WINDOWS,
    )
    if anchor_date:
        logger.info("resolved stage4 shadow rolling anchor_date=%s", anchor_date)
    else:
        logger.warning("unable to resolve a ready stage4 shadow anchor date; falling back to protocol defaults")
    experiment_spec = build_stage4_shadow_experiment_spec(
        mock=args.mock,
        llm_dry_run=args.llm_dry_run,
        anchor_date=anchor_date,
    )
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
            report = _build_stage4_run_report(
                controller=controller,
                successful_cycles_target=args.successful_cycles_target,
                status="interrupted",
                error=exc,
            )
            logger.warning("release-gate stage1 run interrupted")
        except Exception as exc:
            exit_code = 1
            report = _build_stage4_run_report(
                controller=controller,
                successful_cycles_target=args.successful_cycles_target,
                status="failed",
                error=exc,
            )
            logger.exception("release-gate stage1 run failed")
    finally:
        config.stop_on_freeze = original_stop_on_freeze

    run_report_path = output_dir / "run_report.json"
    _write_json(run_report_path, report)

    summary = summarize_release_gate_run(
        output_dir,
        run_report=report,
        label=args.label,
    )
    report_json_path, report_markdown_path = write_release_gate_report(output_dir, summary)

    logger.info("run report written to %s", run_report_path)
    logger.info("release-gate JSON report written to %s", report_json_path)
    logger.info("release-gate Markdown report written to %s", report_markdown_path)

    print(
        json.dumps(
            {
                "run_report_path": str(run_report_path),
                "release_gate_report_json": str(report_json_path),
                "release_gate_report_markdown": str(report_markdown_path),
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
