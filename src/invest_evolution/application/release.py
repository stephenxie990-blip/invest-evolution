"""Canonical release verification and shadow-gate entrypoints."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

from invest_evolution.application.training.observability import (
    summarize_release_gate_run,
    write_release_gate_report,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class ShadowGateThresholds:
    profile: str = "strict"
    allowed_statuses: tuple[str, ...] = ("completed", "completed_with_skips")
    successful_cycles_min: int = 30
    unexpected_reject_count_max: int = 0
    governance_blocked_count_max: int = 0
    validation_pass_count_min: int = 2
    promote_count_min: int = 1
    candidate_missing_rate_max: float = 0.50
    needs_more_optimization_rate_max: float = 0.70
    artifact_completeness_min: float = 1.0
    research_feedback_gate_required: bool = False
    research_feedback_gate_must_pass: bool = False


@dataclass(frozen=True)
class ReleaseVerificationBundle:
    name: str
    description: str
    tests: list[str]


DEFAULT_THRESHOLDS = ShadowGateThresholds(
    research_feedback_gate_required=True,
    research_feedback_gate_must_pass=True,
)

SHADOW_GATE_PROFILE_CATALOG: dict[str, ShadowGateThresholds] = {
    "strict": DEFAULT_THRESHOLDS,
    "smoke": ShadowGateThresholds(
        profile="smoke",
        successful_cycles_min=1,
        validation_pass_count_min=0,
        promote_count_min=0,
        candidate_missing_rate_max=1.0,
        needs_more_optimization_rate_max=1.0,
    ),
}

RELEASE_P0_TESTS = [
    "tests/test_web_server_runtime_and_bool.py",
    "tests/test_web_server_security.py",
    "tests/test_runtime_api_contract.py",
    "tests/test_web_server_stateless_runtime.py",
    "tests/test_web_server_contract_headers.py",
    "tests/test_web_training_lab_api.py",
    "tests/test_web_governance_api.py",
    "tests/test_v2_web_managers_api.py",
    "tests/test_control_plane_api.py",
    "tests/test_train_event_stream.py",
    "tests/test_runtime_service.py",
    "tests/test_deploy_topology_assets.py",
    "tests/test_gunicorn_conf.py",
]

RELEASE_P1_TESTS = [
    "tests/test_web_server_data_api.py",
    "tests/test_web_server_memory_api.py",
    "tests/test_control_plane.py",
    "tests/test_train_control_plane_bootstrap.py",
    "tests/test_runtime_read_routes.py",
    "tests/test_v2_contracts.py",
    "tests/test_environment_bootstrap_assets.py",
]

COMMANDER_BRAIN_INTEGRATION_TESTS = [
    "tests/test_brain_runtime.py",
    "tests/test_brain_scheduler.py",
    "tests/test_brain_extensions.py",
    "tests/test_commander_cli_view.py",
    "tests/test_commander_agent_validation.py",
    "tests/test_commander_transcript_golden.py",
    "tests/test_commander_mutating_workflow_golden.py",
    "tests/test_commander_direct_planner_golden.py",
    "tests/test_commander_unified_entry.py",
]

PERFORMANCE_REGRESSION_TESTS = [
    "tests/test_brain_extensions.py",
    "tests/test_memory.py",
    "tests/test_factors_and_indicators_suite.py",
    "tests/test_market_data_ingestion.py",
    "tests/test_data_unification.py",
    "tests/test_training_persistence_boundary.py",
    "tests/test_release_management_suite.py",
]


def _python_module_cmd(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def bundle_catalog() -> dict[str, ReleaseVerificationBundle]:
    return {
        "p0": ReleaseVerificationBundle(
            name="p0",
            description="Primary web/runtime split-topology regression bundle.",
            tests=RELEASE_P0_TESTS,
        ),
        "p1": ReleaseVerificationBundle(
            name="p1",
            description="Secondary web/runtime regression and control-plane extension bundle.",
            tests=RELEASE_P1_TESTS,
        ),
        "commander-brain": ReleaseVerificationBundle(
            name="commander-brain",
            description="Higher-level commander and brain integration regression bundle.",
            tests=COMMANDER_BRAIN_INTEGRATION_TESTS,
        ),
        "performance-regression": ReleaseVerificationBundle(
            name="performance-regression",
            description="Focused performance, ingestion, and release artifact regression bundle.",
            tests=PERFORMANCE_REGRESSION_TESTS,
        ),
        "all": ReleaseVerificationBundle(
            name="all",
            description="Union of p0, p1, commander-brain, and performance-regression verification bundles.",
            tests=[
                *RELEASE_P0_TESTS,
                *RELEASE_P1_TESTS,
                *COMMANDER_BRAIN_INTEGRATION_TESTS,
                *PERFORMANCE_REGRESSION_TESTS,
            ],
        ),
    }


def build_bundle_command(bundle_name: str) -> list[str]:
    catalog = bundle_catalog()
    if bundle_name not in catalog:
        supported = ", ".join(sorted(catalog))
        raise ValueError(f"unsupported bundle: {bundle_name}. expected one of: {supported}")
    return _python_module_cmd("pytest", "-q", *catalog[bundle_name].tests)


def _required_artifact_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "run_report": run_dir / "run_report.json",
        "report_json": run_dir / "release_gate_divergence_report.json",
        "report_markdown": run_dir / "release_gate_divergence_report.md",
        "runtime_generations_dir": run_dir / "runtime_generations",
    }


def _required_artifact_present(name: str, path: Path) -> bool:
    if name.endswith("_dir"):
        return path.is_dir()
    return path.exists()


def _artifact_completeness(summary: dict[str, Any], run_dir: Path) -> float:
    required_paths = _required_artifact_paths(run_dir)
    required_present = sum(
        1
        for name, path in required_paths.items()
        if _required_artifact_present(name, path)
    )
    required_ratio = required_present / max(1, len(required_paths))

    successful_cycles = int(dict(summary.get("window") or {}).get("successful_cycles") or 0)
    cycle_count = len(list(summary.get("cycles") or []))
    if successful_cycles <= 0:
        cycle_ratio = 1.0
    else:
        cycle_ratio = min(1.0, cycle_count / successful_cycles)
    return round(min(required_ratio, cycle_ratio), 4)


def shadow_gate_profile_catalog() -> dict[str, ShadowGateThresholds]:
    return dict(SHADOW_GATE_PROFILE_CATALOG)


def resolve_shadow_gate_thresholds(profile: str) -> ShadowGateThresholds:
    normalized = str(profile or "strict").strip().lower() or "strict"
    if normalized not in SHADOW_GATE_PROFILE_CATALOG:
        supported = ", ".join(sorted(SHADOW_GATE_PROFILE_CATALOG))
        raise ValueError(f"unsupported shadow gate profile: {profile}. expected one of: {supported}")
    return SHADOW_GATE_PROFILE_CATALOG[normalized]


def apply_shadow_gate_threshold_overrides(
    thresholds: ShadowGateThresholds,
    *,
    successful_cycles_min: int | None = None,
    unexpected_reject_count_max: int | None = None,
    governance_blocked_count_max: int | None = None,
    validation_pass_count_min: int | None = None,
    promote_count_min: int | None = None,
    candidate_missing_rate_max: float | None = None,
    needs_more_optimization_rate_max: float | None = None,
    artifact_completeness_min: float | None = None,
) -> ShadowGateThresholds:
    overrides: dict[str, int | float] = {}
    if successful_cycles_min is not None:
        overrides["successful_cycles_min"] = int(successful_cycles_min)
    if unexpected_reject_count_max is not None:
        overrides["unexpected_reject_count_max"] = int(unexpected_reject_count_max)
    if governance_blocked_count_max is not None:
        overrides["governance_blocked_count_max"] = int(governance_blocked_count_max)
    if validation_pass_count_min is not None:
        overrides["validation_pass_count_min"] = int(validation_pass_count_min)
    if promote_count_min is not None:
        overrides["promote_count_min"] = int(promote_count_min)
    if candidate_missing_rate_max is not None:
        overrides["candidate_missing_rate_max"] = float(candidate_missing_rate_max)
    if needs_more_optimization_rate_max is not None:
        overrides["needs_more_optimization_rate_max"] = float(needs_more_optimization_rate_max)
    if artifact_completeness_min is not None:
        overrides["artifact_completeness_min"] = float(artifact_completeness_min)
    if not overrides:
        return thresholds
    return replace(thresholds, **overrides)


def _research_feedback_gate_contract_ready(
    research_feedback_gate: dict[str, Any],
) -> bool:
    active = bool(research_feedback_gate.get("active", False))
    if active:
        return True
    passed = bool(research_feedback_gate.get("passed", False))
    reason = str(research_feedback_gate.get("reason") or "").strip()
    return passed and reason == "requested_regime_feedback_unavailable"


def evaluate_release_shadow_gate(
    run_dir: str | Path,
    *,
    thresholds: ShadowGateThresholds | None = None,
    profile: str = "strict",
    label: str | None = None,
) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    resolved_thresholds = thresholds or resolve_shadow_gate_thresholds(profile)
    summary = summarize_release_gate_run(root, label=label)
    write_release_gate_report(root, summary)
    runtime_generations_dir = root / "runtime_generations"
    legacy_generations_root = root / "data" / "evolution" / "generations"
    legacy_generation_paths = [
        path
        for path in legacy_generations_root.rglob("*")
        if path.is_file()
    ]

    window = dict(summary.get("window") or {})
    new_governance = dict(summary.get("new_governance") or {})
    positive_evidence = dict(summary.get("positive_evidence") or {})
    artifact_completeness = _artifact_completeness(summary, root)
    run_status = str(window.get("status") or "").strip()
    freeze_gate_evaluation = dict(summary.get("freeze_gate_evaluation") or {})
    research_feedback_gate = dict(freeze_gate_evaluation.get("research_feedback_gate") or {})
    research_feedback_gate_active = bool(research_feedback_gate.get("active", False))
    research_feedback_gate_passed = bool(research_feedback_gate.get("passed", False))
    research_feedback_gate_reason = str(research_feedback_gate.get("reason") or "").strip()
    research_feedback_gate_contract_ready = _research_feedback_gate_contract_ready(
        research_feedback_gate
    )

    checks = {
        "run_status": run_status in set(resolved_thresholds.allowed_statuses),
        "successful_cycles": int(window.get("successful_cycles") or 0)
        >= resolved_thresholds.successful_cycles_min,
        "unexpected_reject_count": int(new_governance.get("unexpected_reject_count") or 0)
        <= resolved_thresholds.unexpected_reject_count_max,
        "governance_blocked_count": int(new_governance.get("governance_blocked_count") or 0)
        <= resolved_thresholds.governance_blocked_count_max,
        "validation_pass_count": int(positive_evidence.get("validation_pass_count") or 0)
        >= resolved_thresholds.validation_pass_count_min,
        "promote_count": int(positive_evidence.get("promote_count") or 0)
        >= resolved_thresholds.promote_count_min,
        "candidate_missing_rate": float(new_governance.get("candidate_missing_rate") or 0.0)
        <= resolved_thresholds.candidate_missing_rate_max,
        "needs_more_optimization_rate": float(new_governance.get("needs_more_optimization_rate") or 0.0)
        <= resolved_thresholds.needs_more_optimization_rate_max,
        "artifact_completeness": artifact_completeness >= resolved_thresholds.artifact_completeness_min,
        "runtime_generations_dir": runtime_generations_dir.is_dir(),
        "legacy_generation_paths": len(legacy_generation_paths) == 0,
    }
    if resolved_thresholds.research_feedback_gate_required:
        checks["research_feedback_gate_contract_ready"] = research_feedback_gate_contract_ready
    if resolved_thresholds.research_feedback_gate_must_pass:
        checks["research_feedback_gate_passed"] = research_feedback_gate_passed
    return {
        "run_dir": str(root),
        "profile": resolved_thresholds.profile,
        "thresholds": {
            "allowed_statuses": list(resolved_thresholds.allowed_statuses),
            "successful_cycles_min": resolved_thresholds.successful_cycles_min,
            "unexpected_reject_count_max": resolved_thresholds.unexpected_reject_count_max,
            "governance_blocked_count_max": resolved_thresholds.governance_blocked_count_max,
            "validation_pass_count_min": resolved_thresholds.validation_pass_count_min,
            "promote_count_min": resolved_thresholds.promote_count_min,
            "candidate_missing_rate_max": resolved_thresholds.candidate_missing_rate_max,
            "needs_more_optimization_rate_max": resolved_thresholds.needs_more_optimization_rate_max,
            "artifact_completeness_min": resolved_thresholds.artifact_completeness_min,
            "research_feedback_gate_required": resolved_thresholds.research_feedback_gate_required,
            "research_feedback_gate_must_pass": resolved_thresholds.research_feedback_gate_must_pass,
        },
        "metrics": {
            "run_status": run_status,
            "successful_cycles": int(window.get("successful_cycles") or 0),
            "unexpected_reject_count": int(new_governance.get("unexpected_reject_count") or 0),
            "governance_blocked_count": int(new_governance.get("governance_blocked_count") or 0),
            "validation_pass_count": int(positive_evidence.get("validation_pass_count") or 0),
            "promote_count": int(positive_evidence.get("promote_count") or 0),
            "candidate_missing_rate": float(new_governance.get("candidate_missing_rate") or 0.0),
            "needs_more_optimization_rate": float(new_governance.get("needs_more_optimization_rate") or 0.0),
            "artifact_completeness": artifact_completeness,
            "runtime_generations_dir": str(runtime_generations_dir),
            "legacy_generation_paths": [str(path) for path in legacy_generation_paths],
            "research_feedback_gate_active": research_feedback_gate_active,
            "research_feedback_gate_passed": research_feedback_gate_passed,
            "research_feedback_gate_reason": research_feedback_gate_reason,
            "research_feedback_gate_contract_ready": research_feedback_gate_contract_ready,
            "research_feedback_sample_count": int(research_feedback_gate.get("sample_count") or 0),
            "research_feedback_bias": str(research_feedback_gate.get("bias") or ""),
        },
        "freeze_gate_evaluation": freeze_gate_evaluation,
        "checks": checks,
        "passed": all(checks.values()),
    }


def release_verification_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run named release verification bundles.")
    parser.add_argument(
        "--bundle",
        choices=sorted(bundle_catalog()),
        default="p0",
        help="Named verification bundle to run.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print bundle names and tests without running them.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    catalog = bundle_catalog()
    bundle = catalog[args.bundle]
    if args.list:
        print(f"{bundle.name}: {bundle.description}")
        for test_path in bundle.tests:
            print(test_path)
        return 0

    command = build_bundle_command(args.bundle)
    print(f"==> release-verification:{bundle.name}")
    print(f"==> command: {' '.join(command)}")
    result = subprocess.run(command, cwd=PROJECT_ROOT)
    return result.returncode


def release_shadow_gate_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Stage 4 release shadow gate thresholds.")
    parser.add_argument("--run-dir", required=True, help="Shadow gate output directory.")
    parser.add_argument("--label", default=None, help="Optional label for regenerated reports.")
    parser.add_argument(
        "--profile",
        choices=sorted(shadow_gate_profile_catalog()),
        default="strict",
        help="Threshold profile. Use smoke for deterministic pipeline validation; strict for manual sign-off.",
    )
    parser.add_argument("--successful-cycles-min", type=int, default=None, help="Override minimum successful cycles.")
    parser.add_argument(
        "--unexpected-reject-count-max",
        type=int,
        default=None,
        help="Override maximum unexpected reject count.",
    )
    parser.add_argument(
        "--governance-blocked-count-max",
        type=int,
        default=None,
        help="Override maximum governance blocked count.",
    )
    parser.add_argument(
        "--validation-pass-count-min",
        type=int,
        default=None,
        help="Override minimum validation pass count.",
    )
    parser.add_argument("--promote-count-min", type=int, default=None, help="Override minimum promote count.")
    parser.add_argument(
        "--candidate-missing-rate-max",
        type=float,
        default=None,
        help="Override maximum candidate missing rate.",
    )
    parser.add_argument(
        "--needs-more-optimization-rate-max",
        type=float,
        default=None,
        help="Override maximum needs-more-optimization rate.",
    )
    parser.add_argument(
        "--artifact-completeness-min",
        type=float,
        default=None,
        help="Override minimum artifact completeness ratio.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    thresholds = apply_shadow_gate_threshold_overrides(
        resolve_shadow_gate_thresholds(args.profile),
        successful_cycles_min=args.successful_cycles_min,
        unexpected_reject_count_max=args.unexpected_reject_count_max,
        governance_blocked_count_max=args.governance_blocked_count_max,
        validation_pass_count_min=args.validation_pass_count_min,
        promote_count_min=args.promote_count_min,
        candidate_missing_rate_max=args.candidate_missing_rate_max,
        needs_more_optimization_rate_max=args.needs_more_optimization_rate_max,
        artifact_completeness_min=args.artifact_completeness_min,
    )
    result = evaluate_release_shadow_gate(
        args.run_dir,
        thresholds=thresholds,
        profile=args.profile,
        label=args.label,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


def _build_main_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Release utilities for verification bundles and Stage 4 shadow-gate validation."
    )
    subparsers = parser.add_subparsers(dest="command")

    verify = subparsers.add_parser("verify", help="Run named release verification bundles.")
    verify.add_argument(
        "--bundle",
        choices=sorted(bundle_catalog()),
        default="p0",
        help="Named verification bundle to run.",
    )
    verify.add_argument(
        "--list",
        action="store_true",
        help="Print bundle names and tests without running them.",
    )

    shadow = subparsers.add_parser("shadow-gate", help="Validate Stage 4 release shadow gate thresholds.")
    shadow.add_argument("--run-dir", required=True, help="Shadow gate output directory.")
    shadow.add_argument("--label", default=None, help="Optional label for regenerated reports.")
    shadow.add_argument(
        "--profile",
        choices=sorted(shadow_gate_profile_catalog()),
        default="strict",
        help="Threshold profile. Use smoke for deterministic pipeline validation; strict for manual sign-off.",
    )
    shadow.add_argument("--successful-cycles-min", type=int, default=None, help="Override minimum successful cycles.")
    shadow.add_argument(
        "--unexpected-reject-count-max",
        type=int,
        default=None,
        help="Override maximum unexpected reject count.",
    )
    shadow.add_argument(
        "--governance-blocked-count-max",
        type=int,
        default=None,
        help="Override maximum governance blocked count.",
    )
    shadow.add_argument(
        "--validation-pass-count-min",
        type=int,
        default=None,
        help="Override minimum validation pass count.",
    )
    shadow.add_argument("--promote-count-min", type=int, default=None, help="Override minimum promote count.")
    shadow.add_argument(
        "--candidate-missing-rate-max",
        type=float,
        default=None,
        help="Override maximum candidate missing rate.",
    )
    shadow.add_argument(
        "--needs-more-optimization-rate-max",
        type=float,
        default=None,
        help="Override maximum needs-more-optimization rate.",
    )
    shadow.add_argument(
        "--artifact-completeness-min",
        type=float,
        default=None,
        help="Override minimum artifact completeness ratio.",
    )
    return parser


def _normalize_main_argv(argv: Sequence[str] | None) -> list[str]:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        return ["verify"]
    if args[0] in {"verify", "shadow-gate", "-h", "--help"}:
        return args
    return ["verify", *args]


def main(argv: Sequence[str] | None = None) -> int:
    normalized = _normalize_main_argv(argv)
    parser = _build_main_parser()
    args = parser.parse_args(normalized)

    if args.command == "shadow-gate":
        return release_shadow_gate_main(
            [
                "--run-dir",
                args.run_dir,
                "--profile",
                args.profile,
                *(
                    ["--successful-cycles-min", str(args.successful_cycles_min)]
                    if args.successful_cycles_min is not None
                    else []
                ),
                *(
                    ["--unexpected-reject-count-max", str(args.unexpected_reject_count_max)]
                    if args.unexpected_reject_count_max is not None
                    else []
                ),
                *(
                    ["--governance-blocked-count-max", str(args.governance_blocked_count_max)]
                    if args.governance_blocked_count_max is not None
                    else []
                ),
                *(
                    ["--validation-pass-count-min", str(args.validation_pass_count_min)]
                    if args.validation_pass_count_min is not None
                    else []
                ),
                *(["--promote-count-min", str(args.promote_count_min)] if args.promote_count_min is not None else []),
                *(
                    ["--candidate-missing-rate-max", str(args.candidate_missing_rate_max)]
                    if args.candidate_missing_rate_max is not None
                    else []
                ),
                *(
                    ["--needs-more-optimization-rate-max", str(args.needs_more_optimization_rate_max)]
                    if args.needs_more_optimization_rate_max is not None
                    else []
                ),
                *(
                    ["--artifact-completeness-min", str(args.artifact_completeness_min)]
                    if args.artifact_completeness_min is not None
                    else []
                ),
                *(["--label", args.label] if args.label else []),
            ]
        )
    if args.command == "verify":
        verify_argv = [*(["--bundle", args.bundle] if args.bundle else [])]
        if args.list:
            verify_argv.append("--list")
        return release_verification_main(verify_argv)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
