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
import contextlib
import contextvars
import logging
import os
import queue
import socket
import threading
import uuid
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
from app.commander_support.status import (
    build_persisted_status_payload,
    collect_data_status,
)
from app.commander_support.training import (
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
from app.commander_support.training_plan import (
    build_experiment_spec_from_plan,
    build_run_cycles_kwargs,
    load_training_plan_artifact,
)
from app.commander_support.identity import (
    build_commander_soul,
    build_commander_system_prompt,
    build_heartbeat_tasks_markdown,
)
from app.commander_support.ask import (
    execute_runtime_ask,
    record_runtime_ask_activity,
)
from app.commander_support.config import (
    apply_runtime_path_overrides as apply_runtime_path_overrides_impl,
    build_commander_config_from_args,
    relocate_commander_state_paths,
    sync_runtime_path_config as sync_runtime_path_config_impl,
)
from app.commander_support.cli import (
    build_parser as build_commander_cli_parser,
    run_async as run_commander_cli_async,
    run_cli_main,
)
from app.commander_support.runtime_state import (
    acquire_runtime_lock,
    apply_restored_body_state,
    build_finished_task,
    build_started_task,
    copy_runtime_task,
    is_pid_alive,
    read_runtime_lock_payload,
    release_runtime_lock,
)
from app.commander_support.runtime_lifecycle import (
    drain_runtime_notifications,
    ensure_runtime_storage,
    persist_runtime_snapshot,
    restore_runtime_from_persisted_state,
    setup_cron_callback,
    start_runtime_flow,
    start_runtime_background_services,
    stop_runtime_flow,
    stop_runtime_background_services,
    write_runtime_identity,
)
from app.commander_support.runtime_query import (
    get_events_summary_response,
    get_events_tail_response,
    get_runtime_diagnostics_response,
    get_status_response,
    get_training_lab_summary_response,
)
from app.commander_support.runtime_mutation import (
    add_cron_job_response,
    list_cron_jobs_response,
    reload_plugins_response,
    reload_strategies_response,
    remove_cron_job_response,
    serve_forever_loop,
)
from app.commander_support.plugin import (
    load_plugin_tools,
    register_fusion_tools as register_fusion_tools_impl,
)
from app.commander_support.services import (
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
from app.commander_support.observability import (
    append_event_row,
    build_memory_detail,
    memory_brief_row,
)
from app.commander_support.domain_catalog import get_domain_agent_kind, get_domain_tools
from app.strategy_gene_registry import StrategyGene, StrategyGeneRegistry
from app.commander_support.workflow import (
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

__all__ = [
    "CommanderConfig",
    "CommanderRuntime",
    "StrategyGene",
    "StrategyGeneRegistry",
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

_REQUEST_EVENT_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "commander_request_event_context",
    default={},
)


@dataclass
class RuntimeEventSubscription:
    subscription_id: str
    event_queue: queue.Queue
    session_key: str = ""
    chat_id: str = ""
    request_id: str = ""
    emitted_count: int = 0
    suppressed_count: int = 0
    last_display_by_key: dict[str, str] = field(default_factory=dict)
    last_progress_bucket_by_stage: dict[str, int] = field(default_factory=dict)
    last_module_title_by_scope: dict[str, str] = field(default_factory=dict)
    seen_meeting_updates: set[str] = field(default_factory=set)
    routing_decision_emitted: bool = False
    collected_packets: list[dict[str, Any]] = field(default_factory=list)


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
        self._pending_runtime_tasks: list[dict[str, Any]] = []
        self._task_lock = threading.RLock()
        self._stream_lock = threading.RLock()
        self._event_subscriptions: dict[str, RuntimeEventSubscription] = {}
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

    def _current_request_event_context(self) -> dict[str, Any]:
        return dict(_REQUEST_EVENT_CONTEXT.get() or {})

    @contextlib.contextmanager
    def _request_event_context(
        self,
        *,
        session_key: str = "",
        chat_id: str = "",
        request_id: str = "",
        channel: str = "",
    ):
        base = self._current_request_event_context()
        for key, value in {
            "session_key": session_key,
            "chat_id": chat_id,
            "request_id": request_id,
            "channel": channel,
        }.items():
            normalized = str(value or "").strip()
            if normalized:
                base[key] = normalized
        token = _REQUEST_EVENT_CONTEXT.set(base)
        try:
            yield dict(base)
        finally:
            _REQUEST_EVENT_CONTEXT.reset(token)

    def _normalize_event_payload(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = dict(payload or {})
        context = self._current_request_event_context()
        for key in ("session_key", "chat_id", "request_id", "channel"):
            value = str(normalized.get(key) or context.get(key) or "").strip()
            if value:
                normalized[key] = value
        return normalized

    @staticmethod
    def _event_context_from_row(row: dict[str, Any]) -> dict[str, str]:
        payload = dict(row.get("payload") or {})
        resolved: dict[str, str] = {}
        for key in ("session_key", "chat_id", "request_id", "channel"):
            value = str(row.get(key) or payload.get(key) or "").strip()
            if value:
                resolved[key] = value
        return resolved

    @staticmethod
    def _stream_stage_for_event(row: dict[str, Any]) -> str:
        payload = dict(row.get("payload") or {})
        stage = str(payload.get("stage") or "").strip()
        if stage:
            return stage
        mapping = {
            EVENT_ASK_STARTED: "request_received",
            EVENT_ASK_FINISHED: "request_completed",
            EVENT_TASK_STARTED: "task_started",
            EVENT_TASK_FINISHED: "task_completed",
            EVENT_TRAINING_STARTED: "training",
            EVENT_TRAINING_FINISHED: "training",
            "routing_started": "model_routing",
            "regime_classified": "model_routing",
            "routing_decided": "model_routing",
            "model_switch_applied": "model_routing",
            "model_switch_blocked": "model_routing",
            "agent_status": "agent",
            "agent_progress": "agent",
            "module_log": "module",
            "meeting_speech": "meeting",
            "cycle_start": "training",
            "cycle_complete": "training",
            "cycle_skipped": "training",
            "data_download_triggered": "data",
        }
        return mapping.get(str(row.get("event") or ""), "")

    @staticmethod
    def _stream_phase_label(stage: str) -> str:
        mapping = {
            "request_received": "收到请求",
            "request_completed": "请求完成",
            "task_started": "任务开始",
            "task_completed": "任务结束",
            "training": "训练执行",
            "model_routing": "模型路由",
            "agent": "Agent 执行",
            "module": "模块处理",
            "meeting": "会议播报",
            "data": "数据处理",
            "selection_meeting": "选股会议",
            "review_meeting": "复盘会议",
            "simulation": "模拟交易",
            "data_loading": "数据加载",
        }
        return mapping.get(str(stage or ""), str(stage or "").replace("_", " "))

    @staticmethod
    def _stream_kind_for_event(row: dict[str, Any]) -> str:
        event_name = str(row.get("event") or "")
        payload = dict(row.get("payload") or {})
        if payload.get("requires_confirmation") or str(payload.get("confirmation_state") or "").strip():
            return "confirmation_update"
        if str(payload.get("risk_level") or "").strip():
            return "risk_update"
        if event_name in {"cycle_complete", "cycle_skipped"}:
            return "artifact_update"
        if event_name in {"module_log"}:
            return "module_update"
        if event_name in {"meeting_speech"}:
            return "meeting_update"
        if event_name in {"agent_status", "agent_progress"}:
            return "agent_update"
        if event_name in {"routing_started", "regime_classified", "routing_decided", "model_switch_applied", "model_switch_blocked"}:
            return "routing_update"
        return "stage_update"

    @staticmethod
    def _stream_tags_for_event(row: dict[str, Any]) -> list[str]:
        event_name = str(row.get("event") or "")
        payload = dict(row.get("payload") or {})
        tags: list[str] = []
        stream_kind = CommanderRuntime._stream_kind_for_event(row)
        if stream_kind:
            tags.append(stream_kind)
        if str(payload.get("risk_level") or "").strip():
            tags.append("risk_update")
        if payload.get("requires_confirmation") or str(payload.get("confirmation_state") or "").strip():
            tags.append("confirmation_update")
        if event_name in {"cycle_complete", "cycle_skipped"}:
            tags.append("artifact_update")
        if event_name in {"module_log"}:
            tags.append("module_update")
        if event_name in {"meeting_speech"}:
            tags.append("meeting_update")
        if event_name in {"agent_status", "agent_progress"}:
            tags.append("agent_update")
        deduped: list[str] = []
        for tag in tags:
            if tag and tag not in deduped:
                deduped.append(tag)
        return deduped

    @staticmethod
    def _stream_risk_summary(risk_level: str) -> str:
        mapping = {
            "low": "低风险，可继续观察。",
            "medium": "中风险，建议先核对关键参数或数据状态。",
            "high": "高风险，建议先人工确认再继续。",
        }
        return mapping.get(str(risk_level or "").strip(), "")

    @staticmethod
    def _stream_confirmation_summary(*, requires_confirmation: bool, confirmation_state: str, status: str) -> str:
        if requires_confirmation or confirmation_state == "pending_confirmation" or status == STATUS_CONFIRMATION_REQUIRED:
            return "当前仍需人工确认，系统尚未执行最终写入。"
        if confirmation_state in {"confirmed", "approved"}:
            return "确认已完成，系统可继续执行后续动作。"
        return ""

    def _stream_artifacts_for_event(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row.get("payload") or {})
        artifacts = dict(payload.get("artifacts") or {}) if isinstance(payload.get("artifacts"), dict) else {}
        cycle_id = payload.get("cycle_id")
        if cycle_id not in (None, ""):
            try:
                artifacts.update(self.body._artifact_paths_for_cycle(int(cycle_id)))  # pylint: disable=protected-access
            except Exception as exc:
                logger.warning("Failed to resolve artifact paths for cycle %s: %s", cycle_id, exc)
        config_snapshot_path = str(payload.get("config_snapshot_path") or "").strip()
        if config_snapshot_path:
            artifacts.setdefault("config_snapshot_path", config_snapshot_path)
        return artifacts

    @staticmethod
    def _stream_artifact_summary(artifacts: dict[str, Any]) -> str:
        labels = {
            "cycle_result_path": "周期结果",
            "selection_meeting_json_path": "选股会议(JSON)",
            "selection_meeting_markdown_path": "选股会议(Markdown)",
            "review_meeting_json_path": "复盘会议(JSON)",
            "review_meeting_markdown_path": "复盘会议(Markdown)",
            "config_snapshot_path": "配置快照",
            "optimization_events_path": "优化事件",
        }
        items: list[str] = []
        for key, label in labels.items():
            value = str((artifacts or {}).get(key) or "").strip()
            if not value:
                continue
            items.append(f"{label}：{Path(value).name}")
            if len(items) >= 3:
                break
        return "；".join(items)

    @staticmethod
    def _stream_display_priority(stream_kind: str) -> int:
        mapping = {
            "confirmation_update": 100,
            "risk_update": 90,
            "artifact_update": 80,
            "meeting_update": 70,
            "routing_update": 65,
            "agent_update": 60,
            "module_update": 55,
            "stage_update": 50,
        }
        return int(mapping.get(str(stream_kind or ""), 40))

    @staticmethod
    def _format_confidence_value(value: Any) -> str:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return ""
        if numeric <= 1:
            return f"{numeric:.0%}"
        return f"{numeric:.2f}"

    @staticmethod
    def _stream_routing_decision_text(packet: dict[str, Any]) -> str:
        event_name = str(packet.get("event") or "").strip()
        regime = str(packet.get("regime") or "").strip()
        current_model = str(packet.get("current_model") or "").strip()
        selected_model = str(packet.get("selected_model") or "").strip()
        decision_source = str(packet.get("decision_source") or "").strip()
        hold_reason = str(packet.get("hold_reason") or "").strip()
        reasoning = BrainRuntime._truncate_text(packet.get("reasoning"), limit=120)
        regime_confidence = CommanderRuntime._format_confidence_value(packet.get("regime_confidence"))
        decision_confidence = CommanderRuntime._format_confidence_value(packet.get("decision_confidence"))
        switch_applied = bool(packet.get("switch_applied"))
        hold_current = bool(packet.get("hold_current"))

        parts: list[str] = ["模型路由决策"]
        if regime:
            regime_text = f"市场状态 {regime}"
            if regime_confidence:
                regime_text += f"（置信度 {regime_confidence}）"
            parts.append(regime_text)

        if selected_model and switch_applied and current_model and current_model != selected_model:
            parts.append(f"已从 {current_model} 切换到 {selected_model}")
        elif selected_model and hold_current and current_model:
            parts.append(f"建议模型 {selected_model}，本次继续保持 {current_model}")
        elif selected_model and current_model and selected_model == current_model:
            parts.append(f"继续使用 {current_model}")
        elif selected_model:
            parts.append(f"建议模型 {selected_model}")
        elif current_model:
            parts.append(f"当前模型 {current_model}")

        if decision_source:
            source_text = f"决策来源 {decision_source}"
            if decision_confidence:
                source_text += f"（置信度 {decision_confidence}）"
            parts.append(source_text)
        elif decision_confidence:
            parts.append(f"决策置信度 {decision_confidence}")

        if hold_reason:
            parts.append(f"保持原因：{hold_reason}")
        elif event_name == "model_switch_blocked":
            parts.append("本次未执行模型切换")

        if reasoning:
            parts.append(f"依据：{reasoning}")
        return "；".join(part for part in parts if part) + "。"

    def _stream_display_text(self, packet: dict[str, Any]) -> str:
        stream_kind = str(packet.get("stream_kind") or "").strip()
        phase_label = str(packet.get("phase_label") or "").strip()
        base = str(
            packet.get("human_reply")
            or packet.get("broadcast_text")
            or packet.get("label")
            or packet.get("event")
            or ""
        ).strip()
        risk_summary = str(packet.get("risk_summary") or "").strip()
        confirmation_summary = str(packet.get("confirmation_summary") or "").strip()
        artifacts = dict(packet.get("artifacts") or {})
        artifact_summary = self._stream_artifact_summary(artifacts)

        if stream_kind == "confirmation_update":
            parts = [base or phase_label]
            if risk_summary:
                parts.append(f"风险提示：{risk_summary}")
            if confirmation_summary:
                parts.append(f"确认要求：{confirmation_summary}")
            return "；".join(part for part in parts if part)
        if stream_kind == "risk_update":
            parts = [base or phase_label]
            if risk_summary:
                parts.append(f"风险提示：{risk_summary}")
            return "；".join(part for part in parts if part)
        if stream_kind == "artifact_update":
            parts = [base or phase_label]
            if artifact_summary:
                parts.append(f"相关产物：{artifact_summary}")
            return "；".join(part for part in parts if part)
        if stream_kind == "routing_update":
            return self._stream_routing_decision_text(packet)
        if stream_kind in {"routing_update", "meeting_update", "agent_update", "module_update"}:
            if phase_label and base and not base.startswith(f"{phase_label}："):
                return f"{phase_label}：{base}"
        if phase_label and base and phase_label not in {"", base} and not base.startswith(f"{phase_label}："):
            return f"{phase_label}：{base}"
        return base

    def _build_stream_event_packet(self, row: dict[str, Any]) -> dict[str, Any]:
        event_name = str(row.get("event") or "")
        payload = dict(row.get("payload") or {})
        context = self._event_context_from_row(row)
        status = str(payload.get("status") or "").strip()
        risk_level = str(payload.get("risk_level") or "").strip()
        confirmation_state = str(payload.get("confirmation_state") or "").strip()
        requires_confirmation = bool(payload.get("requires_confirmation")) or status == STATUS_CONFIRMATION_REQUIRED
        stage = self._stream_stage_for_event(row)
        artifacts = self._stream_artifacts_for_event(row)
        packet = {
            "type": "runtime_event",
            "id": str(row.get("id") or ""),
            "ts": str(row.get("ts") or ""),
            "event": event_name,
            "source": str(row.get("source") or ""),
            "kind": "internal" if BrainRuntime._is_internal_runtime_event(event_name) else "business",
            "stream_kind": self._stream_kind_for_event(row),
            "stream_tags": self._stream_tags_for_event(row),
            "stage": stage,
            "phase_label": self._stream_phase_label(stage),
            "label": BrainRuntime._event_human_label(event_name),
            "detail": BrainRuntime._event_detail_text(row),
            "broadcast_text": BrainRuntime._event_broadcast_text(row),
            "human_reply": BrainRuntime._event_broadcast_text(row) or BrainRuntime._event_human_label(event_name),
            "status": status,
            "risk_level": risk_level,
            "risk_summary": self._stream_risk_summary(risk_level),
            "requires_confirmation": requires_confirmation,
            "confirmation_state": confirmation_state,
            "confirmation_summary": self._stream_confirmation_summary(
                requires_confirmation=requires_confirmation,
                confirmation_state=confirmation_state,
                status=status,
            ),
            "session_key": context.get("session_key", ""),
            "chat_id": context.get("chat_id", ""),
            "request_id": context.get("request_id", ""),
            "channel": context.get("channel", ""),
            "agent": str(payload.get("agent") or "").strip(),
            "module": str(payload.get("module") or "").strip(),
            "module_title": str(payload.get("title") or "").strip(),
            "meeting": str(payload.get("meeting") or "").strip(),
            "speaker": str(payload.get("speaker") or "").strip(),
            "artifacts": artifacts,
            "has_decision": bool(payload.get("decision")),
            "suggestion_count": len(list(payload.get("suggestions") or [])) if isinstance(payload.get("suggestions"), list) else 0,
            "pick_count": len(list(payload.get("picks") or [])) if isinstance(payload.get("picks"), list) else 0,
            "current_model": str(payload.get("current_model") or "").strip(),
            "selected_model": str(payload.get("selected_model") or "").strip(),
            "selected_config": str(payload.get("selected_config") or "").strip(),
            "regime": str(payload.get("regime") or "").strip(),
            "decision_source": str(payload.get("decision_source") or "").strip(),
            "hold_reason": str(payload.get("hold_reason") or "").strip(),
            "reasoning": str(payload.get("reasoning") or "").strip(),
            "switch_applied": bool(payload.get("switch_applied")),
            "hold_current": bool(payload.get("hold_current")),
            "regime_confidence": payload.get("regime_confidence"),
            "decision_confidence": payload.get("decision_confidence"),
        }
        if payload.get("progress_pct") not in (None, ""):
            packet["progress_pct"] = payload.get("progress_pct")
        packet["display_priority"] = self._stream_display_priority(str(packet.get("stream_kind") or ""))
        packet["display_text"] = self._stream_display_text(packet)
        return packet

    def subscribe_event_stream(
        self,
        *,
        session_key: str = "",
        chat_id: str = "",
        request_id: str = "",
    ) -> tuple[str, queue.Queue]:
        subscription_id = f"sub:{uuid.uuid4().hex[:16]}"
        subscription = RuntimeEventSubscription(
            subscription_id=subscription_id,
            event_queue=queue.Queue(maxsize=256),
            session_key=str(session_key or "").strip(),
            chat_id=str(chat_id or "").strip(),
            request_id=str(request_id or "").strip(),
        )
        with self._stream_lock:
            self._event_subscriptions[subscription_id] = subscription
        return subscription_id, subscription.event_queue

    @staticmethod
    def _stream_packet_key(packet: dict[str, Any]) -> str:
        return "|".join(
            [
                str(packet.get("stream_kind") or ""),
                str(packet.get("stage") or ""),
                str(packet.get("agent") or ""),
                str(packet.get("module") or ""),
                str(packet.get("meeting") or ""),
                str(packet.get("event") or ""),
            ]
        )

    @staticmethod
    def _stream_text_has_terminal_signal(text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return False
        keywords = (
            "完成",
            "已完成",
            "成功",
            "失败",
            "异常",
            "结论",
            "决议",
            "决定",
            "最终",
            "切换",
            "阻止",
            "确认",
            "产物",
        )
        return any(keyword in content for keyword in keywords)

    @staticmethod
    def _should_suppress_routing_update(packet: dict[str, Any]) -> bool:
        event_name = str(packet.get("event") or "").strip()
        return event_name in {"routing_started", "regime_classified"}

    @staticmethod
    def _meeting_packet_is_material(packet: dict[str, Any]) -> bool:
        if bool(packet.get("has_decision")):
            return True
        if int(packet.get("suggestion_count") or 0) > 0:
            return True
        if int(packet.get("pick_count") or 0) > 0:
            return True
        return CommanderRuntime._stream_text_has_terminal_signal(str(packet.get("display_text") or ""))

    def _should_emit_stream_packet(
        self,
        subscription: RuntimeEventSubscription,
        packet: dict[str, Any],
    ) -> bool:
        stream_kind = str(packet.get("stream_kind") or "").strip()
        display_text = str(packet.get("display_text") or "").strip()
        packet_key = self._stream_packet_key(packet)
        event_name = str(packet.get("event") or "").strip()
        if display_text and subscription.last_display_by_key.get(packet_key) == display_text:
            subscription.suppressed_count += 1
            return False

        if stream_kind == "routing_update" and self._should_suppress_routing_update(packet):
            subscription.suppressed_count += 1
            return False
        if stream_kind == "routing_update":
            if event_name == "routing_decided":
                subscription.routing_decision_emitted = True
            elif subscription.routing_decision_emitted and event_name in {"model_switch_applied", "model_switch_blocked"}:
                subscription.suppressed_count += 1
                return False

        if stream_kind == "agent_update" and packet.get("progress_pct") not in (None, ""):
            try:
                progress_pct = packet.get("progress_pct")
                bucket = int(progress_pct) // 10 if progress_pct is not None else -1
            except (TypeError, ValueError):
                bucket = -1
            stage = str(packet.get("stage") or "agent").strip() or "agent"
            previous_bucket = subscription.last_progress_bucket_by_stage.get(stage)
            if previous_bucket is not None and bucket >= 0 and bucket <= previous_bucket:
                subscription.suppressed_count += 1
                return False
            if bucket >= 0:
                subscription.last_progress_bucket_by_stage[stage] = bucket

        if stream_kind == "module_update":
            stage = str(packet.get("stage") or "module").strip() or "module"
            module = str(packet.get("module") or "").strip() or "module"
            title = str(packet.get("module_title") or "").strip() or str(packet.get("label") or "").strip()
            scope_key = f"{stage}|{module}"
            previous_title = subscription.last_module_title_by_scope.get(scope_key)
            if previous_title and previous_title == title and not self._stream_text_has_terminal_signal(display_text):
                subscription.suppressed_count += 1
                return False
            if title:
                subscription.last_module_title_by_scope[scope_key] = title

        if stream_kind == "meeting_update":
            meeting = str(packet.get("meeting") or "").strip() or str(packet.get("stage") or "meeting").strip() or "meeting"
            if meeting in subscription.seen_meeting_updates and not self._meeting_packet_is_material(packet):
                subscription.suppressed_count += 1
                return False
            subscription.seen_meeting_updates.add(meeting)

        if display_text:
            subscription.last_display_by_key[packet_key] = display_text
        return True

    def _record_stream_packet(
        self,
        subscription: RuntimeEventSubscription,
        packet: dict[str, Any],
    ) -> None:
        subscription.emitted_count += 1
        subscription.collected_packets.append(dict(packet))
        if len(subscription.collected_packets) > 64:
            subscription.collected_packets = subscription.collected_packets[-64:]

    @staticmethod
    def _risk_rank(value: str) -> int:
        return {"low": 1, "medium": 2, "high": 3}.get(str(value or "").strip(), 0)

    def build_stream_summary_packet(self, subscription_id: str) -> dict[str, Any]:
        with self._stream_lock:
            subscription = self._event_subscriptions.get(str(subscription_id or ""))
            if subscription is None:
                return {
                    "type": "runtime_summary",
                    "stream_kind": "summary",
                    "display_priority": 110,
                    "display_text": "本次会话流式播报已结束。",
                }
            packets = list(subscription.collected_packets)
            emitted_count = int(subscription.emitted_count)
            suppressed_count = int(subscription.suppressed_count)

        phase_labels: list[str] = []
        highest_risk = ""
        highest_risk_summary = ""
        requires_confirmation = False
        confirmation_summary = ""
        artifact_names: list[str] = []
        last_display_text = ""
        for packet in packets:
            phase_label = str(packet.get("phase_label") or "").strip()
            if phase_label and phase_label not in phase_labels:
                phase_labels.append(phase_label)
            risk_level = str(packet.get("risk_level") or "").strip()
            if self._risk_rank(risk_level) > self._risk_rank(highest_risk):
                highest_risk = risk_level
                highest_risk_summary = self._stream_risk_summary(risk_level)
            if bool(packet.get("requires_confirmation")):
                requires_confirmation = True
            confirmation_text = str(packet.get("confirmation_summary") or "").strip()
            if confirmation_text:
                confirmation_summary = confirmation_text
            last_display_text = str(packet.get("display_text") or last_display_text or "").strip()
            artifacts = dict(packet.get("artifacts") or {})
            for value in artifacts.values():
                name = Path(str(value)).name if str(value or "").strip() else ""
                if name and name not in artifact_names:
                    artifact_names.append(name)
                if len(artifact_names) >= 3:
                    break
            if len(artifact_names) >= 3:
                break

        parts = [f"本次共播报 {emitted_count} 条事件"]
        if suppressed_count:
            parts.append(f"已合并/抑制 {suppressed_count} 条高频更新")
        if phase_labels:
            parts.append("主要阶段：" + " → ".join(phase_labels[:5]))
        if highest_risk:
            parts.append("最高风险：" + highest_risk_summary)
        if requires_confirmation and confirmation_summary:
            parts.append("确认状态：" + confirmation_summary)
        if artifact_names:
            parts.append("关键产物：" + "、".join(artifact_names[:3]))
        if last_display_text:
            parts.append("最后播报：" + last_display_text)

        display_text = "；".join(part for part in parts if part) + "。"
        return {
            "type": "runtime_summary",
            "stream_kind": "summary",
            "stream_tags": ["summary"],
            "display_priority": 110,
            "display_text": display_text,
            "human_reply": display_text,
            "session_key": subscription.session_key,
            "chat_id": subscription.chat_id,
            "request_id": subscription.request_id,
            "event_count": emitted_count,
            "suppressed_count": suppressed_count,
            "phase_labels": phase_labels,
            "highest_risk_level": highest_risk,
            "highest_risk_summary": highest_risk_summary,
            "requires_confirmation": requires_confirmation,
            "confirmation_summary": confirmation_summary,
            "artifact_names": artifact_names[:3],
            "last_display_text": last_display_text,
        }

    @staticmethod
    def _upsert_human_section(
        sections: list[dict[str, Any]],
        section: dict[str, Any],
        *,
        after_labels: tuple[str, ...] = ("执行性质", "结论"),
    ) -> list[dict[str, Any]]:
        label = str(section.get("label") or "").strip()
        if not label:
            return list(sections or [])

        normalized: list[dict[str, Any]] = []
        insert_index: int | None = None
        for item in list(sections or []):
            if not isinstance(item, dict):
                continue
            item_label = str(item.get("label") or "").strip()
            if item_label == label:
                continue
            normalized.append(item)
            if item_label in after_labels:
                insert_index = len(normalized)

        if insert_index is None:
            normalized.append(section)
            return normalized

        normalized.insert(insert_index, section)
        return normalized

    @staticmethod
    def _append_unique_text(target: list[str], value: str) -> None:
        text = str(value or "").strip()
        if text and text not in target:
            target.append(text)

    @staticmethod
    def _stream_summary_sections(summary: dict[str, Any]) -> list[dict[str, Any]]:
        summary_text = str(summary.get("display_text") or "").strip()
        event_count = int(summary.get("event_count") or 0)
        suppressed_count = int(summary.get("suppressed_count") or 0)
        phase_labels = [str(item) for item in list(summary.get("phase_labels") or []) if str(item or "").strip()]
        artifact_names = [str(item) for item in list(summary.get("artifact_names") or []) if str(item or "").strip()]
        highest_risk_summary = str(summary.get("highest_risk_summary") or "").strip()
        confirmation_summary = str(summary.get("confirmation_summary") or "").strip()
        last_display_text = str(summary.get("last_display_text") or "").strip()

        sections: list[dict[str, Any]] = []
        stream_items: list[str] = []
        if event_count:
            stream_items.append(f"本次共播报 {event_count} 条事件")
        if suppressed_count:
            stream_items.append(f"已合并/抑制 {suppressed_count} 条高频更新")
        if last_display_text:
            stream_items.append(f"最后播报：{last_display_text}")
        if stream_items:
            sections.append({"label": "流式过程", "items": stream_items, "text": summary_text})
        elif summary_text:
            sections.append({"label": "流式过程", "text": summary_text})

        if phase_labels:
            sections.append({"label": "主要阶段", "items": phase_labels})

        risk_items: list[str] = []
        if highest_risk_summary:
            risk_items.append(f"最高风险：{highest_risk_summary}")
        if confirmation_summary:
            risk_items.append(f"确认状态：{confirmation_summary}")
        if risk_items:
            sections.append({"label": "流式风险与确认", "items": risk_items})

        if artifact_names:
            sections.append({"label": "关键产物", "items": artifact_names})
        return sections

    @staticmethod
    def merge_stream_summary_into_reply_payload(
        payload: dict[str, Any] | None,
        summary_packet: dict[str, Any] | None,
    ) -> dict[str, Any]:
        body = dict(payload or {})
        summary = dict(summary_packet or {})
        summary_text = str(summary.get("display_text") or "").strip()
        if not summary_text:
            return body

        body["stream_summary"] = summary
        human = dict(body.get("human_readable") or {})
        if not human:
            human = {
                "summary": str(body.get("message") or body.get("reply") or "").strip(),
                "receipt_text": str(body.get("message") or body.get("reply") or "").strip(),
                "sections": [],
                "bullets": [],
                "facts": [],
                "risks": [],
                "suggested_actions": [],
            }

        sections = list(human.get("sections") or [])
        for section in CommanderRuntime._stream_summary_sections(summary):
            sections = CommanderRuntime._upsert_human_section(sections, section)
        if not any(str(section.get("label") or "") == "流式过程摘要" for section in sections if isinstance(section, dict)):
            sections = CommanderRuntime._upsert_human_section(
                sections,
                {"label": "流式过程摘要", "text": summary_text},
                after_labels=("关键产物", "流式风险与确认", "主要阶段", "流式过程", "执行性质", "结论"),
            )
        human["sections"] = sections

        bullets = [str(item) for item in list(human.get("bullets") or []) if str(item or "").strip()]
        stream_bullet = f"流式过程摘要：{summary_text}"
        CommanderRuntime._append_unique_text(bullets, stream_bullet)
        phase_labels = [str(item) for item in list(summary.get("phase_labels") or []) if str(item or "").strip()]
        if phase_labels:
            CommanderRuntime._append_unique_text(bullets, "主要阶段：" + " → ".join(phase_labels[:5]))
        human["bullets"] = bullets

        facts = [str(item) for item in list(human.get("facts") or []) if str(item or "").strip()]
        CommanderRuntime._append_unique_text(facts, stream_bullet)
        artifact_names = [str(item) for item in list(summary.get("artifact_names") or []) if str(item or "").strip()]
        if artifact_names:
            CommanderRuntime._append_unique_text(facts, "关键产物：" + "、".join(artifact_names[:3]))
        if phase_labels:
            CommanderRuntime._append_unique_text(facts, "主要阶段：" + " → ".join(phase_labels[:5]))
        human["facts"] = facts

        risks = [str(item) for item in list(human.get("risks") or []) if str(item or "").strip()]
        highest_risk_summary = str(summary.get("highest_risk_summary") or "").strip()
        if highest_risk_summary:
            CommanderRuntime._append_unique_text(risks, "最高风险：" + highest_risk_summary)
        confirmation_summary = str(summary.get("confirmation_summary") or "").strip()
        if confirmation_summary:
            CommanderRuntime._append_unique_text(risks, "确认状态：" + confirmation_summary)
        if risks:
            human["risks"] = risks

        suggested_actions = [str(item) for item in list(human.get("suggested_actions") or []) if str(item or "").strip()]
        if bool(summary.get("requires_confirmation")):
            CommanderRuntime._append_unique_text(suggested_actions, "如需继续执行，请先人工确认后再继续。")
        human["suggested_actions"] = suggested_actions

        receipt_text = str(human.get("receipt_text") or "").strip()
        receipt_lines = [line for line in receipt_text.splitlines() if str(line or "").strip()]
        for line in [
            "流式过程：" + summary_text,
            ("主要阶段：" + " → ".join(phase_labels[:5])) if phase_labels else "",
            ("关键产物：" + "、".join(artifact_names[:3])) if artifact_names else "",
            ("流式风险：" + highest_risk_summary) if highest_risk_summary else "",
            ("流式确认：" + confirmation_summary) if confirmation_summary else "",
            "流式过程摘要：" + summary_text,
        ]:
            if line and line not in receipt_lines:
                receipt_lines.append(line)
        receipt_text = "\n".join(receipt_lines) if receipt_lines else ("流式过程摘要：" + summary_text)
        human["receipt_text"] = receipt_text
        human["stream_summary"] = summary
        body["human_readable"] = human
        return body

    def unsubscribe_event_stream(self, subscription_id: str) -> None:
        with self._stream_lock:
            self._event_subscriptions.pop(str(subscription_id or ""), None)

    @staticmethod
    def _event_matches_subscription(row: dict[str, Any], subscription: RuntimeEventSubscription) -> bool:
        context = CommanderRuntime._event_context_from_row(row)
        if subscription.request_id:
            return context.get("request_id", "") == subscription.request_id
        if subscription.session_key and context.get("session_key", "") != subscription.session_key:
            return False
        if subscription.chat_id and context.get("chat_id", "") != subscription.chat_id:
            return False
        return bool(subscription.session_key or subscription.chat_id)

    def _publish_stream_event(self, row: dict[str, Any]) -> None:
        packet = self._build_stream_event_packet(row)
        with self._stream_lock:
            subscriptions = list(self._event_subscriptions.values())
        for subscription in subscriptions:
            if not self._event_matches_subscription(row, subscription):
                continue
            if not self._should_emit_stream_packet(subscription, packet):
                continue
            self._record_stream_packet(subscription, packet)
            try:
                subscription.event_queue.put_nowait(packet)
            except queue.Full:
                try:
                    subscription.event_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscription.event_queue.put_nowait(packet)
                except queue.Full:
                    continue

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
                    pending_request_id = str(self._pending_runtime_tasks[index].get("request_id") or "").strip()
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
        await start_runtime_flow(
            is_started=self._started,
            ensure_runtime_storage=self._ensure_runtime_storage,
            begin_task=self._begin_task,
            set_runtime_state=self._set_runtime_state,
            acquire_runtime_lock=self._acquire_runtime_lock,
            ensure_default_templates=self.strategy_registry.ensure_default_templates,
            reload_strategies=lambda: self.strategy_registry.reload(),
            load_plugins=self._load_plugins,
            write_commander_identity=self._write_commander_identity,
            start_background_services=lambda: start_runtime_background_services(
                cron=self.cron,
                heartbeat=self.heartbeat,
                bridge=self.bridge,
                heartbeat_enabled=self.cfg.heartbeat_enabled,
                bridge_enabled=self.cfg.bridge_enabled,
                drain_notifications=self._drain_notifications,
                autopilot_enabled=self.cfg.autopilot_enabled,
                autopilot_loop=self.body.autopilot_loop,
                training_interval_sec=self.cfg.training_interval_sec,
            ),
            mark_started=self._set_started_flag,
            set_background_tasks=self._set_background_tasks,
            complete_runtime_task=self._complete_runtime_task,
            end_task=self._end_task,
            release_runtime_lock=self._release_runtime_lock,
            persist_state=self._persist_state,
            starting_state=RUNTIME_STATE_STARTING,
            idle_state=STATUS_IDLE,
            error_state=STATUS_ERROR,
            ok_status=STATUS_OK,
        )

    async def stop(self) -> None:
        await stop_runtime_flow(
            is_started=self._started,
            begin_task=self._begin_task,
            set_runtime_state=self._set_runtime_state,
            stop_background_services=lambda: stop_runtime_background_services(
                body=self.body,
                autopilot_task=self._autopilot_task,
                notify_task=self._notify_task,
                bridge=self.bridge,
                heartbeat=self.heartbeat,
                cron=self.cron,
                brain=self.brain,
            ),
            mark_started=self._set_started_flag,
            release_runtime_lock=self._release_runtime_lock,
            complete_runtime_task=self._complete_runtime_task,
            stopping_state=RUNTIME_STATE_STOPPING,
            stopped_state=RUNTIME_STATE_STOPPED,
            ok_status=STATUS_OK,
        )

    async def ask(
        self,
        message: str,
        session_key: str = "commander:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        request_id: str = "",
    ) -> str:
        resolved_request_id = str(request_id or self.new_request_id()).strip()
        with self._request_event_context(
            session_key=session_key,
            chat_id=chat_id,
            request_id=resolved_request_id,
            channel=channel,
        ):
            return await execute_runtime_ask(
                message=message,
                session_key=session_key,
                channel=channel,
                chat_id=chat_id,
                request_id=resolved_request_id,
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
            request_context=self._current_request_event_context(),
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
        plan = self.create_training_plan(
            rounds=rounds,
            mock=mock,
            goal="direct training run",
            notes="auto-generated from invest_train",
            tags=["direct", "auto"],
            source="direct",
            auto_generated=True,
        )
        return await self.execute_training_plan(
            plan["plan_id"],
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
        self._ensure_runtime_storage()
        plan_path, plan = self._load_training_plan_artifact(str(plan_id))
        experiment_spec, rounds, mock = self._build_experiment_spec_from_plan(plan)
        resolved_request_id = str(
            request_id
            or self._current_request_event_context().get("request_id")
            or self.new_request_id()
        ).strip()
        resolved_session_key = str(
            session_key
            or self._current_request_event_context().get("session_key")
            or f"train:{plan_id}"
        ).strip()
        resolved_chat_id = str(
            chat_id
            or self._current_request_event_context().get("chat_id")
            or str(plan_id)
        ).strip()
        resolved_channel = str(
            channel
            or self._current_request_event_context().get("channel")
            or "runtime"
        ).strip()
        with self._request_event_context(
            session_key=resolved_session_key,
            chat_id=resolved_chat_id,
            request_id=resolved_request_id,
            channel=resolved_channel,
        ):
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
        return reload_strategies_response(
            self,
            ensure_runtime_storage=self._ensure_runtime_storage,
            begin_task=self._begin_task,
            set_runtime_state=self._set_runtime_state,
            write_commander_identity=self._write_commander_identity,
            complete_runtime_task=self._complete_runtime_task,
            attach_domain_mutating_workflow=self._attach_domain_mutating_workflow,
            reloading_state=RUNTIME_STATE_RELOADING_STRATEGIES,
            idle_state=STATUS_IDLE,
            ok_status=STATUS_OK,
        )

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
        return add_cron_job_response(
            self,
            name=name,
            message=message,
            every_sec=every_sec,
            deliver=deliver,
            channel=channel,
            to=to,
            persist_state=self._persist_state,
            attach_domain_mutating_workflow=self._attach_domain_mutating_workflow,
            ok_status=STATUS_OK,
        )

    def list_cron_jobs(self) -> dict[str, Any]:
        return list_cron_jobs_response(
            self,
            attach_domain_readonly_workflow=self._attach_domain_readonly_workflow,
        )

    def remove_cron_job(self, job_id: str) -> dict[str, Any]:
        return remove_cron_job_response(
            self,
            job_id=job_id,
            persist_state=self._persist_state,
            attach_domain_mutating_workflow=self._attach_domain_mutating_workflow,
            ok_status=STATUS_OK,
            not_found_status=STATUS_NOT_FOUND,
        )

    async def serve_forever(self, interactive: bool = False) -> None:
        await serve_forever_loop(
            start_runtime=self.start,
            ask_runtime=self.ask,
            interactive=interactive,
        )

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
            logger=logger,
        )

    def reload_plugins(self) -> dict[str, Any]:
        return reload_plugins_response(
            self,
            ensure_runtime_storage=self._ensure_runtime_storage,
            load_plugins=self._load_plugins,
            attach_domain_mutating_workflow=self._attach_domain_mutating_workflow,
            ok_status=STATUS_OK,
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
        persist_runtime_snapshot(
            state_file=self.cfg.state_file,
            build_persisted_state_payload=self._build_persisted_state_payload,
        )

    def _write_commander_identity(self) -> None:
        write_runtime_identity(
            workspace=self.cfg.workspace,
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
