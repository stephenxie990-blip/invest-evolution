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
import inspect
import ast
import asyncio
import json
import logging
import os
import socket
from copy import deepcopy

import numpy as np
import textwrap
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from brain.runtime import BrainRuntime
from brain.schema_contract import (
    ARTIFACT_KINDS,
    ARTIFACT_TAXONOMY_SCHEMA_VERSION,
    BOUNDED_WORKFLOW_SCHEMA_VERSION,
    COVERAGE_KIND_WORKFLOW_PHASE,
    COVERAGE_SCHEMA_VERSION,
    PLAN_SCHEMA_VERSION,
    TASK_BUS_SCHEMA_VERSION,
)
from brain.scheduler import CronService, HeartbeatService
from brain.tools import build_commander_tools
from brain.memory import MemoryStore
from brain.bridge import BridgeHub, BridgeMessage
from brain.plugins import PluginLoader
from config import PROJECT_ROOT, RUNTIME_DIR, OUTPUT_DIR, LOGS_DIR, MEMORY_DIR, SESSIONS_DIR, WORKSPACE_DIR, config
from config.control_plane import resolve_component_llm, resolve_default_llm
from config.services import EvolutionConfigService, RuntimePathConfigService
from market_data import DataManager, DataSourceUnavailableError, MockDataProvider
from app.train import SelfLearningController, TrainingResult
from app.lab.artifacts import TrainingLabArtifactStore
from app.lab.evaluation import (
    build_promotion_summary,
    build_training_evaluation_summary,
    build_training_memory_summary,
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
    build_runtime_diagnostics,
    memory_brief_row,
    read_event_rows,
    summarize_event_rows,
)
from app.stock_analysis import StockAnalysisService

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


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    return value


def _classify_workflow_artifact(value: Any) -> str:
    if isinstance(value, str):
        lower = value.lower()
        if "/" in value or chr(92) in value or lower.endswith((".json", ".jsonl", ".md", ".csv", ".txt", ".log", ".yaml", ".yml")):
            return "path"
        return "scalar"
    if isinstance(value, (int, float, bool)) or value is None:
        return "scalar"
    if isinstance(value, list):
        return "collection"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _build_workflow_artifact_taxonomy(artifacts: dict[str, Any]) -> dict[str, Any]:
    items = dict(artifacts or {})
    kinds = {key: _classify_workflow_artifact(value) for key, value in items.items()}
    return {
        "schema_version": ARTIFACT_TAXONOMY_SCHEMA_VERSION,
        "count": len(items),
        "keys": sorted(items.keys()),
        "kinds": kinds,
        "path_keys": sorted([key for key, kind in kinds.items() if kind == "path"]),
        "object_keys": sorted([key for key, kind in kinds.items() if kind == "object"]),
        "collection_keys": sorted([key for key, kind in kinds.items() if kind == "collection"]),
        "known_kinds": list(ARTIFACT_KINDS),
    }


def _build_workflow_coverage(*, workflow: list[str], phase_stats: dict[str, Any] | None = None, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    coverage = {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "coverage_kind": COVERAGE_KIND_WORKFLOW_PHASE,
        "workflow_step_count": len(list(workflow or [])),
        "completed_workflow_step_count": len(list(workflow or [])),
        "workflow_step_coverage": 1.0 if workflow else 1.0,
        "phase_stat_key_count": len(dict(phase_stats or {})),
    }
    if existing:
        coverage.update(dict(existing))
    coverage.setdefault("schema_version", COVERAGE_SCHEMA_VERSION)
    coverage.setdefault("coverage_kind", COVERAGE_KIND_WORKFLOW_PHASE)
    return coverage


def _build_mock_provider() -> MockDataProvider:
    stock_count = max(30, int(getattr(config, "max_stocks", 30) or 30))
    min_history_days = max(250, int(getattr(config, "min_history_days", 200) or 200))
    simulation_days = max(30, int(getattr(config, "simulation_days", 30) or 30))
    seed_cutoff_min = min_history_days + 20
    total_days = max(1600, min_history_days + simulation_days + 900)
    return MockDataProvider(
        stock_count=stock_count,
        days=total_days,
        start_date="20180101",
        seed_cutoff_min=seed_cutoff_min,
        seed_cutoff_tail=max(60, simulation_days + 10),
    )


def _apply_runtime_path_overrides(cfg: "CommanderConfig", overrides: dict[str, Any]) -> "CommanderConfig":
    for key in RuntimePathConfigService.EDITABLE_KEYS:
        if value := overrides.get(key):
            setattr(cfg, key, Path(value).expanduser().resolve())
    cfg.__post_init__()
    return cfg




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
        default_state_dir = RUNTIME_DIR / "state"
        state_parent_changed = self.state_file.parent != OUTPUT_DIR / "commander"
        if self.runtime_state_dir == default_state_dir and state_parent_changed:
            self.runtime_state_dir = self.state_file.parent
        if self.training_output_dir == OUTPUT_DIR / "training" and state_parent_changed:
            self.training_output_dir = self.state_file.parent / "training"
        if self.meeting_log_dir == LOGS_DIR / "meetings" and state_parent_changed:
            self.meeting_log_dir = self.state_file.parent / "meetings"
        for attr, filename in _STATE_DIR_RELOCATIONS.items():
            if getattr(self, attr) == default_state_dir / filename:
                setattr(self, attr, self.runtime_state_dir / filename)

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "CommanderConfig":
        cfg = cls()
        runtime_paths = RuntimePathConfigService(project_root=PROJECT_ROOT).load_overrides()
        _apply_runtime_path_overrides(cfg, runtime_paths)

        if workspace := getattr(args, "workspace", None):
            cfg.workspace = Path(workspace).expanduser().resolve()
        if strategy_dir := getattr(args, "strategy_dir", None):
            cfg.strategy_dir = Path(strategy_dir).expanduser().resolve()

        if model := getattr(args, "model", None):
            cfg.model = model
        if api_key := getattr(args, "api_key", None):
            cfg.api_key = api_key
        if api_base := getattr(args, "api_base", None):
            cfg.api_base = api_base

        if getattr(args, "mock", False):
            cfg.mock_mode = True
        if getattr(args, "no_autopilot", False):
            cfg.autopilot_enabled = False
        if getattr(args, "no_heartbeat", False):
            cfg.heartbeat_enabled = False

        if train_interval_sec := getattr(args, "train_interval_sec", None):
            cfg.training_interval_sec = max(60, int(train_interval_sec))
        if heartbeat_interval_sec := getattr(args, "heartbeat_interval_sec", None):
            cfg.heartbeat_interval_sec = max(60, int(heartbeat_interval_sec))

        return cfg


# ---------------------------------------------------------------------------
# Strategy genes
# ---------------------------------------------------------------------------

@dataclass
class StrategyGene:
    """A strategy asset loaded from md/json/py."""

    gene_id: str
    name: str
    kind: str
    path: str
    enabled: bool = True
    priority: int = 50
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StrategyGeneRegistry:
    """Loads editable strategy genes from local files."""

    SUPPORTED_SUFFIXES = {".md", ".json", ".py"}

    def __init__(self, strategy_dir: Path):
        self.strategy_dir = strategy_dir
        self.genes: list[StrategyGene] = []

    def ensure_default_templates(self) -> None:
        self.strategy_dir.mkdir(parents=True, exist_ok=True)

        md_file = self.strategy_dir / "momentum_trend.md"
        if not md_file.exists():
            md_file.write_text(
                textwrap.dedent(
                    """\
                    ---
                    id: momentum_trend
                    name: Momentum Trend Gene
                    enabled: true
                    priority: 80
                    description: Focus on strong trend continuation with volume confirmation.
                    ---

                    # Momentum Trend Gene

                    Entry:
                    - MA5 > MA20 > MA60
                    - RSI in [45, 78]
                    - volume_ratio >= 1.5

                    Exit:
                    - hard_stop: 5%
                    - take_profit: 15%
                    - trailing_drawdown: 8%
                    """
                ),
                encoding="utf-8",
            )

        json_file = self.strategy_dir / "mean_reversion.json"
        if not json_file.exists():
            json_file.write_text(
                json.dumps(
                    {
                        "id": "mean_reversion",
                        "name": "Mean Reversion Gene",
                        "enabled": True,
                        "priority": 60,
                        "description": "Catch oversold rebounds with strict risk limits.",
                        "rules": {
                            "entry": {
                                "rsi_max": 30,
                                "drop_20d_min": 0.12,
                                "volume_ratio_min": 1.2,
                            },
                            "risk": {
                                "stop_loss_pct": 0.06,
                                "take_profit_pct": 0.10,
                                "max_hold_days": 12,
                            },
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        py_file = self.strategy_dir / "risk_guard.py"
        if not py_file.exists():
            py_file.write_text(
                textwrap.dedent(
                    '''\
                    """Risk guard gene.

                    This file is intentionally simple and editable.
                    Commander only parses metadata by default.
                    """

                    GENE_META = {
                        "id": "risk_guard",
                        "name": "Risk Guard Gene",
                        "enabled": True,
                        "priority": 95,
                        "description": "Portfolio level drawdown and exposure guardrails.",
                    }

                    def suggest_risk_overrides(context: dict) -> dict:
                        """Optional helper function if you want Python-based custom logic."""
                        drawdown = float(context.get("drawdown", 0.0))
                        if drawdown > 0.10:
                            return {"position_size": 0.10, "max_positions": 2}
                        return {"position_size": 0.20, "max_positions": 5}
                    '''
                ),
                encoding="utf-8",
            )

    def reload(self, create_dir: bool = True) -> list[StrategyGene]:
        if create_dir:
            self.strategy_dir.mkdir(parents=True, exist_ok=True)
        elif not self.strategy_dir.exists():
            self.genes = []
            return []
        genes: list[StrategyGene] = []

        for path in sorted(self.strategy_dir.glob("*")):
            if not path.is_file() or path.suffix.lower() not in self.SUPPORTED_SUFFIXES:
                continue
            try:
                gene = self._load_gene(path)
                if gene:
                    genes.append(gene)
            except Exception as exc:
                logger.warning("Load strategy gene failed %s: %s", path, exc)

        genes.sort(key=lambda g: (-g.priority, g.gene_id))
        self.genes = genes
        return genes

    def list_genes(self, only_enabled: bool = False) -> list[StrategyGene]:
        if not only_enabled:
            return list(self.genes)
        return [g for g in self.genes if g.enabled]

    def to_summary(self) -> str:
        if not self.genes:
            return "No strategy genes loaded."
        lines = []
        for g in self.genes:
            status = "ON" if g.enabled else "OFF"
            lines.append(f"- [{status}] {g.gene_id} ({g.kind}, P{g.priority}): {g.description}")
        return "\n".join(lines)

    def _load_gene(self, path: Path) -> StrategyGene | None:
        loader = {
            ".md": self._load_md_gene,
            ".json": self._load_json_gene,
            ".py": self._load_py_gene,
        }.get(path.suffix.lower())
        return loader(path) if loader else None

    def _load_md_gene(self, path: Path) -> StrategyGene:
        text = path.read_text(encoding="utf-8")
        front, body = self._split_front_matter(text)

        gene_id = str(front.get("id") or path.stem)
        name = str(front.get("name") or gene_id)
        enabled = self._to_bool(front.get("enabled", True))
        priority = self._to_int(front.get("priority", 50), 50)
        description = str(front.get("description") or self._first_nonempty_line(body) or "")

        return StrategyGene(
            gene_id=gene_id,
            name=name,
            kind="md",
            path=str(path),
            enabled=enabled,
            priority=priority,
            description=description,
            metadata={"front_matter": front},
        )

    def _load_json_gene(self, path: Path) -> StrategyGene:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON strategy gene must be an object")

        warnings = self._validate_json_gene(data, path)
        for w in warnings:
            logger.warning("Strategy gene %s: %s", path.name, w)

        gene_id = str(data.get("id") or path.stem)
        name = str(data.get("name") or gene_id)
        enabled = self._to_bool(data.get("enabled", True))
        priority = max(0, min(100, self._to_int(data.get("priority", 50), 50)))
        description = str(data.get("description") or "")

        metadata = dict(data)
        return StrategyGene(
            gene_id=gene_id,
            name=name,
            kind="json",
            path=str(path),
            enabled=enabled,
            priority=priority,
            description=description,
            metadata=metadata,
        )

    @staticmethod
    def _validate_json_gene(data: dict, path: Path) -> list[str]:
        """Lightweight schema validation for JSON strategy genes.

        Returns a list of warning messages (empty if all checks pass).
        """
        warnings: list[str] = []

        # Required fields
        if "id" not in data:
            warnings.append("missing required field 'id', will use filename as id")
        elif not isinstance(data["id"], str):
            warnings.append(f"field 'id' should be string, got {type(data['id']).__name__}")

        if "name" not in data:
            warnings.append("missing required field 'name', will use id as name")
        elif not isinstance(data["name"], str):
            warnings.append(f"field 'name' should be string, got {type(data['name']).__name__}")

        # Optional typed fields
        if "enabled" in data and not isinstance(data["enabled"], (bool, int, float, str)):
            warnings.append(f"field 'enabled' has unexpected type {type(data['enabled']).__name__}")

        if "priority" in data:
            try:
                p = int(data["priority"])
                if p < 0 or p > 100:
                    warnings.append(f"field 'priority' value {p} out of range [0, 100], will be clamped")
            except (TypeError, ValueError):
                warnings.append(f"field 'priority' is not a valid integer: {data['priority']!r}")

        if "description" in data and not isinstance(data["description"], str):
            warnings.append(f"field 'description' should be string, got {type(data['description']).__name__}")

        if "rules" in data and not isinstance(data["rules"], dict):
            warnings.append(f"field 'rules' should be an object, got {type(data['rules']).__name__}")

        return warnings

    def _load_py_gene(self, path: Path) -> StrategyGene:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)

        module_doc = ast.get_docstring(tree) or ""
        meta: dict[str, Any] = {}
        functions: list[str] = []

        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {"GENE_META", "STRATEGY_META", "META"}:
                        try:
                            literal = ast.literal_eval(node.value)
                            if isinstance(literal, dict):
                                meta = literal
                        except Exception:
                            pass
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)

        gene_id = str(meta.get("id") or path.stem)
        name = str(meta.get("name") or gene_id)
        enabled = self._to_bool(meta.get("enabled", True))
        priority = self._to_int(meta.get("priority", 50), 50)

        description = str(meta.get("description") or self._first_nonempty_line(module_doc) or "")
        if not description:
            description = "Python strategy gene"

        return StrategyGene(
            gene_id=gene_id,
            name=name,
            kind="py",
            path=str(path),
            enabled=enabled,
            priority=priority,
            description=description,
            metadata={"meta": meta, "functions": functions},
        )

    @staticmethod
    def _first_nonempty_line(text: str) -> str:
        for line in text.splitlines():
            s = line.strip()
            if s:
                return s
        return ""

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() not in {"0", "false", "off", "no", ""}

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, text

        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break

        if end is None:
            return {}, text

        front_lines = lines[1:end]
        body = "\n".join(lines[end + 1:])

        front: dict[str, str] = {}
        for line in front_lines:
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            front[k.strip()] = v.strip()
        return front, body


# ---------------------------------------------------------------------------
# Investment body service
# ---------------------------------------------------------------------------

class InvestmentBodyService:
    """Long-running body service: executes training cycles and tracks state."""

    def __init__(self, cfg: CommanderConfig, on_runtime_event: Optional[callable] = None):
        self.cfg = cfg
        self._runtime_event_sink = on_runtime_event
        self._mock_provider: Optional[MockDataProvider] = _build_mock_provider() if cfg.mock_mode else None
        self.controller = SelfLearningController(
            data_provider=self._mock_provider,
            output_dir=str(self.cfg.training_output_dir),
            meeting_log_dir=str(self.cfg.meeting_log_dir),
            config_audit_log_path=str(self.cfg.config_audit_log_path),
            config_snapshot_dir=str(self.cfg.config_snapshot_dir),
        )
        self._real_data_manager = self.controller.data_manager if not cfg.mock_mode else DataManager()
        self._mock_data_manager: Optional[DataManager] = self.controller.data_manager if cfg.mock_mode else None
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

        self.total_cycles = 0
        self.success_cycles = 0
        self.no_data_cycles = 0
        self.failed_cycles = 0
        self.last_result: Optional[dict[str, Any]] = None
        self.last_error: str = ""
        self.last_run_at: str = ""
        self.training_state: str = STATUS_IDLE
        self.current_task: Optional[dict[str, Any]] = None
        self.last_completed_task: Optional[dict[str, Any]] = None

    def _write_training_lock(self, payload: dict[str, Any]) -> None:
        self.cfg.training_lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.training_lock_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _clear_training_lock(self) -> None:
        self.cfg.training_lock_file.unlink(missing_ok=True)

    def _emit_runtime_event(self, event: str, payload: dict[str, Any]) -> None:
        if self._runtime_event_sink:
            try:
                self._runtime_event_sink(event, payload)
            except Exception:
                logger.exception("Failed to emit runtime event: %s", event)

    @staticmethod
    def _derive_run_status(results: list[dict[str, Any]]) -> str:
        if not results:
            return "empty"
        ok_count = sum(1 for item in results if item.get("status") == STATUS_OK)
        no_data_count = sum(1 for item in results if item.get("status") == STATUS_NO_DATA)
        error_count = sum(1 for item in results if item.get("status") == STATUS_ERROR)
        if error_count and ok_count == 0 and no_data_count == 0:
            return "failed"
        if error_count:
            return "partial_failure"
        if ok_count == 0 and no_data_count > 0:
            return "insufficient_data"
        if no_data_count > 0:
            return "completed_with_skips"
        return STATUS_COMPLETED

    def _get_mock_data_manager(self) -> DataManager:
        if self._mock_provider is None:
            self._mock_provider = _build_mock_provider()
        if self._mock_data_manager is None:
            self._mock_data_manager = DataManager(data_provider=self._mock_provider)
        return self._mock_data_manager

    def _activate_run_mode(self, *, force_mock: bool) -> str:
        active_mock = bool(force_mock or self.cfg.mock_mode)
        if active_mock:
            self.controller.data_manager = self._get_mock_data_manager()
            self.controller.requested_data_mode = "mock"
            self.controller.set_llm_dry_run(True)
            return "mock"
        self.controller.data_manager = self._real_data_manager
        self.controller.requested_data_mode = getattr(self._real_data_manager, "requested_mode", "live")
        self.controller.set_llm_dry_run(False)
        return str(self.controller.requested_data_mode)

    @staticmethod
    def _extract_data_source_error(payload: dict[str, Any]) -> dict[str, Any] | None:
        results = list(payload.get("results") or [])
        if not results:
            return None
        errors = [item for item in results if item.get("status") == STATUS_ERROR and item.get("error_code") == DataSourceUnavailableError.error_code]
        if len(errors) != len(results):
            return None
        first = dict(errors[0])
        nested = first.get("error_payload")
        if isinstance(nested, dict):
            return dict(nested)
        return {
            "error": str(first.get("error") or "训练数据源不可用"),
            "error_code": DataSourceUnavailableError.error_code,
            "cutoff_date": first.get("cutoff_date"),
            "stock_count": first.get("stock_count"),
            "min_history_days": first.get("min_history_days"),
            "requested_data_mode": first.get("requested_data_mode", "live"),
            "available_sources": first.get("available_sources", {}),
            "offline_diagnostics": first.get("offline_diagnostics", {}),
            "online_error": first.get("online_error", ""),
            "suggestions": first.get("suggestions", []),
            "allow_mock_fallback": first.get("allow_mock_fallback", False),
        }

    def _last_cycle_meta(self) -> tuple[dict[str, Any], int]:
        meta = dict(getattr(self.controller, "last_cycle_meta", {}) or {})
        cycle_id = meta.get("cycle_id", self.controller.current_cycle_id + 1)
        return meta, cycle_id

    def _build_nodata_cycle_item(
        self,
        *,
        cycle_meta: dict[str, Any],
        cycle_id: int,
        requested_data_mode: str,
    ) -> dict[str, Any]:
        return {
            "status": STATUS_NO_DATA,
            "cycle_id": cycle_id,
            "cutoff_date": cycle_meta.get("cutoff_date"),
            "stage": cycle_meta.get("stage"),
            "reason": cycle_meta.get("reason"),
            "requested_data_mode": cycle_meta.get("requested_data_mode", requested_data_mode),
            "effective_data_mode": cycle_meta.get("effective_data_mode"),
            "data_mode": cycle_meta.get("effective_data_mode") or cycle_meta.get("data_mode"),
            "llm_mode": cycle_meta.get("llm_mode", getattr(self.controller, "llm_mode", "live")),
            "degraded": bool(cycle_meta.get("degraded", False)),
            "degrade_reason": cycle_meta.get("degrade_reason", ""),
            "timestamp": cycle_meta.get("timestamp", self.last_run_at),
            "artifacts": self._artifact_paths_for_cycle(cycle_id),
        }

    def _build_data_source_error_cycle_item(
        self,
        *,
        error_payload: dict[str, Any],
        cycle_meta: dict[str, Any],
        cycle_id: int,
        requested_data_mode: str,
    ) -> dict[str, Any]:
        return {
            "status": STATUS_ERROR,
            "cycle_id": cycle_id,
            "cutoff_date": cycle_meta.get("cutoff_date") or error_payload.get("cutoff_date"),
            "stage": cycle_meta.get("stage", "data_loading"),
            "error": error_payload["error"],
            "error_code": error_payload["error_code"],
            "error_payload": error_payload,
            "requested_data_mode": error_payload.get("requested_data_mode", requested_data_mode),
            "effective_data_mode": "unavailable",
            "data_mode": "unavailable",
            "llm_mode": cycle_meta.get("llm_mode", getattr(self.controller, "llm_mode", "live")),
            "degraded": True,
            "degrade_reason": error_payload["error"],
            "stock_count": error_payload.get("stock_count"),
            "min_history_days": error_payload.get("min_history_days"),
            "available_sources": error_payload.get("available_sources"),
            "offline_diagnostics": error_payload.get("offline_diagnostics"),
            "online_error": error_payload.get("online_error"),
            "suggestions": error_payload.get("suggestions"),
            "allow_mock_fallback": error_payload.get("allow_mock_fallback"),
            "timestamp": self.last_run_at,
            "artifacts": self._artifact_paths_for_cycle(cycle_id),
        }

    def _build_generic_error_cycle_item(
        self,
        *,
        exc: Exception,
        cycle_meta: dict[str, Any],
        cycle_id: int,
        requested_data_mode: str,
    ) -> dict[str, Any]:
        return {
            "status": STATUS_ERROR,
            "cycle_id": cycle_id,
            "cutoff_date": cycle_meta.get("cutoff_date"),
            "stage": cycle_meta.get("stage"),
            "error": str(exc),
            "requested_data_mode": cycle_meta.get("requested_data_mode", requested_data_mode),
            "effective_data_mode": cycle_meta.get("effective_data_mode"),
            "data_mode": cycle_meta.get("effective_data_mode") or cycle_meta.get("data_mode"),
            "llm_mode": cycle_meta.get("llm_mode", getattr(self.controller, "llm_mode", "live")),
            "degraded": bool(cycle_meta.get("degraded", False)),
            "degrade_reason": cycle_meta.get("degrade_reason", ""),
            "timestamp": self.last_run_at,
            "artifacts": self._artifact_paths_for_cycle(cycle_id),
        }

    async def run_cycles(
        self,
        rounds: int = 1,
        force_mock: bool = False,
        task_source: str = "direct",
        experiment_spec: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._lock.locked():
            return {
                "status": STATUS_BUSY,
                "error": "training already in progress",
                "summary": self.snapshot(),
            }

        requested_data_mode = self._activate_run_mode(force_mock=force_mock)
        rounds = max(1, int(rounds))
        results: list[dict[str, Any]] = []
        task_started_at = datetime.now().isoformat()
        self.training_state = STATUS_TRAINING
        self.current_task = {
            "type": "training",
            "source": task_source,
            "rounds": rounds,
            "force_mock": bool(force_mock),
            "requested_data_mode": requested_data_mode,
            "llm_mode": str(getattr(self.controller, "llm_mode", "live") or "live"),
            "started_at": task_started_at,
            "experiment_spec": _jsonable(dict(experiment_spec or {})),
        }
        self._write_training_lock(self.current_task)
        self._emit_runtime_event(EVENT_TRAINING_STARTED, self.current_task)

        try:
            self.controller.configure_experiment(experiment_spec or {})
            async with self._lock:
                for _ in range(rounds):
                    self.total_cycles += 1
                    self.last_run_at = datetime.now().isoformat()
                    try:
                        cycle_result = await asyncio.to_thread(self.controller.run_training_cycle)
                        if cycle_result is None:
                            self.no_data_cycles += 1
                            cycle_meta, cycle_id = self._last_cycle_meta()
                            item = self._build_nodata_cycle_item(
                                cycle_meta=cycle_meta,
                                cycle_id=cycle_id,
                                requested_data_mode=requested_data_mode,
                            )
                        else:
                            self.success_cycles += 1
                            item = self._to_result_dict(cycle_result)
                    except Exception as exc:
                        self.failed_cycles += 1
                        cycle_meta, cycle_id = self._last_cycle_meta()
                        if isinstance(exc, DataSourceUnavailableError):
                            error_payload = exc.to_dict()
                            self.last_error = error_payload["error"]
                            item = self._build_data_source_error_cycle_item(
                                error_payload=error_payload,
                                cycle_meta=cycle_meta,
                                cycle_id=cycle_id,
                                requested_data_mode=requested_data_mode,
                            )
                            logger.warning("Commander body cycle failed due to unavailable data source")
                        else:
                            self.last_error = str(exc)
                            item = self._build_generic_error_cycle_item(
                                exc=exc,
                                cycle_meta=cycle_meta,
                                cycle_id=cycle_id,
                                requested_data_mode=requested_data_mode,
                            )
                            logger.exception("Commander body cycle failed")
                    self.last_result = item
                    results.append(item)
        finally:
            run_status = self._derive_run_status(results)
            self.training_state = STATUS_IDLE
            self.last_completed_task = {
                **(self.current_task or {}),
                "finished_at": datetime.now().isoformat(),
                "result_count": len(results),
                "last_status": results[-1].get("status") if results else "empty",
                "run_status": run_status,
            }
            self.current_task = None
            self._clear_training_lock()
            self._emit_runtime_event(EVENT_TRAINING_FINISHED, self.last_completed_task or {})

        return _jsonable({
            "status": run_status,
            "rounds": rounds,
            "results": results,
            "summary": self.snapshot(),
        })

    async def autopilot_loop(self, interval_sec: int) -> None:
        logger.info("Body autopilot loop started (interval=%ss)", interval_sec)
        try:
            while not self._stop_event.is_set():
                await self.run_cycles(rounds=1, task_source="autopilot")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval_sec)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Body autopilot loop stopped")

    def stop(self) -> None:
        self._stop_event.set()

    def snapshot(self) -> dict[str, Any]:
        rolling = {}
        if hasattr(self.controller, "_rolling_self_assessment"):
            try:
                rolling = self.controller._rolling_self_assessment()  # pylint: disable=protected-access
            except Exception:
                rolling = {}

        return _jsonable({
            "total_cycles": self.total_cycles,
            "investment_model": getattr(self.controller, "model_name", "momentum"),
            "investment_model_config": getattr(self.controller, "model_config_path", ""),
            "model_routing_enabled": getattr(self.controller, "model_routing_enabled", False),
            "model_routing_mode": getattr(self.controller, "model_routing_mode", "off"),
            "last_routing_decision": getattr(self.controller, "last_routing_decision", {}),
            "success_cycles": self.success_cycles,
            "no_data_cycles": self.no_data_cycles,
            "failed_cycles": self.failed_cycles,
            "last_result": self.last_result,
            "last_error": self.last_error,
            "last_run_at": self.last_run_at,
            "current_cycle_id": self.controller.current_cycle_id,
            "rolling_self_assessment": rolling,
            "training_state": self.training_state,
            "is_training": self._lock.locked(),
            "current_task": self.current_task,
            "last_completed_task": self.last_completed_task,
            "training_lock_file": str(self.cfg.training_lock_file),
        })

    def _artifact_paths_for_cycle(self, cycle_id: int | None) -> dict[str, str]:
        if not cycle_id:
            return {}
        cid = int(cycle_id)
        return {
            "cycle_result_path": str(self.cfg.training_output_dir / f"cycle_{cid}.json"),
            "selection_meeting_json_path": str(self.cfg.meeting_log_dir / "selection" / f"meeting_{cid:04d}.json"),
            "selection_meeting_markdown_path": str(self.cfg.meeting_log_dir / "selection" / f"meeting_{cid:04d}.md"),
            "review_meeting_json_path": str(self.cfg.meeting_log_dir / "review" / f"review_{cid:04d}.json"),
            "review_meeting_markdown_path": str(self.cfg.meeting_log_dir / "review" / f"review_{cid:04d}.md"),
            "optimization_events_path": str(self.cfg.training_output_dir / "optimization_events.jsonl"),
        }

    def _to_result_dict(self, result: TrainingResult) -> dict[str, Any]:
        return _jsonable({
            "status": STATUS_OK,
            "cycle_id": result.cycle_id,
            "cutoff_date": result.cutoff_date,
            "selected_count": len(result.selected_stocks),
            "selected_stocks": result.selected_stocks[:20],
            "initial_capital": result.initial_capital,
            "final_value": result.final_value,
            "return_pct": result.return_pct,
            "is_profit": result.is_profit,
            "trade_count": len(result.trade_history),
            "analysis": (result.analysis or "")[:400],
            "params": result.params,
            "data_mode": result.data_mode,
            "requested_data_mode": result.requested_data_mode,
            "effective_data_mode": result.effective_data_mode,
            "llm_mode": result.llm_mode,
            "degraded": result.degraded,
            "degrade_reason": result.degrade_reason,
            "selection_mode": result.selection_mode,
            "agent_used": result.agent_used,
            "llm_used": result.llm_used,
            "benchmark_passed": result.benchmark_passed,
            "model_name": result.model_name,
            "config_name": result.config_name,
            "routing_decision": result.routing_decision,
            "strategy_scores": result.strategy_scores,
            "review_applied": result.review_applied,
            "config_snapshot_path": result.config_snapshot_path,
            "optimization_event_count": len(result.optimization_events or []),
            "optimization_events": result.optimization_events,
            "audit_tags": result.audit_tags,
            "artifacts": self._artifact_paths_for_cycle(result.cycle_id),
            "timestamp": datetime.now().isoformat(),
        })


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
        self.stock_analysis = StockAnalysisService(strategy_dir=self.cfg.stock_strategy_dir, model=self.cfg.model, api_key=self.cfg.api_key, api_base=self.cfg.api_base, enable_llm_react=not self.cfg.mock_mode)
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

    def _on_body_event(self, event: str, payload: dict[str, Any]) -> None:
        self._append_runtime_event(event, payload, source="body")
        if event == EVENT_TRAINING_STARTED:
            self._update_runtime_fields(state=STATUS_TRAINING, current_task=payload)
        elif event == EVENT_TRAINING_FINISHED:
            self._update_runtime_fields(state=STATUS_IDLE, current_task=None, last_task=payload)
        self._persist_state()

    def _append_runtime_event(self, event: str, payload: dict[str, Any], *, source: str = "runtime") -> dict[str, Any]:
        return append_event_row(self.cfg.runtime_events_path, event, payload, source=source)

    def _set_runtime_state(self, state: str) -> None:
        self._update_runtime_fields(state=state)

    @staticmethod
    def _copy_runtime_task(task: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if task is None:
            return None
        return deepcopy(task)

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
        task = {
            "type": task_type,
            "source": source,
            "started_at": datetime.now().isoformat(),
            **metadata,
        }
        self._update_runtime_fields(current_task=task)
        self._append_runtime_event(EVENT_TASK_STARTED, task, source="runtime")

    def _end_task(self, status: str = STATUS_OK, **metadata: Any) -> None:
        with self._task_lock:
            if self.current_task is None:
                return
            self.last_task = {
                **self._copy_runtime_task(self.current_task),
                "finished_at": datetime.now().isoformat(),
                "status": status,
                **metadata,
            }
            self._append_runtime_event(EVENT_TASK_FINISHED, self.last_task, source="runtime")
            self.current_task = None

    def _complete_runtime_task(self, *, status: str = STATUS_OK, state: str | None = None, **metadata: Any) -> None:
        if state is not None:
            self._set_runtime_state(state)
        self._end_task(status, **metadata)
        self._persist_state()

    def _record_ask_activity(self, event: str, *, session_key: str, channel: str, chat_id: str) -> None:
        payload = {"session_key": session_key, "channel": channel, "chat_id": chat_id}
        self.memory.append_audit(event, session_key, {"channel": channel, "chat_id": chat_id})
        self._append_runtime_event(event, payload, source="brain")

    def _read_runtime_lock_payload(self) -> dict[str, Any]:
        try:
            raw = self.cfg.runtime_lock_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            logger.warning("Failed to read runtime lock payload %s: %s", self.cfg.runtime_lock_file, exc)
            return {}

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Invalid runtime lock payload %s: %s", self.cfg.runtime_lock_file, exc)
            return {}

        if not isinstance(data, dict):
            logger.warning("Runtime lock payload must be a JSON object: %s", self.cfg.runtime_lock_file)
            return {}
        return data

    def _is_pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _acquire_runtime_lock(self) -> None:
        self.cfg.runtime_lock_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "instance_id": self.instance_id,
            "started_at": datetime.now().isoformat(),
            "workspace": str(self.cfg.workspace),
        }

        while True:
            try:
                fd = os.open(self.cfg.runtime_lock_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                existing = self._read_runtime_lock_payload()
                existing_pid = int(existing.get("pid") or 0)
                if existing_pid and self._is_pid_alive(existing_pid):
                    raise RuntimeError(
                        f"Commander runtime already active (pid={existing_pid}, host={existing.get('host', '')})"
                    )
                if existing and existing_pid:
                    try:
                        self.cfg.runtime_lock_file.unlink()
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        raise RuntimeError(f"Failed to clear stale runtime lock: {exc}") from exc
                    continue
                raise RuntimeError(
                    f"Commander runtime lock exists but is unreadable: {self.cfg.runtime_lock_file}"
                )

            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
            except Exception:
                self.cfg.runtime_lock_file.unlink(missing_ok=True)
                raise

            self._runtime_lock_acquired = True
            return

    def _release_runtime_lock(self) -> None:
        if self._runtime_lock_acquired:
            existing = self._read_runtime_lock_payload()
            existing_pid = int(existing.get("pid") or 0)
            existing_instance = str(existing.get("instance_id") or "")
            if not existing or existing_pid == os.getpid() or existing_instance == self.instance_id:
                self.cfg.runtime_lock_file.unlink(missing_ok=True)
            else:
                logger.warning(
                    "Runtime lock ownership changed before release; keeping lock file intact: %s",
                    self.cfg.runtime_lock_file,
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

            await self.cron.start()
            if self.cfg.heartbeat_enabled:
                await self.heartbeat.start()
            if self.cfg.bridge_enabled:
                await self.bridge.start()

            self._notify_task = asyncio.create_task(self._drain_notifications())
            if self.cfg.autopilot_enabled:
                self._autopilot_task = asyncio.create_task(self.body.autopilot_loop(self.cfg.training_interval_sec))

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

        self.body.stop()
        if self._autopilot_task:
            self._autopilot_task.cancel()
            await asyncio.gather(self._autopilot_task, return_exceptions=True)
            self._autopilot_task = None
        if self._notify_task:
            self._notify_task.cancel()
            await asyncio.gather(self._notify_task, return_exceptions=True)
            self._notify_task = None

        self.bridge.stop()
        self.heartbeat.stop()
        self.cron.stop()
        await self.brain.close()
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
        self._ensure_runtime_storage()
        self._begin_task("ask", channel, session_key=session_key, chat_id=chat_id)
        self.memory.append(
            kind="user",
            session_key=session_key,
            content=message,
            metadata={"channel": channel, "chat_id": chat_id},
        )
        self._record_ask_activity(EVENT_ASK_STARTED, session_key=session_key, channel=channel, chat_id=chat_id)
        try:
            response = await self.brain.process_direct(message, session_key=session_key)
            self.memory.append(
                kind="assistant",
                session_key=session_key,
                content=response or "",
                metadata={"channel": channel, "chat_id": chat_id},
            )
            self._record_ask_activity(EVENT_ASK_FINISHED, session_key=session_key, channel=channel, chat_id=chat_id)
            self._complete_runtime_task(status=STATUS_OK)
            return response
        except Exception:
            self._complete_runtime_task(status=STATUS_ERROR)
            raise

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
        return self._attach_bounded_workflow(
            payload,
            domain="analytics",
            operation="get_investment_models",
            runtime_method="CommanderRuntime.get_investment_models",
            runtime_tool="invest_investment_models",
            agent_kind="bounded_analytics_agent",
            writes_state=False,
            available_tools=self._analytics_domain_tools(),
            workflow=["analytics_scope_resolve", "investment_models_read", "finalize"],
            phase_stats={"count": int(payload.get("count", len(list(payload.get("items") or []))))},
        )

    def get_leaderboard(self) -> dict[str, Any]:
        payload = get_leaderboard_payload(self)
        return self._attach_bounded_workflow(
            payload,
            domain="analytics",
            operation="get_leaderboard",
            runtime_method="CommanderRuntime.get_leaderboard",
            runtime_tool="invest_leaderboard",
            agent_kind="bounded_analytics_agent",
            writes_state=False,
            available_tools=self._analytics_domain_tools(),
            workflow=["analytics_scope_resolve", "leaderboard_read", "finalize"],
            phase_stats={"count": int(payload.get("count", len(list(payload.get("items") or []))))},
        )

    def get_allocator_preview(self, *, regime: str = "oscillation", top_n: int = 3, as_of_date: str | None = None) -> dict[str, Any]:
        payload = get_allocator_payload(self, regime=regime, top_n=top_n, as_of_date=as_of_date)
        return self._attach_bounded_workflow(
            payload,
            domain="analytics",
            operation="get_allocator_preview",
            runtime_method="CommanderRuntime.get_allocator_preview",
            runtime_tool="invest_allocator",
            agent_kind="bounded_analytics_agent",
            writes_state=False,
            available_tools=self._analytics_domain_tools(),
            workflow=["analytics_scope_resolve", "allocator_preview_read", "finalize"],
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
        return self._attach_bounded_workflow(
            payload,
            domain="analytics",
            operation="get_model_routing_preview",
            runtime_method="CommanderRuntime.get_model_routing_preview",
            runtime_tool="invest_model_routing_preview",
            agent_kind="bounded_analytics_agent",
            writes_state=False,
            available_tools=self._analytics_domain_tools(),
            workflow=["analytics_scope_resolve", "routing_preview_read", "finalize"],
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
        return self._attach_bounded_workflow(
            payload,
            domain="config",
            operation="list_agent_prompts",
            runtime_method="CommanderRuntime.list_agent_prompts",
            runtime_tool="invest_agent_prompts_list",
            agent_kind="bounded_config_agent",
            writes_state=False,
            available_tools=self._config_domain_tools(),
            workflow=["config_scope_resolve", "agent_prompts_read", "finalize"],
            phase_stats={"count": len(items)},
        )

    def update_agent_prompt(self, *, agent_name: str, system_prompt: str) -> dict[str, Any]:
        payload = update_agent_prompt_payload(agent_name=agent_name, system_prompt=system_prompt)
        self._append_runtime_event("agent_prompt_updated", {"agent_name": agent_name}, source="config")
        return self._attach_mutating_workflow(
            payload,
            domain="config",
            operation="update_agent_prompt",
            runtime_method="CommanderRuntime.update_agent_prompt",
            runtime_tool="invest_agent_prompts_update",
            agent_kind="bounded_config_agent",
            available_tools=self._config_domain_tools(),
            workflow=["config_scope_resolve", "agent_prompt_write", "finalize"],
            phase_stats={"agent_name": agent_name, "prompt_length": len(system_prompt)},
        )

    def get_runtime_paths(self) -> dict[str, Any]:
        payload = get_runtime_paths_payload(self, project_root=PROJECT_ROOT)
        return self._attach_bounded_workflow(
            payload,
            domain="config",
            operation="get_runtime_paths",
            runtime_method="CommanderRuntime.get_runtime_paths",
            runtime_tool="invest_runtime_paths_get",
            agent_kind="bounded_config_agent",
            writes_state=False,
            available_tools=self._config_domain_tools(),
            workflow=["config_scope_resolve", "runtime_paths_read", "finalize"],
            phase_stats={"path_count": len(dict(payload.get("paths") or {})) if isinstance(payload, dict) else 0},
        )

    def update_runtime_paths(self, patch: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            return self._build_confirmation_required_workflow(
                domain="config",
                operation="update_runtime_paths",
                runtime_method="CommanderRuntime.update_runtime_paths",
                runtime_tool="invest_runtime_paths_update",
                agent_kind="bounded_config_agent",
                available_tools=self._config_domain_tools(),
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
        return self._attach_mutating_workflow(
            payload,
            domain="config",
            operation="update_runtime_paths",
            runtime_method="CommanderRuntime.update_runtime_paths",
            runtime_tool="invest_runtime_paths_update",
            agent_kind="bounded_config_agent",
            available_tools=self._config_domain_tools(),
            workflow=["config_scope_resolve", "runtime_paths_write", "finalize"],
            phase_stats={"updated_count": len(list(payload.get("updated") or [])), "confirmed": True},
        )

    @staticmethod
    def _config_domain_tools() -> list[str]:
        return [
            "invest_control_plane_get",
            "invest_control_plane_update",
            "invest_evolution_config_get",
            "invest_evolution_config_update",
            "invest_runtime_paths_get",
            "invest_runtime_paths_update",
            "invest_agent_prompts_list",
            "invest_agent_prompts_update",
        ]

    @staticmethod
    def _data_domain_tools() -> list[str]:
        return [
            "invest_data_status",
            "invest_data_download",
            "invest_data_capital_flow",
            "invest_data_dragon_tiger",
            "invest_data_intraday_60m",
        ]

    @staticmethod
    def _training_domain_tools() -> list[str]:
        return [
            "invest_train",
            "invest_quick_test",
            "invest_training_plan_create",
            "invest_training_plan_list",
            "invest_training_plan_execute",
            "invest_training_runs_list",
            "invest_training_evaluations_list",
            "invest_training_lab_summary",
        ]

    @staticmethod
    def _runtime_domain_tools() -> list[str]:
        return [
            "invest_quick_status",
            "invest_deep_status",
            "invest_events_tail",
            "invest_events_summary",
            "invest_runtime_diagnostics",
        ]

    @staticmethod
    def _memory_domain_tools() -> list[str]:
        return [
            "invest_memory_search",
            "invest_memory_list",
            "invest_memory_get",
        ]

    @staticmethod
    def _scheduler_domain_tools() -> list[str]:
        return [
            "invest_cron_add",
            "invest_cron_list",
            "invest_cron_remove",
        ]

    @staticmethod
    def _analytics_domain_tools() -> list[str]:
        return [
            "invest_investment_models",
            "invest_leaderboard",
            "invest_allocator",
            "invest_model_routing_preview",
        ]

    @staticmethod
    def _strategy_domain_tools() -> list[str]:
        return [
            "invest_list_strategies",
            "invest_reload_strategies",
            "invest_stock_strategies",
        ]

    @staticmethod
    def _plugin_domain_tools() -> list[str]:
        return [
            "invest_plugins_reload",
        ]


    def _attach_bounded_workflow(
        self,
        payload: Any,
        *,
        domain: str,
        operation: str,
        runtime_method: str,
        runtime_tool: str,
        agent_kind: str,
        writes_state: bool,
        available_tools: list[str],
        workflow: list[str],
        phase_stats: dict[str, Any] | None = None,
        extra_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = dict(payload) if isinstance(payload, dict) else {"status": STATUS_OK, "content": payload}
        body.setdefault(
            "entrypoint",
            {
                "kind": "commander_bounded_workflow",
                "domain": domain,
                "runtime_method": runtime_method,
                "runtime_tool": runtime_tool,
                "meeting_path": False,
                "agent_kind": agent_kind,
                "agent_system": "commander_bounded_workflows",
            },
        )
        orchestration = dict(body.get("orchestration") or {})
        orchestration["mode"] = str(orchestration.get("mode") or ("bounded_mutating_workflow" if writes_state else "bounded_readonly_workflow"))
        orchestration["available_tools"] = list(available_tools)
        orchestration["allowed_tools"] = list(orchestration.get("allowed_tools") or available_tools)
        normalized_workflow = list(workflow)
        normalized_phase_stats = _jsonable(dict(phase_stats or {}))
        orchestration["workflow"] = normalized_workflow
        orchestration["phase_stats"] = normalized_phase_stats
        policy = dict(orchestration.get("policy") or {})
        policy.update(
            {
                "source": "commander_runtime",
                "domain": domain,
                "agent_kind": agent_kind,
                "runtime_tool": runtime_tool,
                "fixed_boundary": True,
                "fixed_workflow": True,
                "writes_state": writes_state,
                "tool_catalog_scope": f"{domain}_domain",
            }
        )
        if extra_policy:
            policy.update(_jsonable(dict(extra_policy)))
        orchestration["policy"] = policy
        artifacts = {
            "workspace": str(self.cfg.workspace),
            "runtime_tool": runtime_tool,
            "runtime_method": runtime_method,
            "domain": domain,
            "operation": operation,
        }
        if isinstance(body.get("artifacts"), dict):
            artifacts.update(dict(body.get("artifacts") or {}))
        coverage = _build_workflow_coverage(
            workflow=normalized_workflow,
            phase_stats=normalized_phase_stats,
            existing=body.get("coverage") if isinstance(body.get("coverage"), dict) else None,
        )
        body["orchestration"] = orchestration
        body["protocol"] = {
            "schema_version": BOUNDED_WORKFLOW_SCHEMA_VERSION,
            "task_bus_schema_version": TASK_BUS_SCHEMA_VERSION,
            "plan_schema_version": PLAN_SCHEMA_VERSION,
            "coverage_schema_version": COVERAGE_SCHEMA_VERSION,
            "artifact_taxonomy_schema_version": ARTIFACT_TAXONOMY_SCHEMA_VERSION,
            "domain": domain,
            "operation": operation,
        }
        body["artifacts"] = _jsonable(artifacts)
        body["coverage"] = _jsonable(coverage)
        body["artifact_taxonomy"] = _jsonable(_build_workflow_artifact_taxonomy(artifacts))
        return _jsonable(body)

    def _attach_mutating_workflow(
        self,
        payload: Any,
        *,
        domain: str,
        operation: str,
        runtime_method: str,
        runtime_tool: str,
        agent_kind: str,
        available_tools: list[str],
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

    def _build_confirmation_required_workflow(
        self,
        *,
        domain: str,
        operation: str,
        runtime_method: str,
        runtime_tool: str,
        agent_kind: str,
        available_tools: list[str],
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
        return self._attach_mutating_workflow(
            payload,
            domain=domain,
            operation=operation,
            runtime_method=runtime_method,
            runtime_tool=runtime_tool,
            agent_kind=agent_kind,
            available_tools=available_tools,
            workflow=[f"{domain}_scope_resolve", "gate_confirmation", "finalize"],
            phase_stats=phase_stats,
            extra_policy={"confirmation_gate": True},
        )

    def build_training_confirmation_required(self, *, rounds: int, mock: bool) -> dict[str, Any]:
        return self._build_confirmation_required_workflow(
            domain="training",
            operation="train_once",
            runtime_method="CommanderRuntime.train_once",
            runtime_tool="invest_train",
            agent_kind="bounded_training_agent",
            available_tools=self._training_domain_tools(),
            message="多轮真实训练属于高风险操作，请使用 confirm=true 再执行。",
            pending={"rounds": int(rounds), "mock": bool(mock)},
            phase_stats={"rounds": int(rounds), "mock": bool(mock), "requires_confirmation": True},
        )

    def get_evolution_config(self) -> dict[str, Any]:
        payload = get_evolution_config_payload(project_root=PROJECT_ROOT, live_config=config)
        config_payload = dict(payload.get("config") or {}) if isinstance(payload, dict) else {}
        return self._attach_bounded_workflow(
            payload,
            domain="config",
            operation="get_evolution_config",
            runtime_method="CommanderRuntime.get_evolution_config",
            runtime_tool="invest_evolution_config_get",
            agent_kind="bounded_config_agent",
            writes_state=False,
            available_tools=self._config_domain_tools(),
            workflow=["config_scope_resolve", "evolution_config_read", "finalize"],
            phase_stats={"config_key_count": len(config_payload)},
        )

    def update_evolution_config(self, patch: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
        if not confirm and any(key in patch for key in ("investment_model", "investment_model_config", "data_source", "model_routing_enabled", "model_routing_mode")):
            return self._build_confirmation_required_workflow(
                domain="config",
                operation="update_evolution_config",
                runtime_method="CommanderRuntime.update_evolution_config",
                runtime_tool="invest_evolution_config_update",
                agent_kind="bounded_config_agent",
                available_tools=self._config_domain_tools(),
                message="当前 patch 会影响训练主链路，请用 confirm=true 再执行。",
                pending={"patch": patch},
                phase_stats={"pending_key_count": len(dict(patch or {})), "requires_confirmation": True},
            )
        payload = update_evolution_config_payload(patch=patch, project_root=PROJECT_ROOT, live_config=config, source="commander")
        controller = getattr(getattr(self, "body", None), "controller", None)
        if controller is not None and hasattr(controller, "refresh_runtime_from_config"):
            controller.refresh_runtime_from_config()
        self._append_runtime_event("evolution_config_updated", {"updated": payload.get("updated", [])}, source="config")
        return self._attach_mutating_workflow(
            payload,
            domain="config",
            operation="update_evolution_config",
            runtime_method="CommanderRuntime.update_evolution_config",
            runtime_tool="invest_evolution_config_update",
            agent_kind="bounded_config_agent",
            available_tools=self._config_domain_tools(),
            workflow=["config_scope_resolve", "evolution_config_write", "finalize"],
            phase_stats={"updated_count": len(list(payload.get("updated") or [])), "confirmed": bool(confirm)},
        )

    def get_control_plane(self) -> dict[str, Any]:
        payload = get_control_plane_payload(project_root=PROJECT_ROOT)
        config_payload = dict(payload.get("config") or {}) if isinstance(payload, dict) else {}
        return self._attach_bounded_workflow(
            payload,
            domain="config",
            operation="get_control_plane",
            runtime_method="CommanderRuntime.get_control_plane",
            runtime_tool="invest_control_plane_get",
            agent_kind="bounded_config_agent",
            writes_state=False,
            available_tools=self._config_domain_tools(),
            workflow=["config_scope_resolve", "control_plane_read", "finalize"],
            phase_stats={"config_section_count": len(config_payload)},
        )

    def update_control_plane(self, patch: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            return self._build_confirmation_required_workflow(
                domain="config",
                operation="update_control_plane",
                runtime_method="CommanderRuntime.update_control_plane",
                runtime_tool="invest_control_plane_update",
                agent_kind="bounded_config_agent",
                available_tools=self._config_domain_tools(),
                message="control plane 更新需要重启才能全局生效，请用 confirm=true 再执行。",
                pending={"patch": patch},
                extra_payload={"restart_required": True},
                phase_stats={"pending_key_count": len(dict(patch or {})), "requires_confirmation": True, "restart_required": True},
            )
        payload = update_control_plane_payload(patch=patch, project_root=PROJECT_ROOT, source="commander")
        self._append_runtime_event("control_plane_updated", {"updated": payload.get("updated", [])}, source="config")
        return self._attach_mutating_workflow(
            payload,
            domain="config",
            operation="update_control_plane",
            runtime_method="CommanderRuntime.update_control_plane",
            runtime_tool="invest_control_plane_update",
            agent_kind="bounded_config_agent",
            available_tools=self._config_domain_tools(),
            workflow=["config_scope_resolve", "control_plane_write", "finalize"],
            phase_stats={"updated_count": len(list(payload.get("updated") or [])), "confirmed": bool(confirm), "restart_required": True},
        )

    def get_data_status(self, *, refresh: bool = False) -> dict[str, Any]:
        payload = get_data_status_payload(refresh=refresh)
        quality = dict(payload.get("quality") or {}) if isinstance(payload, dict) else {}
        return self._attach_bounded_workflow(
            payload,
            domain="data",
            operation="get_data_status",
            runtime_method="CommanderRuntime.get_data_status",
            runtime_tool="invest_data_status",
            agent_kind="bounded_data_agent",
            writes_state=False,
            available_tools=self._data_domain_tools(),
            workflow=["data_scope_resolve", "data_status_refresh" if refresh else "data_status_read", "finalize"],
            phase_stats={"requested_refresh": bool(refresh), "health_status": quality.get("health_status", "unknown")},
        )

    def get_capital_flow(self, *, codes: list[str] | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 200) -> dict[str, Any]:
        payload = get_capital_flow_payload(codes=codes, start_date=start_date, end_date=end_date, limit=limit)
        return self._attach_bounded_workflow(
            payload,
            domain="data",
            operation="get_capital_flow",
            runtime_method="CommanderRuntime.get_capital_flow",
            runtime_tool="invest_data_capital_flow",
            agent_kind="bounded_data_agent",
            writes_state=False,
            available_tools=self._data_domain_tools(),
            workflow=["data_scope_resolve", "capital_flow_query", "finalize"],
            phase_stats={"count": int(payload.get("count", 0)), "limit": int(limit)},
        )

    def get_dragon_tiger(self, *, codes: list[str] | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 200) -> dict[str, Any]:
        payload = get_dragon_tiger_payload(codes=codes, start_date=start_date, end_date=end_date, limit=limit)
        return self._attach_bounded_workflow(
            payload,
            domain="data",
            operation="get_dragon_tiger",
            runtime_method="CommanderRuntime.get_dragon_tiger",
            runtime_tool="invest_data_dragon_tiger",
            agent_kind="bounded_data_agent",
            writes_state=False,
            available_tools=self._data_domain_tools(),
            workflow=["data_scope_resolve", "dragon_tiger_query", "finalize"],
            phase_stats={"count": int(payload.get("count", 0)), "limit": int(limit)},
        )

    def get_intraday_60m(self, *, codes: list[str] | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 500) -> dict[str, Any]:
        payload = get_intraday_60m_payload(codes=codes, start_date=start_date, end_date=end_date, limit=limit)
        return self._attach_bounded_workflow(
            payload,
            domain="data",
            operation="get_intraday_60m",
            runtime_method="CommanderRuntime.get_intraday_60m",
            runtime_tool="invest_data_intraday_60m",
            agent_kind="bounded_data_agent",
            writes_state=False,
            available_tools=self._data_domain_tools(),
            workflow=["data_scope_resolve", "intraday_60m_query", "finalize"],
            phase_stats={"count": int(payload.get("count", 0)), "limit": int(limit)},
        )

    def get_data_download_status(self) -> dict[str, Any]:
        payload = get_data_download_status_payload()
        return self._attach_bounded_workflow(
            payload,
            domain="data",
            operation="get_data_download_status",
            runtime_method="CommanderRuntime.get_data_download_status",
            runtime_tool="invest_data_download",
            agent_kind="bounded_data_agent",
            writes_state=False,
            available_tools=self._data_domain_tools(),
            workflow=["data_scope_resolve", "download_job_read", "finalize"],
            phase_stats={"job_status": str(payload.get("status", "unknown"))},
        )

    def trigger_data_download(self, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            return self._build_confirmation_required_workflow(
                domain="data",
                operation="trigger_data_download",
                runtime_method="CommanderRuntime.trigger_data_download",
                runtime_tool="invest_data_download",
                agent_kind="bounded_data_agent",
                available_tools=self._data_domain_tools(),
                message="后台数据同步会访问外部数据源，请用 confirm=true 再执行。",
                extra_payload={"job": get_data_download_status_payload()},
                phase_stats={"requires_confirmation": True},
            )
        payload = trigger_data_download()
        self._append_runtime_event("data_download_triggered", payload, source="data")
        return self._attach_mutating_workflow(
            payload,
            domain="data",
            operation="trigger_data_download",
            runtime_method="CommanderRuntime.trigger_data_download",
            runtime_tool="invest_data_download",
            agent_kind="bounded_data_agent",
            available_tools=self._data_domain_tools(),
            workflow=["data_scope_resolve", "download_job_trigger", "finalize"],
            phase_stats={"job_status": str(payload.get("status", "unknown")), "confirmed": True},
        )

    def list_memory(self, *, query: str = "", limit: int = 20) -> dict[str, Any]:
        rows = self.memory.search(query=query, limit=limit)
        items = [memory_brief_row(row) for row in rows]
        return self._attach_bounded_workflow(
            {"count": len(items), "items": items},
            domain="memory",
            operation="list_memory",
            runtime_method="CommanderRuntime.list_memory",
            runtime_tool="invest_memory_list",
            agent_kind="bounded_memory_agent",
            writes_state=False,
            available_tools=self._memory_domain_tools(),
            workflow=["memory_scope_resolve", "memory_query", "finalize"],
            phase_stats={"query": query, "count": len(items), "limit": int(limit)},
        )

    def get_memory_detail(self, record_id: str) -> dict[str, Any]:
        row = self.memory.get(record_id)
        if row is None:
            raise FileNotFoundError("memory record not found")
        payload = build_memory_detail(self, row)
        return self._attach_bounded_workflow(
            payload,
            domain="memory",
            operation="get_memory_detail",
            runtime_method="CommanderRuntime.get_memory_detail",
            runtime_tool="invest_memory_get",
            agent_kind="bounded_memory_agent",
            writes_state=False,
            available_tools=self._memory_domain_tools(),
            workflow=["memory_scope_resolve", "memory_detail_read", "finalize"],
            phase_stats={"record_id": str(record_id)},
        )

    def get_events_tail(self, *, limit: int = 50) -> dict[str, Any]:
        rows = read_event_rows(self.cfg.runtime_events_path, limit=limit)
        return self._attach_bounded_workflow(
            {"count": len(rows), "items": rows},
            domain="runtime",
            operation="get_events_tail",
            runtime_method="CommanderRuntime.get_events_tail",
            runtime_tool="invest_events_tail",
            agent_kind="bounded_runtime_agent",
            writes_state=False,
            available_tools=self._runtime_domain_tools(),
            workflow=["runtime_scope_resolve", "events_tail_read", "finalize"],
            phase_stats={"count": len(rows), "limit": int(limit)},
        )

    def get_events_summary(self, *, limit: int = 100) -> dict[str, Any]:
        rows = read_event_rows(self.cfg.runtime_events_path, limit=limit)
        payload = {"status": STATUS_OK, "summary": summarize_event_rows(rows), "items": rows}
        return self._attach_bounded_workflow(
            payload,
            domain="runtime",
            operation="get_events_summary",
            runtime_method="CommanderRuntime.get_events_summary",
            runtime_tool="invest_events_summary",
            agent_kind="bounded_runtime_agent",
            writes_state=False,
            available_tools=self._runtime_domain_tools(),
            workflow=["runtime_scope_resolve", "events_summary_read", "finalize"],
            phase_stats={"count": len(rows), "limit": int(limit)},
        )

    def get_runtime_diagnostics(self, *, event_limit: int = 50, memory_limit: int = 20) -> dict[str, Any]:
        payload = build_runtime_diagnostics(self, event_limit=event_limit, memory_limit=memory_limit)
        return self._attach_bounded_workflow(
            payload,
            domain="runtime",
            operation="get_runtime_diagnostics",
            runtime_method="CommanderRuntime.get_runtime_diagnostics",
            runtime_tool="invest_runtime_diagnostics",
            agent_kind="bounded_runtime_agent",
            writes_state=False,
            available_tools=self._runtime_domain_tools(),
            workflow=["runtime_scope_resolve", "diagnostics_build", "finalize"],
            phase_stats={"event_limit": int(event_limit), "memory_limit": int(memory_limit)},
        )

    def get_training_lab_summary(self, *, limit: int = 5) -> dict[str, Any]:
        payload = {
            "status": STATUS_OK,
            **self._lab_counts(),
            "latest_plans": self.list_training_plans(limit=limit).get("items", []),
            "latest_runs": self.list_training_runs(limit=limit).get("items", []),
            "latest_evaluations": self.list_training_evaluations(limit=limit).get("items", []),
        }
        return self._attach_bounded_workflow(
            payload,
            domain="training",
            operation="get_training_lab_summary",
            runtime_method="CommanderRuntime.get_training_lab_summary",
            runtime_tool="invest_training_lab_summary",
            agent_kind="bounded_training_agent",
            writes_state=False,
            available_tools=self._training_domain_tools(),
            workflow=["training_scope_resolve", "lab_summary_read", "finalize"],
            phase_stats={
                "limit": int(limit),
                "plan_count": int(payload.get("plan_count", 0)),
                "run_count": int(payload.get("run_count", 0)),
                "evaluation_count": int(payload.get("evaluation_count", 0)),
            },
        )

    def ask_stock(self, *, question: str, query: str, strategy: str = "chan_theory", days: int = 60) -> dict[str, Any]:
        return self.stock_analysis.ask_stock(question=question, query=query, strategy=strategy, days=days)

    def list_stock_strategies(self) -> dict[str, Any]:
        payload = {"status": STATUS_OK, "items": self.stock_analysis.list_strategies()}
        return self._attach_bounded_workflow(
            payload,
            domain="strategy",
            operation="list_stock_strategies",
            runtime_method="CommanderRuntime.list_stock_strategies",
            runtime_tool="invest_stock_strategies",
            agent_kind="bounded_strategy_agent",
            writes_state=False,
            available_tools=self._strategy_domain_tools(),
            workflow=["strategy_scope_resolve", "stock_strategy_inventory_read", "finalize"],
            phase_stats={"count": len(list(payload.get("items") or []))},
        )

    def _load_leaderboard_snapshot(self) -> dict[str, Any]:
        path = Path(self.cfg.training_output_dir).parent / "leaderboard.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _build_promotion_summary(
        self,
        *,
        plan: dict[str, Any],
        ok_results: list[dict[str, Any]],
        avg_return_pct: float | None,
        avg_strategy_score: float | None,
        benchmark_pass_rate: float,
    ) -> dict[str, Any]:
        baseline_models = [str(x) for x in ((plan.get("model_scope") or {}).get("baseline_models") or []) if str(x).strip()]
        board = self._load_leaderboard_snapshot()
        entries = list(board.get("entries") or [])
        baseline_entries = [entry for entry in entries if str(entry.get("model_name") or "") in baseline_models]
        return build_promotion_summary(
            plan=plan,
            ok_results=ok_results,
            avg_return_pct=avg_return_pct,
            avg_strategy_score=avg_strategy_score,
            benchmark_pass_rate=benchmark_pass_rate,
            baseline_entries=baseline_entries,
        )

    def _build_training_evaluation_summary(self, payload: dict[str, Any], *, plan: dict[str, Any], run_id: str, error: str = "") -> dict[str, Any]:
        results = list(payload.get("results") or [])
        ok_results = [item for item in results if item.get("status") == STATUS_OK]
        returns = [float(item.get("return_pct") or 0.0) for item in ok_results]
        strategy_scores = [float((item.get("strategy_scores") or {}).get("overall_score", 0.0) or 0.0) for item in ok_results]
        benchmark_passes = sum(1 for item in ok_results if bool(item.get("benchmark_passed", False)))
        avg_return_pct = round(sum(returns) / len(returns), 4) if returns else None
        avg_strategy_score = round(sum(strategy_scores) / len(strategy_scores), 4) if strategy_scores else None
        benchmark_pass_rate = round(benchmark_passes / len(ok_results), 4) if ok_results else 0.0
        promotion = self._build_promotion_summary(
            plan=plan,
            ok_results=ok_results,
            avg_return_pct=avg_return_pct,
            avg_strategy_score=avg_strategy_score,
            benchmark_pass_rate=benchmark_pass_rate,
        )
        return build_training_evaluation_summary(
            payload=payload,
            plan=plan,
            run_id=run_id,
            error=error,
            promotion=promotion,
            run_path=str(self._training_run_path(run_id)),
            evaluation_path=str(self._training_eval_path(run_id)),
        )

    def _record_training_lab_artifacts(self, *, plan: dict[str, Any], payload: dict[str, Any], status: str, error: str = "") -> dict[str, Any]:
        run_id = self._new_training_run_id()
        eval_payload = self._build_training_evaluation_summary(payload, plan=plan, run_id=run_id, error=error)
        return self.training_lab.record_training_lab_artifacts(
            plan=plan,
            payload=payload,
            status=status,
            eval_payload=eval_payload,
            run_id=run_id,
            error=error,
        )

    def _append_training_memory(self, payload: dict[str, Any], *, rounds: int, mock: bool, status: str, error: str = "") -> None:
        results = list(payload.get("results") or [])
        summary = build_training_memory_summary(payload=payload, rounds=rounds, mock=mock, status=status, error=error)
        summary_line = (
            f"训练记录 | status={status} | rounds={rounds} | mock={'true' if mock else 'false'} | "
            f"成功={summary['success_count']} | 跳过={summary['skipped_count']} | 失败={summary['error_count']}"
        )
        requested_modes = list(summary.get("requested_data_modes") or [])
        effective_modes = list(summary.get("effective_data_modes") or [])
        llm_modes = list(summary.get("llm_modes") or [])
        if summary.get("avg_return_pct") is not None:
            summary_line += f" | 平均收益={summary['avg_return_pct']:+.2f}%"
        cycle_ids = list(summary.get("cycle_ids") or [])
        if cycle_ids:
            summary_line += f" | 周期={','.join(str(x) for x in cycle_ids)}"
        if requested_modes:
            summary_line += f" | 请求模式={','.join(requested_modes)}"
        if effective_modes:
            summary_line += f" | 实际模式={','.join(effective_modes)}"
        if llm_modes:
            summary_line += f" | LLM={','.join(llm_modes)}"
        if summary.get("degraded_count"):
            summary_line += f" | degraded={summary['degraded_count']}"
        if error:
            summary_line += f" | error={error}"

        self.memory.append(
            kind="training_run",
            session_key="runtime:train",
            content=summary_line,
            metadata={
                "training_run": True,
                "summary": _jsonable(summary),
                "results": _jsonable(results),
                "runtime_summary": _jsonable(payload.get("summary") or {}),
                "source": "runtime.train_once",
            },
        )

    def _load_training_plan_artifact(self, plan_id: str) -> tuple[Path, dict[str, Any]]:
        plan_path = self._training_plan_path(str(plan_id))
        if not plan_path.exists():
            raise FileNotFoundError(f"training plan not found: {plan_id}")
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid training plan json: {plan_id}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"training plan must decode to an object: {plan_id}")
        return plan_path, payload

    @staticmethod
    def _build_experiment_spec_from_plan(plan: dict[str, Any]) -> tuple[dict[str, Any], int, bool]:
        spec = dict(plan.get("spec") or {})
        rounds = int(spec.get("rounds", 1) or 1)
        mock = bool(spec.get("mock", False))
        experiment_spec = {
            "spec": spec,
            "protocol": dict(plan.get("protocol") or {}),
            "dataset": dict(plan.get("dataset") or {}),
            "model_scope": dict(plan.get("model_scope") or {}),
            "optimization": dict(plan.get("optimization") or {}),
            "llm": dict(plan.get("llm") or {}),
        }
        return experiment_spec, rounds, mock

    def _build_run_cycles_kwargs(
        self,
        *,
        plan: dict[str, Any],
        rounds: int,
        mock: bool,
        experiment_spec: dict[str, Any],
    ) -> dict[str, Any]:
        run_cycles_kwargs = {
            "rounds": rounds,
            "force_mock": mock,
            "task_source": str(plan.get("source", "manual")),
        }
        try:
            run_cycles_signature = inspect.signature(self.body.run_cycles)
            if "experiment_spec" in run_cycles_signature.parameters:
                run_cycles_kwargs["experiment_spec"] = experiment_spec
        except (TypeError, ValueError):
            run_cycles_kwargs["experiment_spec"] = experiment_spec
        return run_cycles_kwargs

    @staticmethod
    def _attach_training_lab_paths(payload: dict[str, Any], lab: dict[str, Any]) -> None:
        payload["training_lab"] = {
            "plan": {"plan_id": lab["plan"]["plan_id"], "path": lab["plan"]["artifacts"]["plan_path"]},
            "run": {"run_id": lab["run"]["run_id"], "path": lab["evaluation"]["artifacts"]["run_path"]},
            "evaluation": {"run_id": lab["evaluation"]["run_id"], "path": lab["evaluation"]["artifacts"]["evaluation_path"]},
        }

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
        return self._attach_bounded_workflow(
            payload,
            domain="training",
            operation="execute_training_plan",
            runtime_method="CommanderRuntime.execute_training_plan",
            runtime_tool="invest_training_plan_execute",
            agent_kind="bounded_training_agent",
            writes_state=True,
            available_tools=self._training_domain_tools(),
            workflow=["training_scope_resolve", "training_plan_load", "training_cycles_execute", "training_artifacts_record", "finalize"],
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

        plan["status"] = "running"
        plan["started_at"] = datetime.now().isoformat()
        self._write_json_artifact(plan_path, plan)
        self._begin_task("train_plan", str(plan.get("source", "manual")), rounds=rounds, mock=mock, plan_id=plan_id)
        self._set_runtime_state(STATUS_TRAINING)
        self.memory.append_audit("train_requested", "runtime:train", {"rounds": rounds, "mock": mock, "plan_id": plan_id})
        try:
            run_cycles_kwargs = self._build_run_cycles_kwargs(
                plan=plan,
                rounds=rounds,
                mock=mock,
                experiment_spec=experiment_spec,
            )
            out = await self.body.run_cycles(**run_cycles_kwargs)
            if data_error := self.body._extract_data_source_error(out):
                raise DataSourceUnavailableError.from_payload(data_error)
            status = str(out.get("status", STATUS_OK))
            lab = self._record_training_lab_artifacts(plan=plan, payload=out, status=status)
            self._attach_training_lab_paths(out, lab)
            self._append_training_memory(out, rounds=rounds, mock=mock, status=status)
            self._complete_runtime_task(
                state=STATUS_IDLE if status != STATUS_BUSY else STATUS_BUSY,
                status=status,
                rounds=rounds,
                mock=mock,
                plan_id=plan_id,
            )
            return self._wrap_training_execution_payload(out, plan_id=str(plan_id), rounds=rounds, mock=mock)
        except Exception as exc:
            error_payload = {"results": [], "summary": self.body.snapshot()}
            lab = self._record_training_lab_artifacts(plan=plan, payload=error_payload, status=STATUS_ERROR, error=str(exc))
            self._attach_training_lab_paths(error_payload, lab)
            self._append_training_memory(error_payload, rounds=rounds, mock=mock, status=STATUS_ERROR, error=str(exc))
            self._complete_runtime_task(
                state=STATUS_ERROR,
                status=STATUS_ERROR,
                rounds=rounds,
                mock=mock,
                plan_id=plan_id,
            )
            raise

    def reload_strategies(self) -> dict[str, Any]:
        self._ensure_runtime_storage()
        self._begin_task("reload_strategies", "direct")
        self._set_runtime_state(RUNTIME_STATE_RELOADING_STRATEGIES)
        self.strategy_registry.ensure_default_templates()
        genes = self.strategy_registry.reload()
        self._write_commander_identity()
        self._complete_runtime_task(state=STATUS_IDLE, status=STATUS_OK, gene_count=len(genes))
        return self._attach_bounded_workflow(
            {
                "status": STATUS_OK,
                "count": len(genes),
                "genes": [g.to_dict() for g in genes],
            },
            domain="strategy",
            operation="reload_strategies",
            runtime_method="CommanderRuntime.reload_strategies",
            runtime_tool="invest_reload_strategies",
            agent_kind="bounded_strategy_agent",
            writes_state=True,
            available_tools=self._strategy_domain_tools(),
            workflow=["strategy_scope_resolve", "strategy_reload", "finalize"],
            phase_stats={"gene_count": len(genes)},
        )

    @staticmethod
    def _normalize_status_detail(detail: str) -> str:
        detail_mode = str(detail or "fast").strip().lower()
        return detail_mode if detail_mode in {"fast", "slow"} else "fast"

    def _collect_data_status(self, detail_mode: str) -> dict[str, Any]:
        try:
            from market_data.datasets import WebDatasetService
            return WebDatasetService().get_status_summary(refresh=(detail_mode == "slow"))
        except Exception as exc:
            return {"status": STATUS_ERROR, "error": str(exc), "detail_mode": detail_mode}

    def _collect_status_event_rows(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return read_event_rows(self.cfg.runtime_events_path, limit=limit)

    def _collect_training_lab_status(self, *, include_recent: bool) -> dict[str, Any]:
        payload = {**self._lab_counts()}
        if include_recent:
            payload.update(
                {
                    "latest_plans": self.list_training_plans(limit=3).get("items", []),
                    "latest_runs": self.list_training_runs(limit=3).get("items", []),
                    "latest_evaluations": self.list_training_evaluations(limit=3).get("items", []),
                }
            )
        else:
            payload.update({"latest_plans": [], "latest_runs": [], "latest_evaluations": []})
        return payload

    def _build_status_payload(
        self,
        *,
        detail_mode: str,
        event_rows: list[dict[str, Any]] | None = None,
        include_recent_training_lab: bool = True,
    ) -> dict[str, Any]:
        runtime_state, current_task, last_task = self._snapshot_runtime_fields()
        rows = list(event_rows or [])
        return _jsonable(
            {
                "ts": datetime.now().isoformat(),
                "detail_mode": detail_mode,
                "instance_id": self.instance_id,
                "workspace": str(self.cfg.workspace),
                "strategy_dir": str(self.cfg.strategy_dir),
                "model": self.cfg.model,
                "autopilot_enabled": self.cfg.autopilot_enabled,
                "heartbeat_enabled": self.cfg.heartbeat_enabled,
                "training_interval_sec": self.cfg.training_interval_sec,
                "heartbeat_interval_sec": self.cfg.heartbeat_interval_sec,
                "runtime": {
                    "state": runtime_state,
                    "started": self._started,
                    "current_task": current_task,
                    "last_task": last_task,
                    "runtime_lock_file": str(self.cfg.runtime_lock_file),
                    "runtime_lock_active": self.cfg.runtime_lock_file.exists(),
                    "training_lock_file": str(self.cfg.training_lock_file),
                    "training_lock_active": self.cfg.training_lock_file.exists(),
                },
                "brain": {
                    "tool_count": len(self.brain.tools),
                    "session_count": self.brain.session_count,
                    "cron": self.cron.status(),
                },
                "body": self.body.snapshot(),
                "memory": self.memory.stats(),
                "bridge": self.bridge.status(),
                "plugins": {"count": len(self._plugin_tool_names), "items": sorted(self._plugin_tool_names)},
                "strategies": {
                    "total": len(self.strategy_registry.genes),
                    "enabled": len(self.strategy_registry.list_genes(only_enabled=True)),
                    "items": [g.to_dict() for g in self.strategy_registry.genes],
                },
                "config": self.config_service.get_masked_payload(),
                "data": self._collect_data_status(detail_mode),
                "events": summarize_event_rows(rows),
                "training_lab": self._collect_training_lab_status(include_recent=include_recent_training_lab),
            }
        )

    def _build_persisted_state_payload(self) -> dict[str, Any]:
        return self._build_status_payload(
            detail_mode=self._normalize_status_detail("fast"),
            event_rows=[],
            include_recent_training_lab=False,
        )

    def status(self, *, detail: str = "fast") -> dict[str, Any]:
        detail_mode = self._normalize_status_detail(detail)
        event_rows = self._collect_status_event_rows(limit=20)
        payload = self._build_status_payload(
            detail_mode=detail_mode,
            event_rows=event_rows,
            include_recent_training_lab=True,
        )
        return self._attach_bounded_workflow(
            payload,
            domain="runtime",
            operation="status",
            runtime_method="CommanderRuntime.status",
            runtime_tool="invest_deep_status" if detail_mode == "slow" else "invest_quick_status",
            agent_kind="bounded_runtime_agent",
            writes_state=False,
            available_tools=self._runtime_domain_tools(),
            workflow=["runtime_scope_resolve", "status_refresh" if detail_mode == "slow" else "status_read", "finalize"],
            phase_stats={"detail_mode": detail_mode, "event_count": len(event_rows)},
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
        return self._attach_bounded_workflow(
            {"status": STATUS_OK, "job": job.to_dict()},
            domain="scheduler",
            operation="add_cron_job",
            runtime_method="CommanderRuntime.add_cron_job",
            runtime_tool="invest_cron_add",
            agent_kind="bounded_scheduler_agent",
            writes_state=True,
            available_tools=self._scheduler_domain_tools(),
            workflow=["scheduler_scope_resolve", "cron_add", "finalize"],
            phase_stats={"job_id": getattr(job, 'id', ''), "every_sec": int(every_sec)},
        )

    def list_cron_jobs(self) -> dict[str, Any]:
        rows = [j.to_dict() for j in self.cron.list_jobs()]
        return self._attach_bounded_workflow(
            {"count": len(rows), "items": rows},
            domain="scheduler",
            operation="list_cron_jobs",
            runtime_method="CommanderRuntime.list_cron_jobs",
            runtime_tool="invest_cron_list",
            agent_kind="bounded_scheduler_agent",
            writes_state=False,
            available_tools=self._scheduler_domain_tools(),
            workflow=["scheduler_scope_resolve", "cron_list", "finalize"],
            phase_stats={"count": len(rows)},
        )

    def remove_cron_job(self, job_id: str) -> dict[str, Any]:
        ok = self.cron.remove_job(str(job_id))
        self._persist_state()
        return self._attach_bounded_workflow(
            {"status": STATUS_OK if ok else STATUS_NOT_FOUND, "job_id": str(job_id)},
            domain="scheduler",
            operation="remove_cron_job",
            runtime_method="CommanderRuntime.remove_cron_job",
            runtime_tool="invest_cron_remove",
            agent_kind="bounded_scheduler_agent",
            writes_state=True,
            available_tools=self._scheduler_domain_tools(),
            workflow=["scheduler_scope_resolve", "cron_remove", "finalize"],
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
        for tool in build_commander_tools(self):
            self.brain.tools.register(tool)
        self._load_plugins(persist=False)

    def _load_plugins(self, persist: bool = True) -> dict[str, Any]:
        for name in list(self._plugin_tool_names):
            self.brain.tools.unregister(name)
        self._plugin_tool_names.clear()

        loaded = []
        for tool in self.plugin_loader.load_tools():
            self.brain.tools.register(tool)
            self._plugin_tool_names.add(tool.name)
            loaded.append(tool.name)

        if persist:
            self._persist_state()
        return {"count": len(loaded), "tools": loaded, "plugin_dir": str(self.cfg.plugin_dir)}

    def reload_plugins(self) -> dict[str, Any]:
        self._ensure_runtime_storage()
        payload = self._load_plugins(persist=True)
        return self._attach_bounded_workflow(
            {"status": STATUS_OK, **payload},
            domain="plugin",
            operation="reload_plugins",
            runtime_method="CommanderRuntime.reload_plugins",
            runtime_tool="invest_plugins_reload",
            agent_kind="bounded_plugin_agent",
            writes_state=True,
            available_tools=self._plugin_domain_tools(),
            workflow=["plugin_scope_resolve", "plugin_reload", "finalize"],
            phase_stats={"plugin_count": int(payload.get("count", 0))},
        )

    def _ensure_runtime_storage(self) -> None:
        directories = {
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
        }
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        self.training_lab.ensure_storage()
        self.memory.ensure_storage()

    async def _on_bridge_message(self, msg: BridgeMessage) -> str:
        session_key = msg.session_key or f"{msg.channel}:{msg.chat_id}"
        return await self.ask(msg.content, session_key=session_key, channel=msg.channel, chat_id=msg.chat_id)

    def _setup_cron_callback(self) -> None:
        async def on_cron_job(job: Any) -> str | None:
            response = await self.ask(job.message, session_key=f"cron:{job.id}")
            if job.deliver:
                notify = f"[cron][{job.channel}:{job.to}] {response or ''}"
                await self._notifications.put(notify)
            return response

        self.cron.on_job = on_cron_job

    async def _on_heartbeat_execute(self, tasks: str) -> str:
        return await self.ask(tasks, session_key="heartbeat")

    async def _on_heartbeat_notify(self, response: str) -> None:
        await self._notifications.put(f"[heartbeat] {response}")

    async def _drain_notifications(self) -> None:
        while True:
            try:
                msg = await asyncio.wait_for(self._notifications.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            preview = msg.strip() if isinstance(msg, str) else str(msg)
            if preview:
                logger.info("%s", preview[:300])

    def _build_system_prompt(self) -> str:
        return textwrap.dedent(
            f"""\
            You are Investment Evolution Commander.
            Brain runtime and body runtime are fused in one process.
            Workspace: {self.cfg.workspace}
            Strategy directory: {self.cfg.strategy_dir}

            Mission boundary:
            1. Serve investment evolution and runtime operations only.
            2. Keep every decision auditable, tool-grounded, and risk-aware.
            3. Never fabricate strategy state, training results, config values, or file changes.

            Tool operating policy:
            1. For runtime inspection, prefer `invest_quick_status` by default; use `invest_deep_status` only when deeper freshness is required. `invest_status` is backward-compatible alias only.
            2. For observability and recent activity, use `invest_events_summary`, `invest_events_tail`, and `invest_runtime_diagnostics`.
            3. For strategy inventory, use `invest_list_strategies`; if strategy files changed, call `invest_reload_strategies` before analysis or training.
            4. For health checks, prefer `invest_quick_test` before heavier training.
            5. For training execution, use `invest_train` with explicit `rounds` and `mock` args.
            6. For lab artifacts, use the `invest_training_plan_*`, `invest_training_runs_list`, and `invest_training_evaluations_list` tools.
            7. For model analytics, use `invest_investment_models`, `invest_leaderboard`, `invest_allocator`, and `invest_model_routing_preview`.
            8. For config management, use the dedicated `invest_*_get` / `invest_*_update` tools and respect confirmation requirements on risky writes.
            9. For data queries, use `invest_data_status`, `invest_data_capital_flow`, `invest_data_dragon_tiger`, `invest_data_intraday_60m`, and `invest_data_download`.
            10. For memory lookup, use `invest_memory_search`, `invest_memory_list`, and `invest_memory_get`.
            11. For scheduling changes, use `invest_cron_list`, `invest_cron_add`, `invest_cron_remove`.
            12. For plugin tool refresh, use `invest_plugins_reload`.
            13. For natural-language stock analysis, use `invest_ask_stock` and `invest_stock_strategies`.

            Execution discipline:
            1. Read-only questions should stay read-only unless the user explicitly requests execution.
            2. Do not trigger training, cron mutation, or plugin reload unless the user asked, or the prior task clearly requires it.
            3. For risky writes that require confirmation, ask the user to confirm rather than guessing.
            4. If a tool fails or arguments are invalid, explain the issue and retry with corrected arguments when possible.
            5. After using tools, summarize verified facts first, then risks, then recommended next action.
            6. Keep replies concise; do not output fake tool syntax or unverifiable promises.
            7. Treat this Commander window as the primary human entrypoint; prefer tools over telling the user to open the web UI.

            Active strategy genes:
            {self.strategy_registry.to_summary()}
            """
        )

    def _persist_state(self) -> None:
        payload = self._build_persisted_state_payload()
        self.cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_commander_identity(self) -> None:
        soul_file = self.cfg.workspace / "SOUL.md"
        heartbeat_file = self.cfg.workspace / "HEARTBEAT.md"

        soul = textwrap.dedent(
            f"""            # Investment Evolution Commander

            You are the fused commander of this runtime:
            - Brain: local brain runtime in `brain/runtime.py`
            - Body: in-process investment engine (`invest/` package + entry modules)
            - Genes: pluggable strategy files in `{self.cfg.strategy_dir}`

            Core rules:
            1. Every decision must serve investment evolution goals.
            2. Treat this Commander workspace as the primary human entrypoint for training, diagnostics, config management, data inspection, and stock-analysis workflows.
            3. Prefer using `invest_quick_status`, `invest_runtime_diagnostics`, `invest_training_plan_create`, `invest_training_plan_execute`, `invest_leaderboard`, and `invest_list_strategies`; avoid `invest_status` except for backward compatibility.
            4. If strategy files changed, call `invest_reload_strategies` before new cycle decisions.
            5. Keep risk under control, respect confirmation-required writes, and preserve reproducible logs.

            Active genes:
            {self.strategy_registry.to_summary()}
            """
        )
        soul_file.write_text(soul, encoding="utf-8")

        if not heartbeat_file.exists():
            heartbeat_file.write_text(
                textwrap.dedent(
                    """                    # HEARTBEAT TASKS

                    If strategy files changed or no training cycle has run recently:
                    1) call invest_quick_status
                    2) call invest_runtime_diagnostics
                    3) call invest_list_strategies
                    4) run invest_train(rounds=1) when needed
                    """
                ),
                encoding="utf-8",
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified Commander for Invest Evolution")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workspace", help="Workspace path for commander runtime")
        p.add_argument("--strategy-dir", help="Strategy gene directory (md/json/py)")
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

    p_train = sub.add_parser("train-once", help="Run training cycles once")
    add_common_args(p_train)
    p_train.add_argument("--rounds", type=int, default=1, help="Number of cycles to run")

    p_ask = sub.add_parser("ask", help="Send one message to fused commander brain")
    add_common_args(p_ask)
    p_ask.add_argument("-m", "--message", required=True, help="User message")

    p_genes = sub.add_parser("strategies", help="List strategy genes")
    add_common_args(p_genes)
    p_genes.add_argument("--reload", action="store_true", help="Reload strategy genes from disk")
    p_genes.add_argument("--only-enabled", action="store_true", help="Show only enabled genes")

    return parser


async def _run_async(args: argparse.Namespace) -> int:
    cfg = CommanderConfig.from_args(args)
    runtime = CommanderRuntime(cfg)

    if args.cmd == "status":
        print(json.dumps(runtime.status(detail=getattr(args, "detail", "fast")), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "train-once":
        out = await runtime.train_once(rounds=max(1, int(args.rounds)), mock=cfg.mock_mode)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "ask":
        reply = await runtime.ask(args.message, session_key="cli:direct", channel="cli", chat_id="direct")
        print(reply)
        return 0

    if args.cmd == "strategies":
        if args.reload:
            runtime.reload_strategies()
        genes = runtime.strategy_registry.list_genes(only_enabled=bool(args.only_enabled))
        print(
            json.dumps(
                {"count": len(genes), "items": [g.to_dict() for g in genes]},
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


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    try:
        return asyncio.run(_run_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
