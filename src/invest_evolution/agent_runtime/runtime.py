"""Agent runtime core, guardrails, lifecycle, and stable facade imports."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import textwrap
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from invest_evolution.agent_runtime.planner import (
    MUTATING_DEFAULT_REASON_CODES,
    RISK_LEVEL_HIGH,
    RISK_LEVEL_LOW,
    RISK_LEVEL_MEDIUM,
    TRAINING_DEFAULT_REASON_CODES,
    build_config_overview_plan,
    build_data_focus_plan,
    build_model_analytics_plan,
    build_mutating_task_bus,
    build_playbook_plan,
    build_plugin_reload_plan,
    build_readonly_task_bus,
    build_runtime_status_plan,
    build_training_execution_plan,
    build_training_history_plan,
)
from invest_evolution.agent_runtime.presentation import (
    BrainHumanReadablePresenter,
    StructuredOutputAdapter,
    build_bounded_entrypoint,
    build_bounded_orchestration,
    build_bounded_policy,
    build_protocol_response,
)
from invest_evolution.agent_runtime.tools import (
    BrainToolRegistry,
    RUNTIME_OBSERVABILITY_TOOL_NAMES,
    ToolArgumentParseError,
    parse_tool_args,
)
from invest_evolution.common.utils import LLMGateway, LLMGatewayError, LLMUnavailableError


def _dict_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _list_payload(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return list(value)


def _find_placeholder_paths(value: Any, *, prefix: str = "") -> list[str]:
    matches: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            matches.extend(_find_placeholder_paths(item, prefix=path))
        return matches
    if isinstance(value, list):
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            matches.extend(_find_placeholder_paths(item, prefix=path))
        return matches
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("<") and text.endswith(">"):
            return [prefix or "value"]
    return matches


def _flatten_leaf_paths(value: Any, *, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_flatten_leaf_paths(item, prefix=path))
        return paths
    if isinstance(value, list):
        paths: list[str] = []
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            paths.extend(_flatten_leaf_paths(item, prefix=path))
        return paths
    return [prefix or "value"]


def _flatten_leaf_entries(value: Any, *, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        entries: list[tuple[str, Any]] = []
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            entries.extend(_flatten_leaf_entries(item, prefix=path))
        return entries
    if isinstance(value, list):
        entries: list[tuple[str, Any]] = []
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            entries.extend(_flatten_leaf_entries(item, prefix=path))
        return entries
    return [(prefix or "value", value)]


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class _ProtocolEmissionContext:
    payload: dict[str, Any]
    entrypoint: dict[str, Any]
    task_bus: dict[str, Any]
    intent: str
    operation: str


def enforce_path_within_root(root: Path, candidate: str | Path) -> Path:
    base_root = root.expanduser().resolve()
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = (base_root / path).resolve()
    else:
        path = path.resolve()
    try:
        path.relative_to(base_root)
    except ValueError as exc:
        raise ValueError(f"{path} escapes {base_root}") from exc
    return path


class RuntimeGuardrails:
    _PATCH_TOOLS = {
        "invest_control_plane_update",
        "invest_runtime_paths_update",
        "invest_evolution_config_update",
    }
    _MUTATING_TOOLS = _PATCH_TOOLS | {
        "invest_training_plan_create",
        "invest_training_plan_execute",
        "invest_data_download",
        "invest_train",
        "invest_agent_prompts_update",
    }
    _PATCH_SCOPE_RULES = {
        "invest_control_plane_update": {
            "forbidden_fragments": (
                "training_output_dir",
                "runtime_paths",
                "workspace",
                "output_dir",
                "simulation_days",
                "stop_loss_pct",
                "take_profit_pct",
            ),
            "reason_code": "cross_scope_patch",
            "message": "Guardrail blocked a control plane patch that belongs to runtime paths or evolution config scope.",
        },
        "invest_runtime_paths_update": {
            "forbidden_fragments": (
                "llm",
                "bindings",
                "provider",
                "api_key",
                "governance",
                "default_manager_id",
                "stop_loss_pct",
            ),
            "reason_code": "cross_scope_patch",
            "message": "Guardrail blocked a runtime paths patch that belongs to control plane or evolution config scope.",
        },
        "invest_evolution_config_update": {
            "forbidden_fragments": (
                "llm",
                "bindings",
                "provider",
                "api_key",
                "training_output_dir",
                "workspace",
                "bridge_inbox",
                "bridge_outbox",
            ),
            "reason_code": "cross_scope_patch",
            "message": "Guardrail blocked an evolution config patch that belongs to control plane or runtime paths scope.",
        },
    }

    def evaluate(self, *, tool_name: str, params: dict[str, Any]) -> dict[str, Any] | None:
        name = str(tool_name or "")
        payload = _dict_payload(params)
        placeholder_paths = _find_placeholder_paths(payload)
        if name in self._MUTATING_TOOLS and placeholder_paths:
            return self._blocked_payload(
                tool_name=name,
                reason_codes=["placeholder_arguments"],
                message="Guardrail blocked placeholder arguments in a mutating tool call.",
                details={"paths": placeholder_paths},
            )

        if name in self._PATCH_TOOLS and not _dict_payload(payload.get("patch")):
            return self._blocked_payload(
                tool_name=name,
                reason_codes=["empty_patch"],
                message="Guardrail blocked an empty patch for a high-risk config update.",
                details={"required": ["patch"]},
            )

        plan_id = str(payload.get("plan_id") or "").strip()
        if name == "invest_training_plan_execute" and not plan_id:
            return self._blocked_payload(
                tool_name=name,
                reason_codes=["missing_plan_id"],
                message="Guardrail blocked training plan execution without a concrete plan_id.",
                details={"required": ["plan_id"]},
            )

        patch_violation = self._evaluate_patch_scope(tool_name=name, payload=payload)
        if patch_violation is not None:
            return patch_violation

        runtime_paths_violation = (
            self._evaluate_runtime_paths_patch(payload=payload)
            if name == "invest_runtime_paths_update"
            else None
        )
        if runtime_paths_violation is not None:
            return runtime_paths_violation

        agent_prompt_violation = (
            self._evaluate_agent_prompt_update(payload=payload)
            if name == "invest_agent_prompts_update"
            else None
        )
        if agent_prompt_violation is not None:
            return agent_prompt_violation

        plan_violation = self._evaluate_training_plan_create(payload=payload) if name == "invest_training_plan_create" else None
        if plan_violation is not None:
            return plan_violation
        return None

    def _evaluate_patch_scope(self, *, tool_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        rule = self._PATCH_SCOPE_RULES.get(tool_name)
        patch = _dict_payload(payload.get("patch"))
        if not rule or not patch:
            return None
        leaf_paths = _flatten_leaf_paths(patch)
        forbidden = tuple(str(item) for item in rule.get("forbidden_fragments") or ())
        offending = [
            path for path in leaf_paths
            if any(fragment in path for fragment in forbidden)
        ]
        if not offending:
            return None
        return self._blocked_payload(
            tool_name=tool_name,
            reason_codes=[str(rule.get("reason_code") or "cross_scope_patch")],
            message=str(rule.get("message") or "Guardrail blocked a cross-scope patch."),
            details={"paths": offending},
        )

    def _evaluate_training_plan_create(self, *, payload: dict[str, Any]) -> dict[str, Any] | None:
        rounds = _safe_int(payload.get("rounds"))
        protocol = _dict_payload(payload.get("protocol"))
        dataset = _dict_payload(payload.get("dataset"))
        llm = _dict_payload(payload.get("llm"))

        min_history_days = _safe_int(dataset.get("min_history_days"))
        simulation_days = _safe_int(dataset.get("simulation_days"))
        if (
            min_history_days is not None
            and simulation_days is not None
            and min_history_days < simulation_days
        ):
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["history_window_too_short"],
                message="Guardrail blocked a training plan whose min_history_days is shorter than simulation_days.",
                details={
                    "min_history_days": min_history_days,
                    "simulation_days": simulation_days,
                },
            )

        review_window = _dict_payload(protocol.get("review_window"))
        review_mode = str(review_window.get("mode") or "").strip().lower()
        if review_mode and review_mode not in {"single_cycle", "rolling"}:
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["invalid_review_window_mode"],
                message="Guardrail blocked a training plan with an unsupported review_window.mode.",
                details={"review_window_mode": review_mode},
            )

        review_size = _safe_int(review_window.get("size") or review_window.get("window"))
        if review_mode == "single_cycle" and review_size not in (None, 1):
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["single_cycle_window_size_conflict"],
                message="Guardrail blocked a single_cycle review window whose size is not 1.",
                details={"review_window_mode": review_mode, "review_window_size": review_size},
            )
        if rounds is not None and review_size is not None and review_size > max(1, rounds):
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["review_window_exceeds_rounds"],
                message="Guardrail blocked a training plan whose review window exceeds total rounds.",
                details={"rounds": rounds, "review_window_size": review_size},
            )

        cutoff_policy = _dict_payload(protocol.get("cutoff_policy"))
        cutoff_mode = str(cutoff_policy.get("mode") or "").strip().lower()
        if cutoff_mode and cutoff_mode not in {"random", "fixed", "rolling", "sequence", "regime_balanced"}:
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["invalid_cutoff_policy_mode"],
                message="Guardrail blocked a training plan with an unsupported cutoff_policy.mode.",
                details={"cutoff_policy_mode": cutoff_mode},
            )
        if cutoff_mode == "fixed" and not str(cutoff_policy.get("date") or "").strip():
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["fixed_cutoff_missing_date"],
                message="Guardrail blocked a fixed cutoff policy without a concrete date.",
                details={"cutoff_policy_mode": cutoff_mode, "required": ["cutoff_policy.date"]},
            )
        if cutoff_mode == "sequence" and not _list_payload(cutoff_policy.get("dates")):
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["sequence_cutoff_missing_dates"],
                message="Guardrail blocked a sequence cutoff policy without any scheduled dates.",
                details={"cutoff_policy_mode": cutoff_mode, "required": ["cutoff_policy.dates"]},
            )
        if cutoff_mode == "regime_balanced" and not _list_payload(cutoff_policy.get("target_regimes")):
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["regime_balanced_missing_targets"],
                message="Guardrail blocked a regime_balanced cutoff policy without target regimes.",
                details={"cutoff_policy_mode": cutoff_mode, "required": ["cutoff_policy.target_regimes"]},
            )

        llm_mode = str(llm.get("mode") or "").strip().lower()
        dry_run = bool(llm.get("dry_run", False))
        if llm_mode and llm_mode not in {"live", "dry_run"}:
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["invalid_llm_mode"],
                message="Guardrail blocked a training plan with an unsupported llm.mode.",
                details={"llm_mode": llm_mode},
            )
        if dry_run and llm_mode == "live":
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["conflicting_llm_mode"],
                message="Guardrail blocked a training plan with conflicting llm.dry_run=true and llm.mode=live.",
                details={"llm": {"mode": llm_mode, "dry_run": dry_run}},
            )

        promotion_gate = _dict_payload(_dict_payload(payload.get("optimization")).get("promotion_gate"))
        min_samples = _safe_int(promotion_gate.get("min_samples"))
        if rounds is not None and min_samples is not None and min_samples > max(1, rounds):
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["promotion_gate_exceeds_rounds"],
                message="Guardrail blocked a training plan whose promotion gate min_samples exceeds total rounds.",
                details={"rounds": rounds, "min_samples": min_samples},
            )
        return None

    def _evaluate_runtime_paths_patch(self, *, payload: dict[str, Any]) -> dict[str, Any] | None:
        patch = _dict_payload(payload.get("patch"))
        if not patch:
            return None
        blank_paths: list[str] = []
        non_absolute_paths: list[str] = []
        for leaf_path, value in _flatten_leaf_entries(patch):
            text = str(value or "").strip()
            if not text:
                blank_paths.append(leaf_path)
                continue
            if not Path(text).is_absolute():
                non_absolute_paths.append(leaf_path)
        if blank_paths:
            return self._blocked_payload(
                tool_name="invest_runtime_paths_update",
                reason_codes=["blank_runtime_path"],
                message="Guardrail blocked a runtime paths patch with blank path values.",
                details={"paths": blank_paths},
            )
        if non_absolute_paths:
            return self._blocked_payload(
                tool_name="invest_runtime_paths_update",
                reason_codes=["relative_runtime_path"],
                message="Guardrail blocked a runtime paths patch with non-absolute path values.",
                details={"paths": non_absolute_paths},
            )
        return None

    def _evaluate_agent_prompt_update(self, *, payload: dict[str, Any]) -> dict[str, Any] | None:
        agent_name = str(payload.get("name") or "").strip()
        system_prompt = str(payload.get("system_prompt") or "").strip()
        if not agent_name:
            return self._blocked_payload(
                tool_name="invest_agent_prompts_update",
                reason_codes=["missing_agent_name"],
                message="Guardrail blocked an agent prompt update without a concrete agent name.",
                details={"required": ["name"]},
            )
        if not system_prompt:
            return self._blocked_payload(
                tool_name="invest_agent_prompts_update",
                reason_codes=["empty_system_prompt"],
                message="Guardrail blocked an agent prompt update with an empty system_prompt.",
                details={"required": ["system_prompt"]},
            )
        return None

    @staticmethod
    def _blocked_payload(
        *,
        tool_name: str,
        reason_codes: list[str],
        message: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "status": "guardrail_blocked",
            "message": message,
            "guardrails": {
                "decision": "block",
                "tool_name": tool_name,
                "reason_codes": list(reason_codes),
                "details": dict(details),
            },
        }


__all__ = ["RuntimeGuardrails"]
logger = logging.getLogger(__name__)
MILLISECONDS_PER_SECOND = 1000


def _now_ms() -> int:
    return int(time.time() * MILLISECONDS_PER_SECOND)


@dataclass
class CronJob:
    id: str
    name: str
    message: str
    every_sec: int
    enabled: bool = True
    deliver: bool = False
    channel: str = "cli"
    to: str = "commander"
    next_run_at_ms: int = 0
    last_run_at_ms: int = 0
    last_status: str = ""
    last_error: str = ""
    created_at_ms: int = field(default_factory=_now_ms)
    updated_at_ms: int = field(default_factory=_now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CronService:
    """Simple interval cron service for local runtime."""

    def __init__(self, store_path: Path):
        self.store_path = Path(store_path)
        self.jobs: list[CronJob] = []
        self.on_job: Optional[Callable[[CronJob], Awaitable[Optional[str]]]] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._load()
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Cron service started with %s jobs", len(self.jobs))

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self._save()

    def status(self) -> dict[str, Any]:
        next_wake = None
        enabled_jobs = [job for job in self.jobs if job.enabled]
        if enabled_jobs:
            next_wake = min(job.next_run_at_ms for job in enabled_jobs)
        return {
            "enabled": self._running,
            "jobs": len(enabled_jobs),
            "next_wake_at_ms": next_wake,
        }

    def list_jobs(self) -> list[CronJob]:
        return list(self.jobs)

    def add_job(
        self,
        name: str,
        message: str,
        every_sec: int,
        deliver: bool = False,
        channel: str = "cli",
        to: str = "commander",
    ) -> CronJob:
        now = _now_ms()
        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            message=message,
            every_sec=max(1, int(every_sec)),
            deliver=deliver,
            channel=channel,
            to=to,
            next_run_at_ms=now + max(1, int(every_sec)) * MILLISECONDS_PER_SECOND,
        )
        self.jobs.append(job)
        self._save()
        return job

    def remove_job(self, job_id: str) -> bool:
        before = len(self.jobs)
        self.jobs = [job for job in self.jobs if job.id != job_id]
        changed = len(self.jobs) < before
        if changed:
            self._save()
        return changed

    async def _run_loop(self) -> None:
        try:
            while self._running:
                now = _now_ms()
                for job in self.jobs:
                    if not job.enabled:
                        continue
                    if job.next_run_at_ms and now >= job.next_run_at_ms:
                        await self._execute(job)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            return

    async def _execute(self, job: CronJob) -> None:
        now = _now_ms()
        job.last_run_at_ms = now
        job.updated_at_ms = now

        try:
            if self.on_job:
                await self.on_job(job)
            job.last_status = "ok"
            job.last_error = ""
        except Exception as exc:
            job.last_status = "error"
            job.last_error = str(exc)
            logger.exception("Cron job failed: %s", job.id)

        job.next_run_at_ms = _now_ms() + max(1, int(job.every_sec)) * MILLISECONDS_PER_SECOND
        self._save()

    def _load(self) -> None:
        if not self.store_path.exists():
            self.jobs = []
            return

        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._quarantine_corrupt_store(exc)
            self.jobs = []
            return

        raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []
        jobs: list[CronJob] = []
        invalid_jobs = 0
        for item in raw_jobs:
            try:
                job = CronJob(
                    id=item["id"],
                    name=item["name"],
                    message=item.get("message", ""),
                    every_sec=max(1, int(item.get("every_sec", 3600))),
                    enabled=bool(item.get("enabled", True)),
                    deliver=bool(item.get("deliver", False)),
                    channel=item.get("channel", "cli"),
                    to=item.get("to", "commander"),
                    next_run_at_ms=int(item.get("next_run_at_ms") or (_now_ms() + 3600 * MILLISECONDS_PER_SECOND)),
                    last_run_at_ms=int(item.get("last_run_at_ms", 0)),
                    last_status=item.get("last_status", ""),
                    last_error=item.get("last_error", ""),
                    created_at_ms=int(item.get("created_at_ms", _now_ms())),
                    updated_at_ms=int(item.get("updated_at_ms", _now_ms())),
                )
                jobs.append(job)
            except (KeyError, TypeError, ValueError) as exc:
                invalid_jobs += 1
                logger.warning("Skipping invalid cron job payload from %s: %s", self.store_path, exc)
                continue
        if invalid_jobs:
            logger.warning("Skipped %s invalid cron jobs while loading %s", invalid_jobs, self.store_path)
        self.jobs = jobs

    def _quarantine_corrupt_store(self, exc: Exception) -> None:
        logger.error("Failed to load cron store %s: %s", self.store_path, exc)
        try:
            quarantine_path = self.store_path.with_name(
                f"{self.store_path.stem}.corrupt.{int(time.time())}{self.store_path.suffix}"
            )
            self.store_path.rename(quarantine_path)
            logger.warning("Moved corrupt cron store to %s", quarantine_path)
        except OSError as move_exc:
            logger.warning("Failed to quarantine corrupt cron store %s: %s", self.store_path, move_exc)

    def _save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "jobs": [job.to_dict() for job in self.jobs],
        }
        self.store_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class HeartbeatService:
    """Simple periodic heartbeat executor."""

    def __init__(
        self,
        workspace: Path,
        on_execute: Optional[Callable[[str], Awaitable[str]]] = None,
        on_notify: Optional[Callable[[str], Awaitable[None]]] = None,
        interval_s: int = 1800,
        enabled: bool = True,
    ):
        self.workspace = Path(workspace)
        self.on_execute = on_execute
        self.on_notify = on_notify
        self.interval_s = max(1, int(interval_s))
        self.enabled = enabled

        self._running = False
        self._task: Optional[asyncio.Task] = None

    @property
    def heartbeat_file(self) -> Path:
        return self.workspace / "HEARTBEAT.md"

    async def start(self) -> None:
        if not self.enabled:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Heartbeat started, interval=%ss", self.interval_s)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
        except asyncio.CancelledError:
            return

    async def _tick(self) -> None:
        if not self.heartbeat_file.exists():
            return

        try:
            content = self.heartbeat_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read heartbeat file %s: %s", self.heartbeat_file, exc)
            return

        tasks = self._extract_tasks(content)
        if not tasks:
            return

        if not self.on_execute:
            return

        try:
            result = await self.on_execute(tasks)
            if result and self.on_notify:
                await self.on_notify(result)
        except Exception:
            logger.exception("Heartbeat task execution failed")

    @staticmethod
    def _extract_tasks(content: str) -> str:
        lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            lines.append(stripped)
        return "\n".join(lines).strip()

# ---------------------------------------------------------------------------
# Session and runtime
# ---------------------------------------------------------------------------

@dataclass
class BrainSession:
    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    updated_at: datetime = field(default_factory=datetime.now)


class BrainRuntime:
    """Lightweight local agent loop with tool-calling."""

    def __init__(
        self,
        workspace: Path,
        model: str,
        api_key: str = "",
        api_base: str = "",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        max_iterations: int = 20,
        memory_window: int = 120,
        system_prompt_provider: Optional[Callable[[], str]] = None,
    ):
        self.workspace = Path(workspace)
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_iterations = max_iterations
        self.memory_window = memory_window
        self.system_prompt_provider = system_prompt_provider

        self.tools = BrainToolRegistry()
        self.sessions: dict[str, BrainSession] = {}
        self.guardrails = RuntimeGuardrails()
        self.structured_output = StructuredOutputAdapter()
        self.governance_metrics: dict[str, dict[str, Any]] = {
            "guardrails": {"block_count": 0, "last_reason_codes": []},
            "structured_output": {"validated_count": 0, "repaired_count": 0, "fallback_count": 0, "degraded_count": 0},
        }
        self.gateway = LLMGateway(
            model=self.model,
            api_key=self.api_key,
            api_base=self.api_base,
            timeout=120,
            max_retries=2,
        )

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    async def close(self) -> None:
        """Reserved for future resource cleanup."""
        return

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        session = self.sessions.setdefault(session_key, BrainSession(key=session_key))

        # Allow explicit tool execution without LLM: /tool <name> {json}
        explicit = await self._try_explicit_tool(content)
        if explicit is not None:
            explicit = self._wrap_tool_response(
                explicit,
                user_goal=content,
                tool_names=[self._extract_explicit_tool_name(content)],
                mode="explicit_tool",
            )
            self._append_turn(session, {"role": "user", "content": content}, {"role": "assistant", "content": explicit})
            return explicit

        builtin = await self._try_builtin_intent(content)
        if builtin is not None:
            self._append_turn(session, {"role": "user", "content": content}, {"role": "assistant", "content": builtin})
            return builtin

        if not self.gateway.available:
            fallback = (
                "LLM is not configured. Check control-plane default bindings or provider api_key, "
                "or use explicit tool calls: "
                "`/tool invest_quick_status {}` / `/tool invest_train {\"rounds\":1,\"mock\":true}`"
            )
            self._append_turn(session, {"role": "user", "content": content}, {"role": "assistant", "content": fallback})
            return fallback

        messages = self._build_messages(session, content)
        result = await self._run_loop(messages, user_goal=content, on_progress=on_progress)
        self._append_turn(session, {"role": "user", "content": content}, {"role": "assistant", "content": result})
        return result

    def _build_messages(self, session: BrainSession, content: str) -> list[dict[str, Any]]:
        system_prompt = self._system_prompt()
        history = session.messages[-self.memory_window:]
        return [
            {"role": "system", "content": system_prompt},
            *history,
            {
                "role": "user",
                "content": (
                    f"[Runtime]\nTime: {datetime.now().isoformat()}\n"
                    f"Workspace: {self.workspace}\n\n{content}"
                ),
            },
        ]

    def _system_prompt(self) -> str:
        if self.system_prompt_provider:
            return self.system_prompt_provider()
        return textwrap.dedent(
            """\
            You are the Investment Evolution runtime agent.
            Your job is to help the user inspect status, playbooks, memory, and training through registered tools.

            Operating rules:
            1. Ground every factual statement in either the user message, prior tool outputs, or runtime metadata in this chat.
            2. Never invent tool results, file contents, market facts, config values, or training outcomes.
            3. When a tool is needed, call the single most relevant tool first and pass a valid JSON object as arguments.
            4. If a request can be answered from existing context, answer directly without unnecessary tool calls.
            5. If arguments are uncertain or a tool is unsuitable, say so explicitly instead of guessing.

            Response rules:
            - Be concise, operational, and audit-friendly.
            - Distinguish facts, risks, and next actions when helpful.
            - For state-changing actions, rely on tool outputs rather than promises or speculation.
            - Do not emit fake function calls, placeholder JSON, or unsupported claims.
            """
        )

    async def _run_loop(
        self,
        messages: list[dict[str, Any]],
        user_goal: str = "",
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        final = ""
        tool_trace: list[dict[str, Any]] = []

        for _ in range(max(1, self.max_iterations)):
            defs = self.tools.get_definitions()
            try:
                response = await self.gateway.acompletion_raw(
                    messages=self._sanitize_messages(messages),
                    temperature=self.temperature,
                    max_tokens=max(1, self.max_tokens),
                    tools=defs or None,
                    tool_choice="auto",
                )
            except LLMUnavailableError:
                return "LLM is not configured. Check control-plane default bindings or provider api_key, or use explicit tool calls."
            except LLMGatewayError as exc:
                logger.warning("brain runtime llm error: %s", exc)
                return "LLM request failed. Try explicit tool mode or retry later."
            choice = response.choices[0].message

            tool_calls = getattr(choice, "tool_calls", None) or []
            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": choice.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tool_calls
                        ],
                    }
                )

                for tc in tool_calls:
                    if on_progress:
                        try:
                            await on_progress(f"tool: {tc.function.name}")
                        except Exception as exc:
                            logger.warning("BrainRuntime progress callback failed for tool %s: %s", tc.function.name, exc)
                    args: dict[str, Any] = {}
                    try:
                        args = self._parse_tool_args(tc.function.arguments)
                    except ToolArgumentParseError as exc:
                        result = f"Error: invalid tool arguments for {tc.function.name}: {exc}"
                    else:
                        guardrail_payload = self.guardrails.evaluate(tool_name=tc.function.name, params=args)
                        if guardrail_payload is not None:
                            self._record_guardrail_event(guardrail_payload)
                            result = json.dumps(guardrail_payload, ensure_ascii=False)
                        else:
                            result = await self.tools.execute(tc.function.name, args)
                    tool_trace.append({"action": {"tool": tc.function.name, "args": args}})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.function.name,
                            "content": result,
                        }
                    )
                continue

            final = (choice.content or "").strip()
            break

        if not final:
            final = "I could not produce a final response within iteration limits."
        return self._wrap_tool_response(final, user_goal=user_goal, tool_names=[str(item.get("action", {}).get("tool") or "") for item in tool_trace], mode="llm_tool_loop", tool_calls=tool_trace)

    @staticmethod
    def _sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clean: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "assistant" and "tool_calls" in msg and content is None:
                content = ""
            clean_msg = {
                "role": role,
                "content": content,
            }
            if "tool_calls" in msg:
                clean_msg["tool_calls"] = msg["tool_calls"]
            if "tool_call_id" in msg:
                clean_msg["tool_call_id"] = msg["tool_call_id"]
            if "name" in msg:
                clean_msg["name"] = msg["name"]
            clean.append(clean_msg)
        return clean

    @staticmethod
    def _parse_tool_args(raw: Any) -> dict[str, Any]:
        return parse_tool_args(raw)

    async def _try_explicit_tool(self, content: str) -> Optional[str]:
        stripped = content.strip()
        if not stripped.startswith("/tool "):
            return None

        # format: /tool <name> <json-args>
        parts = stripped.split(" ", 2)
        if len(parts) < 2:
            return "Error: Usage /tool <name> {json-args}"

        name = parts[1].strip()
        args: dict[str, Any] = {}
        if len(parts) >= 3:
            raw = parts[2].strip()
            if raw:
                try:
                    args = self._parse_tool_args(raw)
                except ToolArgumentParseError as exc:
                    return f"Error: invalid tool arguments for {name}: {exc}"
        guardrail_payload = self.guardrails.evaluate(tool_name=name, params=args)
        if guardrail_payload is not None:
            self._record_guardrail_event(guardrail_payload)
            return json.dumps(guardrail_payload, ensure_ascii=False)
        return await self.tools.execute(name, args)

    @staticmethod
    def _tool_trace(tool_names: list[str]) -> list[dict[str, Any]]:
        return [{"action": {"tool": name, "args": {}}} for name in tool_names if name]

    @staticmethod
    def _extract_explicit_tool_name(content: str) -> str:
        stripped = str(content or "").strip()
        if not stripped.startswith("/tool "):
            return ""
        parts = stripped.split(" ", 2)
        return parts[1].strip() if len(parts) >= 2 else ""

    @staticmethod
    def _try_parse_json_object(raw: Any) -> dict[str, Any] | None:
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        if not text or not text.startswith("{"):
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _is_mutating_tool(name: str) -> bool:
        tool = str(name or "")
        if tool in {
            "invest_train",
            "invest_data_download",
            "invest_plugins_reload",
            "invest_cron_add",
            "invest_cron_remove",
            "invest_control_plane_update",
            "invest_runtime_paths_update",
            "invest_evolution_config_update",
            "invest_training_plan_create",
            "invest_training_plan_execute",
            "invest_agent_prompts_update",
        }:
            return True
        return tool.endswith("_update")

    def _risk_level_for_tools(self, tool_names: list[str]) -> str:
        names = {str(name or "") for name in tool_names}
        if any(name in {"invest_train", "invest_control_plane_update", "invest_runtime_paths_update", "invest_evolution_config_update"} for name in names):
            return RISK_LEVEL_HIGH
        if any(self._is_mutating_tool(name) for name in names):
            return RISK_LEVEL_MEDIUM
        return RISK_LEVEL_LOW

    @staticmethod
    def _intent_for_tools(tool_names: list[str]) -> str:
        names = {str(name or "") for name in tool_names if str(name or "")}
        if "invest_ask_stock" in names:
            return "stock_analysis"
        if any(name.startswith("invest_data_") for name in names):
            return "data_operations"
        if any(name in {
            "invest_train",
            "invest_quick_test",
            "invest_training_plan_create",
            "invest_training_plan_list",
            "invest_training_plan_execute",
            "invest_training_runs_list",
            "invest_training_evaluations_list",
            "invest_training_lab_summary",
        } for name in names):
            return "training_execution"
        if any(name in {
            "invest_control_plane_get",
            "invest_control_plane_update",
            "invest_runtime_paths_get",
            "invest_runtime_paths_update",
            "invest_evolution_config_get",
            "invest_evolution_config_update",
            "invest_agent_prompts_list",
            "invest_agent_prompts_update",
        } for name in names):
            return "config_management"
        if any(name in RUNTIME_OBSERVABILITY_TOOL_NAMES for name in names):
            return "runtime_observability"
        if any(name in {"invest_list_playbooks", "invest_reload_playbooks"} for name in names):
            return "playbook_inventory"
        if "invest_stock_strategies" in names:
            return "strategy_inventory"
        if any(name in {"invest_leaderboard", "invest_managers", "invest_allocator", "invest_governance_preview"} for name in names):
            return "model_analytics"
        if any(name in {"invest_memory_search", "invest_memory_list", "invest_memory_get"} for name in names):
            return "memory_lookup"
        if any(name in {"invest_cron_add", "invest_cron_list", "invest_cron_remove"} for name in names):
            return "scheduler_management"
        if "invest_plugins_reload" in names:
            return "plugin_management"
        return "runtime_tooling"

    @staticmethod
    def _extract_rounds_from_goal(user_goal: str, default: int = 1) -> int:
        text = str(user_goal or "")
        match = re.search(r"(\d+)\s*(轮|次)", text)
        return max(1, int(match.group(1))) if match else max(1, int(default or 1))

    @staticmethod
    def _infer_mock_from_goal(user_goal: str) -> bool:
        low = str(user_goal or "").lower()
        return any(token in low for token in ["mock", "演示", "测试", "dry-run", "quick", "快速测试"])

    @staticmethod
    def _infer_refresh_from_goal(user_goal: str) -> bool:
        low = str(user_goal or "").lower()
        return any(token in low for token in ["refresh", "刷新", "重新检查", "重算"])

    @staticmethod
    def _infer_stock_strategy_from_goal(user_goal: str) -> str:
        low = str(user_goal or "").lower()
        if any(token in low for token in ["趋势跟随", "trend following", "趋势策略"]):
            return "trend_following"
        return "chan_theory"

    @staticmethod
    def _infer_days_from_goal(user_goal: str, default: int = 60) -> int:
        text = str(user_goal or "")
        match = re.search(r"(\d{2,4})\s*(?:个)?(?:交易)?(?:日|天)", text)
        if not match:
            return max(30, int(default or 60))
        return max(30, min(500, int(match.group(1))))

    @staticmethod
    def _infer_data_focus_from_goal(user_goal: str) -> str:
        low = str(user_goal or "").lower()
        if any(token in low for token in ["资金流", "capital flow"]):
            return "capital_flow"
        if any(token in low for token in ["龙虎榜", "dragon tiger"]):
            return "dragon_tiger"
        if any(token in low for token in ["60m", "60分钟", "60 分钟", "分时", "intraday"]):
            return "intraday_60m"
        if any(token in low for token in ["下载", "同步", "拉取", "download", "sync"]):
            return "download"
        return "status"

    @staticmethod
    def _infer_config_focus_from_goal(user_goal: str) -> str:
        low = str(user_goal or "").lower()
        if any(token in low for token in ["prompt", "提示词", "agent prompt", "角色提示"]):
            return "prompts"
        if any(token in low for token in ["路径", "workspace", "输出目录", "runtime path"]):
            return "paths"
        if any(token in low for token in ["控制面", "control plane", "模型绑定", "llm 绑定", "绑定"]):
            return "control_plane"
        return "evolution"

    def _recommended_plan_for_intent(
        self,
        *,
        intent: str,
        tool_names: list[str],
        writes_state: bool,
        user_goal: str,
    ) -> list[dict[str, Any]]:
        rounds = self._extract_rounds_from_goal(user_goal, default=1)
        mock = self._infer_mock_from_goal(user_goal)
        refresh = self._infer_refresh_from_goal(user_goal)
        strategy = self._infer_stock_strategy_from_goal(user_goal)
        days = self._infer_days_from_goal(user_goal, default=60)
        data_focus = self._infer_data_focus_from_goal(user_goal)
        config_focus = self._infer_config_focus_from_goal(user_goal)

        if intent in {"training_execution", "training_lab_summary"}:
            if writes_state:
                return build_training_execution_plan(rounds=rounds, mock=mock, user_goal=user_goal, limit=5)
            return build_training_history_plan(limit=5)
        if intent in {"config_management", "config_overview", "config_prompts", "runtime_paths"}:
            if intent == "config_overview":
                return build_config_overview_plan(config_focus=config_focus, writes_state=writes_state)
            if config_focus == "prompts":
                plan = [{"tool": "invest_agent_prompts_list", "args": {}}]
                if writes_state:
                    plan.append({"tool": "invest_agent_prompts_update", "args": {"name": "<agent>", "system_prompt": "<prompt>"}})
                return plan
            if config_focus == "paths":
                plan = [{"tool": "invest_runtime_paths_get", "args": {}}]
                if writes_state:
                    plan.extend([
                        {"tool": "invest_runtime_paths_update", "args": {"patch": {"<path_key>": "<new_path>"}, "confirm": False}},
                        {"tool": "invest_runtime_diagnostics", "args": {"event_limit": 50, "memory_limit": 20}},
                    ])
                return plan
            if config_focus == "control_plane":
                plan = [{"tool": "invest_control_plane_get", "args": {}}]
                if writes_state:
                    plan.extend([
                        {"tool": "invest_control_plane_update", "args": {"patch": {"<section>": "<value>"}, "confirm": False}},
                        {"tool": "invest_runtime_diagnostics", "args": {"event_limit": 50, "memory_limit": 20}},
                    ])
                return plan
            plan = [
                {"tool": "invest_evolution_config_get", "args": {}},
                {"tool": "invest_control_plane_get", "args": {}},
                {"tool": "invest_runtime_paths_get", "args": {}},
            ]
            if writes_state:
                plan.extend([
                    {"tool": "invest_evolution_config_update", "args": {"patch": {"<param>": "<value>"}, "confirm": False}},
                    {"tool": "invest_runtime_diagnostics", "args": {"event_limit": 50, "memory_limit": 20}},
                ])
            return plan
        if intent in {"data_operations", "data_status"}:
            return build_data_focus_plan(data_focus=data_focus, refresh=refresh, writes_state=writes_state)
        if intent == "stock_analysis":
            return [
                {"tool": "invest_stock_strategies", "args": {}},
                {"tool": "invest_ask_stock", "args": {"query": user_goal or "<stock>", "question": user_goal or "<question>", "strategy": strategy, "days": days}},
            ]
        if intent in {"runtime_observability", "runtime_status", "runtime_status_and_training", "runtime_diagnostics", "config_risk_diagnostics"}:
            primary_tool = "invest_deep_status" if any(token in str(user_goal or "") for token in ["深度", "slow", "deep"]) else "invest_quick_status"
            return build_runtime_status_plan(
                primary_tool=primary_tool,
                detail_mode="fast",
                summary_limit=100,
                event_limit=50,
                memory_limit=20,
            )
        if intent == "playbook_inventory":
            return build_playbook_plan("playbook_inventory")
        if intent == "strategy_inventory":
            return [{"tool": "invest_stock_strategies", "args": {}}]
        if intent == "model_analytics":
            return build_model_analytics_plan("model_analytics")
        if intent == "memory_lookup":
            return [
                {"tool": "invest_memory_search", "args": {"query": user_goal or "", "limit": 10}},
                {"tool": "invest_memory_list", "args": {"query": user_goal or "", "limit": 10}},
            ]
        if intent == "scheduler_management":
            plan = [{"tool": "invest_cron_list", "args": {}}]
            if writes_state:
                plan.append({"tool": "invest_cron_add", "args": {"message": user_goal or "<job>", "cron": "0 * * * *"}})
            return plan
        if intent == "plugin_management":
            return [
                *build_plugin_reload_plan(),
                {"tool": "invest_runtime_diagnostics", "args": {"event_limit": 50, "memory_limit": 20}},
            ]
        return [{"tool": name, "args": {}} for name in tool_names]

    @staticmethod
    def _payload_coverage(payload: dict[str, Any]) -> dict[str, Any] | None:
        direct = payload.get("coverage")
        if isinstance(direct, dict):
            return dict(direct)
        orchestration = dict(payload.get("orchestration") or {})
        coverage = orchestration.get("coverage")
        if isinstance(coverage, dict):
            return dict(coverage)
        return None

    @staticmethod
    def _payload_artifacts(payload: dict[str, Any], *, base: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(base)
        direct = payload.get("artifacts")
        if isinstance(direct, dict):
            artifacts.update(direct)
        training_lab = payload.get("training_lab")
        if isinstance(training_lab, dict):
            artifacts.setdefault("training_lab", training_lab)
        return artifacts

    def _build_task_bus_for_payload(
        self,
        *,
        payload: dict[str, Any],
        user_goal: str,
        intent: str,
        operation: str,
        mode: str,
        tool_names: list[str],
        writes_state: bool,
        risk_level: str,
        recommended_plan: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        reasons: list[str] | None = None,
        artifacts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = str(payload.get("status", "ok"))
        requires_confirmation = status == "confirmation_required"
        decision = "confirm" if requires_confirmation else "allow"
        normalized_artifacts = self._payload_artifacts(payload, base=dict(artifacts or {}))
        coverage = self._payload_coverage(payload)
        builder = build_mutating_task_bus if writes_state else build_readonly_task_bus
        kwargs = {
            "intent": intent,
            "operation": operation,
            "user_goal": user_goal,
            "mode": mode,
            "available_tools": self.tools.tool_names,
            "recommended_plan": list(recommended_plan),
            "tool_calls": list(tool_calls),
            "artifacts": normalized_artifacts,
            "coverage": coverage,
            "status": status,
        }
        if writes_state:
            kwargs.update(
                {
                    "risk_level": risk_level,
                    "decision": decision,
                    "requires_confirmation": requires_confirmation,
                    "reasons": list(reasons or MUTATING_DEFAULT_REASON_CODES),
                }
            )
        return builder(**kwargs)

    def _resolve_protocol_task_bus(
        self,
        *,
        payload: dict[str, Any],
        user_goal: str,
        intent: str,
        operation: str,
        mode: str,
        tool_names: list[str],
        writes_state: bool,
        risk_level: str,
        recommended_plan: list[dict[str, Any]] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasons: list[str] | None = None,
        artifacts: dict[str, Any] | None = None,
        use_existing_task_bus: bool = True,
    ) -> dict[str, Any]:
        existing = _dict_payload(payload.get("task_bus"))
        if use_existing_task_bus and existing:
            return existing
        plan = list(
            recommended_plan
            or self._recommended_plan_for_intent(
                intent=intent,
                tool_names=tool_names,
                writes_state=writes_state,
                user_goal=user_goal,
            )
        )
        calls = list(tool_calls or self._tool_trace(tool_names))
        return self._build_task_bus_for_payload(
            payload=payload,
            user_goal=user_goal,
            intent=intent,
            operation=operation,
            mode=mode,
            tool_names=tool_names,
            writes_state=writes_state,
            risk_level=risk_level,
            recommended_plan=plan,
            tool_calls=calls,
            reasons=reasons,
            artifacts=artifacts,
        )

    def _build_protocol_emission_context(
        self,
        *,
        payload: dict[str, Any],
        entrypoint: dict[str, Any],
        user_goal: str,
        intent: str,
        operation: str,
        mode: str,
        tool_names: list[str],
        writes_state: bool,
        risk_level: str,
        recommended_plan: list[dict[str, Any]] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasons: list[str] | None = None,
        artifacts: dict[str, Any] | None = None,
        use_existing_task_bus: bool = True,
    ) -> _ProtocolEmissionContext:
        return _ProtocolEmissionContext(
            payload=dict(payload),
            entrypoint=dict(entrypoint),
            task_bus=self._resolve_protocol_task_bus(
                payload=payload,
                user_goal=user_goal,
                intent=intent,
                operation=operation,
                mode=mode,
                tool_names=tool_names,
                writes_state=writes_state,
                risk_level=risk_level,
                recommended_plan=recommended_plan,
                tool_calls=tool_calls,
                reasons=reasons,
                artifacts=artifacts,
                use_existing_task_bus=use_existing_task_bus,
            ),
            intent=intent,
            operation=operation,
        )

    def _wrap_tool_response(
        self,
        result: Any,
        *,
        user_goal: str,
        tool_names: list[str],
        mode: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> str:
        names = [str(name or "") for name in tool_names if str(name or "")]
        if not names or not any(name.startswith("invest_") for name in names):
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
        payload = self._try_parse_json_object(result) or {}
        status = str(payload.get("status", "ok")) if payload else "ok"
        writes_state = any(self._is_mutating_tool(name) for name in names)
        risk_level = self._risk_level_for_tools(names)
        if not payload:
            payload = {"status": status, "reply": str(result)}
        elif "reply" not in payload and not payload.get("message") and isinstance(result, str) and not result.strip().startswith("{"):
            payload["reply"] = str(result)
        primary_tool = names[-1] if names else ""
        if primary_tool:
            payload = self.structured_output.normalize_payload(tool_name=primary_tool, payload=payload)
            self._record_structured_output_status(payload)
            payload.setdefault("governance_metrics", {})
            payload["governance_metrics"]["runtime"] = self._governance_metrics_snapshot()
        inferred_intent = self._intent_for_tools(names)
        entrypoint = {
            "kind": "commander_tool_runtime",
            "resolver": f"BrainRuntime.{mode}",
            "mode": mode,
            "meeting_path": False,
            "tools": names,
            "intent": inferred_intent,
        }
        context = self._build_protocol_emission_context(
            payload=payload,
            entrypoint=entrypoint,
            user_goal=user_goal,
            intent=inferred_intent,
            operation=mode,
            mode=mode,
            tool_names=names,
            writes_state=writes_state,
            risk_level=risk_level,
            tool_calls=tool_calls,
            artifacts={"workspace": str(self.workspace), "tools": names, "mode": mode},
        )
        return self._emit_protocol_payload(context)

    def _record_guardrail_event(self, payload: dict[str, Any]) -> None:
        guardrails = _dict_payload(payload.get("guardrails"))
        metrics = self.governance_metrics.setdefault("guardrails", {"block_count": 0, "last_reason_codes": []})
        metrics["block_count"] = int(metrics.get("block_count", 0) or 0) + 1
        metrics["last_reason_codes"] = list(guardrails.get("reason_codes") or [])

    def _record_structured_output_status(self, payload: dict[str, Any]) -> None:
        structured = _dict_payload(payload.get("structured_output"))
        status = str(structured.get("status") or "")
        metrics = self.governance_metrics.setdefault(
            "structured_output",
            {"validated_count": 0, "repaired_count": 0, "fallback_count": 0, "degraded_count": 0},
        )
        if status == "validated":
            metrics["validated_count"] = int(metrics.get("validated_count", 0) or 0) + 1
        elif status == "repaired":
            metrics["repaired_count"] = int(metrics.get("repaired_count", 0) or 0) + 1
        elif status == "fallback":
            metrics["fallback_count"] = int(metrics.get("fallback_count", 0) or 0) + 1
        elif status == "fallback_degraded":
            metrics["fallback_count"] = int(metrics.get("fallback_count", 0) or 0) + 1
            metrics["degraded_count"] = int(metrics.get("degraded_count", 0) or 0) + 1

    def _governance_metrics_snapshot(self) -> dict[str, Any]:
        return {
            "guardrails": {
                "block_count": int(_dict_payload(self.governance_metrics.get("guardrails")).get("block_count", 0) or 0),
                "last_reason_codes": list(_dict_payload(self.governance_metrics.get("guardrails")).get("last_reason_codes") or []),
            },
            "structured_output": {
                "validated_count": int(_dict_payload(self.governance_metrics.get("structured_output")).get("validated_count", 0) or 0),
                "repaired_count": int(_dict_payload(self.governance_metrics.get("structured_output")).get("repaired_count", 0) or 0),
                "fallback_count": int(_dict_payload(self.governance_metrics.get("structured_output")).get("fallback_count", 0) or 0),
                "degraded_count": int(_dict_payload(self.governance_metrics.get("structured_output")).get("degraded_count", 0) or 0),
            },
        }

    @staticmethod
    def _default_protocol_text(payload: dict[str, Any]) -> str:
        return str(payload.get("message") or payload.get("reply") or "")

    def _serialize_protocol_payload(
        self,
        *,
        payload: dict[str, Any],
        entrypoint: dict[str, Any],
        task_bus: dict[str, Any],
        intent: str,
        operation: str,
    ) -> str:
        default_text = self._default_protocol_text(payload)
        wrapped_payload = build_protocol_response(
            payload=payload,
            entrypoint=entrypoint,
            task_bus=task_bus,
            default_message=default_text,
            default_reply=default_text,
        )
        wrapped_payload = self._attach_human_readable_receipt(
            wrapped_payload,
            intent=intent,
            operation=operation,
        )
        return json.dumps(wrapped_payload, ensure_ascii=False, indent=2)

    def _emit_protocol_payload(self, context: _ProtocolEmissionContext) -> str:
        return self._serialize_protocol_payload(
            payload=context.payload,
            entrypoint=context.entrypoint,
            task_bus=context.task_bus,
            intent=context.intent,
            operation=context.operation,
        )

    @staticmethod
    def _builtin_protocol_entrypoint(*, intent: str, operation: str) -> dict[str, Any]:
        return {
            "kind": "commander_builtin_intent",
            "resolver": "BrainRuntime._try_builtin_intent",
            "intent": intent,
            "operation": operation,
            "meeting_path": False,
        }

    @staticmethod
    def _build_builtin_combo_payload(
        *,
        payload_intent: str,
        runtime_intent: str,
        operation: str,
        agent_kind: str,
        tool_names: list[str],
        workflow: list[str],
        tool_catalog_scope: str,
        sections: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "intent": payload_intent,
            **sections,
            "entrypoint": build_bounded_entrypoint(
                kind="commander_builtin_workflow",
                resolver="BrainRuntime._try_builtin_intent",
                intent=runtime_intent,
                operation=operation,
                meeting_path=False,
                agent_kind=agent_kind,
            ),
            "orchestration": build_bounded_orchestration(
                mode="builtin_bounded_readonly_workflow",
                available_tools=list(tool_names),
                allowed_tools=list(tool_names),
                workflow=list(workflow),
                phase_stats={"section_count": len(sections)},
                policy=build_bounded_policy(
                    source="commander_builtin_intent",
                    agent_kind=agent_kind,
                    fixed_boundary=True,
                    fixed_workflow=True,
                    writes_state=False,
                    tool_catalog_scope=tool_catalog_scope,
                ),
            ),
        }

    def _wrap_builtin_payload(
        self,
        payload: Any,
        *,
        user_goal: str,
        intent: str,
        operation: str,
        tool_names: list[str],
        writes_state: bool = False,
        risk_level: str = RISK_LEVEL_LOW,
        recommended_plan: list[dict[str, Any]] | None = None,
        reasons: list[str] | None = None,
    ) -> str:
        if not isinstance(payload, dict):
            return json.dumps({
                "status": "ok",
                "content": payload,
                "entrypoint": {
                    "kind": "commander_builtin_intent",
                    "resolver": "BrainRuntime._try_builtin_intent",
                    "intent": intent,
                    "operation": operation,
                },
            }, ensure_ascii=False, indent=2)
        payload = dict(payload)
        entrypoint = self._builtin_protocol_entrypoint(intent=intent, operation=operation)
        context = self._build_protocol_emission_context(
            payload=payload,
            entrypoint=entrypoint,
            user_goal=user_goal,
            intent=intent,
            operation=operation,
            mode="builtin_intent",
            tool_names=tool_names,
            writes_state=writes_state,
            risk_level=risk_level,
            recommended_plan=recommended_plan,
            reasons=reasons,
            artifacts={
                "workspace": str(self.workspace),
                "intent": intent,
                "operation": operation,
            },
            use_existing_task_bus=False,
        )
        return self._emit_protocol_payload(context)

    @staticmethod
    def _runtime_state_bullets(runtime_payload: dict[str, Any]) -> list[str]:
        state = str(runtime_payload.get("state") or "unknown")
        current_task = dict(runtime_payload.get("current_task") or {})
        last_task = dict(runtime_payload.get("last_task") or {})
        bullets = [f"运行状态：{state}"]
        if current_task.get("type"):
            bullets.append(f"当前任务：{current_task.get('type')}")
        if last_task.get("type"):
            bullets.append(f"最近完成：{last_task.get('type')} / {last_task.get('status', '')}".rstrip(" /"))
        return bullets

    @staticmethod
    def _training_lab_bullets(training_lab: dict[str, Any]) -> list[str]:
        if not training_lab:
            return []
        return [
            f"训练计划：{int(training_lab.get('plan_count', 0) or 0)}",
            f"训练运行：{int(training_lab.get('run_count', 0) or 0)}",
            f"训练评估：{int(training_lab.get('evaluation_count', 0) or 0)}",
        ]

    @staticmethod
    def _truncate_text(value: Any, *, limit: int = 120) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    @staticmethod
    def _is_internal_runtime_event(event_name: Any) -> bool:
        return str(event_name or "") in {"ask_started", "ask_finished", "task_started", "task_finished"}

    @staticmethod
    def _top_event_distribution(counts: dict[str, Any], *, limit: int = 3) -> str:
        ordered = sorted(
            ((str(name), int(value or 0)) for name, value in dict(counts or {}).items()),
            key=lambda item: (-item[1], item[0]),
        )
        return "，".join(f"{name}×{count}" for name, count in ordered[:limit])

    @staticmethod
    def _event_human_label(event_name: str) -> str:
        mapping = {
            "ask_started": "对话请求开始",
            "ask_finished": "对话请求完成",
            "task_started": "运行任务开始",
            "task_finished": "运行任务完成",
            "training_started": "训练开始",
            "training_finished": "训练完成",
            "governance_started": "组合治理开始",
            "manager_activation_decided": "组合治理决策完成",
            "governance_applied": "组合治理已应用",
            "governance_blocked": "组合治理被阻断",
            "regime_classified": "市场状态识别完成",
            "cycle_start": "训练周期开始",
            "cycle_complete": "训练周期完成",
            "cycle_skipped": "训练周期被跳过",
            "agent_status": "Agent 状态更新",
            "agent_progress": "Agent 进度更新",
            "module_log": "模块日志更新",
            "meeting_speech": "会议发言更新",
            "data_download_triggered": "数据下载已触发",
            "runtime_paths_updated": "运行路径已更新",
            "evolution_config_updated": "训练配置已更新",
            "control_plane_updated": "控制面已更新",
            "agent_prompt_updated": "Agent Prompt 已更新",
        }
        return mapping.get(str(event_name or ""), str(event_name or "").replace("_", " "))

    @staticmethod
    def _event_detail_text(row: dict[str, Any]) -> str:
        payload = dict(row.get("payload") or {})
        event_name = str(row.get("event") or "")
        if event_name == "ask_started":
            channel = str(payload.get("channel") or "").strip()
            message_length = payload.get("message_length")
            details = []
            if channel:
                details.append(f"来源 {channel}")
            if message_length not in (None, ""):
                details.append(f"消息长度 {message_length}")
            if details:
                return "已接收对话请求，" + "，".join(details) + "。"
            return "已接收新的对话请求。"
        if event_name == "ask_finished":
            intent = str(payload.get("intent") or "").strip()
            status = str(payload.get("status") or "").strip()
            risk_level = str(payload.get("risk_level") or "").strip()
            details = []
            if intent:
                details.append(f"意图 {intent}")
            if status:
                details.append(f"状态 {status}")
            if risk_level:
                details.append(f"风险 {risk_level}")
            if details:
                return "对话处理结束，" + "，".join(details) + "。"
            return "对话处理结束。"
        if event_name == "task_started":
            task_type = str(payload.get("type") or "").strip()
            source = str(payload.get("source") or "").strip()
            if task_type and source:
                return f"开始执行 {task_type} 任务，来源 {source}。"
            if task_type:
                return f"开始执行 {task_type} 任务。"
        if event_name == "task_finished":
            task_type = str(payload.get("type") or "").strip()
            status = str(payload.get("status") or "").strip()
            if task_type and status:
                return f"{task_type} 任务已结束，状态 {status}。"
            if status:
                return f"运行任务已结束，状态 {status}。"
        if event_name == "manager_activation_decided":
            regime = str(payload.get("regime") or "").strip()
            dominant_manager_id = str(payload.get("dominant_manager_id") or "").strip()
            active_manager_ids = [
                str(item).strip()
                for item in list(payload.get("active_manager_ids") or [])
                if str(item).strip()
            ]
            if not active_manager_ids and dominant_manager_id:
                active_manager_ids = [dominant_manager_id]
            manager_budget_weights = {
                str(key): float(value)
                for key, value in dict(payload.get("manager_budget_weights") or {}).items()
                if str(key).strip()
            }
            details: list[str] = []
            if regime:
                details.append(f"识别为 {regime} 市场")
            if active_manager_ids:
                details.append(f"激活经理 {', '.join(active_manager_ids)}")
            if dominant_manager_id:
                details.append(f"主导经理 {dominant_manager_id}")
            if manager_budget_weights:
                details.append(
                    "预算分配 "
                    + " / ".join(
                        f"{key}:{value:.2f}"
                        for key, value in manager_budget_weights.items()
                    )
                )
            if details:
                return "，".join(details) + "。"
        if event_name == "governance_applied":
            dominant_manager_id = str(
                payload.get("dominant_manager_id")
                or ""
            ).strip()
            active_manager_ids = [
                str(item).strip()
                for item in list(payload.get("active_manager_ids") or [])
                if str(item).strip()
            ]
            if not active_manager_ids and dominant_manager_id:
                active_manager_ids = [dominant_manager_id]
            if active_manager_ids and dominant_manager_id:
                return f"治理已应用，激活经理 {', '.join(active_manager_ids)}，主导经理 {dominant_manager_id}。"
            if active_manager_ids:
                return f"治理已应用，激活经理 {', '.join(active_manager_ids)}。"
        if event_name == "governance_blocked":
            hold_reason = str(payload.get("hold_reason") or "").strip()
            if hold_reason:
                return f"治理调整被阻断，原因是：{hold_reason}"
            return "治理调整被 guardrail 阻断。"
        if event_name == "cycle_start":
            cutoff_date = str(payload.get("cutoff_date") or "").strip()
            requested_mode = str(payload.get("requested_data_mode") or "").strip()
            llm_mode = str(payload.get("llm_mode") or "").strip()
            details = []
            if cutoff_date:
                details.append(f"截断日期 {cutoff_date}")
            if requested_mode:
                details.append(f"数据模式 {requested_mode}")
            if llm_mode:
                details.append(f"LLM 模式 {llm_mode}")
            if details:
                return "本轮训练已启动，" + "，".join(details) + "。"
        if event_name == "cycle_complete":
            cycle_id = payload.get("cycle_id")
            return_pct = payload.get("return_pct")
            if cycle_id is not None and return_pct not in (None, ""):
                return f"训练周期 #{cycle_id} 已完成，收益率约为 {return_pct}。"
            if cycle_id is not None:
                return f"训练周期 #{cycle_id} 已完成。"
        if event_name == "cycle_skipped":
            stage = str(payload.get("stage") or "").strip()
            reason = str(payload.get("reason") or "").strip()
            if stage and reason:
                return f"训练周期在 {stage} 阶段被跳过，原因是：{reason}"
            if reason:
                return f"训练周期被跳过，原因是：{reason}"
        if event_name == "agent_status":
            agent = str(payload.get("agent") or "").strip()
            status = str(payload.get("status") or "").strip()
            stage = str(payload.get("stage") or "").strip()
            progress_pct = payload.get("progress_pct")
            message = BrainRuntime._truncate_text(payload.get("message"), limit=80)
            parts = []
            if agent:
                parts.append(agent)
            if status:
                parts.append(status)
            if stage:
                parts.append(f"阶段 {stage}")
            if progress_pct not in (None, ""):
                parts.append(f"进度 {progress_pct}%")
            if message:
                parts.append(message)
            if parts:
                return "，".join(parts) + "。"
        if event_name == "module_log":
            module = str(payload.get("module") or "").strip()
            title = str(payload.get("title") or "").strip()
            message = BrainRuntime._truncate_text(payload.get("message"), limit=80)
            parts = [part for part in [module, title, message] if part]
            if parts:
                return " / ".join(parts) + "。"
        if event_name == "meeting_speech":
            speaker = str(payload.get("speaker") or "").strip()
            meeting = str(payload.get("meeting") or "").strip()
            speech = BrainRuntime._truncate_text(payload.get("speech"), limit=80)
            prefix = " / ".join(part for part in [meeting, speaker] if part)
            if prefix and speech:
                return f"{prefix}：{speech}"
        if event_name == "data_download_triggered":
            status = str(payload.get("status") or "").strip()
            message = BrainRuntime._truncate_text(payload.get("message"), limit=80)
            if status and message:
                return f"数据同步状态：{status}，{message}"
        if event_name in {"runtime_paths_updated", "evolution_config_updated", "control_plane_updated"}:
            updated = payload.get("updated")
            if isinstance(updated, list) and updated:
                return "更新字段：" + "，".join(str(item) for item in updated[:4])
        return ""

    @staticmethod
    def _event_broadcast_text(row: dict[str, Any]) -> str:
        event_name = str(row.get("event") or "").strip()
        if not event_name:
            return ""
        label = BrainRuntime._event_human_label(event_name)
        detail = BrainRuntime._event_detail_text(row)
        source = str(row.get("source") or "").strip()
        if detail:
            return f"{label}：{detail}"
        if source:
            return f"{label}（来源 {source}）"
        return label

    @staticmethod
    def _event_explanation_bullets(
        event_summary: dict[str, Any],
        *,
        recent_events: list[dict[str, Any]] | None = None,
    ) -> tuple[list[str], dict[str, Any], str]:
        summary = dict(event_summary or {})
        rows = list(recent_events or [])
        preferred_latest: dict[str, Any] = {}
        latest_internal: dict[str, Any] = {}
        for row in reversed(rows):
            event_name = str(row.get("event") or "")
            if not event_name:
                continue
            if not BrainRuntime._is_internal_runtime_event(event_name):
                preferred_latest = dict(row)
                break
            if not latest_internal:
                latest_internal = dict(row)
        latest = dict(preferred_latest or latest_internal or summary.get("latest") or {})
        counts = dict(summary.get("counts") or {})
        external_counts = {
            str(name): int(value or 0)
            for name, value in counts.items()
            if not BrainRuntime._is_internal_runtime_event(name)
        }
        bullets: list[str] = []
        latest_event: dict[str, Any] = {}
        explanation = ""
        if latest:
            event_name = str(latest.get("event") or "unknown")
            source = str(latest.get("source") or "").strip()
            detail_text = BrainRuntime._event_detail_text(latest)
            latest_event = {
                "event": event_name,
                "source": source,
                "ts": str(latest.get("ts") or ""),
                "kind": "internal" if BrainRuntime._is_internal_runtime_event(event_name) else "business",
                "label": BrainRuntime._event_human_label(event_name),
                "detail": detail_text,
                "broadcast_text": BrainRuntime._event_broadcast_text(latest),
            }
            if not BrainRuntime._is_internal_runtime_event(event_name):
                detail = f"最近业务事件：{event_name}（{BrainRuntime._event_human_label(event_name)}）"
                if source:
                    detail += f"（来源 {source}）"
                bullets.append(detail)
                if detail_text:
                    bullets.append("事件细节：" + detail_text)
        if external_counts:
            distribution = BrainRuntime._top_event_distribution(external_counts)
            bullets.append("业务事件分布：" + distribution)
            if preferred_latest:
                explanation = (
                    f"最近一次业务事件是 {latest_event['event']}"
                    + (f"（{latest_event.get('label')}）" if latest_event.get("label") else "")
                    + (f"（来源 {latest_event['source']}）" if latest_event.get("source") else "")
                    + "。"
                )
                if latest_event.get("detail"):
                    explanation += f" {latest_event['detail']}"
                if distribution:
                    explanation += f" 当前窗口内主要业务事件分布为：{distribution}。"
        elif counts:
            distribution = BrainRuntime._top_event_distribution(counts)
            bullets.append("交互事件分布：" + distribution)
            explanation = "当前窗口内主要记录的是交互与调度事件，尚未出现新的业务事件。"
            if distribution:
                explanation += f" 最近的事件分布为：{distribution}。"
        return bullets, latest_event, explanation

    def _build_human_readable_receipt(
        self,
        payload: dict[str, Any],
        *,
        intent: str,
        operation: str,
    ) -> dict[str, Any]:
        return BrainHumanReadablePresenter.build_human_readable_receipt(
            payload,
            intent=intent,
            operation=operation,
        )

    def _attach_human_readable_receipt(
        self,
        payload: dict[str, Any],
        *,
        intent: str,
        operation: str,
    ) -> dict[str, Any]:
        return BrainHumanReadablePresenter.attach_human_readable_receipt(
            payload,
            intent=intent,
            operation=operation,
        )


    async def _try_builtin_intent(self, content: str) -> Optional[str]:
        text = str(content or "").strip()
        if not text:
            return None
        low = text.lower()
        names = set(self.tools._tools.keys())  # pylint: disable=protected-access

        def has(name: str) -> bool:
            return name in names

        async def run(name: str, args: dict[str, Any] | None = None) -> Optional[str]:
            if not has(name):
                return None
            return await self.tools.execute(name, args or {})

        async def run_json(name: str, args: dict[str, Any] | None = None) -> Any:
            result = await run(name, args)
            if result is None:
                return None
            try:
                return json.loads(result)
            except Exception:
                return result

        def has_any(haystack: str, terms: list[str]) -> bool:
            return any(term in haystack for term in terms)

        data_terms = ["数据", "行情", "日线", "资金流", "龙虎榜", "data"]
        data_status_terms = ["数据状态", "数据健康", "data status", "data health", "刷新数据"]
        diagnostics_terms = ["诊断", "diagnostics", "runtime", "运行诊断", "运行信息", "日志", "log"]
        event_terms = ["事件", "events", "最近事件", "事件摘要"]
        event_explanation_terms = [
            "发生了什么",
            "最近发生了什么",
            "解释最近发生了什么",
            "最近怎么了",
            "what happened",
            "recent activity",
        ]
        training_lab_terms = ["训练实验室", "training lab", "最近训练", "训练记录", "训练结果", "实验记录", "run list", "eval"]
        training_exec_terms = ["训练", "跑一轮", "开始训练", "执行训练", "run training", "train once", "train"]
        status_terms = ["系统状态", "系统概览", "状态", "status", "系统情况"]
        leaderboard_terms = ["排行榜", "榜单", "leaderboard"]
        playbook_terms = ["playbook", "playbooks", "playbook list", "剧本", "列出剧本", "有哪些剧本"]
        quick_test_terms = ["快速测试", "健康检查", "quick test", "smoke"]
        config_terms = ["配置", "config", "设置"]
        control_terms = ["控制面", "control plane", "模型绑定", "llm 绑定", "绑定"]
        path_terms = ["路径", "runtime path", "输出目录", "workspace"]
        prompt_terms = ["prompt", "提示词", "agent prompt", "agent prompts", "角色提示"]
        stock_explicit_terms = ["问股", "股票", "个股", "stock"]
        stock_strategy_terms = ["缠论", "均线", "macd", "rsi", "趋势", "筹码"]
        stock_verbs = ["分析", "看看", "看下", "看一下", "研究"]
        conflict_terms = data_terms + status_terms + diagnostics_terms + event_terms + training_lab_terms + training_exec_terms + leaderboard_terms + playbook_terms + config_terms + control_terms + path_terms + prompt_terms
        stock_code_like = bool(re.search(r"(?i)\b(?:sh|sz)\.?\d{6}\b|\b\d{6}\b", text))
        asks_recent_training = has_any(text, training_lab_terms)
        asks_training_exec = has_any(low, training_exec_terms) and not asks_recent_training
        asks_status = has_any(text, status_terms)
        asks_data_status = has_any(text, data_status_terms) or (has_any(text, data_terms) and has_any(text, ["状态", "健康", "刷新", "refresh", "诊断", "check"]))
        asks_diagnostics = (
            has_any(text, diagnostics_terms)
            or has_any(text, event_terms)
            or has_any(low, event_explanation_terms)
        )
        asks_config = has_any(low, config_terms) or has_any(low, control_terms) or has_any(low, path_terms) or has_any(low, prompt_terms)
        asks_stock = (
            has_any(low, stock_explicit_terms)
            or stock_code_like
            or (has_any(text, stock_strategy_terms) and has_any(text, stock_verbs))
            or (
                not has_any(text, conflict_terms)
                and any(text.startswith(prefix) for prefix in ["看看", "看下", "看一下", "分析", "分析一下", "研究", "研究一下"])
            )
        )

        if has_any(text, ["深度状态", "慢状态", "deep status"]):
            payload = await run_json("invest_deep_status")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="runtime_status", operation="invest_deep_status", tool_names=["invest_deep_status"])

        if asks_data_status:
            payload = await run_json("invest_data_status", {"refresh": any(token in low for token in ["refresh", "刷新", "slow"])})
            return self._wrap_builtin_payload(payload, user_goal=text, intent="data_status", operation="invest_data_status", tool_names=["invest_data_status"])

        if asks_status and asks_recent_training:
            quick = await run_json("invest_quick_status")
            lab = await run_json("invest_training_lab_summary")
            payload = self._build_builtin_combo_payload(
                payload_intent="status_and_recent_training",
                runtime_intent="runtime_status_and_training",
                operation="status_and_recent_training",
                agent_kind="bounded_runtime_agent",
                tool_names=["invest_quick_status", "invest_training_lab_summary"],
                workflow=["runtime_scope_resolve", "quick_status_read", "training_lab_read", "finalize"],
                tool_catalog_scope="runtime_training_combo",
                sections={"quick_status": quick, "training_lab": lab},
            )
            return self._wrap_builtin_payload(payload, user_goal=text, intent="runtime_status_and_training", operation="status_and_recent_training", tool_names=["invest_quick_status", "invest_training_lab_summary"])

        if asks_diagnostics:
            payload = await run_json("invest_runtime_diagnostics")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="runtime_diagnostics", operation="invest_runtime_diagnostics", tool_names=["invest_runtime_diagnostics"])

        if asks_recent_training:
            payload = await run_json("invest_training_lab_summary")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="training_lab_summary", operation="invest_training_lab_summary", tool_names=["invest_training_lab_summary"])

        if has_any(text, leaderboard_terms):
            payload = await run_json("invest_leaderboard")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="leaderboard", operation="invest_leaderboard", tool_names=["invest_leaderboard"])

        if has_any(low, playbook_terms):
            payload = await run_json("invest_list_playbooks")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="playbook_inventory", operation="invest_list_playbooks", tool_names=["invest_list_playbooks"])

        if has_any(text, quick_test_terms):
            payload = await run_json("invest_quick_test")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="runtime_quick_test", operation="invest_quick_test", tool_names=["invest_quick_test"])

        if asks_config:
            if has_any(low, prompt_terms):
                payload = await run_json("invest_agent_prompts_list")
                return self._wrap_builtin_payload(payload, user_goal=text, intent="config_prompts", operation="invest_agent_prompts_list", tool_names=["invest_agent_prompts_list"])
            if has_any(low, path_terms):
                payload = await run_json("invest_runtime_paths_get")
                return self._wrap_builtin_payload(payload, user_goal=text, intent="runtime_paths", operation="invest_runtime_paths_get", tool_names=["invest_runtime_paths_get"])
            if any(token in text for token in ["有没有问题", "有问题", "异常", "风险"]):
                payload = await run_json("invest_runtime_diagnostics")
                return self._wrap_builtin_payload(payload, user_goal=text, intent="config_risk_diagnostics", operation="invest_runtime_diagnostics", tool_names=["invest_runtime_diagnostics"])
            control_plane = await run_json("invest_control_plane_get")
            evolution_config = await run_json("invest_evolution_config_get")
            payload = self._build_builtin_combo_payload(
                payload_intent="config_overview",
                runtime_intent="config_overview",
                operation="config_overview",
                agent_kind="bounded_config_agent",
                tool_names=["invest_control_plane_get", "invest_evolution_config_get"],
                workflow=["config_scope_resolve", "control_plane_read", "evolution_config_read", "finalize"],
                tool_catalog_scope="config_overview_combo",
                sections={
                    "control_plane": control_plane,
                    "evolution_config": evolution_config,
                },
            )
            return self._wrap_builtin_payload(payload, user_goal=text, intent="config_overview", operation="config_overview", tool_names=["invest_control_plane_get", "invest_evolution_config_get"])

        if asks_training_exec:
            rounds_match = re.search(r"(\d+)\s*(轮|次)", text)
            rounds = int(rounds_match.group(1)) if rounds_match else 1
            mock = any(token in low for token in ["mock", "演示", "测试", "dry-run", "quick"])
            confirm = any(token in low for token in ["确认", "confirm"])
            payload = await run_json("invest_train", {"rounds": rounds, "mock": mock, "confirm": confirm})
            risk_level = RISK_LEVEL_LOW if mock else RISK_LEVEL_HIGH if rounds > 1 else RISK_LEVEL_MEDIUM
            return self._wrap_builtin_payload(
                payload,
                user_goal=text,
                intent="training_execution",
                operation="invest_train",
                tool_names=["invest_train"],
                writes_state=True,
                risk_level=risk_level,
                reasons=list(TRAINING_DEFAULT_REASON_CODES),
            )

        if asks_status:
            payload = await run_json("invest_quick_status")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="runtime_status", operation="invest_quick_status", tool_names=["invest_quick_status"])

        if asks_stock:
            result = await run("invest_ask_stock", {"query": text, "question": text})
            if result and not result.startswith("Error executing invest_ask_stock"):
                return result
            if self.gateway.available:
                return None
            return result
        return None

    def _append_turn(self, session: BrainSession, user_msg: dict[str, Any], assistant_msg: dict[str, Any]) -> None:
        session.messages.append(user_msg)
        session.messages.append(assistant_msg)
        if len(session.messages) > self.memory_window * 4:
            session.messages = session.messages[-self.memory_window * 4:]
        session.updated_at = datetime.now()
