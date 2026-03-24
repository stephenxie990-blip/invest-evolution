#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence, TypedDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from invest_evolution.application.release import bundle_catalog, build_bundle_command  # noqa: E402

MANUAL_SIGNOFF_DOC = PROJECT_ROOT / "docs" / "RELEASE_READINESS.md"


class ShadowProfileDefaults(TypedDict):
    cycles: int
    successful_cycles_target: int
    llm_dry_run: bool


SHADOW_PROFILE_DEFAULTS: dict[str, ShadowProfileDefaults] = {
    "strict": {
        "cycles": 120,
        "successful_cycles_target": 30,
        "llm_dry_run": False,
    },
    "smoke": {
        "cycles": 5,
        "successful_cycles_target": 5,
        "llm_dry_run": True,
    },
}


@dataclass(frozen=True)
class ReleaseReadinessStep:
    name: str
    command: list[str]


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be >= 0")
    return parsed


def _python_cmd(*args: str) -> list[str]:
    return [sys.executable, *args]


def _default_shadow_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return PROJECT_ROOT / "outputs" / f"release_shadow_gate_{stamp}"


def _resolve_shadow_profile_defaults(
    *,
    shadow_profile: str,
    shadow_cycles: int | None,
    shadow_successful_cycles_target: int | None,
    shadow_llm_dry_run: bool,
) -> tuple[int, int, bool]:
    normalized_profile = str(shadow_profile or "smoke").strip().lower() or "smoke"
    if normalized_profile not in SHADOW_PROFILE_DEFAULTS:
        supported = ", ".join(sorted(SHADOW_PROFILE_DEFAULTS))
        raise ValueError(f"unsupported shadow profile: {shadow_profile}. expected one of: {supported}")
    defaults = SHADOW_PROFILE_DEFAULTS[normalized_profile]
    resolved_cycles = int(shadow_cycles if shadow_cycles is not None else defaults["cycles"])
    resolved_target = int(
        shadow_successful_cycles_target
        if shadow_successful_cycles_target is not None
        else defaults["successful_cycles_target"]
    )
    resolved_llm_dry_run = bool(shadow_llm_dry_run or defaults["llm_dry_run"])
    return resolved_cycles, resolved_target, resolved_llm_dry_run


def build_release_readiness_steps(
    *,
    include_p1: bool = True,
    include_commander_brain: bool = False,
    include_performance_regression: bool = True,
    include_shadow_gate: bool = False,
    shadow_profile: str = "smoke",
    shadow_output_dir: str | Path | None = None,
    shadow_cycles: int | None = None,
    shadow_successful_cycles_target: int | None = None,
    shadow_force_full_cycles: bool = True,
    shadow_mock: bool = False,
    shadow_llm_dry_run: bool = False,
    shadow_verify_successful_cycles_min: int | None = None,
    shadow_verify_validation_pass_count_min: int | None = None,
    shadow_verify_promote_count_min: int | None = None,
) -> list[ReleaseReadinessStep]:
    steps = [
        ReleaseReadinessStep(
            name="stage0-env-bootstrap-check",
            command=_python_cmd("scripts/bootstrap_env.py", "--check"),
        ),
        ReleaseReadinessStep(
            name="stage0-verification-smoke",
            command=_python_cmd("scripts/run_verification_smoke.py"),
        ),
        ReleaseReadinessStep(
            name="stage1-freeze-gate-quick",
            command=_python_cmd("-m", "invest_evolution.application.freeze_gate", "--mode", "quick"),
        ),
        ReleaseReadinessStep(
            name="stage2-p0-web-runtime-bundle",
            command=build_bundle_command("p0"),
        ),
    ]
    if include_p1:
        steps.append(
            ReleaseReadinessStep(
                name="stage2-p1-web-runtime-bundle",
                command=build_bundle_command("p1"),
            )
        )
    if include_commander_brain:
        steps.append(
            ReleaseReadinessStep(
                name="stage2-commander-brain-integration-bundle",
                command=build_bundle_command("commander-brain"),
            )
        )
    if include_performance_regression:
        steps.append(
            ReleaseReadinessStep(
                name="stage2-performance-regression-bundle",
                command=build_bundle_command("performance-regression"),
            )
        )
    if include_shadow_gate:
        shadow_dir = Path(shadow_output_dir or _default_shadow_output_dir()).expanduser().resolve()
        resolved_cycles, resolved_target, resolved_llm_dry_run = _resolve_shadow_profile_defaults(
            shadow_profile=shadow_profile,
            shadow_cycles=shadow_cycles,
            shadow_successful_cycles_target=shadow_successful_cycles_target,
            shadow_llm_dry_run=shadow_llm_dry_run,
        )
        shadow_command = _python_cmd(
            "scripts/run_release_gate_stage1.py",
            "--cycles",
            str(resolved_cycles),
            "--successful-cycles-target",
            str(resolved_target),
            "--output",
            str(shadow_dir),
            "--label",
            f"stage4_release_shadow_{shadow_profile}",
        )
        if shadow_force_full_cycles:
            shadow_command.append("--force-full-cycles")
        if shadow_mock:
            shadow_command.append("--mock")
        if resolved_llm_dry_run:
            shadow_command.append("--llm-dry-run")
        steps.extend(
            [
                ReleaseReadinessStep(
                    name=f"stage4-release-shadow-{shadow_profile}-run",
                    command=shadow_command,
                ),
                ReleaseReadinessStep(
                    name=f"stage4-release-shadow-{shadow_profile}-verify",
                    command=[
                        *_python_cmd(
                        "-m",
                        "invest_evolution.application.release",
                        "shadow-gate",
                        "--run-dir",
                        str(shadow_dir),
                        "--profile",
                        shadow_profile,
                        ),
                        *(
                            ["--successful-cycles-min", str(shadow_verify_successful_cycles_min)]
                            if shadow_verify_successful_cycles_min is not None
                            else []
                        ),
                        *(
                            ["--validation-pass-count-min", str(shadow_verify_validation_pass_count_min)]
                            if shadow_verify_validation_pass_count_min is not None
                            else []
                        ),
                        *(
                            ["--promote-count-min", str(shadow_verify_promote_count_min)]
                            if shadow_verify_promote_count_min is not None
                            else []
                        ),
                    ],
                ),
            ]
        )
    return steps


def run_release_readiness_gate(
    *,
    include_p1: bool = True,
    include_commander_brain: bool = False,
    include_performance_regression: bool = True,
    include_shadow_gate: bool = False,
    shadow_profile: str = "smoke",
    shadow_output_dir: str | Path | None = None,
    shadow_cycles: int | None = None,
    shadow_successful_cycles_target: int | None = None,
    shadow_force_full_cycles: bool = True,
    shadow_mock: bool = False,
    shadow_llm_dry_run: bool = False,
    shadow_verify_successful_cycles_min: int | None = None,
    shadow_verify_validation_pass_count_min: int | None = None,
    shadow_verify_promote_count_min: int | None = None,
) -> int:
    steps = build_release_readiness_steps(
        include_p1=include_p1,
        include_commander_brain=include_commander_brain,
        include_performance_regression=include_performance_regression,
        include_shadow_gate=include_shadow_gate,
        shadow_profile=shadow_profile,
        shadow_output_dir=shadow_output_dir,
        shadow_cycles=shadow_cycles,
        shadow_successful_cycles_target=shadow_successful_cycles_target,
        shadow_force_full_cycles=shadow_force_full_cycles,
        shadow_mock=shadow_mock,
        shadow_llm_dry_run=shadow_llm_dry_run,
        shadow_verify_successful_cycles_min=shadow_verify_successful_cycles_min,
        shadow_verify_validation_pass_count_min=shadow_verify_validation_pass_count_min,
        shadow_verify_promote_count_min=shadow_verify_promote_count_min,
    )
    for step in steps:
        print(f"==> {step.name}")
        result = subprocess.run(step.command, cwd=PROJECT_ROOT)
        if result.returncode != 0:
            print(f"release readiness gate failed at step: {step.name}", file=sys.stderr)
            return result.returncode or 1
    print(f"manual sign-off checklist: {MANUAL_SIGNOFF_DOC}")
    print("release readiness automated stages passed")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the automated release-readiness stages before Stage 4 shadow qualification and Stage 5 manual sign-off."
    )
    parser.add_argument("--list", action="store_true", help="Print planned commands without executing them.")
    parser.add_argument("--no-p1", action="store_true", help="Skip the wider P1 web/runtime regression bundle.")
    parser.add_argument(
        "--include-commander-brain",
        action="store_true",
        help="Include the larger commander/brain integration regression bundle.",
    )
    parser.add_argument(
        "--no-performance-regression",
        action="store_true",
        help="Skip the focused performance and release-artifact regression bundle.",
    )
    parser.add_argument(
        "--include-shadow-gate",
        action="store_true",
        help="Include Stage 4 release shadow verification. Defaults to the smoke profile for deterministic automation.",
    )
    parser.add_argument(
        "--shadow-profile",
        choices=sorted(SHADOW_PROFILE_DEFAULTS),
        default="smoke",
        help="Stage 4 shadow profile. smoke is automation-safe; strict is the real manual sign-off threshold.",
    )
    parser.add_argument(
        "--shadow-output-dir",
        default=str(_default_shadow_output_dir()),
        help="Fresh output directory for the Stage 4 shadow gate run.",
    )
    parser.add_argument(
        "--shadow-cycles",
        type=int,
        default=None,
        help="Maximum cycles for the Stage 4 shadow gate run. Defaults depend on the selected profile.",
    )
    parser.add_argument(
        "--shadow-successful-cycles-target",
        type=int,
        default=None,
        help="Successful cycle target for the Stage 4 shadow gate run. Defaults depend on the selected profile.",
    )
    parser.add_argument(
        "--no-shadow-force-full-cycles",
        action="store_true",
        help="Allow the shadow gate run to stop early if freeze gate triggers.",
    )
    parser.add_argument("--shadow-mock", action="store_true", help="Run the Stage 4 shadow gate in mock data mode.")
    parser.add_argument(
        "--shadow-llm-dry-run",
        action="store_true",
        help="Run the Stage 4 shadow gate without real LLM calls.",
    )
    parser.add_argument(
        "--shadow-verify-successful-cycles-min",
        type=_non_negative_int,
        default=None,
        help="Override shadow-gate verify minimum successful cycles for probe runs.",
    )
    parser.add_argument(
        "--shadow-verify-validation-pass-count-min",
        type=_non_negative_int,
        default=None,
        help="Override shadow-gate verify minimum validation pass count for probe runs.",
    )
    parser.add_argument(
        "--shadow-verify-promote-count-min",
        type=_non_negative_int,
        default=None,
        help="Override shadow-gate verify minimum promote count for probe runs.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    supported_bundles = bundle_catalog()
    for bundle_name in ("p0", "p1", "commander-brain", "performance-regression"):
        if bundle_name not in supported_bundles:
            raise ValueError(f"missing required release verification bundle: {bundle_name}")

    steps = build_release_readiness_steps(
        include_p1=not args.no_p1,
        include_commander_brain=args.include_commander_brain,
        include_performance_regression=not args.no_performance_regression,
        include_shadow_gate=args.include_shadow_gate,
        shadow_profile=args.shadow_profile,
        shadow_output_dir=args.shadow_output_dir,
        shadow_cycles=args.shadow_cycles,
        shadow_successful_cycles_target=args.shadow_successful_cycles_target,
        shadow_force_full_cycles=not args.no_shadow_force_full_cycles,
        shadow_mock=args.shadow_mock,
        shadow_llm_dry_run=args.shadow_llm_dry_run,
        shadow_verify_successful_cycles_min=args.shadow_verify_successful_cycles_min,
        shadow_verify_validation_pass_count_min=args.shadow_verify_validation_pass_count_min,
        shadow_verify_promote_count_min=args.shadow_verify_promote_count_min,
    )
    if args.list:
        for step in steps:
            print(f"{step.name}: {' '.join(step.command)}")
        print(f"manual-signoff-checklist: {MANUAL_SIGNOFF_DOC}")
        return 0
    return run_release_readiness_gate(
        include_p1=not args.no_p1,
        include_commander_brain=args.include_commander_brain,
        include_performance_regression=not args.no_performance_regression,
        include_shadow_gate=args.include_shadow_gate,
        shadow_profile=args.shadow_profile,
        shadow_output_dir=args.shadow_output_dir,
        shadow_cycles=args.shadow_cycles,
        shadow_successful_cycles_target=args.shadow_successful_cycles_target,
        shadow_force_full_cycles=not args.no_shadow_force_full_cycles,
        shadow_mock=args.shadow_mock,
        shadow_llm_dry_run=args.shadow_llm_dry_run,
        shadow_verify_successful_cycles_min=args.shadow_verify_successful_cycles_min,
        shadow_verify_validation_pass_count_min=args.shadow_verify_validation_pass_count_min,
        shadow_verify_promote_count_min=args.shadow_verify_promote_count_min,
    )


if __name__ == "__main__":
    raise SystemExit(main())
