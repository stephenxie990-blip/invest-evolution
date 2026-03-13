"""
Unified Commander runtime for the fused Brain + Invest system.

Design goals:
1. One process, one entrypoint: local brain runtime and investment body run together.
2. 24/7 daemon mode with optional autopilot training cycles.
3. Strategy genes are pluggable files (md/json/py) and hot-reloadable.
4. Scheduler primitives (cron + heartbeat) are implemented locally in src/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from brain.runtime import BrainRuntime
from brain.planner_catalog import (
    build_commander_bounded_workflow_plan,
)
from brain.scheduler import CronService, HeartbeatService
from brain.tool_metadata import (
    INVEST_DEEP_STATUS_TOOL_NAME,
    INVEST_QUICK_STATUS_TOOL_NAME,
)
from brain.tools import build_commander_tools
from brain.memory import MemoryStore
from brain.bridge import BridgeHub, BridgeMessage
from brain.plugins import PluginLoader
from config import PROJECT_ROOT, RUNTIME_DIR, OUTPUT_DIR, LOGS_DIR, MEMORY_DIR, SESSIONS_DIR, WORKSPACE_DIR, config
from config.control_plane import resolve_component_llm, resolve_default_llm
from config.services import EvolutionConfigService, RuntimePathConfigService
from invest.meetings import MeetingRecorder
from app.lab.artifacts import TrainingLabArtifactStore
from app.investment_body_service import InvestmentBodyService
from app.commander_status_support import (
    build_persisted_status_payload,
    collect_data_status,
    normalize_status_detail,
)
from app.commander_training_support import (
    append_training_memory,
    attach_training_lab_paths,
    build_commander_promotion_summary,
    build_commander_training_evaluation_summary,
    execute_training_plan_flow,
    load_leaderboard_snapshot,
    record_training_lab_artifacts,
    summarize_research_feedback_promotion,
    summarize_training_evaluation_brief,
)
from app.commander_training_plan_support import (
    build_experiment_spec_from_plan,
    build_run_cycles_kwargs,
    load_training_plan_artifact,
)
from app.commander_identity_support import (
    build_commander_soul,
    build_commander_system_prompt,
    build_heartbeat_tasks_markdown,
)
from app.commander_ask_support import (
    execute_runtime_ask,
    record_runtime_ask_activity,
)
from app.commander_config_support import (
    apply_runtime_path_overrides as apply_runtime_path_overrides_impl,
    build_commander_config_from_args,
    relocate_commander_state_paths,
    sync_runtime_path_config as sync_runtime_path_config_impl,
)
from app.commander_cli import (
    build_parser as build_commander_cli_parser,
    run_async as run_commander_cli_async,
    run_cli_main,
)
from app.commander_runtime_state_support import (
    acquire_runtime_lock,
    apply_restored_body_state,
    build_finished_task,
    build_started_task,
    copy_runtime_task,
    is_pid_alive,
    read_runtime_lock_payload,
    release_runtime_lock,
)
from app.commander_runtime_lifecycle_support import (
    drain_runtime_notifications,
    ensure_runtime_storage,
    load_persisted_runtime_state,
    persist_runtime_state,
    setup_cron_callback,
    start_runtime_background_services,
    stop_runtime_background_services,
    write_commander_identity_artifacts,
)
from app.commander_runtime_query_support import (
    get_events_summary_response,
    get_events_tail_response,
    get_runtime_diagnostics_response,
    get_status_response,
    get_training_lab_summary_response,
)
from app.commander_plugin_support import (
    load_plugin_tools,
    register_fusion_tools as register_fusion_tools_impl,
)
from app.commander_services import (
    get_allocator_payload,
    get_capital_flow_payload,
    get_control_plane_payload,
    get_data_download_status_payload,
    get_data_status_payload,
    get_dragon_tiger_payload,
    get_evolution_config_payload,
    get_investment_models_payload,
    get_intraday_60m_payload,
    get_leaderboard_payload,
    get_model_routing_preview_payload,
    get_runtime_paths_payload,
    list_agent_prompts_payload,
    trigger_data_download,
    update_agent_prompt_payload,
    update_control_plane_payload,
    update_evolution_config_payload,
    update_runtime_paths_payload,
)
from app.commander_observability import (
    append_event_row,
    build_memory_detail,
    memory_brief_row,
)
from app.commander_domain_catalog import get_domain_agent_kind, get_domain_tools
from app.strategy_gene_registry import StrategyGeneRegistry
from app.commander_workflow_support import (
    attach_bounded_workflow_response,
    jsonable as _jsonable,
)
from app.research_services import (
    get_research_attributions_payload,
    get_research_calibration_payload,
    get_research_cases_payload,
)
from app.stock_analysis import StockAnalysisService
from invest.research.case_store import ResearchCaseStore

logger = logging.getLogger(__name__)

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
RUNTIME_STATE_RELOADING_STRATEGIES = "reloading_strategies"

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


def _commander_llm_default(field_name: str, fallback: str = "") -> str:
    try:
        default_fast = resolve_default_llm("fast")
        resolved = resolve_component_llm(
            "commander.brain",
            fallback_model=default_fast.model,
            fallback_api_key=default_fast.api_key,
            fallback_api_base=default_fast.api_base,
        )
    except Exception:
        return fallback
    value = getattr(resolved, field_name, "") or fallback
    return str(value or fallback)


def _apply_runtime_path_overrides(cfg: "CommanderConfig", overrides: dict[str, Any]) -> "CommanderConfig":
    return apply_runtime_path_overrides_impl(
        cfg,
        overrides,
        editable_keys=RuntimePathConfigService.EDITABLE_KEYS,
    )


def _sync_runtime_path_config(runtime: "CommanderRuntime", payload: dict[str, Any]) -> None:
    sync_runtime_path_config_impl(
        runtime,
        payload,
        editable_keys=RuntimePathConfigService.EDITABLE_KEYS,
        meeting_recorder_cls=MeetingRecorder,
        evolution_config_service_cls=EvolutionConfigService,
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class CommanderConfig:
    """Runtime config for the fused commander."""

    workspace: Path = WORKSPACE_DIR
    strategy_dir: Path = PROJECT_ROOT / "strategies"
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
    meeting_log_dir: Path = LOGS_DIR / "meetings"
    config_audit_log_path: Path = RUNTIME_DIR / "state" / "config_changes.jsonl"
    config_snapshot_dir: Path = RUNTIME_DIR / "state" / "config_snapshots"
    training_plan_dir: Path = RUNTIME_DIR / "state" / "training_plans"
    training_run_dir: Path = RUNTIME_DIR / "state" / "training_runs"
    training_eval_dir: Path = RUNTIME_DIR / "state" / "training_evals"
    runtime_events_path: Path = RUNTIME_DIR / "state" / "commander_events.jsonl"
    stock_strategy_dir: Path = PROJECT_ROOT / "stock_strategies"

    model: str = field(default_factory=lambda: os.environ.get("COMMANDER_MODEL", _commander_llm_default("model")))
    api_key: str = field(default_factory=lambda: os.environ.get("COMMANDER_API_KEY", _commander_llm_default("api_key")))
    api_base: str = field(default_factory=lambda: os.environ.get("COMMANDER_API_BASE", _commander_llm_default("api_base")))
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
            default_meeting_log_dir=LOGS_DIR / "meetings",
            state_dir_relocations=_STATE_DIR_RELOCATIONS,
        )

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "CommanderConfig":
        return build_commander_config_from_args(
            cls,
            args,
            path_config_service_cls=RuntimePathConfigService,
            project_root=PROJECT_ROOT,
            apply_runtime_overrides=_apply_runtime_path_overrides,
        )


# ---------------------------------------------------------------------------
# Commander runtime
# ---------------------------------------------------------------------------

class CommanderRuntime:
    """Unified runtime: local brain + invest body in one process."""

    def __init__(self, cfg: CommanderConfig):
        self.cfg = cfg
        self.config_service = EvolutionConfigService(project_root=PROJECT_ROOT, live_config=config)
        self.instance_id = f"{socket.gethostname()}:{os.getpid()}"
        self.runtime_state = RUNTIME_STATE_INITIALIZED
        self.current_task: Optional[dict[str, Any]] = None
        self.last_task: Optional[dict[str, Any]] = None
        self._task_lock = threading.RLock()
        self._runtime_lock_acquired = False

        self.training_lab = TrainingLabArtifactStore(
            training_plan_dir=self.cfg.training_plan_dir,
            training_run_dir=self.cfg.training_run_dir,
            training_eval_dir=self.cfg.training_eval_dir,
        )
        self.strategy_registry = StrategyGeneRegistry(self.cfg.strategy_dir)
        if self.cfg.strategy_dir.exists():
            self.strategy_registry.reload(create_dir=False)

        self.body = InvestmentBodyService(self.cfg, on_runtime_event=self._on_body_event)
        self.brain = BrainRuntime(
            workspace=self.cfg.workspace,
            model=self.cfg.model,
            api_key=self.cfg.api_key,
            api_base=self.cfg.api_base,
            temperature=self.cfg.temperature,
            max_tokens=self.cfg.max_tokens,
            max_iterations=self.cfg.max_tool_iterations,
            memory_window=self.cfg.memory_window,
            system_prompt_provider=self._build_system_prompt,
        )
        self.cron = CronService(self.cfg.cron_store)
        self.heartbeat = HeartbeatService(
            workspace=self.cfg.workspace,
            on_execute=self._on_heartbeat_execute,
            on_notify=self._on_heartbeat_notify,
            interval_s=self.cfg.heartbeat_interval_sec,
            enabled=self.cfg.heartbeat_enabled,
        )
        self._notifications: asyncio.Queue[str] = asyncio.Queue()
        self.memory = MemoryStore(self.cfg.memory_store, create=False)
        self.plugin_loader = PluginLoader(self.cfg.plugin_dir, create_dir=False)
        self.stock_analysis = StockAnalysisService(strategy_dir=self.cfg.stock_strategy_dir, model=self.cfg.model, api_key=self.cfg.api_key, api_base=self.cfg.api_base, enable_llm_react=not self.cfg.mock_mode, controller_provider=lambda: self.body.controller)
        self.research_case_store = ResearchCaseStore(self.cfg.runtime_state_dir)
        self._plugin_tool_names: set[str] = set()
        self.bridge = BridgeHub(
            inbox_dir=self.cfg.bridge_inbox,
            outbox_dir=self.cfg.bridge_outbox,
            on_message=self._on_bridge_message,
            poll_interval_sec=self.cfg.bridge_poll_interval_sec,
            enabled=self.cfg.bridge_enabled,
        )

        self._register_fusion_tools()
        self._setup_cron_callback()

        self._started = False
        self._notify_task: Optional[asyncio.Task] = None
        self._autopilot_task: Optional[asyncio.Task] = None
        self._restore_persisted_state()

    def _on_body_event(self, event: str, payload: dict[str, Any]) -> None:
        self._append_runtime_event(event, payload, source="body")
        if event == EVENT_TRAINING_STARTED:
            self._update_runtime_fields(state=STATUS_TRAINING, current_task=payload)
        elif event == EVENT_TRAINING_FINISHED:
            self._update_runtime_fields(state=STATUS_IDLE, current_task=None, last_task=payload)
        self._persist_state()

    def _append_runtime_event(self, event: str, payload: dict[str, Any], *, source: str = "runtime") -> dict[str, Any]:
        return append_event_row(self.cfg.runtime_events_path, event, payload, source=source)

    def _restore_persisted_state(self) -> None:
        payload = load_persisted_runtime_state(self.cfg.state_file, logger=logger)
        if payload is None:
            return
        runtime_payload = dict(payload.get("runtime") or {})
        body_payload = dict(payload.get("body") or {})
        self._update_runtime_fields(
            state=runtime_payload.get("state", self.runtime_state),
            current_task=runtime_payload.get("current_task"),
            last_task=runtime_payload.get("last_task"),
        )
        apply_restored_body_state(self.body, body_payload)

    def _set_runtime_state(self, state: str) -> None:
        self._update_runtime_fields(state=state)

    @staticmethod
    def _copy_runtime_task(task: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        return copy_runtime_task(task)

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
        task = build_started_task(task_type, source, **metadata)
        self._update_runtime_fields(current_task=task)
        self._append_runtime_event(EVENT_TASK_STARTED, task, source="runtime")

    def _end_task(self, status: str = STATUS_OK, **metadata: Any) -> None:
        with self._task_lock:
            if self.current_task is None:
                return
            self.last_task = build_finished_task(
                self.current_task,
                status=status,
                copy_task=self._copy_runtime_task,
                **metadata,
            )
            self._append_runtime_event(EVENT_TASK_FINISHED, self.last_task, source="runtime")
            self.current_task = None

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
        extra: dict[str, Any] | None = None,
    ) -> None:
        record_runtime_ask_activity(
            memory=self.memory,
            append_runtime_event=lambda event_name, payload: self._append_runtime_event(
                event_name,
                payload,
                source="brain",
            ),
            event=event,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
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

    async def start(self) -> None:
        if self._started:
            return
        self._ensure_runtime_storage()
        self._begin_task("start", "system")
        self._set_runtime_state(RUNTIME_STATE_STARTING)
        try:
            self._acquire_runtime_lock()
            self.strategy_registry.ensure_default_templates()
            self.strategy_registry.reload()
            self._load_plugins(persist=False)
            self._write_commander_identity()
            self._notify_task, self._autopilot_task = await start_runtime_background_services(
                cron=self.cron,
                heartbeat=self.heartbeat,
                bridge=self.bridge,
                heartbeat_enabled=self.cfg.heartbeat_enabled,
                bridge_enabled=self.cfg.bridge_enabled,
                drain_notifications=self._drain_notifications,
                autopilot_enabled=self.cfg.autopilot_enabled,
                autopilot_loop=self.body.autopilot_loop,
                training_interval_sec=self.cfg.training_interval_sec,
            )

            self._started = True
            self._complete_runtime_task(state=STATUS_IDLE, status=STATUS_OK)
        except Exception:
            self._set_runtime_state(STATUS_ERROR)
            self._end_task(STATUS_ERROR)
            self._release_runtime_lock()
            self._persist_state()
            raise

    async def stop(self) -> None:
        if not self._started:
            return
        self._begin_task("stop", "system")
        self._set_runtime_state(RUNTIME_STATE_STOPPING)
        self._notify_task, self._autopilot_task = await stop_runtime_background_services(
            body=self.body,
            autopilot_task=self._autopilot_task,
            notify_task=self._notify_task,
            bridge=self.bridge,
            heartbeat=self.heartbeat,
            cron=self.cron,
            brain=self.brain,
        )
        self._started = False
        self._release_runtime_lock()
        self._complete_runtime_task(state=RUNTIME_STATE_STOPPED, status=STATUS_OK)

    async def ask(
        self,
        message: str,
        session_key: str = "commander:direct",
        channel: str = "cli",
        chat_id: str = "direct",
    ) -> str:
        return await execute_runtime_ask(
            message=message,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            ensure_runtime_storage=self._ensure_runtime_storage,
            begin_task=self._begin_task,
            memory=self.memory,
            record_ask_activity=self._record_ask_activity,
            process_direct=self.brain.process_direct,
            complete_runtime_task=self._complete_runtime_task,
            status_ok=STATUS_OK,
            status_error=STATUS_ERROR,
            event_ask_started=EVENT_ASK_STARTED,
            event_ask_finished=EVENT_ASK_FINISHED,
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
        model_scope: dict[str, Any] | None = None,
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
            model_scope=model_scope,
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
        model_scope: dict[str, Any] | None = None,
        optimization: dict[str, Any] | None = None,
        llm: dict[str, Any] | None = None,
        source: str = "manual",
        auto_generated: bool = False,
    ) -> dict[str, Any]:
        self._ensure_runtime_storage()
        plan = self._create_training_plan_payload(
            rounds=rounds,
            mock=mock,
            source=source,
            goal=goal,
            notes=notes,
            tags=tags,
            detail_mode=detail_mode,
            protocol=protocol,
            dataset=dataset,
            model_scope=model_scope,
            optimization=optimization,
            llm=llm,
            auto_generated=auto_generated,
        )
        self._write_json_artifact(self._training_plan_path(plan["plan_id"]), plan)
        self._persist_state()
        return plan

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

    def get_investment_models(self) -> dict[str, Any]:
        payload = get_investment_models_payload(self)
        return self._attach_domain_readonly_workflow(
            payload,
            domain="analytics",
            operation="get_investment_models",
            runtime_tool="invest_investment_models",
            phase="investment_models_read",
            phase_stats={"count": int(payload.get("count", len(list(payload.get("items") or []))))},
        )

    def get_leaderboard(self) -> dict[str, Any]:
        payload = get_leaderboard_payload(self)
        return self._attach_domain_readonly_workflow(
            payload,
            domain="analytics",
            operation="get_leaderboard",
            runtime_tool="invest_leaderboard",
            phase="leaderboard_read",
            phase_stats={"count": int(payload.get("count", len(list(payload.get("items") or []))))},
        )

    def get_allocator_preview(self, *, regime: str = "oscillation", top_n: int = 3, as_of_date: str | None = None) -> dict[str, Any]:
        payload = get_allocator_payload(self, regime=regime, top_n=top_n, as_of_date=as_of_date)
        return self._attach_domain_readonly_workflow(
            payload,
            domain="analytics",
            operation="get_allocator_preview",
            runtime_tool="invest_allocator",
            phase="allocator_preview_read",
            phase_stats={"regime": regime, "top_n": int(top_n)},
        )

    def get_model_routing_preview(
        self,
        *,
        cutoff_date: str | None = None,
        stock_count: int | None = None,
        min_history_days: int | None = None,
        allowed_models: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = get_model_routing_preview_payload(
            self,
            cutoff_date=cutoff_date,
            stock_count=stock_count,
            min_history_days=min_history_days,
            allowed_models=allowed_models,
        )
        return self._attach_domain_readonly_workflow(
            payload,
            domain="analytics",
            operation="get_model_routing_preview",
            runtime_tool="invest_model_routing_preview",
            phase="routing_preview_read",
            phase_stats={
                "cutoff_date": cutoff_date or "",
                "stock_count": stock_count,
                "min_history_days": min_history_days,
                "allowed_model_count": len(list(allowed_models or [])),
            },
        )

    def list_agent_prompts(self) -> dict[str, Any]:
        payload = list_agent_prompts_payload()
        items = list(payload.get("items") or []) if isinstance(payload, dict) else []
        return self._attach_domain_readonly_workflow(
            payload,
            domain="config",
            operation="list_agent_prompts",
            runtime_tool="invest_agent_prompts_list",
            phase="agent_prompts_read",
            phase_stats={"count": len(items)},
        )

    def update_agent_prompt(self, *, agent_name: str, system_prompt: str) -> dict[str, Any]:
        payload = update_agent_prompt_payload(agent_name=agent_name, system_prompt=system_prompt)
        self._append_runtime_event("agent_prompt_updated", {"agent_name": agent_name}, source="config")
        return self._attach_domain_mutating_workflow(
            payload,
            domain="config",
            operation="update_agent_prompt",
            runtime_tool="invest_agent_prompts_update",
            phase="agent_prompt_write",
            phase_stats={"agent_name": agent_name, "prompt_length": len(system_prompt)},
        )

    def get_runtime_paths(self) -> dict[str, Any]:
        payload = get_runtime_paths_payload(self, project_root=PROJECT_ROOT)
        return self._attach_domain_readonly_workflow(
            payload,
            domain="config",
            operation="get_runtime_paths",
            runtime_tool="invest_runtime_paths_get",
            phase="runtime_paths_read",
            phase_stats={"path_count": len(dict(payload.get("paths") or {})) if isinstance(payload, dict) else 0},
        )

    def update_runtime_paths(self, patch: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            return self._build_confirmation_required_workflow(
                domain="config",
                operation="update_runtime_paths",
                runtime_tool="invest_runtime_paths_update",
                message="runtime paths 更新会立即改变运行期产物目录，请用 confirm=true 再执行。",
                pending={"patch": patch},
                phase_stats={"pending_key_count": len(dict(patch or {})), "requires_confirmation": True},
            )
        payload = update_runtime_paths_payload(
            patch=patch,
            runtime=self,
            project_root=PROJECT_ROOT,
            sync_runtime=_sync_runtime_path_config,
        )
        self._append_runtime_event("runtime_paths_updated", {"updated": payload.get("updated", [])}, source="config")
        return self._attach_domain_mutating_workflow(
            payload,
            domain="config",
            operation="update_runtime_paths",
            runtime_tool="invest_runtime_paths_update",
            phase="runtime_paths_write",
            phase_stats={"updated_count": len(list(payload.get("updated") or [])), "confirmed": True},
        )

    @staticmethod
    def _domain_tools(domain: str) -> list[str]:
        return get_domain_tools(domain)

    @staticmethod
    def _domain_agent_kind(domain: str, default: str = "bounded_runtime_agent") -> str:
        return get_domain_agent_kind(domain, default=default)


    def _recommended_plan_for_bounded_workflow(
        self,
        *,
        domain: str,
        operation: str,
        runtime_tool: str,
        writes_state: bool,
        phase_stats: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return build_commander_bounded_workflow_plan(
            domain=domain,
            operation=operation,
            runtime_tool=runtime_tool,
            phase_stats=phase_stats,
            payload=payload,
        )

    @staticmethod
    def _runtime_method_label(operation: str, runtime_method: str | None = None) -> str:
        return str(runtime_method or f"CommanderRuntime.{operation}")

    @staticmethod
    def _domain_workflow(domain: str, phase: str, *extra_phases: str) -> list[str]:
        return [f"{domain}_scope_resolve", phase, *extra_phases, "finalize"]

    def _attach_bounded_workflow(
        self,
        payload: Any,
        *,
        domain: str,
        operation: str,
        runtime_method: str | None = None,
        runtime_tool: str,
        agent_kind: str | None = None,
        writes_state: bool,
        available_tools: list[str] | None = None,
        workflow: list[str],
        phase_stats: dict[str, Any] | None = None,
        extra_policy: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
        resolved_agent_kind = str(agent_kind or self._domain_agent_kind(domain))
        resolved_available_tools = list(available_tools or self._domain_tools(domain))
        resolved_runtime_method = self._runtime_method_label(operation, runtime_method)
        recommended_plan = self._recommended_plan_for_bounded_workflow(
            domain=domain,
            operation=operation,
            runtime_tool=runtime_tool,
            writes_state=writes_state,
            phase_stats=phase_stats,
            payload=dict(payload) if isinstance(payload, dict) else None,
        )
        return attach_bounded_workflow_response(
            payload=payload,
            domain=domain,
            operation=operation,
            runtime_method=resolved_runtime_method,
            runtime_tool=runtime_tool,
            agent_kind=resolved_agent_kind,
            writes_state=writes_state,
            available_tools=resolved_available_tools,
            workflow=workflow,
            workspace=str(self.cfg.workspace),
            recommended_plan=recommended_plan,
            phase_stats=phase_stats,
            extra_policy=extra_policy,
        )

    def _attach_readonly_workflow(
        self,
        payload: Any,
        *,
        domain: str,
        operation: str,
        runtime_method: str | None = None,
        runtime_tool: str,
        agent_kind: str | None = None,
        available_tools: list[str] | None = None,
        workflow: list[str],
        phase_stats: dict[str, Any] | None = None,
        extra_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._attach_bounded_workflow(
            payload,
            domain=domain,
            operation=operation,
            runtime_method=runtime_method,
            runtime_tool=runtime_tool,
            agent_kind=agent_kind,
            writes_state=False,
            available_tools=available_tools,
            workflow=workflow,
            phase_stats=phase_stats,
            extra_policy=extra_policy,
        )

    def _attach_mutating_workflow(
        self,
        payload: Any,
        *,
        domain: str,
        operation: str,
        runtime_method: str | None = None,
        runtime_tool: str,
        agent_kind: str | None = None,
        available_tools: list[str] | None = None,
        workflow: list[str],
        phase_stats: dict[str, Any] | None = None,
        extra_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._attach_bounded_workflow(
            payload,
            domain=domain,
            operation=operation,
            runtime_method=runtime_method,
            runtime_tool=runtime_tool,
            agent_kind=agent_kind,
            writes_state=True,
            available_tools=available_tools,
            workflow=workflow,
            phase_stats=phase_stats,
            extra_policy=extra_policy,
        )

    def _attach_domain_readonly_workflow(
        self,
        payload: Any,
        *,
        domain: str,
        operation: str,
        runtime_method: str | None = None,
        runtime_tool: str,
        agent_kind: str | None = None,
        available_tools: list[str] | None = None,
        phase: str,
        extra_phases: tuple[str, ...] = (),
        phase_stats: dict[str, Any] | None = None,
        extra_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._attach_readonly_workflow(
            payload,
            domain=domain,
            operation=operation,
            runtime_method=runtime_method,
            runtime_tool=runtime_tool,
            agent_kind=agent_kind,
            available_tools=available_tools,
            workflow=self._domain_workflow(domain, phase, *extra_phases),
            phase_stats=phase_stats,
            extra_policy=extra_policy,
        )

    def _attach_domain_mutating_workflow(
        self,
        payload: Any,
        *,
        domain: str,
        operation: str,
        runtime_method: str | None = None,
        runtime_tool: str,
        agent_kind: str | None = None,
        available_tools: list[str] | None = None,
        phase: str,
        extra_phases: tuple[str, ...] = (),
        phase_stats: dict[str, Any] | None = None,
        extra_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._attach_mutating_workflow(
            payload,
            domain=domain,
            operation=operation,
            runtime_method=runtime_method,
            runtime_tool=runtime_tool,
            agent_kind=agent_kind,
            available_tools=available_tools,
            workflow=self._domain_workflow(domain, phase, *extra_phases),
            phase_stats=phase_stats,
            extra_policy=extra_policy,
        )

    def _build_confirmation_required_workflow(
        self,
        *,
        domain: str,
        operation: str,
        runtime_method: str | None = None,
        runtime_tool: str,
        agent_kind: str | None = None,
        available_tools: list[str] | None = None,
        message: str,
        pending: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
        phase_stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {"status": STATUS_CONFIRMATION_REQUIRED, "message": message}
        if pending:
            payload["pending"] = _jsonable(dict(pending))
        if extra_payload:
            payload.update(_jsonable(dict(extra_payload)))
        return self._attach_domain_mutating_workflow(
            payload,
            domain=domain,
            operation=operation,
            runtime_method=runtime_method,
            runtime_tool=runtime_tool,
            agent_kind=agent_kind,
            available_tools=available_tools,
            phase="gate_confirmation",
            phase_stats=phase_stats,
            extra_policy={"confirmation_gate": True},
        )

    def build_training_confirmation_required(self, *, rounds: int, mock: bool) -> dict[str, Any]:
        return self._build_confirmation_required_workflow(
            domain="training",
            operation="train_once",
            runtime_tool="invest_train",
            message="多轮真实训练属于高风险操作，请使用 confirm=true 再执行。",
            pending={"rounds": int(rounds), "mock": bool(mock)},
            phase_stats={"rounds": int(rounds), "mock": bool(mock), "requires_confirmation": True},
        )

    def get_evolution_config(self) -> dict[str, Any]:
        payload = get_evolution_config_payload(project_root=PROJECT_ROOT, live_config=config)
        config_payload = dict(payload.get("config") or {}) if isinstance(payload, dict) else {}
        return self._attach_domain_readonly_workflow(
            payload,
            domain="config",
            operation="get_evolution_config",
            runtime_tool="invest_evolution_config_get",
            phase="evolution_config_read",
            phase_stats={"config_key_count": len(config_payload)},
        )

    def update_evolution_config(self, patch: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
        if not confirm and any(key in patch for key in ("investment_model", "investment_model_config", "data_source", "model_routing_enabled", "model_routing_mode")):
            return self._build_confirmation_required_workflow(
                domain="config",
                operation="update_evolution_config",
                runtime_tool="invest_evolution_config_update",
                message="当前 patch 会影响训练主链路，请用 confirm=true 再执行。",
                pending={"patch": patch},
                phase_stats={"pending_key_count": len(dict(patch or {})), "requires_confirmation": True},
            )
        payload = update_evolution_config_payload(patch=patch, project_root=PROJECT_ROOT, live_config=config, source="commander")
        controller = getattr(getattr(self, "body", None), "controller", None)
        if controller is not None and hasattr(controller, "refresh_runtime_from_config"):
            controller.refresh_runtime_from_config()
        self._append_runtime_event("evolution_config_updated", {"updated": payload.get("updated", [])}, source="config")
        return self._attach_domain_mutating_workflow(
            payload,
            domain="config",
            operation="update_evolution_config",
            runtime_tool="invest_evolution_config_update",
            phase="evolution_config_write",
            phase_stats={"updated_count": len(list(payload.get("updated") or [])), "confirmed": bool(confirm)},
        )

    def get_control_plane(self) -> dict[str, Any]:
        payload = get_control_plane_payload(project_root=PROJECT_ROOT)
        config_payload = dict(payload.get("config") or {}) if isinstance(payload, dict) else {}
        return self._attach_domain_readonly_workflow(
            payload,
            domain="config",
            operation="get_control_plane",
            runtime_tool="invest_control_plane_get",
            phase="control_plane_read",
            phase_stats={"config_section_count": len(config_payload)},
        )

    def update_control_plane(self, patch: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            return self._build_confirmation_required_workflow(
                domain="config",
                operation="update_control_plane",
                runtime_tool="invest_control_plane_update",
                message="control plane 更新需要重启才能全局生效，请用 confirm=true 再执行。",
                pending={"patch": patch},
                extra_payload={"restart_required": True},
                phase_stats={"pending_key_count": len(dict(patch or {})), "requires_confirmation": True, "restart_required": True},
            )
        payload = update_control_plane_payload(patch=patch, project_root=PROJECT_ROOT, source="commander")
        self._append_runtime_event("control_plane_updated", {"updated": payload.get("updated", [])}, source="config")
        return self._attach_domain_mutating_workflow(
            payload,
            domain="config",
            operation="update_control_plane",
            runtime_tool="invest_control_plane_update",
            phase="control_plane_write",
            phase_stats={"updated_count": len(list(payload.get("updated") or [])), "confirmed": bool(confirm), "restart_required": True},
        )

    def get_data_status(self, *, refresh: bool = False) -> dict[str, Any]:
        payload = get_data_status_payload(refresh=refresh)
        quality = dict(payload.get("quality") or {}) if isinstance(payload, dict) else {}
        return self._attach_domain_readonly_workflow(
            payload,
            domain="data",
            operation="get_data_status",
            runtime_tool="invest_data_status",
            phase="data_status_refresh" if refresh else "data_status_read",
            phase_stats={"requested_refresh": bool(refresh), "health_status": quality.get("health_status", "unknown")},
        )

    def get_capital_flow(self, *, codes: list[str] | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 200) -> dict[str, Any]:
        payload = get_capital_flow_payload(codes=codes, start_date=start_date, end_date=end_date, limit=limit)
        return self._attach_domain_readonly_workflow(
            payload,
            domain="data",
            operation="get_capital_flow",
            runtime_tool="invest_data_capital_flow",
            phase="capital_flow_query",
            phase_stats={"count": int(payload.get("count", 0)), "limit": int(limit)},
        )

    def get_dragon_tiger(self, *, codes: list[str] | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 200) -> dict[str, Any]:
        payload = get_dragon_tiger_payload(codes=codes, start_date=start_date, end_date=end_date, limit=limit)
        return self._attach_domain_readonly_workflow(
            payload,
            domain="data",
            operation="get_dragon_tiger",
            runtime_tool="invest_data_dragon_tiger",
            phase="dragon_tiger_query",
            phase_stats={"count": int(payload.get("count", 0)), "limit": int(limit)},
        )

    def get_intraday_60m(self, *, codes: list[str] | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 500) -> dict[str, Any]:
        payload = get_intraday_60m_payload(codes=codes, start_date=start_date, end_date=end_date, limit=limit)
        return self._attach_domain_readonly_workflow(
            payload,
            domain="data",
            operation="get_intraday_60m",
            runtime_tool="invest_data_intraday_60m",
            phase="intraday_60m_query",
            phase_stats={"count": int(payload.get("count", 0)), "limit": int(limit)},
        )

    def get_data_download_status(self) -> dict[str, Any]:
        payload = get_data_download_status_payload()
        return self._attach_domain_readonly_workflow(
            payload,
            domain="data",
            operation="get_data_download_status",
            runtime_tool="invest_data_download",
            phase="download_job_read",
            phase_stats={"job_status": str(payload.get("status", "unknown"))},
        )

    def trigger_data_download(self, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            return self._build_confirmation_required_workflow(
                domain="data",
                operation="trigger_data_download",
                runtime_tool="invest_data_download",
                message="后台数据同步会访问外部数据源，请用 confirm=true 再执行。",
                extra_payload={"job": get_data_download_status_payload()},
                phase_stats={"requires_confirmation": True},
            )
        payload = trigger_data_download()
        self._append_runtime_event("data_download_triggered", payload, source="data")
        return self._attach_domain_mutating_workflow(
            payload,
            domain="data",
            operation="trigger_data_download",
            runtime_tool="invest_data_download",
            phase="download_job_trigger",
            phase_stats={"job_status": str(payload.get("status", "unknown")), "confirmed": True},
        )

    def list_memory(self, *, query: str = "", limit: int = 20) -> dict[str, Any]:
        rows = self.memory.search(query=query, limit=limit)
        items = [memory_brief_row(row) for row in rows]
        return self._attach_domain_readonly_workflow(
            {"count": len(items), "items": items},
            domain="memory",
            operation="list_memory",
            runtime_tool="invest_memory_list",
            phase="memory_query",
            phase_stats={"query": query, "count": len(items), "limit": int(limit)},
        )

    def get_memory_detail(self, record_id: str) -> dict[str, Any]:
        row = self.memory.get(record_id)
        if row is None:
            raise FileNotFoundError("memory record not found")
        payload = build_memory_detail(self, row)
        return self._attach_domain_readonly_workflow(
            payload,
            domain="memory",
            operation="get_memory_detail",
            runtime_tool="invest_memory_get",
            phase="memory_detail_read",
            phase_stats={"record_id": str(record_id)},
        )

    def get_events_tail(self, *, limit: int = 50) -> dict[str, Any]:
        return get_events_tail_response(
            self,
            limit=limit,
            attach_domain_readonly_workflow=self._attach_domain_readonly_workflow,
        )

    def get_events_summary(self, *, limit: int = 100) -> dict[str, Any]:
        return get_events_summary_response(
            self,
            limit=limit,
            ok_status=STATUS_OK,
            attach_domain_readonly_workflow=self._attach_domain_readonly_workflow,
        )

    def get_runtime_diagnostics(self, *, event_limit: int = 50, memory_limit: int = 20) -> dict[str, Any]:
        return get_runtime_diagnostics_response(
            self,
            event_limit=event_limit,
            memory_limit=memory_limit,
            attach_domain_readonly_workflow=self._attach_domain_readonly_workflow,
        )

    def get_training_lab_summary(self, *, limit: int = 5) -> dict[str, Any]:
        return get_training_lab_summary_response(
            self,
            limit=limit,
            ok_status=STATUS_OK,
            attach_domain_readonly_workflow=self._attach_domain_readonly_workflow,
        )

    def list_research_cases(self, *, limit: int = 20, policy_id: str = '', symbol: str = '', as_of_date: str = '', horizon: str = '') -> dict[str, Any]:
        payload = get_research_cases_payload(
            case_store=self.research_case_store,
            limit=limit,
            policy_id=policy_id,
            symbol=symbol,
            as_of_date=as_of_date,
            horizon=horizon,
        )
        return self._attach_domain_readonly_workflow(
            payload,
            domain="research",
            operation="list_research_cases",
            runtime_tool="invest_research_cases",
            phase="research_cases_read",
            extra_phases=("research_calibration_read",),
            phase_stats={
                "limit": int(limit),
                "policy_id": str(policy_id or ''),
                "symbol": str(symbol or ''),
                "as_of_date": str(as_of_date or ''),
                "horizon": str(horizon or ''),
                "count": int(payload.get('count', 0) or 0),
            },
        )

    def list_research_attributions(self, *, limit: int = 20) -> dict[str, Any]:
        payload = get_research_attributions_payload(case_store=self.research_case_store, limit=limit)
        return self._attach_domain_readonly_workflow(
            payload,
            domain="research",
            operation="list_research_attributions",
            runtime_tool="invest_research_attributions",
            phase="research_attributions_read",
            phase_stats={"limit": int(limit), "count": int(payload.get('count', 0) or 0)},
        )

    def get_research_calibration(self, *, policy_id: str = '') -> dict[str, Any]:
        payload = get_research_calibration_payload(case_store=self.research_case_store, policy_id=policy_id)
        return self._attach_domain_readonly_workflow(
            payload,
            domain="research",
            operation="get_research_calibration",
            runtime_tool="invest_research_calibration",
            phase="research_calibration_read",
            phase_stats={"policy_id": str(policy_id or ''), "sample_count": int(dict(payload.get('report') or {}).get('sample_count') or 0)},
        )

    def ask_stock(
        self,
        *,
        question: str,
        query: str,
        strategy: str = "chan_theory",
        days: int = 60,
        as_of_date: str = "",
    ) -> dict[str, Any]:
        return self.stock_analysis.ask_stock(
            question=question,
            query=query,
            strategy=strategy,
            days=days,
            as_of_date=as_of_date,
        )

    def list_stock_strategies(self) -> dict[str, Any]:
        payload = {"status": STATUS_OK, "items": self.stock_analysis.list_strategies()}
        return self._attach_domain_readonly_workflow(
            payload,
            domain="strategy",
            operation="list_stock_strategies",
            runtime_tool="invest_stock_strategies",
            phase="stock_strategy_inventory_read",
            phase_stats={"count": len(list(payload.get("items") or []))},
        )

    def _load_leaderboard_snapshot(self) -> dict[str, Any]:
        return load_leaderboard_snapshot(self.cfg.training_output_dir)

    def _build_promotion_summary(
        self,
        *,
        plan: dict[str, Any],
        ok_results: list[dict[str, Any]],
        avg_return_pct: float | None,
        avg_strategy_score: float | None,
        benchmark_pass_rate: float,
    ) -> dict[str, Any]:
        board = self._load_leaderboard_snapshot()
        return build_commander_promotion_summary(
            plan=plan,
            ok_results=ok_results,
            avg_return_pct=avg_return_pct,
            avg_strategy_score=avg_strategy_score,
            benchmark_pass_rate=benchmark_pass_rate,
            leaderboard_entries=list(board.get("entries") or []),
        )

    def _build_training_evaluation_summary(self, payload: dict[str, Any], *, plan: dict[str, Any], run_id: str, error: str = "") -> dict[str, Any]:
        board = self._load_leaderboard_snapshot()
        return build_commander_training_evaluation_summary(
            payload,
            plan=plan,
            run_id=run_id,
            error=error,
            run_path=str(self._training_run_path(run_id)),
            evaluation_path=str(self._training_eval_path(run_id)),
            leaderboard_entries=list(board.get("entries") or []),
        )

    def _record_training_lab_artifacts(self, *, plan: dict[str, Any], payload: dict[str, Any], status: str, error: str = "") -> dict[str, Any]:
        return record_training_lab_artifacts(
            training_lab=self.training_lab,
            build_training_evaluation_summary=self._build_training_evaluation_summary,
            new_run_id=self._new_training_run_id,
            plan=plan,
            payload=payload,
            status=status,
            error=error,
        )

    def _append_training_memory(self, payload: dict[str, Any], *, rounds: int, mock: bool, status: str, error: str = "") -> None:
        append_training_memory(
            self.memory,
            payload,
            rounds=rounds,
            mock=mock,
            status=status,
            error=error,
        )

    def _load_training_plan_artifact(self, plan_id: str) -> tuple[Path, dict[str, Any]]:
        return load_training_plan_artifact(
            self._training_plan_path(str(plan_id)),
            plan_id=str(plan_id),
        )

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
        return build_run_cycles_kwargs(
            self.body.run_cycles,
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
        attach_training_lab_paths(payload, lab)

    def _wrap_training_execution_payload(
        self,
        payload: dict[str, Any],
        *,
        plan_id: str,
        rounds: int,
        mock: bool,
    ) -> dict[str, Any]:
        result_count = len(list(payload.get("results") or []))
        total_cycles = dict(payload.get("summary") or {}).get("total_cycles")
        return self._attach_domain_mutating_workflow(
            payload,
            domain="training",
            operation="execute_training_plan",
            runtime_tool="invest_training_plan_execute",
            phase="training_plan_load",
            extra_phases=("training_cycles_execute", "training_artifacts_record"),
            phase_stats={
                "plan_id": str(plan_id),
                "rounds": int(rounds),
                "mock": bool(mock),
                "result_count": int(result_count),
                "total_cycles": total_cycles,
            },
        )

    async def train_once(self, rounds: int = 1, mock: bool = False) -> dict[str, Any]:
        plan = self.create_training_plan(
            rounds=rounds,
            mock=mock,
            goal="direct training run",
            notes="auto-generated from invest_train",
            tags=["direct", "auto"],
            source="direct",
            auto_generated=True,
        )
        return await self.execute_training_plan(plan["plan_id"])

    async def execute_training_plan(self, plan_id: str) -> dict[str, Any]:
        self._ensure_runtime_storage()
        plan_path, plan = self._load_training_plan_artifact(str(plan_id))
        experiment_spec, rounds, mock = self._build_experiment_spec_from_plan(plan)
        return await execute_training_plan_flow(
            plan_path=plan_path,
            plan=plan,
            experiment_spec=experiment_spec,
            rounds=rounds,
            mock=mock,
            plan_id=str(plan_id),
            body=self.body,
            body_snapshot=self.body.snapshot,
            build_run_cycles_kwargs=self._build_run_cycles_kwargs,
            write_json_artifact=self._write_json_artifact,
            begin_task=self._begin_task,
            set_runtime_state=self._set_runtime_state,
            memory=self.memory,
            record_training_lab_artifacts_impl=self._record_training_lab_artifacts,
            attach_training_lab_paths_impl=self._attach_training_lab_paths,
            append_training_memory_impl=self._append_training_memory,
            complete_runtime_task=self._complete_runtime_task,
            wrap_training_execution_payload=self._wrap_training_execution_payload,
            ok_status=STATUS_OK,
            busy_state=STATUS_BUSY,
            idle_state=STATUS_IDLE,
            training_state=STATUS_TRAINING,
            error_state=STATUS_ERROR,
        )

    def reload_strategies(self) -> dict[str, Any]:
        self._ensure_runtime_storage()
        self._begin_task("reload_strategies", "direct")
        self._set_runtime_state(RUNTIME_STATE_RELOADING_STRATEGIES)
        self.strategy_registry.ensure_default_templates()
        genes = self.strategy_registry.reload()
        self._write_commander_identity()
        self._complete_runtime_task(state=STATUS_IDLE, status=STATUS_OK, gene_count=len(genes))
        return self._attach_domain_mutating_workflow(
            {
                "status": STATUS_OK,
                "count": len(genes),
                "genes": [g.to_dict() for g in genes],
            },
            domain="strategy",
            operation="reload_strategies",
            runtime_tool="invest_reload_strategies",
            phase="strategy_reload",
            phase_stats={"gene_count": len(genes)},
        )

    @staticmethod
    def _normalize_status_detail(detail: str) -> str:
        return normalize_status_detail(detail)

    def _collect_data_status(self, detail_mode: str) -> dict[str, Any]:
        return collect_data_status(detail_mode)

    def _build_persisted_state_payload(self) -> dict[str, Any]:
        return build_persisted_status_payload(self)

    def status(self, *, detail: str = "fast") -> dict[str, Any]:
        return get_status_response(
            self,
            detail=detail,
            attach_domain_readonly_workflow=self._attach_domain_readonly_workflow,
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
        job = self.cron.add_job(name=name, message=message, every_sec=int(every_sec), deliver=bool(deliver), channel=str(channel), to=str(to))
        self._persist_state()
        return self._attach_domain_mutating_workflow(
            {"status": STATUS_OK, "job": job.to_dict()},
            domain="scheduler",
            operation="add_cron_job",
            runtime_tool="invest_cron_add",
            phase="cron_add",
            phase_stats={"job_id": getattr(job, 'id', ''), "every_sec": int(every_sec)},
        )

    def list_cron_jobs(self) -> dict[str, Any]:
        rows = [j.to_dict() for j in self.cron.list_jobs()]
        return self._attach_domain_readonly_workflow(
            {"count": len(rows), "items": rows},
            domain="scheduler",
            operation="list_cron_jobs",
            runtime_tool="invest_cron_list",
            phase="cron_list",
            phase_stats={"count": len(rows)},
        )

    def remove_cron_job(self, job_id: str) -> dict[str, Any]:
        ok = self.cron.remove_job(str(job_id))
        self._persist_state()
        return self._attach_domain_mutating_workflow(
            {"status": STATUS_OK if ok else STATUS_NOT_FOUND, "job_id": str(job_id)},
            domain="scheduler",
            operation="remove_cron_job",
            runtime_tool="invest_cron_remove",
            phase="cron_remove",
            phase_stats={"job_id": str(job_id), "removed": bool(ok)},
        )

    async def serve_forever(self, interactive: bool = False) -> None:
        await self.start()

        if interactive:
            print("Commander interactive mode. Type 'exit' to quit.")
            while True:
                line = await asyncio.to_thread(input, "commander> ")
                cmd = line.strip()
                if not cmd:
                    continue
                if cmd.lower() in {"exit", "quit", "/exit", ":q"}:
                    break
                reply = await self.ask(cmd, session_key="cli:commander", channel="cli", chat_id="commander")
                print(reply)
            return

        while True:
            await asyncio.sleep(1)

    def _register_fusion_tools(self) -> None:
        register_fusion_tools_impl(
            self,
            build_tools=build_commander_tools,
            load_plugins=self._load_plugins,
        )

    def _load_plugins(self, persist: bool = True) -> dict[str, Any]:
        return load_plugin_tools(
            brain_tools=self.brain.tools,
            plugin_loader=self.plugin_loader,
            plugin_tool_names=self._plugin_tool_names,
            plugin_dir=self.cfg.plugin_dir,
            persist=persist,
            persist_state=self._persist_state,
        )

    def reload_plugins(self) -> dict[str, Any]:
        self._ensure_runtime_storage()
        payload = self._load_plugins(persist=True)
        return self._attach_domain_mutating_workflow(
            {"status": STATUS_OK, **payload},
            domain="plugin",
            operation="reload_plugins",
            runtime_tool="invest_plugins_reload",
            phase="plugin_reload",
            phase_stats={"plugin_count": int(payload.get("count", 0))},
        )

    def _ensure_runtime_storage(self) -> None:
        ensure_runtime_storage(
            directories={
                self.cfg.workspace,
                self.cfg.strategy_dir,
                self.cfg.stock_strategy_dir,
                self.cfg.state_file.parent,
                self.cfg.memory_store.parent,
                self.cfg.plugin_dir,
                self.cfg.bridge_inbox,
                self.cfg.bridge_outbox,
                self.cfg.runtime_state_dir,
                self.cfg.runtime_events_path.parent,
            },
            training_lab=self.training_lab,
            memory=self.memory,
        )

    async def _on_bridge_message(self, msg: BridgeMessage) -> str:
        session_key = msg.session_key or f"{msg.channel}:{msg.chat_id}"
        return await self.ask(msg.content, session_key=session_key, channel=msg.channel, chat_id=msg.chat_id)

    def _setup_cron_callback(self) -> None:
        setup_cron_callback(
            cron=self.cron,
            ask=self.ask,
            notifications=self._notifications,
        )

    async def _on_heartbeat_execute(self, tasks: str) -> str:
        return await self.ask(tasks, session_key="heartbeat")

    async def _on_heartbeat_notify(self, response: str) -> None:
        await self._notifications.put(f"[heartbeat] {response}")

    async def _drain_notifications(self) -> None:
        await drain_runtime_notifications(self._notifications, logger=logger)

    def _build_system_prompt(self) -> str:
        return build_commander_system_prompt(
            workspace=str(self.cfg.workspace),
            strategy_dir=str(self.cfg.strategy_dir),
            quick_status_tool_name=INVEST_QUICK_STATUS_TOOL_NAME,
            deep_status_tool_name=INVEST_DEEP_STATUS_TOOL_NAME,
            strategy_summary=self.strategy_registry.to_summary(),
        )

    def _persist_state(self) -> None:
        persist_runtime_state(
            self.cfg.state_file,
            payload=self._build_persisted_state_payload(),
        )

    def _write_commander_identity(self) -> None:
        write_commander_identity_artifacts(
            self.cfg.workspace,
            strategy_dir=str(self.cfg.strategy_dir),
            quick_status_tool_name=INVEST_QUICK_STATUS_TOOL_NAME,
            strategy_summary=self.strategy_registry.to_summary(),
            build_soul=build_commander_soul,
            build_heartbeat=build_heartbeat_tasks_markdown,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    return build_commander_cli_parser()


async def _run_async(args: argparse.Namespace) -> int:
    return await run_commander_cli_async(
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
