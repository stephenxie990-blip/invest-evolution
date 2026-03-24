"""Canonical commander facade and CLI entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import queue
import socket
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from invest_evolution.agent_runtime.plugins import BridgeMessage
from invest_evolution.application.commander.bootstrap import (
    PlaybookEntry,
    PlaybookRegistry,
    apply_runtime_path_overrides as apply_runtime_path_overrides_bootstrap,
    build_commander_config_from_args,
    build_runtime_system_prompt,
    commander_llm_default,
    ensure_runtime_storage_for_runtime,
    initialize_commander_runtime,
    load_runtime_plugins,
    persist_runtime_state,
    register_fusion_tools,
    relocate_commander_state_paths,
    setup_runtime_cron_callback,
    write_runtime_identity_file,
)
from invest_evolution.application.commander.ops import (
    CommanderControlSurfaceMixin,
)
from invest_evolution.application.commander.presentation import build_human_display
from invest_evolution.application.config_surface import (
    update_evolution_config_payload as _update_evolution_config_payload,
)
from invest_evolution.application.commander.runtime import (
    CommanderRuntimeEventStreamMixin,
    acquire_runtime_lock,
    apply_restored_body_state,
    build_events_summary_response_bundle,
    build_events_tail_response_bundle,
    build_runtime_diagnostics_response_bundle,
    build_status_response_bundle,
    build_training_lab_summary_response_bundle,
    build_finished_task,
    build_started_task,
    copy_runtime_task,
    drain_runtime_notifications,
    get_events_summary_response,
    get_events_tail_response,
    get_runtime_diagnostics_response,
    get_training_lab_summary_response,
    is_pid_alive,
    read_runtime_lock_payload,
    release_runtime_lock,
    restore_runtime_from_persisted_state,
)
from invest_evolution.application.commander.status import (
    append_event_row,
    build_persisted_status_payload,
    build_training_memory_entry,
    collect_data_status,
    summarize_research_feedback_promotion,
    summarize_training_evaluation_brief,
)
from invest_evolution.application.commander.workflow import (
    add_cron_job as add_cron_job_action,
    append_training_memory_for_runtime,
    ask_runtime,
    attach_training_lab_paths_for_runtime,
    build_experiment_spec_from_plan,
    build_promotion_summary_for_runtime,
    build_run_cycles_kwargs_for_runtime,
    build_training_evaluation_summary_for_runtime,
    create_training_plan as create_training_plan_action,
    execute_training_plan as execute_training_plan_action,
    list_cron_jobs as list_cron_jobs_action,
    load_leaderboard_snapshot_for_runtime,
    load_training_plan_artifact_for_runtime,
    parse_ask_response_payload,
    record_ask_activity as record_runtime_ask_activity_action,
    record_training_lab_artifacts_for_runtime,
    reload_playbooks as reload_playbooks_action,
    reload_plugins as reload_plugins_action,
    remove_cron_job as remove_cron_job_action,
    serve_forever as serve_forever_action,
    start_runtime,
    stop_runtime,
    train_once as train_once_action,
    wrap_training_execution_payload_for_runtime,
)
from invest_evolution.application.investment_body_service import InvestmentBodyService
from invest_evolution.common.environment import ensure_environment
from invest_evolution.config import (
    LOGS_DIR,
    MEMORY_DIR,
    OUTPUT_DIR,
    PROJECT_ROOT,
    RUNTIME_DIR,
    SESSIONS_DIR,
    WORKSPACE_DIR,
)
from invest_evolution.config.control_plane import RuntimePathConfigService

logger = logging.getLogger(__name__)

_STATUS_RESPONSE_BUNDLE_EXPORTS = (
    build_events_tail_response_bundle,
    build_events_summary_response_bundle,
    build_runtime_diagnostics_response_bundle,
    build_training_lab_summary_response_bundle,
    build_status_response_bundle,
)


def update_evolution_config_payload(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _update_evolution_config_payload(*args, **kwargs)


def _ensure_commander_cli_environment(args: argparse.Namespace) -> None:
    command = str(getattr(args, "cmd", "") or "").strip()
    if command not in {"run", "train-once", "ask"}:
        return
    mock_mode = bool(getattr(args, "mock", False))
    required_modules = ["pandas"] if mock_mode else ["pandas", "requests", "rank_bm25"]
    ensure_environment(
        required_modules=required_modules,
        require_project_python=False,
        validate_requests_stack=not mock_mode,
        component=f"commander:{command}",
    )

HIGH_RISK_EVOLUTION_CONFIG_KEYS = {
    "default_manager_id",
    "default_manager_config_ref",
    "data_source",
    "governance_enabled",
    "governance_mode",
    "manager_active_ids",
    "manager_budget_weights",
}

__all__ = [
    "CommanderConfig",
    "CommanderRuntime",
    "PlaybookEntry",
    "PlaybookRegistry",
    "InvestmentBodyService",
    "STATUS_OK",
    "STATUS_CONFIRMATION_REQUIRED",
    "build_events_tail_response_bundle",
    "build_events_summary_response_bundle",
    "build_runtime_diagnostics_response_bundle",
    "build_training_lab_summary_response_bundle",
    "build_status_response_bundle",
    "get_events_tail_response",
    "get_events_summary_response",
    "get_runtime_diagnostics_response",
    "get_training_lab_summary_response",
    "build_parser",
    "run_async",
    "main",
]

_RUNTIME_FIELD_UNSET = object()

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_BUSY = "busy"
STATUS_IDLE = "idle"
STATUS_TRAINING = "training"
STATUS_COMPLETED = "completed"
STATUS_NO_DATA = "no_data"
STATUS_NOT_FOUND = "not_found"
STATUS_CONFIRMATION_REQUIRED = "confirmation_required"

RUNTIME_STATE_INITIALIZED = "initialized"
RUNTIME_STATE_STARTING = "starting"
RUNTIME_STATE_STOPPING = "stopping"
RUNTIME_STATE_STOPPED = "stopped"
RUNTIME_STATE_RELOADING_PLAYBOOKS = "reloading_playbooks"

EVENT_TASK_STARTED = "task_started"
EVENT_TASK_FINISHED = "task_finished"
EVENT_TRAINING_STARTED = "training_started"
EVENT_TRAINING_FINISHED = "training_finished"
EVENT_ASK_STARTED = "ask_started"
EVENT_ASK_FINISHED = "ask_finished"

_STATE_DIR_RELOCATIONS: dict[str, str] = {
    "runtime_lock_file": "commander.lock",
    "training_lock_file": "training.lock",
    "config_audit_log_path": "config_changes.jsonl",
    "config_snapshot_dir": "config_snapshots",
    "training_plan_dir": "training_plans",
    "training_run_dir": "training_runs",
    "training_eval_dir": "training_evals",
    "runtime_events_path": "commander_events.jsonl",
}

_COMMANDER_FACADE_TYPES = (InvestmentBodyService,)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class CommanderConfig:
    """Runtime config for the fused commander."""

    workspace: Path = WORKSPACE_DIR
    playbook_dir: Path = PROJECT_ROOT / "strategies"
    state_file: Path = OUTPUT_DIR / "commander" / "state.json"
    cron_store: Path = OUTPUT_DIR / "commander" / "cron_jobs.json"
    memory_store: Path = MEMORY_DIR / "commander_memory.jsonl"
    plugin_dir: Path = PROJECT_ROOT / "agent_settings" / "plugins"
    bridge_inbox: Path = SESSIONS_DIR / "inbox"
    bridge_outbox: Path = SESSIONS_DIR / "outbox"
    runtime_state_dir: Path = RUNTIME_DIR / "state"
    runtime_lock_file: Path = RUNTIME_DIR / "state" / "commander.lock"
    training_lock_file: Path = RUNTIME_DIR / "state" / "training.lock"
    training_output_dir: Path = OUTPUT_DIR / "training"
    artifact_log_dir: Path = LOGS_DIR / "artifacts"
    config_audit_log_path: Path = RUNTIME_DIR / "state" / "config_changes.jsonl"
    config_snapshot_dir: Path = RUNTIME_DIR / "state" / "config_snapshots"
    training_plan_dir: Path = RUNTIME_DIR / "state" / "training_plans"
    training_run_dir: Path = RUNTIME_DIR / "state" / "training_runs"
    training_eval_dir: Path = RUNTIME_DIR / "state" / "training_evals"
    runtime_events_path: Path = RUNTIME_DIR / "state" / "commander_events.jsonl"
    stock_strategy_dir: Path = PROJECT_ROOT / "stock_strategies"

    model: str = field(default_factory=lambda: os.environ.get("COMMANDER_MODEL", commander_llm_default("model")))
    api_key: str = field(default_factory=lambda: os.environ.get("COMMANDER_API_KEY", commander_llm_default("api_key")))
    api_base: str = field(default_factory=lambda: os.environ.get("COMMANDER_API_BASE", commander_llm_default("api_base")))
    temperature: float = field(default_factory=lambda: float(os.environ.get("COMMANDER_TEMP", "0.2")))
    max_tokens: int = field(default_factory=lambda: int(os.environ.get("COMMANDER_MAX_TOKENS", "8192")))
    max_tool_iterations: int = field(default_factory=lambda: int(os.environ.get("COMMANDER_MAX_TOOL_ITER", "40")))
    memory_window: int = field(default_factory=lambda: int(os.environ.get("COMMANDER_MEMORY_WINDOW", "120")))

    training_interval_sec: int = field(default_factory=lambda: int(os.environ.get("COMMANDER_TRAIN_INTERVAL_SEC", "3600")))
    heartbeat_interval_sec: int = field(default_factory=lambda: int(os.environ.get("COMMANDER_HEARTBEAT_INTERVAL_SEC", "1800")))

    autopilot_enabled: bool = field(default_factory=lambda: os.environ.get("COMMANDER_AUTOPILOT", "1") != "0")
    heartbeat_enabled: bool = field(default_factory=lambda: os.environ.get("COMMANDER_HEARTBEAT", "1") != "0")
    bridge_enabled: bool = field(default_factory=lambda: os.environ.get("COMMANDER_BRIDGE", "1") != "0")
    bridge_poll_interval_sec: float = field(default_factory=lambda: float(os.environ.get("COMMANDER_BRIDGE_POLL_SEC", "1")))

    send_progress: bool = field(default_factory=lambda: os.environ.get("COMMANDER_SEND_PROGRESS", "1") != "0")
    send_tool_hints: bool = field(default_factory=lambda: os.environ.get("COMMANDER_SEND_TOOL_HINTS", "0") != "0")

    brave_api_key: str = field(default_factory=lambda: os.environ.get("BRAVE_SEARCH_API_KEY", ""))
    exec_timeout: int = field(default_factory=lambda: int(os.environ.get("COMMANDER_EXEC_TIMEOUT", "120")))
    exec_path_append: str = field(default_factory=lambda: os.environ.get("COMMANDER_EXEC_PATH_APPEND", ""))
    restrict_tools_to_workspace: bool = field(default_factory=lambda: os.environ.get("COMMANDER_RESTRICT_TO_WORKSPACE", "0") != "0")

    mock_mode: bool = field(default_factory=lambda: os.environ.get("COMMANDER_MOCK", "0") != "0")

    def __post_init__(self):
        relocate_commander_state_paths(
            self,
            default_state_dir=RUNTIME_DIR / "state",
            default_state_parent=OUTPUT_DIR / "commander",
            default_training_output_dir=OUTPUT_DIR / "training",
            default_artifact_log_dir=LOGS_DIR / "artifacts",
            state_dir_relocations=_STATE_DIR_RELOCATIONS,
        )

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "CommanderConfig":
        return build_commander_config_from_args(
            cls,
            args,
            path_config_service_cls=RuntimePathConfigService,
            project_root=PROJECT_ROOT,
            apply_runtime_overrides=apply_runtime_path_overrides_bootstrap,
        )


# ---------------------------------------------------------------------------
# Commander runtime
# ---------------------------------------------------------------------------

class CommanderRuntime(CommanderControlSurfaceMixin, CommanderRuntimeEventStreamMixin):
    """Unified runtime: local brain + invest body in one process."""

    cfg: CommanderConfig
    config_service: Any
    instance_id: str
    runtime_state: str
    current_task: dict[str, Any] | None
    last_task: dict[str, Any] | None
    _task_lock: Any
    _stream_lock: Any
    _event_subscriptions: dict[str, Any]
    _runtime_lock_acquired: bool
    training_lab: Any
    playbook_registry: Any
    body: Any
    brain: Any
    cron: Any
    heartbeat: Any
    _notifications: Any
    memory: Any
    plugin_loader: Any
    stock_analysis: Any
    research_case_store: Any
    _plugin_tool_names: set[str]
    bridge: Any
    _started: bool
    _notify_task: Any
    _autopilot_task: Any
    _pending_runtime_tasks: list[dict[str, Any]]

    def __init__(self, cfg: CommanderConfig):
        self.cfg = cfg
        initialize_commander_runtime(self)

    def _on_body_event(self, event: str, payload: dict[str, Any]) -> None:
        self._append_runtime_event(event, payload, source="body")
        if event == EVENT_TRAINING_STARTED:
            self._update_runtime_fields(state=STATUS_TRAINING, current_task=payload)
        elif event == EVENT_TRAINING_FINISHED:
            self._update_runtime_fields(state=STATUS_IDLE, current_task=None, last_task=payload)
        self._persist_state()

    def _append_runtime_event(self, event: str, payload: dict[str, Any], *, source: str = "runtime") -> dict[str, Any]:
        normalized_payload = self._normalize_event_payload(payload)
        row = append_event_row(self.cfg.runtime_events_path, event, normalized_payload, source=source)
        self._publish_stream_event(row)
        return row

    def _restore_persisted_state(self) -> None:
        restore_runtime_from_persisted_state(
            state_file=self.cfg.state_file,
            logger=logger,
            update_runtime_fields=self._update_runtime_fields,
            current_state=self.runtime_state,
            apply_restored_body_state_impl=apply_restored_body_state,
            body=self.body,
        )

    def _set_runtime_state(self, state: str) -> None:
        self._update_runtime_fields(state=state)

    @staticmethod
    def _copy_runtime_task(task: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        return copy_runtime_task(task)

    @staticmethod
    def new_request_id() -> str:
        return f"req:{uuid.uuid4().hex[:16]}"

    def _update_runtime_fields(
        self,
        *,
        state: Any = _RUNTIME_FIELD_UNSET,
        current_task: Any = _RUNTIME_FIELD_UNSET,
        last_task: Any = _RUNTIME_FIELD_UNSET,
    ) -> None:
        with self._task_lock:
            if state is not _RUNTIME_FIELD_UNSET:
                self.runtime_state = str(state)
            if current_task is not _RUNTIME_FIELD_UNSET:
                self.current_task = self._copy_runtime_task(current_task)
            if last_task is not _RUNTIME_FIELD_UNSET:
                self.last_task = self._copy_runtime_task(last_task)

    def _snapshot_runtime_fields(self) -> tuple[str, Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        with self._task_lock:
            return (
                self.runtime_state,
                self._copy_runtime_task(self.current_task),
                self._copy_runtime_task(self.last_task),
            )

    def _begin_task(self, task_type: str, source: str, **metadata: Any) -> None:
        request_context = self._current_request_event_context()
        request_id = str(metadata.get("request_id") or request_context.get("request_id") or "").strip()
        task = build_started_task(
            task_type,
            source,
            task_id=f"task:{uuid.uuid4().hex}",
            **({"request_id": request_id} if request_id else {}),
            **metadata,
        )
        with self._task_lock:
            self._pending_runtime_tasks.append(self._copy_runtime_task(task) or {})
            self.current_task = self._copy_runtime_task(task)
        self._append_runtime_event(EVENT_TASK_STARTED, task, source="runtime")

    def _end_task(self, status: str = STATUS_OK, **metadata: Any) -> None:
        request_context = self._current_request_event_context()
        request_id = str(metadata.get("request_id") or request_context.get("request_id") or "").strip()
        with self._task_lock:
            if not self._pending_runtime_tasks and self.current_task is None:
                return
            task_index = None
            if request_id:
                for index in range(len(self._pending_runtime_tasks) - 1, -1, -1):
                    pending_request_id = str(
                        self._pending_runtime_tasks[index].get("request_id") or ""
                    ).strip()
                    if pending_request_id == request_id:
                        task_index = index
                        break
            if self._pending_runtime_tasks:
                if task_index is None:
                    task_index = len(self._pending_runtime_tasks) - 1
                current_task = self._copy_runtime_task(self._pending_runtime_tasks.pop(task_index))
            else:
                current_task = self._copy_runtime_task(self.current_task)
            if current_task is None:
                self.current_task = None
                return
            self.last_task = build_finished_task(
                current_task,
                status=status,
                copy_task=self._copy_runtime_task,
                **metadata,
            )
            self._append_runtime_event(EVENT_TASK_FINISHED, self.last_task, source="runtime")
            self.current_task = (
                self._copy_runtime_task(self._pending_runtime_tasks[-1])
                if self._pending_runtime_tasks
                else None
            )

    def _complete_runtime_task(self, *, status: str = STATUS_OK, state: str | None = None, **metadata: Any) -> None:
        if state is not None:
            self._set_runtime_state(state)
        self._end_task(status, **metadata)
        self._persist_state()

    def _record_ask_activity(
        self,
        event: str,
        *,
        session_key: str,
        channel: str,
        chat_id: str,
        request_id: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        record_runtime_ask_activity_action(
            self,
            event=event,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            request_id=request_id,
            extra=extra,
        )

    def _read_runtime_lock_payload(self) -> dict[str, Any]:
        return read_runtime_lock_payload(self.cfg.runtime_lock_file, logger=logger)

    def _is_pid_alive(self, pid: int) -> bool:
        return is_pid_alive(pid, os_module=os)

    def _acquire_runtime_lock(self) -> None:
        acquire_runtime_lock(
            lock_file=self.cfg.runtime_lock_file,
            instance_id=self.instance_id,
            workspace=str(self.cfg.workspace),
            read_lock_payload=self._read_runtime_lock_payload,
            pid_alive=self._is_pid_alive,
            os_module=os,
            socket_module=socket,
        )
        self._runtime_lock_acquired = True

    def _release_runtime_lock(self) -> None:
        if self._runtime_lock_acquired:
            release_runtime_lock(
                lock_file=self.cfg.runtime_lock_file,
                instance_id=self.instance_id,
                read_lock_payload=self._read_runtime_lock_payload,
                os_module=os,
                logger=logger,
            )
            self._runtime_lock_acquired = False

    def _set_started_flag(self, started: bool) -> None:
        self._started = bool(started)

    def _set_background_tasks(
        self,
        notify_task: Optional[asyncio.Task],
        autopilot_task: Optional[asyncio.Task],
    ) -> None:
        self._notify_task = notify_task
        self._autopilot_task = autopilot_task

    async def start(self) -> None:
        await start_runtime(self)

    async def stop(self) -> None:
        await stop_runtime(self)

    async def ask(
        self,
        message: str,
        session_key: str = "commander:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        request_id: str = "",
    ) -> str:
        return await ask_runtime(
            self,
            message,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            request_id=request_id,
        )

    def _lab_counts(self) -> dict[str, int]:
        return self.training_lab.counts()

    def _new_training_plan_id(self) -> str:
        return self.training_lab.new_plan_id()

    def _new_training_run_id(self) -> str:
        return self.training_lab.new_run_id()

    def _write_json_artifact(self, path: Path, payload: dict[str, Any]) -> Path:
        return self.training_lab.write_json_artifact(path, payload)

    def _training_plan_path(self, plan_id: str) -> Path:
        return self.training_lab.plan_path(plan_id)

    def _training_run_path(self, run_id: str) -> Path:
        return self.training_lab.run_path(run_id)

    def _training_eval_path(self, run_id: str) -> Path:
        return self.training_lab.evaluation_path(run_id)

    def _create_training_plan_payload(
        self,
        *,
        rounds: int,
        mock: bool,
        source: str,
        goal: str = "",
        notes: str = "",
        tags: list[str] | None = None,
        detail_mode: str = "fast",
        protocol: dict[str, Any] | None = None,
        dataset: dict[str, Any] | None = None,
        manager_scope: dict[str, Any] | None = None,
        optimization: dict[str, Any] | None = None,
        llm: dict[str, Any] | None = None,
        plan_id: str | None = None,
        auto_generated: bool = False,
    ) -> dict[str, Any]:
        return self.training_lab.build_training_plan_payload(
            rounds=rounds,
            mock=mock,
            source=source,
            goal=goal,
            notes=notes,
            tags=tags,
            detail_mode=detail_mode,
            protocol=protocol,
            dataset=dataset,
            manager_scope=manager_scope,
            optimization=optimization,
            llm=llm,
            plan_id=plan_id,
            auto_generated=auto_generated,
        )

    def create_training_plan(
        self,
        *,
        rounds: int = 1,
        mock: bool = False,
        goal: str = "",
        notes: str = "",
        tags: list[str] | None = None,
        detail_mode: str = "fast",
        protocol: dict[str, Any] | None = None,
        dataset: dict[str, Any] | None = None,
        manager_scope: dict[str, Any] | None = None,
        optimization: dict[str, Any] | None = None,
        llm: dict[str, Any] | None = None,
        source: str = "manual",
        auto_generated: bool = False,
    ) -> dict[str, Any]:
        return create_training_plan_action(
            self,
            rounds=rounds,
            mock=mock,
            source=source,
            goal=goal,
            notes=notes,
            tags=tags,
            detail_mode=detail_mode,
            protocol=protocol,
            dataset=dataset,
            manager_scope=manager_scope,
            optimization=optimization,
            llm=llm,
            auto_generated=auto_generated,
        )

    def list_training_plans(self, *, limit: int = 20) -> dict[str, Any]:
        return self.training_lab.list_json_artifacts(self.cfg.training_plan_dir, limit=limit)

    def get_training_plan(self, plan_id: str) -> dict[str, Any]:
        return self.training_lab.read_json_artifact(self._training_plan_path(str(plan_id)), label='training plan')

    def get_training_run(self, run_id: str) -> dict[str, Any]:
        return self.training_lab.read_json_artifact(self._training_run_path(str(run_id)), label='training run')

    def get_training_evaluation(self, run_id: str) -> dict[str, Any]:
        return self.training_lab.read_json_artifact(self._training_eval_path(str(run_id)), label='training evaluation')

    def list_training_runs(self, *, limit: int = 20) -> dict[str, Any]:
        return self.training_lab.list_json_artifacts(self.cfg.training_run_dir, limit=limit)

    def list_training_evaluations(self, *, limit: int = 20) -> dict[str, Any]:
        return self.training_lab.list_json_artifacts(self.cfg.training_eval_dir, limit=limit)

    def _load_leaderboard_snapshot(self) -> dict[str, Any]:
        return load_leaderboard_snapshot_for_runtime(self)

    def _build_promotion_summary(
        self,
        *,
        plan: dict[str, Any],
        ok_results: list[dict[str, Any]],
        avg_return_pct: float | None,
        avg_strategy_score: float | None,
        benchmark_pass_rate: float,
    ) -> dict[str, Any]:
        return build_promotion_summary_for_runtime(
            self,
            plan=plan,
            ok_results=ok_results,
            avg_return_pct=avg_return_pct,
            avg_strategy_score=avg_strategy_score,
            benchmark_pass_rate=benchmark_pass_rate,
        )

    def _build_training_evaluation_summary(self, payload: dict[str, Any], *, plan: dict[str, Any], run_id: str, error: str = "") -> dict[str, Any]:
        return build_training_evaluation_summary_for_runtime(
            self,
            payload,
            plan=plan,
            run_id=run_id,
            error=error,
        )

    def _record_training_lab_artifacts(self, *, plan: dict[str, Any], payload: dict[str, Any], status: str, error: str = "") -> dict[str, Any]:
        return record_training_lab_artifacts_for_runtime(
            self,
            plan=plan,
            payload=payload,
            status=status,
            error=error,
        )

    def _append_training_memory(
        self,
        payload: dict[str, Any],
        *,
        rounds: int,
        mock: bool,
        status: str,
        error: str = "",
        build_training_memory_entry: Any = build_training_memory_entry,
    ) -> None:
        append_training_memory_for_runtime(
            self,
            payload,
            rounds=rounds,
            mock=mock,
            status=status,
            error=error,
            build_training_memory_entry_impl=build_training_memory_entry,
        )

    def _load_training_plan_artifact(self, plan_id: str) -> tuple[Path, dict[str, Any]]:
        return load_training_plan_artifact_for_runtime(self, plan_id)

    @staticmethod
    def _build_experiment_spec_from_plan(plan: dict[str, Any]) -> tuple[dict[str, Any], int, bool]:
        return build_experiment_spec_from_plan(plan)

    def _build_run_cycles_kwargs(
        self,
        *,
        plan: dict[str, Any],
        rounds: int,
        mock: bool,
        experiment_spec: dict[str, Any],
    ) -> dict[str, Any]:
        return build_run_cycles_kwargs_for_runtime(
            self,
            plan=plan,
            rounds=rounds,
            mock=mock,
            experiment_spec=experiment_spec,
        )

    @staticmethod
    def _summarize_research_feedback_promotion(promotion: dict[str, Any]) -> dict[str, Any]:
        return summarize_research_feedback_promotion(promotion)

    @classmethod
    def _summarize_training_evaluation_brief(cls, evaluation: dict[str, Any]) -> dict[str, Any]:
        return summarize_training_evaluation_brief(evaluation)

    @classmethod
    def _attach_training_lab_paths(cls, payload: dict[str, Any], lab: dict[str, Any]) -> None:
        attach_training_lab_paths_for_runtime(payload, lab)

    def _wrap_training_execution_payload(
        self,
        payload: dict[str, Any],
        *,
        plan_id: str,
        rounds: int,
        mock: bool,
    ) -> dict[str, Any]:
        return wrap_training_execution_payload_for_runtime(
            self,
            payload,
            plan_id=plan_id,
            rounds=rounds,
            mock=mock,
        )

    async def train_once(
        self,
        rounds: int = 1,
        mock: bool = False,
        *,
        session_key: str = "",
        chat_id: str = "",
        request_id: str = "",
        channel: str = "",
    ) -> dict[str, Any]:
        return await train_once_action(
            self,
            rounds=rounds,
            mock=mock,
            session_key=session_key,
            chat_id=chat_id,
            request_id=request_id,
            channel=channel,
        )

    async def execute_training_plan(
        self,
        plan_id: str,
        *,
        session_key: str = "",
        chat_id: str = "",
        request_id: str = "",
        channel: str = "",
    ) -> dict[str, Any]:
        return await execute_training_plan_action(
            self,
            plan_id,
            session_key=session_key,
            chat_id=chat_id,
            request_id=request_id,
            channel=channel,
        )

    def reload_playbooks(self) -> dict[str, Any]:
        return reload_playbooks_action(self)

    def _collect_data_status(self, detail_mode: str) -> dict[str, Any]:
        return collect_data_status(detail_mode)

    def _build_persisted_state_payload(self) -> dict[str, Any]:
        return build_persisted_status_payload(self)

    def status(self, *, detail: str = "fast") -> dict[str, Any]:
        return self._attach_projected_readonly_bundle(
            build_status_response_bundle(
                self,
                detail=detail,
            )
        )

    def add_cron_job(
        self,
        *,
        name: str,
        message: str,
        every_sec: int,
        deliver: bool = False,
        channel: str = "cli",
        to: str = "commander",
    ) -> dict[str, Any]:
        return add_cron_job_action(
            self,
            name=name,
            message=message,
            every_sec=every_sec,
            deliver=deliver,
            channel=channel,
            to=to,
        )

    def list_cron_jobs(self) -> dict[str, Any]:
        return list_cron_jobs_action(self)

    def remove_cron_job(self, job_id: str) -> dict[str, Any]:
        return remove_cron_job_action(self, job_id)

    async def serve_forever(self, interactive: bool = False) -> None:
        await serve_forever_action(self, interactive=interactive)

    def _register_fusion_tools(self) -> None:
        register_fusion_tools(self)

    def _load_plugins(self, persist: bool = True) -> dict[str, Any]:
        return load_runtime_plugins(self, persist=persist)

    def reload_plugins(self) -> dict[str, Any]:
        return reload_plugins_action(self)

    def _ensure_runtime_storage(self) -> None:
        ensure_runtime_storage_for_runtime(self)

    async def _on_bridge_message(self, msg: BridgeMessage) -> str:
        session_key = msg.session_key or f"{msg.channel}:{msg.chat_id}"
        return await self.ask(msg.content, session_key=session_key, channel=msg.channel, chat_id=msg.chat_id)

    def _setup_cron_callback(self) -> None:
        setup_runtime_cron_callback(self)

    async def _on_heartbeat_execute(self, tasks: str) -> str:
        return await self.ask(tasks, session_key="heartbeat")

    async def _on_heartbeat_notify(self, response: str) -> None:
        await self._notifications.put(f"[heartbeat] {response}")

    async def _drain_notifications(self) -> None:
        await drain_runtime_notifications(self._notifications, logger=logger)

    def _build_system_prompt(self) -> str:
        return build_runtime_system_prompt(self)

    def _persist_state(self) -> None:
        persist_runtime_state(self)

    def _write_commander_identity(self) -> None:
        write_runtime_identity_file(self)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    return build_parser()


async def _run_async(args: argparse.Namespace) -> int:
    return await run_async(
        args,
        config_cls=CommanderConfig,
        runtime_cls=CommanderRuntime,
    )


def main() -> int:
    return run_cli_main(
        config_cls=CommanderConfig,
        runtime_cls=CommanderRuntime,
    )


if __name__ == "__main__":
    raise SystemExit(main())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified Commander for Invest Evolution")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workspace", help="Workspace path for commander runtime")
        p.add_argument("--playbook-dir", help="Commander playbook directory (md/json/py)")
        p.add_argument("--model", help="LLM model id, e.g. minimax/MiniMax-M2.5-highspeed")
        p.add_argument("--api-key", help="LLM API key")
        p.add_argument("--api-base", help="LLM API base URL")
        p.add_argument("--mock", action="store_true", help="Enable mock data mode")

    p_run = sub.add_parser("run", help="Start 24/7 commander daemon")
    add_common_args(p_run)
    p_run.add_argument("--interactive", action="store_true", help="Enable stdin chat while daemon runs")
    p_run.add_argument("--no-autopilot", action="store_true", help="Disable periodic auto-training")
    p_run.add_argument("--no-heartbeat", action="store_true", help="Disable heartbeat loop")
    p_run.add_argument("--train-interval-sec", type=int, help="Autopilot interval seconds")
    p_run.add_argument("--heartbeat-interval-sec", type=int, help="Heartbeat interval seconds")

    p_status = sub.add_parser("status", help="Print commander status snapshot")
    add_common_args(p_status)
    p_status.add_argument("--detail", choices=["fast", "slow"], default="fast", help="Status detail mode")
    p_status.add_argument(
        "--view",
        choices=["auto", "human", "json"],
        default="auto",
        help="CLI output view: auto prints human summary on TTY, json keeps full payload",
    )

    p_train = sub.add_parser("train-once", help="Run training cycles once")
    add_common_args(p_train)
    p_train.add_argument("--rounds", type=int, default=1, help="Number of cycles to run")
    p_train.add_argument(
        "--view",
        choices=["auto", "human", "json"],
        default="auto",
        help="CLI output view: auto prints human summary on TTY, json keeps full payload",
    )

    p_ask = sub.add_parser("ask", help="Send one message to fused commander brain")
    add_common_args(p_ask)
    p_ask.add_argument("-m", "--message", required=True, help="User message")
    p_ask.add_argument(
        "--view",
        choices=["auto", "human", "json"],
        default="auto",
        help="CLI output view: auto prints human receipt on TTY, json keeps full payload",
    )
    p_ask.add_argument(
        "--stream-events",
        action="store_true",
        help="Stream session-bound runtime events before printing the final reply",
    )

    p_genes = sub.add_parser("playbooks", help="List commander playbooks")
    add_common_args(p_genes)
    p_genes.add_argument("--reload", action="store_true", help="Reload commander playbooks from disk")
    p_genes.add_argument("--only-enabled", action="store_true", help="Show only enabled playbooks")

    return parser


async def run_async(
    args: argparse.Namespace,
    *,
    config_cls: Any,
    runtime_cls: Any,
) -> int:
    def emit_payload(payload: Any) -> None:
        selected_view = str(getattr(args, "view", "auto"))
        if selected_view == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        display = build_human_display(payload if isinstance(payload, dict) else {})
        if selected_view == "human" or (selected_view == "auto" and sys.stdout.isatty() and display.get("available")):
            print(str(display.get("text") or json.dumps(payload, ensure_ascii=False, indent=2)))
            return
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    cfg = config_cls.from_args(args)
    runtime = runtime_cls(cfg)

    if args.cmd == "status":
        emit_payload(runtime.status(detail=getattr(args, "detail", "fast")))
        return 0

    if args.cmd == "train-once":
        out = await runtime.train_once(rounds=max(1, int(args.rounds)), mock=cfg.mock_mode)
        emit_payload(out)
        return 0

    if args.cmd == "ask":
        session_key = "cli:direct"
        chat_id = "direct"
        reply = ""
        summary_packet = None
        if bool(getattr(args, "stream_events", False)):
            request_id = runtime.new_request_id()
            subscription_id, event_queue = runtime.subscribe_event_stream(
                session_key=session_key,
                chat_id=chat_id,
                request_id=request_id,
            )
            try:
                task = asyncio.create_task(
                    runtime.ask(
                        args.message,
                        session_key=session_key,
                        channel="cli",
                        chat_id=chat_id,
                        request_id=request_id,
                    )
                )
                while True:
                    try:
                        packet = await asyncio.to_thread(event_queue.get, True, 0.2)
                    except queue.Empty:
                        if task.done():
                            break
                        continue
                    text = str(
                        packet.get("display_text")
                        or packet.get("human_reply")
                        or packet.get("broadcast_text")
                        or packet.get("label")
                        or packet.get("event")
                        or ""
                    ).strip()
                    if text:
                        print(text, flush=True)
                summary_packet = runtime.build_stream_summary_packet(subscription_id)
                summary_text = str(summary_packet.get("display_text") or "").strip()
                if summary_text:
                    print(summary_text, flush=True)
                reply = await task
            finally:
                runtime.unsubscribe_event_stream(subscription_id)
        else:
            reply = await runtime.ask(args.message, session_key=session_key, channel="cli", chat_id=chat_id)
        if str(getattr(args, "view", "auto")) == "json":
            print(reply)
            return 0
        payload = parse_ask_response_payload(reply) or None
        if isinstance(payload, dict) and summary_packet:
            payload = runtime.merge_stream_summary_into_reply_payload(payload, summary_packet)
        display = build_human_display(payload) if isinstance(payload, dict) else {"available": False}
        selected_view = str(getattr(args, "view", "auto"))
        if selected_view == "human" or (selected_view == "auto" and sys.stdout.isatty() and display.get("available")):
            print(str(display.get("text") or reply))
        else:
            print(reply)
        return 0

    if args.cmd == "playbooks":
        if args.reload:
            runtime.reload_playbooks()
        playbooks = runtime.playbook_registry.list_playbooks(only_enabled=bool(args.only_enabled))
        print(
            json.dumps(
                {"count": len(playbooks), "items": [playbook.to_dict() for playbook in playbooks]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.cmd == "run":
        try:
            await runtime.serve_forever(interactive=bool(args.interactive))
            return 0
        finally:
            await runtime.stop()

    raise ValueError(f"Unknown command: {args.cmd}")


def _cli_error_code_for_exception(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return "CMD_VALIDATION"
    if isinstance(exc, RuntimeError):
        return "CMD_RUNTIME"
    return "CMD_UNHANDLED"


def _cli_exit_code_for_exception(exc: Exception) -> int:
    if isinstance(exc, ValueError):
        return 2
    return 1


def _emit_cli_error_payload(exc: Exception, *, cmd: str) -> None:
    payload = {
        "status": "error",
        "error": str(exc),
        "error_code": _cli_error_code_for_exception(exc),
        "command": str(cmd or ""),
    }
    sys.stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_cli_main(
    *,
    config_cls: Any,
    runtime_cls: Any,
) -> int:
    parser = build_parser()
    args = parser.parse_args()
    _ensure_commander_cli_environment(args)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    try:
        return asyncio.run(run_async(args, config_cls=config_cls, runtime_cls=runtime_cls))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.exception(
            "Commander CLI execution failed: cmd=%s error_type=%s",
            getattr(args, "cmd", ""),
            type(exc).__name__,
        )
        _emit_cli_error_payload(exc, cmd=str(getattr(args, "cmd", "") or ""))
        return _cli_exit_code_for_exception(exc)
