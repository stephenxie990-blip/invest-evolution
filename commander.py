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
import textwrap
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
from config import PROJECT_ROOT, config
from market_data import DataManager, MockDataProvider
from train import SelfLearningController, TrainingResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class CommanderConfig:
    """Runtime config for the fused commander."""

    workspace: Path = PROJECT_ROOT / "workspace"
    strategy_dir: Path = PROJECT_ROOT / "strategies"
    state_file: Path = PROJECT_ROOT / "outputs" / "commander" / "state.json"
    cron_store: Path = PROJECT_ROOT / "outputs" / "commander" / "cron_jobs.json"
    memory_store: Path = PROJECT_ROOT / "memory" / "commander_memory.jsonl"
    plugin_dir: Path = PROJECT_ROOT / "agent_settings" / "plugins"
    bridge_inbox: Path = PROJECT_ROOT / "sessions" / "inbox"
    bridge_outbox: Path = PROJECT_ROOT / "sessions" / "outbox"

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

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "CommanderConfig":
        cfg = cls()

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

        cfg.state_file = PROJECT_ROOT / "outputs" / "commander" / "state.json"
        cfg.cron_store = PROJECT_ROOT / "outputs" / "commander" / "cron_jobs.json"
        cfg.memory_store = PROJECT_ROOT / "memory" / "commander_memory.jsonl"
        cfg.plugin_dir = PROJECT_ROOT / "agent_settings" / "plugins"
        cfg.bridge_inbox = PROJECT_ROOT / "sessions" / "inbox"
        cfg.bridge_outbox = PROJECT_ROOT / "sessions" / "outbox"
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

    def __init__(self, cfg: CommanderConfig):
        self.cfg = cfg
        self._mock_provider: Optional[MockDataProvider] = None
        if cfg.mock_mode:
            self._mock_provider = MockDataProvider(stock_count=30, days=1500, start_date="20200101")
        self.controller = SelfLearningController(data_provider=self._mock_provider)
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

        self.total_cycles = 0
        self.success_cycles = 0
        self.no_data_cycles = 0
        self.failed_cycles = 0
        self.last_result: Optional[dict[str, Any]] = None
        self.last_error: str = ""
        self.last_run_at: str = ""

    async def run_cycles(self, rounds: int = 1, force_mock: bool = False) -> dict[str, Any]:
        if force_mock and self._mock_provider is None:
            self._mock_provider = MockDataProvider(stock_count=30, days=1500, start_date="20200101")
            self.controller.data_manager = DataManager(data_provider=self._mock_provider)
            self.controller.llm_caller.dry_run = True

        rounds = max(1, int(rounds))
        results: list[dict[str, Any]] = []

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

        return {
            "rounds": rounds,
            "results": results,
            "summary": self.snapshot(),
        }

    async def autopilot_loop(self, interval_sec: int) -> None:
        logger.info("Body autopilot loop started (interval=%ss)", interval_sec)
        try:
            while not self._stop_event.is_set():
                await self.run_cycles(rounds=1)
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
            "timestamp": datetime.now().isoformat(),
        }


# ---------------------------------------------------------------------------
# Commander runtime
# ---------------------------------------------------------------------------

class CommanderRuntime:
    """Unified runtime: local brain + invest body in one process."""

    def __init__(self, cfg: CommanderConfig):
        self.cfg = cfg
        self.strategy_registry = StrategyGeneRegistry(self.cfg.strategy_dir)
        if self.cfg.strategy_dir.exists():
            self.strategy_registry.reload(create_dir=False)

        self.body = InvestmentBodyService(self.cfg)

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

    async def start(self) -> None:
        if self._started:
            return

        self._ensure_runtime_storage()
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
            self._autopilot_task = asyncio.create_task(
                self.body.autopilot_loop(self.cfg.training_interval_sec)
            )

        self._started = True
        self._persist_state()

    async def stop(self) -> None:
        if not self._started:
            return

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
        self._persist_state()

    async def ask(
        self,
        message: str,
        session_key: str = "commander:direct",
        channel: str = "cli",
        chat_id: str = "direct",
    ) -> str:
        self._ensure_runtime_storage()
        self.memory.append(
            kind="user",
            session_key=session_key,
            content=message,
            metadata={"channel": channel, "chat_id": chat_id},
        )
        response = await self.brain.process_direct(message, session_key=session_key)
        self.memory.append(
            kind="assistant",
            session_key=session_key,
            content=response or "",
            metadata={"channel": channel, "chat_id": chat_id},
        )
        self._persist_state()
        return response

    async def train_once(self, rounds: int = 1, mock: bool = False) -> dict[str, Any]:
        self._ensure_runtime_storage()
        out = await self.body.run_cycles(rounds=rounds, force_mock=mock)
        self._persist_state()
        return out

    def reload_strategies(self) -> dict[str, Any]:
        self._ensure_runtime_storage()
        self.strategy_registry.ensure_default_templates()
        genes = self.strategy_registry.reload()
        self._write_commander_identity()
        self._persist_state()
        return {
            "count": len(genes),
            "genes": [g.to_dict() for g in genes],
        }

    def status(self) -> dict[str, Any]:
        return {
            "ts": datetime.now().isoformat(),
            "workspace": str(self.cfg.workspace),
            "strategy_dir": str(self.cfg.strategy_dir),
            "model": self.cfg.model,
            "autopilot_enabled": self.cfg.autopilot_enabled,
            "heartbeat_enabled": self.cfg.heartbeat_enabled,
            "training_interval_sec": self.cfg.training_interval_sec,
            "heartbeat_interval_sec": self.cfg.heartbeat_interval_sec,
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

        # Daemon mode: wait forever until cancelled/KeyboardInterrupt.
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
        self.memory.ensure_storage()

    async def _on_bridge_message(self, msg: BridgeMessage) -> str:
        session_key = msg.session_key or f"{msg.channel}:{msg.chat_id}"
        return await self.ask(
            msg.content,
            session_key=session_key,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )

    def _setup_cron_callback(self) -> None:
        async def on_cron_job(job: Any) -> str | None:
            response = await self.ask(
                job.message,
                session_key=f"cron:{job.id}",
            )
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
        self.cfg.state_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_commander_identity(self) -> None:
        soul_file = self.cfg.workspace / "SOUL.md"
        heartbeat_file = self.cfg.workspace / "HEARTBEAT.md"

        soul = textwrap.dedent(
            f"""\
            # Investment Evolution Commander

            You are the fused commander of this runtime:
            - Brain: local brain runtime in `brain/runtime.py`
            - Body: in-process investment engine (`*.py` modules in project root)
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
                    """\
                    # HEARTBEAT TASKS

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
