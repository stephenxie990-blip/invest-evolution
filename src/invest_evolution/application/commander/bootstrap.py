"""Commander bootstrap, identity, config, and playbook registry services."""

from __future__ import annotations

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
from typing import Any, Callable, Iterable

import invest_evolution.config as config_module
from invest_evolution.agent_runtime.memory import MemoryStore
from invest_evolution.agent_runtime.plugins import BridgeHub, PluginLoader
from invest_evolution.agent_runtime.runtime import BrainRuntime, CronService, HeartbeatService
from invest_evolution.agent_runtime.tools import (
    INVEST_DEEP_STATUS_TOOL_NAME,
    INVEST_QUICK_STATUS_TOOL_NAME,
    build_commander_tools,
)
from invest_evolution.application.commander.runtime import (
    load_plugin_tools,
    register_fusion_tools as register_fusion_tools_impl,
    setup_cron_callback,
)
from invest_evolution.application.investment_body_service import InvestmentBodyService
from invest_evolution.application.lab import TrainingLabArtifactStore
from invest_evolution.application.stock_analysis import StockAnalysisService
from invest_evolution.config import PROJECT_ROOT, config
from invest_evolution.config.control_plane import (
    EvolutionConfigService,
    RuntimePathConfigService,
    resolve_component_llm,
    resolve_default_llm,
)
from invest_evolution.investment.research import TrainingArtifactRecorder
from invest_evolution.investment.research.case_store import ResearchCaseStore

logger = logging.getLogger(__name__)


def apply_restored_body_state(body: Any, body_payload: dict[str, Any]) -> None:
    body.total_cycles = int(body_payload.get("total_cycles") or 0)
    body.success_cycles = int(body_payload.get("success_cycles") or 0)
    body.no_data_cycles = int(body_payload.get("no_data_cycles") or 0)
    body.failed_cycles = int(body_payload.get("failed_cycles") or 0)
    body.last_result = dict(body_payload.get("last_result") or {}) or None
    body.last_error = str(body_payload.get("last_error") or "")
    body.last_run_at = str(body_payload.get("last_run_at") or "")
    body.training_state = str(body_payload.get("training_state") or body.training_state)
    body.current_task = dict(body_payload.get("current_task") or {}) or None
    body.last_completed_task = dict(body_payload.get("last_completed_task") or {}) or None


def read_runtime_lock_payload(lock_file: Path, *, logger: Any) -> dict[str, Any]:
    try:
        raw = lock_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.warning("Failed to read runtime lock payload %s: %s", lock_file, exc)
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid runtime lock payload %s: %s", lock_file, exc)
        return {}

    if not isinstance(data, dict):
        logger.warning("Runtime lock payload must be a JSON object: %s", lock_file)
        return {}
    return data


def is_pid_alive(pid: int, *, os_module: Any) -> bool:
    if pid <= 0:
        return False
    try:
        os_module.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_runtime_lock(
    *,
    lock_file: Path,
    instance_id: str,
    workspace: str,
    read_lock_payload: Callable[[], dict[str, Any]],
    pid_alive: Callable[[int], bool],
    os_module: Any,
    socket_module: Any,
) -> None:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os_module.getpid(),
        "host": socket_module.gethostname(),
        "instance_id": instance_id,
        "started_at": datetime.now().isoformat(),
        "workspace": workspace,
    }

    while True:
        try:
            fd = os_module.open(lock_file, os_module.O_WRONLY | os_module.O_CREAT | os_module.O_EXCL, 0o644)
        except FileExistsError:
            existing = read_lock_payload()
            existing_pid = int(existing.get("pid") or 0)
            if existing_pid and pid_alive(existing_pid):
                raise RuntimeError(
                    f"Commander runtime already active (pid={existing_pid}, host={existing.get('host', '')})"
                )
            if existing and existing_pid:
                latest = read_lock_payload()
                latest_pid = int(latest.get("pid") or 0)
                if latest != existing:
                    if latest_pid and pid_alive(latest_pid):
                        raise RuntimeError(
                            f"Commander runtime already active (pid={latest_pid}, host={latest.get('host', '')})"
                        )
                    continue
                try:
                    lock_file.unlink()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise RuntimeError(f"Failed to clear stale runtime lock: {exc}") from exc
                continue
            raise RuntimeError(f"Commander runtime lock exists but is unreadable: {lock_file}")

        try:
            with os_module.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
        except OSError:
            lock_file.unlink(missing_ok=True)
            raise
        return


def release_runtime_lock(
    *,
    lock_file: Path,
    instance_id: str,
    read_lock_payload: Callable[[], dict[str, Any]],
    os_module: Any,
    logger: Any,
) -> None:
    existing = read_lock_payload()
    existing_pid = int(existing.get("pid") or 0)
    existing_instance = str(existing.get("instance_id") or "")
    if not existing or existing_pid == os_module.getpid() or existing_instance == instance_id:
        lock_file.unlink(missing_ok=True)
        return
    logger.warning(
        "Runtime lock ownership changed before release; keeping lock file intact: %s",
        lock_file,
    )


def ensure_runtime_storage(
    *,
    directories: Iterable[Path],
    training_lab: Any,
    memory: Any,
) -> None:
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
    training_lab.ensure_storage()
    memory.ensure_storage()


def persist_runtime_state_payload(state_file: Path, *, payload: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = state_file.with_name(f".{state_file.name}.{os.getpid()}.tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    with tmp_file.open("w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    tmp_file.replace(state_file)


def load_persisted_runtime_state(state_file: Path, *, logger: Any) -> dict[str, Any] | None:
    if not state_file.exists():
        return None
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("Failed to restore persisted commander state from %s", state_file, exc_info=True)
        return None
    if not isinstance(payload, dict):
        logger.warning("Persisted commander state must be a JSON object: %s", state_file)
        return None
    return payload


def restore_runtime_from_persisted_state(
    *,
    state_file: Path,
    logger: Any,
    update_runtime_fields: Callable[..., None],
    current_state: str,
    apply_restored_body_state_impl: Callable[[Any, dict[str, Any]], None],
    body: Any,
) -> None:
    payload = load_persisted_runtime_state(state_file, logger=logger)
    if payload is None:
        return
    runtime_payload = dict(payload.get("runtime") or {})
    body_payload = dict(payload.get("body") or {})
    update_runtime_fields(
        state=runtime_payload.get("state", current_state),
        current_task=runtime_payload.get("current_task"),
        last_task=runtime_payload.get("last_task"),
    )
    apply_restored_body_state_impl(body, body_payload)


def persist_runtime_snapshot(
    *,
    state_file: Path,
    build_persisted_state_payload: Callable[[], dict[str, Any]],
) -> None:
    persist_runtime_state_payload(state_file, payload=build_persisted_state_payload())


def write_commander_identity_artifacts(
    workspace: Path,
    *,
    playbook_dir: str,
    quick_status_tool_name: str,
    playbook_summary: str,
    build_soul: Callable[..., str],
    build_heartbeat: Callable[[], str],
) -> None:
    soul_file = workspace / "SOUL.md"
    heartbeat_file = workspace / "HEARTBEAT.md"

    soul = build_soul(
        playbook_dir=playbook_dir,
        quick_status_tool_name=quick_status_tool_name,
        playbook_summary=playbook_summary,
    )
    soul_file.write_text(soul, encoding="utf-8")

    if not heartbeat_file.exists():
        heartbeat_file.write_text(build_heartbeat(), encoding="utf-8")


def write_runtime_identity(
    *,
    workspace: Path,
    playbook_dir: str,
    quick_status_tool_name: str,
    playbook_summary: str,
    build_soul: Callable[..., str],
    build_heartbeat: Callable[[], str],
) -> None:
    write_commander_identity_artifacts(
        workspace,
        playbook_dir=playbook_dir,
        quick_status_tool_name=quick_status_tool_name,
        playbook_summary=playbook_summary,
        build_soul=build_soul,
        build_heartbeat=build_heartbeat,
    )


@dataclass
class PlaybookEntry:
    """A commander playbook loaded from md/json/py assets."""

    playbook_id: str
    name: str
    kind: str
    path: str
    enabled: bool = True
    priority: int = 50
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PlaybookRegistry:
    """Loads editable commander playbooks from local files."""

    SUPPORTED_SUFFIXES = {".md", ".json", ".py"}

    def __init__(self, playbook_dir: Path):
        self.playbook_dir = playbook_dir
        self.playbooks: list[PlaybookEntry] = []

    def ensure_default_playbooks(self) -> None:
        self.playbook_dir.mkdir(parents=True, exist_ok=True)

        md_file = self.playbook_dir / "momentum_trend.md"
        if not md_file.exists():
            md_file.write_text(
                textwrap.dedent(
                    """\
                    ---
                    id: momentum_trend
                    name: Momentum Trend Playbook
                    enabled: true
                    priority: 80
                    description: Focus on strong trend continuation with volume confirmation.
                    ---

                    # Momentum Trend Playbook

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

        json_file = self.playbook_dir / "mean_reversion.json"
        if not json_file.exists():
            json_file.write_text(
                json.dumps(
                    {
                        "id": "mean_reversion",
                        "name": "Mean Reversion Playbook",
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

        py_file = self.playbook_dir / "risk_guard.py"
        if not py_file.exists():
            py_file.write_text(
                textwrap.dedent(
                    '''\
                    """Risk guard playbook.

                    This file is intentionally simple and editable.
                    Commander only parses metadata by default.
                    """

                    PLAYBOOK_META = {
                        "id": "risk_guard",
                        "name": "Risk Guard Playbook",
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

    def reload(self, create_dir: bool = True) -> list[PlaybookEntry]:
        if create_dir:
            self.playbook_dir.mkdir(parents=True, exist_ok=True)
        elif not self.playbook_dir.exists():
            self.playbooks = []
            return []

        playbooks: list[PlaybookEntry] = []
        for path in sorted(self.playbook_dir.glob("*")):
            if not path.is_file() or path.suffix.lower() not in self.SUPPORTED_SUFFIXES:
                continue
            try:
                playbook = self._load_playbook(path)
                if playbook is not None:
                    playbooks.append(playbook)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("Load commander playbook failed %s: %s", path, exc)

        playbooks.sort(key=lambda playbook: (-playbook.priority, playbook.playbook_id))
        self.playbooks = playbooks
        return playbooks

    def list_playbooks(self, only_enabled: bool = False) -> list[PlaybookEntry]:
        if not only_enabled:
            return list(self.playbooks)
        return [playbook for playbook in self.playbooks if playbook.enabled]

    def to_summary(self) -> str:
        if not self.playbooks:
            return "No playbooks loaded."
        return "\n".join(
            f"- [{'ON' if playbook.enabled else 'OFF'}] {playbook.playbook_id} "
            f"({playbook.kind}, P{playbook.priority}): {playbook.description}"
            for playbook in self.playbooks
        )

    def _load_playbook(self, path: Path) -> PlaybookEntry | None:
        loader = {
            ".md": self._load_md_playbook,
            ".json": self._load_json_playbook,
            ".py": self._load_py_playbook,
        }.get(path.suffix.lower())
        return loader(path) if loader else None

    def _load_md_playbook(self, path: Path) -> PlaybookEntry:
        text = path.read_text(encoding="utf-8")
        front, body = self._split_front_matter(text)
        playbook_id = str(front.get("id") or path.stem)
        name = str(front.get("name") or playbook_id)
        enabled = self._to_bool(front.get("enabled", True))
        priority = self._to_int(front.get("priority", 50), 50)
        description = str(front.get("description") or self._first_nonempty_line(body) or "")
        return PlaybookEntry(
            playbook_id=playbook_id,
            name=name,
            kind="md",
            path=str(path),
            enabled=enabled,
            priority=priority,
            description=description,
            metadata={"front_matter": front},
        )

    def _load_json_playbook(self, path: Path) -> PlaybookEntry:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON playbook must be an object")

        warnings = self._validate_json_playbook(data)
        for warning in warnings:
            logger.warning("Commander playbook %s: %s", path.name, warning)

        playbook_id = str(data.get("id") or path.stem)
        name = str(data.get("name") or playbook_id)
        enabled = self._to_bool(data.get("enabled", True))
        priority = max(0, min(100, self._to_int(data.get("priority", 50), 50)))
        description = str(data.get("description") or "")
        return PlaybookEntry(
            playbook_id=playbook_id,
            name=name,
            kind="json",
            path=str(path),
            enabled=enabled,
            priority=priority,
            description=description,
            metadata=dict(data),
        )

    @staticmethod
    def _validate_json_playbook(
        data: dict[str, Any],
        path: Path | None = None,
    ) -> list[str]:
        del path
        warnings: list[str] = []
        if "id" not in data:
            warnings.append("missing required field 'id', will use filename as id")
        elif not isinstance(data["id"], str):
            warnings.append(f"field 'id' should be string, got {type(data['id']).__name__}")
        if "name" not in data:
            warnings.append("missing required field 'name', will use id as name")
        elif not isinstance(data["name"], str):
            warnings.append(f"field 'name' should be string, got {type(data['name']).__name__}")
        if "enabled" in data and not isinstance(data["enabled"], (bool, int, float, str)):
            warnings.append(f"field 'enabled' has unexpected type {type(data['enabled']).__name__}")
        if "priority" in data:
            try:
                priority = int(data["priority"])
                if priority < 0 or priority > 100:
                    warnings.append(
                        f"field 'priority' value {priority} out of range [0, 100], will be clamped"
                    )
            except (TypeError, ValueError):
                warnings.append(f"field 'priority' is not a valid integer: {data['priority']!r}")
        if "description" in data and not isinstance(data["description"], str):
            warnings.append(
                f"field 'description' should be string, got {type(data['description']).__name__}"
            )
        if "rules" in data and not isinstance(data["rules"], dict):
            warnings.append(f"field 'rules' should be an object, got {type(data['rules']).__name__}")
        return warnings

    def _load_py_playbook(self, path: Path) -> PlaybookEntry:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        module_doc = ast.get_docstring(tree) or ""
        meta: dict[str, Any] = {}
        functions: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {
                        "PLAYBOOK_META",
                        "GENE_META",
                        "STRATEGY_META",
                        "META",
                    }:
                        try:
                            literal = ast.literal_eval(node.value)
                            if isinstance(literal, dict):
                                meta = literal
                        except Exception as exc:  # pragma: no cover - defensive logging
                            logger.warning(
                                "Failed to parse python commander playbook metadata from %s: %s",
                                path,
                                exc,
                            )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)

        playbook_id = str(meta.get("id") or path.stem)
        name = str(meta.get("name") or playbook_id)
        enabled = self._to_bool(meta.get("enabled", True))
        priority = self._to_int(meta.get("priority", 50), 50)
        description = str(meta.get("description") or self._first_nonempty_line(module_doc) or "")
        if not description:
            description = "Python playbook"
        return PlaybookEntry(
            playbook_id=playbook_id,
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
            stripped = line.strip()
            if stripped:
                return stripped
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
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                end = index
                break
        if end is None:
            return {}, text
        front_lines = lines[1:end]
        body = "\n".join(lines[end + 1 :])
        front: dict[str, str] = {}
        for line in front_lines:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            front[key.strip()] = value.strip()
        return front, body


def build_commander_system_prompt(
    *,
    workspace: str,
    playbook_dir: str,
    quick_status_tool_name: str,
    deep_status_tool_name: str,
    playbook_summary: str,
) -> str:
    return textwrap.dedent(
        f"""\
        You are Investment Evolution Commander.
        Brain runtime and body runtime are fused in one process.
        Workspace: {workspace}
        Playbook directory: {playbook_dir}

        Mission boundary:
        1. Serve investment evolution and runtime operations only.
        2. Keep every decision auditable, tool-grounded, and risk-aware.
        3. Never fabricate playbook state, training results, config values, or file changes.

        Tool operating policy:
        1. For runtime inspection, prefer `{quick_status_tool_name}` by default; use `{deep_status_tool_name}` only when deeper freshness is required.
        2. For observability and recent activity, use `invest_events_summary`, `invest_events_tail`, and `invest_runtime_diagnostics`.
        3. For playbook inventory, use `invest_list_playbooks`; if playbook files changed, call `invest_reload_playbooks` before analysis or training.
        4. For health checks, prefer `invest_quick_test` before heavier training.
        5. For training execution, use `invest_train` with explicit `rounds` and `mock` args.
        6. For lab artifacts, use the `invest_training_plan_*`, `invest_training_runs_list`, and `invest_training_evaluations_list` tools.
        7. For manager/governance analytics, use `invest_managers`, `invest_leaderboard`, `invest_allocator`, and `invest_governance_preview`.
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
        7. Treat Commander plus `/api/chat` as the primary interaction entrypoint; the system is headless and no web UI should be referenced.
        8. When the user asks for runtime detail, expose actionable status, recent events, diagnostics, and artifact paths directly in natural language.

        Active playbooks:
        {playbook_summary}
        """
    )


def build_commander_soul(
    *,
    playbook_dir: str,
    quick_status_tool_name: str,
    playbook_summary: str,
) -> str:
    return textwrap.dedent(
        f"""\
        # Investment Evolution Commander

        You are the fused commander of this runtime:
        - Brain: local brain runtime in `src/invest_evolution/agent_runtime/runtime.py`
        - Body: in-process investment engine (`invest/` package + entry modules)
        - Playbooks: editable commander files in `{playbook_dir}`

        Core rules:
        1. Every decision must serve investment evolution goals.
        2. Treat this Commander workspace as the primary human entrypoint for training, diagnostics, config management, data inspection, and stock-analysis workflows.
        3. Prefer using `{quick_status_tool_name}`, `invest_runtime_diagnostics`, `invest_training_plan_create`, `invest_training_plan_execute`, `invest_leaderboard`, and `invest_list_playbooks`.
        4. If playbook files changed, call `invest_reload_playbooks` before new cycle decisions.
        5. Keep risk under control, respect confirmation-required writes, and preserve reproducible logs.

        Active playbooks:
        {playbook_summary}
        """
    )


def build_heartbeat_tasks_markdown() -> str:
    return textwrap.dedent(
        """\
        # HEARTBEAT TASKS

        If playbook files changed or no training cycle has run recently:
        1) call invest_quick_status
        2) call invest_runtime_diagnostics
        3) call invest_list_playbooks
        4) run invest_train(rounds=1) when needed
        """
    )


def commander_llm_default(field_name: str, fallback: str = "") -> str:
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


def apply_runtime_path_overrides(
    cfg: Any,
    overrides: dict[str, Any],
    *,
    editable_keys: Iterable[str] | None = None,
) -> Any:
    normalized_keys = tuple(editable_keys or RuntimePathConfigService.EDITABLE_KEYS)
    for key in normalized_keys:
        value = overrides.get(key)
        if value:
            setattr(cfg, key, Path(value).expanduser().resolve())
    if hasattr(cfg, "__post_init__"):
        cfg.__post_init__()
    return cfg


def sync_runtime_path_config(
    runtime: Any,
    payload: dict[str, Any],
    *,
    editable_keys: Iterable[str] | None = None,
    artifact_recorder_cls: Any = TrainingArtifactRecorder,
    evolution_config_service_cls: Any = EvolutionConfigService,
) -> None:
    apply_runtime_path_overrides(
        runtime.cfg,
        payload,
        editable_keys=editable_keys or RuntimePathConfigService.EDITABLE_KEYS,
    )
    controller = runtime.body.controller
    controller.output_dir = Path(runtime.cfg.training_output_dir)
    controller.output_dir.mkdir(parents=True, exist_ok=True)
    controller.artifact_recorder = artifact_recorder_cls(base_dir=str(runtime.cfg.artifact_log_dir))
    controller.config_service = evolution_config_service_cls(
        project_root=config_module.PROJECT_ROOT,
        live_config=config_module.config,
        audit_log_path=Path(runtime.cfg.config_audit_log_path),
        snapshot_dir=Path(runtime.cfg.config_snapshot_dir),
    )


def relocate_commander_state_paths(
    cfg: Any,
    *,
    default_state_dir: Path,
    default_state_parent: Path,
    default_training_output_dir: Path,
    default_artifact_log_dir: Path,
    state_dir_relocations: dict[str, str],
) -> None:
    state_parent_changed = cfg.state_file.parent != default_state_parent
    if cfg.runtime_state_dir == default_state_dir and state_parent_changed:
        cfg.runtime_state_dir = cfg.state_file.parent
    if cfg.training_output_dir == default_training_output_dir and state_parent_changed:
        cfg.training_output_dir = cfg.state_file.parent / "training"
    if cfg.artifact_log_dir == default_artifact_log_dir and state_parent_changed:
        cfg.artifact_log_dir = cfg.state_file.parent / "artifacts"
    for attr, filename in state_dir_relocations.items():
        if getattr(cfg, attr) == default_state_dir / filename:
            setattr(cfg, attr, cfg.runtime_state_dir / filename)


def build_commander_config_from_args(
    config_cls: Any,
    args: Any,
    *,
    path_config_service_cls: Any,
    project_root: Path,
    apply_runtime_overrides: Any,
) -> Any:
    cfg = config_cls()
    runtime_paths = path_config_service_cls(project_root=project_root).load_overrides()
    apply_runtime_overrides(cfg, runtime_paths)

    if workspace := getattr(args, "workspace", None):
        cfg.workspace = Path(workspace).expanduser().resolve()
    if playbook_dir := getattr(args, "playbook_dir", None):
        cfg.playbook_dir = Path(playbook_dir).expanduser().resolve()
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

    train_interval_sec = getattr(args, "train_interval_sec", None)
    if train_interval_sec:
        cfg.training_interval_sec = max(60, int(train_interval_sec))
    heartbeat_interval_sec = getattr(args, "heartbeat_interval_sec", None)
    if heartbeat_interval_sec:
        cfg.heartbeat_interval_sec = max(60, int(heartbeat_interval_sec))

    return cfg


def initialize_commander_runtime(runtime: Any) -> None:
    runtime.config_service = EvolutionConfigService(project_root=PROJECT_ROOT, live_config=config)
    runtime.instance_id = f"{socket.gethostname()}:{os.getpid()}"
    runtime.runtime_state = "initialized"
    runtime.current_task = None
    runtime.last_task = None
    runtime._pending_runtime_tasks = []
    runtime._task_lock = threading.RLock()
    runtime._stream_lock = threading.RLock()
    runtime._event_subscriptions = {}
    runtime._runtime_lock_acquired = False

    runtime.training_lab = TrainingLabArtifactStore(
        training_plan_dir=runtime.cfg.training_plan_dir,
        training_run_dir=runtime.cfg.training_run_dir,
        training_eval_dir=runtime.cfg.training_eval_dir,
    )
    runtime.playbook_registry = PlaybookRegistry(runtime.cfg.playbook_dir)
    if runtime.cfg.playbook_dir.exists():
        runtime.playbook_registry.reload(create_dir=False)

    runtime.body = InvestmentBodyService(runtime.cfg, on_runtime_event=runtime._on_body_event)
    runtime.brain = BrainRuntime(
        workspace=runtime.cfg.workspace,
        model=runtime.cfg.model,
        api_key=runtime.cfg.api_key,
        api_base=runtime.cfg.api_base,
        temperature=runtime.cfg.temperature,
        max_tokens=runtime.cfg.max_tokens,
        max_iterations=runtime.cfg.max_tool_iterations,
        memory_window=runtime.cfg.memory_window,
        system_prompt_provider=runtime._build_system_prompt,
    )
    runtime.cron = CronService(runtime.cfg.cron_store)
    runtime.heartbeat = HeartbeatService(
        workspace=runtime.cfg.workspace,
        on_execute=runtime._on_heartbeat_execute,
        on_notify=runtime._on_heartbeat_notify,
        interval_s=runtime.cfg.heartbeat_interval_sec,
        enabled=runtime.cfg.heartbeat_enabled,
    )
    runtime._notifications = asyncio.Queue()
    runtime.memory = MemoryStore(runtime.cfg.memory_store, create=False)
    runtime.plugin_loader = PluginLoader(runtime.cfg.plugin_dir, create_dir=False)
    runtime.stock_analysis = StockAnalysisService(
        strategy_dir=runtime.cfg.stock_strategy_dir,
        model=runtime.cfg.model,
        api_key=runtime.cfg.api_key,
        api_base=runtime.cfg.api_base,
        enable_llm_react=not runtime.cfg.mock_mode,
        controller_provider=lambda: runtime.body.controller,
    )
    runtime.research_case_store = ResearchCaseStore(runtime.cfg.runtime_state_dir)
    runtime._plugin_tool_names = set()
    runtime.bridge = BridgeHub(
        inbox_dir=runtime.cfg.bridge_inbox,
        outbox_dir=runtime.cfg.bridge_outbox,
        on_message=runtime._on_bridge_message,
        poll_interval_sec=runtime.cfg.bridge_poll_interval_sec,
        enabled=runtime.cfg.bridge_enabled,
    )
    register_fusion_tools(runtime)
    setup_runtime_cron_callback(runtime)
    runtime._started = False
    runtime._notify_task = None
    runtime._autopilot_task = None
    restore_persisted_state(runtime)


def restore_persisted_state(runtime: Any) -> None:
    restore_runtime_from_persisted_state(
        state_file=runtime.cfg.state_file,
        logger=logger,
        update_runtime_fields=runtime._update_runtime_fields,
        current_state=runtime.runtime_state,
        apply_restored_body_state_impl=apply_restored_body_state,
        body=runtime.body,
    )


def ensure_runtime_storage_for_runtime(runtime: Any) -> None:
    ensure_runtime_storage(
        directories={
            runtime.cfg.workspace,
            runtime.cfg.playbook_dir,
            runtime.cfg.stock_strategy_dir,
            runtime.cfg.state_file.parent,
            runtime.cfg.memory_store.parent,
            runtime.cfg.plugin_dir,
            runtime.cfg.bridge_inbox,
            runtime.cfg.bridge_outbox,
            runtime.cfg.runtime_state_dir,
            runtime.cfg.runtime_events_path.parent,
        },
        training_lab=runtime.training_lab,
        memory=runtime.memory,
    )


def register_fusion_tools(runtime: Any) -> None:
    register_fusion_tools_impl(
        runtime,
        build_tools=build_commander_tools,
        load_plugins=runtime._load_plugins,
    )


def load_runtime_plugins(runtime: Any, persist: bool = True) -> dict[str, Any]:
    return load_plugin_tools(
        brain_tools=runtime.brain.tools,
        plugin_loader=runtime.plugin_loader,
        plugin_tool_names=runtime._plugin_tool_names,
        plugin_dir=runtime.cfg.plugin_dir,
        persist=persist,
        persist_state=runtime._persist_state,
        logger=logger,
    )


def setup_runtime_cron_callback(runtime: Any) -> None:
    setup_cron_callback(
        cron=runtime.cron,
        ask=runtime.ask,
        notifications=runtime._notifications,
    )


def build_runtime_system_prompt(runtime: Any) -> str:
    return build_commander_system_prompt(
        workspace=str(runtime.cfg.workspace),
        playbook_dir=str(runtime.cfg.playbook_dir),
        quick_status_tool_name=INVEST_QUICK_STATUS_TOOL_NAME,
        deep_status_tool_name=INVEST_DEEP_STATUS_TOOL_NAME,
        playbook_summary=runtime.playbook_registry.to_summary(),
    )


def persist_runtime_state(runtime: Any) -> None:
    persist_runtime_snapshot(
        state_file=runtime.cfg.state_file,
        build_persisted_state_payload=runtime._build_persisted_state_payload,
    )


def write_runtime_identity_file(runtime: Any) -> None:
    write_runtime_identity(
        workspace=runtime.cfg.workspace,
        playbook_dir=str(runtime.cfg.playbook_dir),
        quick_status_tool_name=INVEST_QUICK_STATUS_TOOL_NAME,
        playbook_summary=runtime.playbook_registry.to_summary(),
        build_soul=build_commander_soul,
        build_heartbeat=build_heartbeat_tasks_markdown,
    )
