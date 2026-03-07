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
import ast
import asyncio
import json
import logging
import os
import socket
import textwrap
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from brain.runtime import BrainRuntime
from brain.scheduler import CronService, HeartbeatService
from brain.tools import build_commander_tools
from brain.memory import MemoryStore
from brain.bridge import BridgeHub, BridgeMessage
from brain.plugins import PluginLoader
from config import PROJECT_ROOT, RUNTIME_DIR, OUTPUT_DIR, LOGS_DIR, MEMORY_DIR, SESSIONS_DIR, WORKSPACE_DIR, config
from config.services import EvolutionConfigService, RuntimePathConfigService
from market_data import DataManager, MockDataProvider
from app.train import SelfLearningController, TrainingResult

logger = logging.getLogger(__name__)


def _apply_runtime_path_overrides(cfg: "CommanderConfig", overrides: dict[str, Any]) -> "CommanderConfig":
    for key in RuntimePathConfigService.EDITABLE_KEYS:
        value = overrides.get(key)
        if value:
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

    model: str = field(default_factory=lambda: os.environ.get("COMMANDER_MODEL", config.llm_fast_model))
    api_key: str = field(default_factory=lambda: os.environ.get("COMMANDER_API_KEY", config.llm_api_key))
    api_base: str = field(default_factory=lambda: os.environ.get("COMMANDER_API_BASE", config.llm_api_base))
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
        if self.runtime_state_dir == default_state_dir and self.state_file.parent != OUTPUT_DIR / "commander":
            self.runtime_state_dir = self.state_file.parent
        if self.runtime_lock_file == default_state_dir / "commander.lock":
            self.runtime_lock_file = self.runtime_state_dir / "commander.lock"
        if self.training_lock_file == default_state_dir / "training.lock":
            self.training_lock_file = self.runtime_state_dir / "training.lock"
        if self.training_output_dir == OUTPUT_DIR / "training" and self.state_file.parent != OUTPUT_DIR / "commander":
            self.training_output_dir = self.state_file.parent / "training"
        if self.meeting_log_dir == LOGS_DIR / "meetings" and self.state_file.parent != OUTPUT_DIR / "commander":
            self.meeting_log_dir = self.state_file.parent / "meetings"
        if self.config_audit_log_path == default_state_dir / "config_changes.jsonl":
            self.config_audit_log_path = self.runtime_state_dir / "config_changes.jsonl"
        if self.config_snapshot_dir == default_state_dir / "config_snapshots":
            self.config_snapshot_dir = self.runtime_state_dir / "config_snapshots"

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "CommanderConfig":
        cfg = cls()
        runtime_paths = RuntimePathConfigService(project_root=PROJECT_ROOT).load_overrides()
        _apply_runtime_path_overrides(cfg, runtime_paths)

        if getattr(args, "workspace", None):
            cfg.workspace = Path(args.workspace).expanduser().resolve()
        if getattr(args, "strategy_dir", None):
            cfg.strategy_dir = Path(args.strategy_dir).expanduser().resolve()

        if getattr(args, "model", None):
            cfg.model = args.model
        if getattr(args, "api_key", None):
            cfg.api_key = args.api_key
        if getattr(args, "api_base", None):
            cfg.api_base = args.api_base

        if getattr(args, "mock", False):
            cfg.mock_mode = True
        if getattr(args, "no_autopilot", False):
            cfg.autopilot_enabled = False
        if getattr(args, "no_heartbeat", False):
            cfg.heartbeat_enabled = False

        if getattr(args, "train_interval_sec", None):
            cfg.training_interval_sec = max(60, int(args.train_interval_sec))
        if getattr(args, "heartbeat_interval_sec", None):
            cfg.heartbeat_interval_sec = max(60, int(args.heartbeat_interval_sec))

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

    def _load_gene(self, path: Path) -> Optional[StrategyGene]:
        suffix = path.suffix.lower()
        if suffix == ".md":
            return self._load_md_gene(path)
        if suffix == ".json":
            return self._load_json_gene(path)
        if suffix == ".py":
            return self._load_py_gene(path)
        return None

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
        self._mock_provider: Optional[MockDataProvider] = None
        if cfg.mock_mode:
            self._mock_provider = MockDataProvider(stock_count=30, days=1500, start_date="20200101")
        self.controller = SelfLearningController(
            data_provider=self._mock_provider,
            output_dir=str(self.cfg.training_output_dir),
            meeting_log_dir=str(self.cfg.meeting_log_dir),
            config_audit_log_path=str(self.cfg.config_audit_log_path),
            config_snapshot_dir=str(self.cfg.config_snapshot_dir),
        )
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

        self.total_cycles = 0
        self.success_cycles = 0
        self.no_data_cycles = 0
        self.failed_cycles = 0
        self.last_result: Optional[dict[str, Any]] = None
        self.last_error: str = ""
        self.last_run_at: str = ""
        self.training_state: str = "idle"
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

    async def run_cycles(self, rounds: int = 1, force_mock: bool = False, task_source: str = "direct") -> dict[str, Any]:
        if self._lock.locked():
            return {
                "status": "busy",
                "error": "training already in progress",
                "summary": self.snapshot(),
            }

        if force_mock and self._mock_provider is None:
            self._mock_provider = MockDataProvider(stock_count=30, days=1500, start_date="20200101")
            self.controller.data_manager = DataManager(data_provider=self._mock_provider)
            self.controller.llm_caller.dry_run = True

        rounds = max(1, int(rounds))
        results: list[dict[str, Any]] = []
        task_started_at = datetime.now().isoformat()
        self.training_state = "training"
        self.current_task = {
            "type": "training",
            "source": task_source,
            "rounds": rounds,
            "force_mock": bool(force_mock),
            "started_at": task_started_at,
        }
        self._write_training_lock(self.current_task)
        self._emit_runtime_event("training_started", self.current_task)

        try:
            async with self._lock:
                for _ in range(rounds):
                    self.total_cycles += 1
                    self.last_run_at = datetime.now().isoformat()
                    try:
                        cycle_result = await asyncio.to_thread(self.controller.run_training_cycle)
                        if cycle_result is None:
                            self.no_data_cycles += 1
                            item = {
                                "status": "no_data",
                                "cycle_id": self.controller.current_cycle_id,
                                "timestamp": self.last_run_at,
                            }
                        else:
                            self.success_cycles += 1
                            item = self._to_result_dict(cycle_result)
                        self.last_result = item
                        results.append(item)
                    except Exception as exc:
                        self.failed_cycles += 1
                        self.last_error = str(exc)
                        item = {
                            "status": "error",
                            "error": str(exc),
                            "timestamp": self.last_run_at,
                        }
                        self.last_result = item
                        results.append(item)
                        logger.exception("Commander body cycle failed")
        finally:
            self.training_state = "idle"
            self.last_completed_task = {
                **(self.current_task or {}),
                "finished_at": datetime.now().isoformat(),
                "result_count": len(results),
                "last_status": results[-1].get("status") if results else "empty",
            }
            self.current_task = None
            self._clear_training_lock()
            self._emit_runtime_event("training_finished", self.last_completed_task or {})

        return {
            "status": "ok",
            "rounds": rounds,
            "results": results,
            "summary": self.snapshot(),
        }

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

        return {
            "total_cycles": self.total_cycles,
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
        }

    @staticmethod
    def _to_result_dict(result: TrainingResult) -> dict[str, Any]:
        return {
            "status": "ok",
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
            "selection_mode": result.selection_mode,
            "agent_used": result.agent_used,
            "llm_used": result.llm_used,
            "benchmark_passed": result.benchmark_passed,
            "review_applied": result.review_applied,
            "config_snapshot_path": result.config_snapshot_path,
            "audit_tags": result.audit_tags,
            "timestamp": datetime.now().isoformat(),
        }


# ---------------------------------------------------------------------------
# Commander runtime
# ---------------------------------------------------------------------------

class CommanderRuntime:
    """Unified runtime: local brain + invest body in one process."""

    def __init__(self, cfg: CommanderConfig):
        self.cfg = cfg
        self.config_service = EvolutionConfigService(project_root=PROJECT_ROOT, live_config=config)
        self.instance_id = f"{socket.gethostname()}:{os.getpid()}"
        self.runtime_state = "initialized"
        self.current_task: Optional[dict[str, Any]] = None
        self.last_task: Optional[dict[str, Any]] = None
        self._task_lock = threading.Lock()
        self._runtime_lock_acquired = False

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
        if event == "training_started":
            self._set_runtime_state("training")
            self.current_task = payload
        elif event == "training_finished":
            self.last_task = payload
            self.current_task = None
            self._set_runtime_state("idle")
        self._persist_state()

    def _set_runtime_state(self, state: str) -> None:
        self.runtime_state = state

    def _begin_task(self, task_type: str, source: str, **metadata: Any) -> None:
        with self._task_lock:
            self.current_task = {
                "type": task_type,
                "source": source,
                "started_at": datetime.now().isoformat(),
                **metadata,
            }

    def _end_task(self, status: str = "ok", **metadata: Any) -> None:
        with self._task_lock:
            if self.current_task is None:
                return
            self.last_task = {
                **self.current_task,
                "finished_at": datetime.now().isoformat(),
                "status": status,
                **metadata,
            }
            self.current_task = None

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
        if self.cfg.runtime_lock_file.exists():
            try:
                existing = json.loads(self.cfg.runtime_lock_file.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
            existing_pid = int(existing.get("pid") or 0)
            if existing_pid and self._is_pid_alive(existing_pid):
                raise RuntimeError(
                    f"Commander runtime already active (pid={existing_pid}, host={existing.get('host', '')})"
                )
            self.cfg.runtime_lock_file.unlink(missing_ok=True)
        payload = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "instance_id": self.instance_id,
            "started_at": datetime.now().isoformat(),
            "workspace": str(self.cfg.workspace),
        }
        self.cfg.runtime_lock_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._runtime_lock_acquired = True

    def _release_runtime_lock(self) -> None:
        if self._runtime_lock_acquired:
            self.cfg.runtime_lock_file.unlink(missing_ok=True)
            self._runtime_lock_acquired = False

    async def start(self) -> None:
        if self._started:
            return
        self._ensure_runtime_storage()
        self._begin_task("start", "system")
        self._set_runtime_state("starting")
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
            self._set_runtime_state("idle")
            self._end_task("ok")
            self._persist_state()
        except Exception:
            self._set_runtime_state("error")
            self._end_task("error")
            self._release_runtime_lock()
            self._persist_state()
            raise

    async def stop(self) -> None:
        if not self._started:
            return
        self._begin_task("stop", "system")
        self._set_runtime_state("stopping")

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
        self._set_runtime_state("stopped")
        self._end_task("ok")
        self._persist_state()

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
        self.memory.append_audit("ask_started", session_key, {"channel": channel, "chat_id": chat_id})
        try:
            response = await self.brain.process_direct(message, session_key=session_key)
            self.memory.append(
                kind="assistant",
                session_key=session_key,
                content=response or "",
                metadata={"channel": channel, "chat_id": chat_id},
            )
            self.memory.append_audit("ask_finished", session_key, {"channel": channel, "chat_id": chat_id})
            self._end_task("ok")
            self._persist_state()
            return response
        except Exception:
            self._end_task("error")
            self._persist_state()
            raise

    async def train_once(self, rounds: int = 1, mock: bool = False) -> dict[str, Any]:
        self._ensure_runtime_storage()
        self._begin_task("train_once", "direct", rounds=rounds, mock=mock)
        self._set_runtime_state("training")
        self.memory.append_audit("train_requested", "runtime:train", {"rounds": rounds, "mock": mock})
        try:
            out = await self.body.run_cycles(rounds=rounds, force_mock=mock, task_source="direct")
            self._set_runtime_state("idle" if out.get("status") != "busy" else "busy")
            self._end_task(out.get("status", "ok"), rounds=rounds, mock=mock)
            self._persist_state()
            return out
        except Exception:
            self._set_runtime_state("error")
            self._end_task("error", rounds=rounds, mock=mock)
            self._persist_state()
            raise

    def reload_strategies(self) -> dict[str, Any]:
        self._ensure_runtime_storage()
        self._begin_task("reload_strategies", "direct")
        self._set_runtime_state("reloading_strategies")
        self.strategy_registry.ensure_default_templates()
        genes = self.strategy_registry.reload()
        self._write_commander_identity()
        self._set_runtime_state("idle")
        self._end_task("ok", gene_count=len(genes))
        self._persist_state()
        return {
            "count": len(genes),
            "genes": [g.to_dict() for g in genes],
        }

    def status(self) -> dict[str, Any]:
        data_status = {}
        try:
            from market_data.datasets import WebDatasetService
            data_status = WebDatasetService().get_status_summary()
        except Exception as exc:
            data_status = {"status": "error", "error": str(exc)}
        return {
            "ts": datetime.now().isoformat(),
            "instance_id": self.instance_id,
            "workspace": str(self.cfg.workspace),
            "strategy_dir": str(self.cfg.strategy_dir),
            "model": self.cfg.model,
            "autopilot_enabled": self.cfg.autopilot_enabled,
            "heartbeat_enabled": self.cfg.heartbeat_enabled,
            "training_interval_sec": self.cfg.training_interval_sec,
            "heartbeat_interval_sec": self.cfg.heartbeat_interval_sec,
            "runtime": {
                "state": self.runtime_state,
                "started": self._started,
                "current_task": self.current_task,
                "last_task": self.last_task,
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
            "data": data_status,
        }

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
        return self._load_plugins(persist=True)

    def _ensure_runtime_storage(self) -> None:
        self.cfg.workspace.mkdir(parents=True, exist_ok=True)
        self.cfg.strategy_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.memory_store.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.plugin_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.bridge_inbox.mkdir(parents=True, exist_ok=True)
        self.cfg.bridge_outbox.mkdir(parents=True, exist_ok=True)
        self.cfg.runtime_state_dir.mkdir(parents=True, exist_ok=True)
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
            f"""            You are Investment Evolution Commander.
            Brain runtime and body runtime are fused in one process.
            Workspace: {self.cfg.workspace}
            Strategy directory: {self.cfg.strategy_dir}

            Mandatory principles:
            1. Serve investment evolution goals only.
            2. Use tools for status, strategy inspection, and training execution.
            3. When strategy files changed, call invest_reload_strategies first.
            4. Keep decisions auditable and risk-aware.

            Active strategy genes:
            {self.strategy_registry.to_summary()}
            """
        )

    def _persist_state(self) -> None:
        payload = self.status()
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
            2. Prefer using `invest_status`, `invest_list_strategies`, `invest_train` tools.
            3. If strategy files changed, call `invest_reload_strategies` before new cycle decisions.
            4. Keep risk under control and preserve reproducible logs.

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
                    1) call invest_status
                    2) call invest_list_strategies
                    3) run invest_train(rounds=1) when needed
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
        print(json.dumps(runtime.status(), ensure_ascii=False, indent=2))
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
