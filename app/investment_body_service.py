"""Investment body service extracted from commander runtime."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Optional

from app.commander_support.workflow import jsonable as _jsonable
from app.train import SelfLearningController, TrainingResult
from config import config
from market_data import DataManager, DataSourceUnavailableError, MockDataProvider

if TYPE_CHECKING:
    from app.commander import CommanderConfig

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_BUSY = "busy"
STATUS_IDLE = "idle"
STATUS_TRAINING = "training"
STATUS_COMPLETED = "completed"
STATUS_NO_DATA = "no_data"

EVENT_TRAINING_STARTED = "training_started"
EVENT_TRAINING_FINISHED = "training_finished"


def build_mock_provider() -> MockDataProvider:
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


class InvestmentBodyService:
    """Long-running body service: executes training cycles and tracks state."""

    def __init__(
        self,
        cfg: CommanderConfig,
        on_runtime_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ):
        self.cfg = cfg
        self._runtime_event_sink = on_runtime_event
        self._mock_provider: Optional[MockDataProvider] = build_mock_provider() if cfg.mock_mode else None
        self.controller = SelfLearningController(
            data_provider=self._mock_provider,
            output_dir=str(self.cfg.training_output_dir),
            meeting_log_dir=str(self.cfg.meeting_log_dir),
            config_audit_log_path=str(self.cfg.config_audit_log_path),
            config_snapshot_dir=str(self.cfg.config_snapshot_dir),
        )
        self._real_data_manager = self.controller.data_manager if not cfg.mock_mode else DataManager()
        self._mock_data_manager: Optional[DataManager] = (
            self.controller.data_manager if cfg.mock_mode else None
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
        self.training_state: str = STATUS_IDLE
        self.current_task: Optional[dict[str, Any]] = None
        self.last_completed_task: Optional[dict[str, Any]] = None

    def _write_training_lock(self, payload: dict[str, Any]) -> None:
        self.cfg.training_lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.training_lock_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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
            self._mock_provider = build_mock_provider()
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
        errors = [
            item
            for item in results
            if item.get("status") == STATUS_ERROR
            and item.get("error_code") == DataSourceUnavailableError.error_code
        ]
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
                            self.last_error = str(error_payload.get("error") or "")
                            item = self._build_data_source_error_cycle_item(
                                error_payload=error_payload,
                                cycle_meta=cycle_meta,
                                cycle_id=cycle_id,
                                requested_data_mode=requested_data_mode,
                            )
                            logger.warning(
                                "Commander body cycle failed due to unavailable data source"
                            )
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

        return _jsonable(
            {
                "status": run_status,
                "rounds": rounds,
                "results": results,
                "summary": self.snapshot(),
            }
        )

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

        return _jsonable(
            {
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
                "research_feedback": getattr(self.controller, "last_research_feedback", {}),
                "freeze_gate_evaluation": getattr(
                    self.controller,
                    "last_freeze_gate_evaluation",
                    {},
                ),
                "research_feedback_optimization": getattr(
                    self.controller,
                    "last_feedback_optimization",
                    {},
                ),
                "training_state": self.training_state,
                "is_training": self._lock.locked(),
                "current_task": self.current_task,
                "last_completed_task": self.last_completed_task,
                "training_lock_file": str(self.cfg.training_lock_file),
            }
        )

    def _artifact_paths_for_cycle(self, cycle_id: int | None) -> dict[str, str]:
        if not cycle_id:
            return {}
        cid = int(cycle_id)
        return {
            "cycle_result_path": str(self.cfg.training_output_dir / f"cycle_{cid}.json"),
            "selection_meeting_json_path": str(
                self.cfg.meeting_log_dir / "selection" / f"meeting_{cid:04d}.json"
            ),
            "selection_meeting_markdown_path": str(
                self.cfg.meeting_log_dir / "selection" / f"meeting_{cid:04d}.md"
            ),
            "review_meeting_json_path": str(
                self.cfg.meeting_log_dir / "review" / f"review_{cid:04d}.json"
            ),
            "review_meeting_markdown_path": str(
                self.cfg.meeting_log_dir / "review" / f"review_{cid:04d}.md"
            ),
            "optimization_events_path": str(
                self.cfg.training_output_dir / "optimization_events.jsonl"
            ),
        }

    def _to_result_dict(self, result: TrainingResult) -> dict[str, Any]:
        return _jsonable(
            {
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
                "research_feedback": result.research_feedback,
                "audit_tags": result.audit_tags,
                "artifacts": self._artifact_paths_for_cycle(result.cycle_id),
                "timestamp": datetime.now().isoformat(),
            }
        )
