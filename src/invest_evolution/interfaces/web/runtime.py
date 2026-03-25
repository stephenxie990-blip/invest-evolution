"""Web runtime facades and ephemeral runtime state."""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Protocol, cast

from invest_evolution.application.config_surface import get_runtime_paths_payload
from invest_evolution.application.commander.status import (
    build_training_lab_status,
    read_event_rows,
    summarize_event_rows,
)
from invest_evolution.application.lab import collect_core_explainability_artifacts
from invest_evolution.common.utils import list_json_artifact_paths, safe_read_json_dict

logger = logging.getLogger(__name__)


def load_default_commander_runtime_types() -> tuple[type[Any], type[Any]]:
    from invest_evolution.application.commander_main import (
        CommanderConfig,
        CommanderRuntime,
    )

    return CommanderConfig, CommanderRuntime


def _load_persisted_runtime_state_payload(state_file: Path) -> dict[str, Any] | None:
    if not state_file.exists():
        return None
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning(
            "Failed to restore persisted commander state from %s",
            state_file,
            exc_info=True,
        )
        return None
    if not isinstance(payload, dict):
        logger.warning(
            "Persisted commander state must be a JSON object: %s", state_file
        )
        return None
    return payload


@dataclass
class WebRuntimeEphemeralState:
    """Container for mutable process-local web state."""

    event_history_limit: int = 200
    event_buffer_limit: int = 512
    event_history: deque[dict[str, Any]] = field(init=False)
    event_buffer: Queue[dict[str, Any]] = field(init=False)
    event_condition: threading.Condition = field(default_factory=threading.Condition)
    event_dispatcher_started: bool = False
    event_seq: int = 0
    data_download_lock: threading.Lock = field(default_factory=threading.Lock)
    data_download_running: bool = False
    rate_limit_lock: threading.Lock = field(default_factory=threading.Lock)
    rate_limit_events: dict[tuple[str, str, str], deque[float]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        self.event_history = deque(maxlen=max(1, int(self.event_history_limit or 1)))
        self.event_buffer = Queue(maxsize=max(1, int(self.event_buffer_limit or 1)))

    def reset(self, *, reset_rate_limits: bool = True) -> None:
        if reset_rate_limits:
            with self.rate_limit_lock:
                self.rate_limit_events = {}
        with self.event_condition:
            self.event_history.clear()
            self.event_seq = 0
        while True:
            try:
                self.event_buffer.get_nowait()
            except Empty:
                break
        self.data_download_running = False

    def compact_rate_limit_events(self, *, window_start: float, max_keys: int) -> None:
        if not self.rate_limit_events:
            return
        stale_keys = [
            key
            for key, queue in self.rate_limit_events.items()
            if (not queue) or queue[-1] <= window_start
        ]
        for key in stale_keys:
            self.rate_limit_events.pop(key, None)
        overflow = len(self.rate_limit_events) - max(1, int(max_keys))
        if overflow <= 0:
            return
        ranked = sorted(
            self.rate_limit_events.items(),
            key=lambda item: item[1][-1] if item[1] else 0.0,
        )
        for key, _ in ranked[:overflow]:
            self.rate_limit_events.pop(key, None)


@dataclass
class WebRuntimeStateContainer:
    """Container for runtime pointers and mutable process-local web state."""

    ephemeral_state: WebRuntimeEphemeralState
    runtime: Any | None = None
    loop: Any | None = None
    runtime_facade_override: RuntimeFacade | Any | None = None
    runtime_shutdown_registered: bool = False
    runtime_bootstrap_lock: threading.Lock = field(default_factory=threading.Lock)
    event_dispatcher_thread: threading.Thread | None = None

    def bind_runtime(
        self,
        *,
        runtime: Any | None,
        loop: Any | None,
    ) -> None:
        self.runtime = runtime
        self.loop = loop

    def set_runtime_facade_override(self, facade: RuntimeFacade | Any | None) -> None:
        self.runtime_facade_override = facade

    def set_runtime_shutdown_registered(self, value: bool) -> None:
        self.runtime_shutdown_registered = bool(value)

    def set_event_dispatcher_thread(self, thread: threading.Thread | None) -> None:
        self.event_dispatcher_thread = thread

    def sync_from_compat_aliases(
        self,
        *,
        runtime: Any | None,
        loop: Any | None,
        runtime_facade_override: RuntimeFacade | Any | None,
        runtime_shutdown_registered: bool,
        event_dispatcher_thread: threading.Thread | None,
    ) -> bool:
        """Synchronize container state from module-level compatibility aliases.

        Returns True when any field changed.
        """
        changed = False
        if runtime is not self.runtime or loop is not self.loop:
            self.bind_runtime(runtime=runtime, loop=loop)
            changed = True
        if runtime_facade_override is not self.runtime_facade_override:
            self.set_runtime_facade_override(runtime_facade_override)
            changed = True
        normalized_shutdown = bool(runtime_shutdown_registered)
        if normalized_shutdown != bool(self.runtime_shutdown_registered):
            self.set_runtime_shutdown_registered(normalized_shutdown)
            changed = True
        if event_dispatcher_thread is not self.event_dispatcher_thread:
            self.set_event_dispatcher_thread(event_dispatcher_thread)
            changed = True
        return changed


RuntimeSupplier = Callable[[], Any]
LoopSupplier = Callable[[], Any]
ResponseBuilder = Callable[[], Any]
PathSupplier = Callable[[], Path]
DataStatusReader = Callable[[str], dict[str, Any]]
ConfigPayloadReader = Callable[[], dict[str, Any]]
_TrainingLabKindSpec = tuple[str, str, str]

_DEFAULT_WEB_STATUS_TRAINING_LAB_LIMIT = 3
_DEFAULT_WEB_STATUS_EVENTS_SUMMARY_LIMIT = 20
_EMPTY_LEADERBOARD_PAYLOAD = {
    "generated_at": "",
    "total_managers": 0,
    "eligible_managers": 0,
    "entries": [],
}
_TRAINING_LAB_KIND_SPECS: dict[str, _TrainingLabKindSpec] = {
    "plan": ("list_training_plans", "get_training_plan", "_training_plan_dir"),
    "run": ("list_training_runs", "get_training_run", "_training_run_dir"),
    "evaluation": (
        "list_training_evaluations",
        "get_training_evaluation",
        "_training_eval_dir",
    ),
}
_STATUS_MAPPING_SECTION_KEYS = (
    "brain",
    "body",
    "memory",
    "bridge",
    "plugins",
    "playbooks",
)


@dataclass(frozen=True)
class _StateBackedSurfaceSnapshot:
    config: dict[str, Any]
    runtime_paths: dict[str, Any]
    training_lab: dict[str, Any] | None = None


class RuntimeFacade(Protocol):
    def get_runtime(self) -> Any: ...

    def get_loop(self) -> Any: ...

    def require_runtime(
        self,
        *,
        runtime_not_ready_response: ResponseBuilder,
        require_loop: bool = False,
    ) -> Any: ...

    def status_snapshot(
        self,
        *,
        detail_mode: str,
        runtime_not_ready_response: ResponseBuilder,
    ) -> Any: ...

    def build_health_payload(
        self,
        *,
        event_buffer_size: int,
        event_history_size: int,
        event_dispatcher_started: bool,
    ) -> dict[str, Any]: ...

    def events_summary_snapshot(
        self,
        *,
        limit: int,
        ok_status: str,
    ) -> Any: ...

    def training_lab_list_snapshot(
        self,
        *,
        kind: str,
        limit: int,
    ) -> Any: ...

    def training_lab_detail_snapshot(
        self,
        *,
        kind: str,
        artifact_id: str,
    ) -> Any: ...

    def leaderboard_snapshot(self) -> Any: ...


def _resolve_training_lab_kind_spec(kind: str) -> _TrainingLabKindSpec:
    spec = _TRAINING_LAB_KIND_SPECS.get(kind)
    if spec is None:
        raise ValueError(f"unsupported training artifact kind: {kind}")
    return spec


@dataclass(frozen=True)
class InProcessRuntimeFacade:
    runtime_getter: RuntimeSupplier
    loop_getter: LoopSupplier

    def get_runtime(self) -> Any:
        return self.runtime_getter()

    def get_loop(self) -> Any:
        return self.loop_getter()

    def require_runtime(
        self,
        *,
        runtime_not_ready_response: ResponseBuilder,
        require_loop: bool = False,
    ) -> Any:
        runtime = self.get_runtime()
        if runtime is None:
            return runtime_not_ready_response()
        if require_loop and self.get_loop() is None:
            return runtime_not_ready_response()
        return runtime

    def status_snapshot(
        self,
        *,
        detail_mode: str,
        runtime_not_ready_response: ResponseBuilder,
    ) -> Any:
        runtime = self.require_runtime(
            runtime_not_ready_response=runtime_not_ready_response,
            require_loop=False,
        )
        if isinstance(runtime, tuple):
            return runtime
        return runtime.status(detail=detail_mode)

    def build_health_payload(
        self,
        *,
        event_buffer_size: int,
        event_history_size: int,
        event_dispatcher_started: bool,
    ) -> dict[str, Any]:
        loop = self.get_loop()
        return {
            "status": "ok",
            "service": "invest-web",
            "runtime": {
                "mode": "embedded",
                "initialized": self.get_runtime() is not None,
                "live_runtime": self.get_runtime() is not None,
                "loop_running": bool(loop is not None and loop.is_running()),
                "provider": "embedded",
                "event_buffer_size": int(event_buffer_size),
                "event_history_size": int(event_history_size),
                "event_dispatcher_started": bool(event_dispatcher_started),
            },
        }

    def events_summary_snapshot(
        self,
        *,
        limit: int,
        ok_status: str,
    ) -> Any:
        runtime = self.get_runtime()
        if runtime is None:
            return {
                "status": ok_status,
                "summary": {
                    "count": 0,
                    "counts": {},
                    "latest": None,
                    "window_start": "",
                    "window_end": "",
                },
                "items": [],
            }
        return runtime.get_events_summary(limit=limit)

    def training_lab_list_snapshot(
        self,
        *,
        kind: str,
        limit: int,
    ) -> Any:
        runtime = self.get_runtime()
        if runtime is None:
            return {"count": 0, "items": []}
        method_name, _, _ = _resolve_training_lab_kind_spec(kind)
        return getattr(runtime, method_name)(limit=limit)

    def training_lab_detail_snapshot(
        self,
        *,
        kind: str,
        artifact_id: str,
    ) -> Any:
        runtime = self.get_runtime()
        if runtime is None:
            raise FileNotFoundError(f"training {kind} not found: {artifact_id}")
        _, method_name, _ = _resolve_training_lab_kind_spec(kind)
        return getattr(runtime, method_name)(artifact_id)

    def leaderboard_snapshot(self) -> Any:
        runtime = self.get_runtime()
        if runtime is None:
            return {
                "generated_at": "",
                "total_managers": 0,
                "eligible_managers": 0,
                "entries": [],
            }
        return runtime.get_leaderboard()


@dataclass(frozen=True)
class DelegatingRuntimeFacade:
    facade_getter: Callable[[], RuntimeFacade]

    def _get_facade(self) -> RuntimeFacade:
        return self.facade_getter()

    def get_runtime(self) -> Any:
        return self._get_facade().get_runtime()

    def get_loop(self) -> Any:
        return self._get_facade().get_loop()

    def require_runtime(
        self,
        *,
        runtime_not_ready_response: ResponseBuilder,
        require_loop: bool = False,
    ) -> Any:
        return self._get_facade().require_runtime(
            runtime_not_ready_response=runtime_not_ready_response,
            require_loop=require_loop,
        )

    def status_snapshot(
        self,
        *,
        detail_mode: str,
        runtime_not_ready_response: ResponseBuilder,
    ) -> Any:
        return self._get_facade().status_snapshot(
            detail_mode=detail_mode,
            runtime_not_ready_response=runtime_not_ready_response,
        )

    def build_health_payload(
        self,
        *,
        event_buffer_size: int,
        event_history_size: int,
        event_dispatcher_started: bool,
    ) -> dict[str, Any]:
        return self._get_facade().build_health_payload(
            event_buffer_size=event_buffer_size,
            event_history_size=event_history_size,
            event_dispatcher_started=event_dispatcher_started,
        )

    def events_summary_snapshot(
        self,
        *,
        limit: int,
        ok_status: str,
    ) -> Any:
        return self._get_facade().events_summary_snapshot(
            limit=limit, ok_status=ok_status
        )

    def training_lab_list_snapshot(
        self,
        *,
        kind: str,
        limit: int,
    ) -> Any:
        return self._get_facade().training_lab_list_snapshot(kind=kind, limit=limit)

    def training_lab_detail_snapshot(
        self,
        *,
        kind: str,
        artifact_id: str,
    ) -> Any:
        return self._get_facade().training_lab_detail_snapshot(
            kind=kind, artifact_id=artifact_id
        )

    def leaderboard_snapshot(self) -> Any:
        return self._get_facade().leaderboard_snapshot()


@dataclass(frozen=True)
class StateBackedRuntimeFacade:
    project_root_getter: PathSupplier
    state_file_getter: PathSupplier
    runtime_lock_file_getter: PathSupplier
    training_lock_file_getter: PathSupplier
    runtime_events_path_getter: PathSupplier
    data_status_getter: DataStatusReader
    config_payload_getter: ConfigPayloadReader | None = None

    def _training_state_dir(self) -> Path:
        return (
            Path(self.project_root_getter()).expanduser().resolve()
            / "runtime"
            / "state"
        )

    def _training_plan_dir(self) -> Path:
        return self._training_state_dir() / "training_plans"

    def _training_run_dir(self) -> Path:
        return self._training_state_dir() / "training_runs"

    def _training_eval_dir(self) -> Path:
        return self._training_state_dir() / "training_evals"

    def get_runtime(self) -> Any:
        return None

    def get_loop(self) -> Any:
        return None

    def require_runtime(
        self,
        *,
        runtime_not_ready_response: ResponseBuilder,
        require_loop: bool = False,
    ) -> Any:
        del require_loop
        return runtime_not_ready_response()

    def _read_runtime_paths_payload(self) -> dict[str, Any]:
        project_root = Path(self.project_root_getter()).expanduser().resolve()
        try:
            payload = get_runtime_paths_payload(project_root=project_root)
        except (OSError, TypeError, ValueError):
            logger.exception(
                "Failed to resolve runtime path payload for project_root=%s",
                project_root,
            )
            return {}
        return dict(payload.get("config") or {})

    def _runtime_paths_payload(self) -> dict[str, Any]:
        return dict(self._read_runtime_paths_payload())

    def _project_root(self) -> Path:
        return Path(self.project_root_getter()).expanduser().resolve()

    def _default_workspace_path(self) -> str:
        return str(self._project_root() / "runtime" / "workspace")

    def _default_playbook_dir(self) -> str:
        return str(self._project_root() / "strategies")

    @staticmethod
    def _merge_mapping_section(
        current: Any, defaults: dict[str, Any]
    ) -> dict[str, Any]:
        merged = dict(defaults)
        if isinstance(current, dict):
            merged.update(dict(current))
        return merged

    @classmethod
    def _merge_status_mapping_sections(
        cls,
        payload: dict[str, Any],
        *,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(payload)
        for key in _STATUS_MAPPING_SECTION_KEYS:
            merged[key] = cls._merge_mapping_section(
                merged.get(key),
                dict(fallback.get(key) or {}),
            )
        return merged

    def _config_payload(self) -> dict[str, Any]:
        if self.config_payload_getter is None:
            return {}
        payload = self.config_payload_getter()
        return dict(payload) if isinstance(payload, dict) else {}

    def _surface_snapshot(
        self,
        *,
        training_lab_limit: int | None = None,
    ) -> _StateBackedSurfaceSnapshot:
        training_lab = (
            self._training_lab_status(limit=training_lab_limit)
            if training_lab_limit is not None
            else None
        )
        return _StateBackedSurfaceSnapshot(
            config=self._config_payload(),
            runtime_paths=self._runtime_paths_payload(),
            training_lab=training_lab,
        )

    @staticmethod
    def _normalize_training_lab_limit(raw_value: Any) -> int:
        try:
            return max(1, int(raw_value))
        except (TypeError, ValueError):
            return _DEFAULT_WEB_STATUS_TRAINING_LAB_LIMIT

    @staticmethod
    def _normalize_events_summary_limit(raw_value: Any) -> int:
        try:
            return max(1, int(raw_value))
        except (TypeError, ValueError):
            return _DEFAULT_WEB_STATUS_EVENTS_SUMMARY_LIMIT

    def _read_json_file(self, path: Path) -> dict[str, Any]:
        return safe_read_json_dict(path)

    def _build_events_summary(self, config_payload: dict[str, Any]) -> dict[str, Any]:
        event_summary_limit = self._normalize_events_summary_limit(
            config_payload.get(
                "web_status_events_summary_limit",
                _DEFAULT_WEB_STATUS_EVENTS_SUMMARY_LIMIT,
            )
        )
        return summarize_event_rows(
            read_event_rows(
                self.runtime_events_path_getter(), limit=event_summary_limit
            )
        )

    def _list_json_artifacts(self, directory: Path, *, limit: int) -> dict[str, Any]:
        paths = list_json_artifact_paths(directory, limit=limit, default=20)
        items: list[dict[str, Any]] = []
        for path in paths:
            item: dict[str, Any] = {"path": str(path), "name": path.name}
            try:
                payload = self._read_json_file(path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict):
                for key in (
                    "plan_id",
                    "run_id",
                    "status",
                    "created_at",
                    "last_run_id",
                    "last_run_at",
                ):
                    if key in payload:
                        item[key] = payload.get(key)
                artifacts = dict(payload.get("artifacts") or {})
                if artifacts:
                    item["artifacts"] = artifacts
                spec = dict(payload.get("spec") or {})
                if spec:
                    item["spec"] = spec
                run_payload = dict(payload.get("payload") or {})
                results = [
                    dict(entry)
                    for entry in list(run_payload.get("results") or [])
                    if isinstance(entry, dict)
                ]
                if results:
                    latest = dict(results[-1])
                    item["latest_result"] = {
                        "cycle_id": latest.get("cycle_id"),
                        "status": str(latest.get("status") or ""),
                        "return_pct": latest.get("return_pct"),
                        "benchmark_passed": bool(latest.get("benchmark_passed", False)),
                        "core_artifacts": collect_core_explainability_artifacts(latest),
                        "promotion_record": dict(latest.get("promotion_record") or {}),
                        "lineage_record": dict(latest.get("lineage_record") or {}),
                    }
                assessment = dict(payload.get("assessment") or {})
                if assessment:
                    item["assessment"] = {
                        "success_count": int(assessment.get("success_count", 0) or 0),
                        "no_data_count": int(assessment.get("no_data_count", 0) or 0),
                        "error_count": int(assessment.get("error_count", 0) or 0),
                        "avg_return_pct": assessment.get("avg_return_pct"),
                        "benchmark_pass_rate": assessment.get("benchmark_pass_rate"),
                        "latest_result": dict(assessment.get("latest_result") or {}),
                    }
                promotion = dict(payload.get("promotion") or {})
                if promotion:
                    research_feedback = dict(promotion.get("research_feedback") or {})
                    item["promotion"] = {
                        "verdict": str(promotion.get("verdict") or ""),
                        "passed": bool(promotion.get("passed", False)),
                        "research_feedback": {
                            "enabled": bool(research_feedback.get("enabled", False)),
                            "passed": bool(research_feedback.get("passed", False)),
                            "summary": str(research_feedback.get("summary") or ""),
                        },
                    }
                governance_metrics = dict(payload.get("governance_metrics") or {})
                if governance_metrics:
                    item["governance_metrics"] = governance_metrics
                realism_summary = dict(payload.get("realism_summary") or {})
                if realism_summary:
                    item["realism_summary"] = realism_summary
            items.append(item)
        return {"count": len(paths), "items": items}

    def _resolve_training_lab_dir(self, kind: str) -> Path:
        _, _, builder_name = _resolve_training_lab_kind_spec(kind)
        return cast(Callable[[], Path], getattr(self, builder_name))()

    def _resolve_training_lab_path(self, kind: str, artifact_id: str) -> Path:
        directory = self._resolve_training_lab_dir(kind)
        filename = str(artifact_id or "").strip()
        if not filename:
            raise FileNotFoundError(f"training {kind} not found: {artifact_id}")
        return directory / f"{filename}.json"

    def _read_training_lab_artifact(
        self, kind: str, artifact_id: str
    ) -> dict[str, Any]:
        path = self._resolve_training_lab_path(kind, artifact_id)
        if not path.exists():
            raise FileNotFoundError(f"training {kind} not found: {path}")
        payload = self._read_json_file(path)
        if not isinstance(payload, dict):
            raise FileNotFoundError(f"training {kind} not found: {path}")
        return payload

    def _leaderboard_path(self) -> Path:
        runtime_paths = self._surface_snapshot().runtime_paths
        training_output_dir = Path(
            runtime_paths.get("training_output_dir")
            or (self._project_root() / "runtime" / "outputs" / "training")
        )
        return training_output_dir.parent / "leaderboard.json"

    @staticmethod
    def _json_artifact_count(directory: Path) -> int:
        return len(list(directory.glob("*.json"))) if directory.exists() else 0

    def _training_lab_counts(self) -> dict[str, int]:
        return {
            "plan_count": self._json_artifact_count(self._training_plan_dir()),
            "run_count": self._json_artifact_count(self._training_run_dir()),
            "evaluation_count": self._json_artifact_count(self._training_eval_dir()),
        }

    def _training_lab_status(self, *, limit: int) -> dict[str, Any]:
        latest_items = {
            kind: self._list_json_artifacts(
                self._resolve_training_lab_dir(kind), limit=limit
            ).get(
                "items",
                [],
            )
            for kind in _TRAINING_LAB_KIND_SPECS
        }
        return build_training_lab_status(
            lab_counts=self._training_lab_counts(),
            latest_plans=list(latest_items.get("plan") or []),
            latest_runs=list(latest_items.get("run") or []),
            latest_evaluations=list(latest_items.get("evaluation") or []),
            include_recent=True,
        )

    def _build_offline_runtime_payload(self) -> dict[str, Any]:
        runtime_lock_file = self.runtime_lock_file_getter()
        training_lock_file = self.training_lock_file_getter()
        runtime_lock_active = runtime_lock_file.exists()
        training_lock_active = training_lock_file.exists()
        return {
            "state": "running" if runtime_lock_active else "stopped",
            "started": False,
            "current_task": None,
            "last_task": None,
            "runtime_lock_file": str(runtime_lock_file),
            "runtime_lock_active": runtime_lock_active,
            "training_lock_file": str(training_lock_file),
            "training_lock_active": training_lock_active,
            "state_source": "runtime_state",
            "live_runtime": runtime_lock_active,
        }

    def _build_fallback_status_payload(
        self,
        detail_mode: str,
        *,
        surface: _StateBackedSurfaceSnapshot,
    ) -> dict[str, Any]:
        training_lab = dict(surface.training_lab or {})
        events_summary = self._build_events_summary(surface.config)
        return {
            "ts": datetime.now().isoformat(),
            "detail_mode": detail_mode,
            "instance_id": "",
            "workspace": self._default_workspace_path(),
            "playbook_dir": self._default_playbook_dir(),
            "model": "",
            "autopilot_enabled": False,
            "heartbeat_enabled": False,
            "training_interval_sec": 0,
            "heartbeat_interval_sec": 0,
            "runtime": self._build_offline_runtime_payload(),
            "brain": {
                "tool_count": 0,
                "session_count": 0,
                "cron": {},
                "governance_metrics": {},
            },
            "body": {
                "total_cycles": 0,
                "success_cycles": 0,
                "no_data_cycles": 0,
                "failed_cycles": 0,
                "last_result": None,
                "last_error": "",
                "last_run_at": "",
                "training_state": "idle",
                "current_task": None,
                "last_completed_task": None,
            },
            "memory": {},
            "bridge": {},
            "plugins": {"count": 0, "items": []},
            "playbooks": {"total": 0, "enabled": 0, "items": []},
            "config": dict(surface.config),
            "data": self.data_status_getter(detail_mode),
            "events": events_summary,
            "training_lab": training_lab,
            "runtime_paths": dict(surface.runtime_paths),
        }

    def _read_persisted_payload(self) -> dict[str, Any]:
        payload = _load_persisted_runtime_state_payload(self.state_file_getter())
        if not isinstance(payload, dict):
            return {}
        return dict(payload)

    def _merge_status_payload(
        self,
        payload: dict[str, Any],
        *,
        detail_mode: str,
        surface: _StateBackedSurfaceSnapshot,
    ) -> dict[str, Any]:
        fallback = self._build_fallback_status_payload(detail_mode, surface=surface)
        if not payload:
            return fallback

        merged = dict(fallback)
        merged.update(dict(payload))
        runtime_defaults = dict(fallback["runtime"])
        runtime_payload = self._merge_mapping_section(
            merged.get("runtime"), runtime_defaults
        )
        runtime_payload.update(
            {
                "runtime_lock_file": runtime_defaults["runtime_lock_file"],
                "runtime_lock_active": runtime_defaults["runtime_lock_active"],
                "training_lock_file": runtime_defaults["training_lock_file"],
                "training_lock_active": runtime_defaults["training_lock_active"],
                "state_source": "runtime_state",
                "live_runtime": runtime_defaults["live_runtime"],
            }
        )
        if not str(runtime_payload.get("state") or "").strip():
            runtime_payload["state"] = runtime_defaults["state"]
        merged["runtime"] = runtime_payload
        merged["ts"] = datetime.now().isoformat()
        merged["detail_mode"] = detail_mode
        merged["data"] = self.data_status_getter(detail_mode)
        merged["events"] = self._build_events_summary(surface.config)
        merged = self._merge_status_mapping_sections(merged, fallback=fallback)
        merged["training_lab"] = dict(surface.training_lab or {})
        merged["config"] = dict(surface.config)
        merged["runtime_paths"] = dict(surface.runtime_paths)
        return merged

    def status_snapshot(
        self,
        *,
        detail_mode: str,
        runtime_not_ready_response: ResponseBuilder,
    ) -> Any:
        del runtime_not_ready_response
        payload = self._read_persisted_payload()
        config_payload = self._config_payload()
        training_lab_limit = self._normalize_training_lab_limit(
            config_payload.get(
                "web_status_training_lab_limit",
                _DEFAULT_WEB_STATUS_TRAINING_LAB_LIMIT,
            )
        )
        surface = self._surface_snapshot(training_lab_limit=training_lab_limit)
        return self._merge_status_payload(
            payload,
            detail_mode=detail_mode,
            surface=surface,
        )

    def build_health_payload(
        self,
        *,
        event_buffer_size: int,
        event_history_size: int,
        event_dispatcher_started: bool,
    ) -> dict[str, Any]:
        state_file = self.state_file_getter()
        runtime_lock_file = self.runtime_lock_file_getter()
        training_lock_file = self.training_lock_file_getter()
        return {
            "status": "ok",
            "service": "invest-web",
            "runtime": {
                "mode": "state_backed",
                "initialized": bool(
                    state_file.exists()
                    or runtime_lock_file.exists()
                    or training_lock_file.exists()
                ),
                "live_runtime": bool(runtime_lock_file.exists()),
                "state_file": str(state_file),
                "state_available": bool(state_file.exists()),
                "runtime_lock_file": str(runtime_lock_file),
                "runtime_lock_active": bool(runtime_lock_file.exists()),
                "training_lock_file": str(training_lock_file),
                "training_lock_active": bool(training_lock_file.exists()),
                "loop_running": False,
                "provider": "state-backed",
                "event_buffer_size": int(event_buffer_size),
                "event_history_size": int(event_history_size),
                "event_dispatcher_started": bool(event_dispatcher_started),
            },
        }

    def events_summary_snapshot(
        self,
        *,
        limit: int,
        ok_status: str,
    ) -> Any:
        rows = read_event_rows(self.runtime_events_path_getter(), limit=limit)
        return {
            "status": ok_status,
            "summary": summarize_event_rows(rows),
            "items": rows,
        }

    def training_lab_list_snapshot(
        self,
        *,
        kind: str,
        limit: int,
    ) -> Any:
        return self._list_json_artifacts(
            self._resolve_training_lab_dir(kind), limit=limit
        )

    def training_lab_detail_snapshot(
        self,
        *,
        kind: str,
        artifact_id: str,
    ) -> Any:
        return self._read_training_lab_artifact(kind, artifact_id)

    def leaderboard_snapshot(self) -> Any:
        path = self._leaderboard_path()
        if not path.exists():
            return dict(_EMPTY_LEADERBOARD_PAYLOAD)
        try:
            payload = self._read_json_file(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            logger.exception("Failed to read leaderboard snapshot from %s", path)
            return dict(_EMPTY_LEADERBOARD_PAYLOAD)
        if not isinstance(payload, dict):
            return dict(_EMPTY_LEADERBOARD_PAYLOAD)
        return payload


__all__ = [
    "DelegatingRuntimeFacade",
    "InProcessRuntimeFacade",
    "RuntimeFacade",
    "StateBackedRuntimeFacade",
    "WebRuntimeEphemeralState",
    "WebRuntimeStateContainer",
    "load_default_commander_runtime_types",
]
