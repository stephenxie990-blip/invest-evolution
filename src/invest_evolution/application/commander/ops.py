"""Commander ops surface and control mixins."""

from __future__ import annotations

import json
import logging
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from invest_evolution.application.config_surface import (
    get_control_plane_payload,
    get_evolution_config_payload,
    get_runtime_paths_payload,
    invalid_evolution_config_patch_keys,
    list_agent_prompts_payload,
    update_agent_prompt_payload,
    update_control_plane_payload,
    update_evolution_config_payload,
    update_runtime_paths_payload,
)
from invest_evolution.application.commander.bootstrap import (
    sync_runtime_path_config as sync_runtime_path_config_bootstrap,
)
from invest_evolution.application.commander.status import (
    build_events_summary_response_bundle,
    build_events_tail_response_bundle,
    build_memory_detail,
    build_runtime_diagnostics_response_bundle,
    build_training_lab_summary_response_bundle,
    memory_brief_row,
)
from invest_evolution.application.commander.workflow import (
    attach_domain_mutating_workflow as attach_domain_mutating_workflow_impl,
    attach_domain_readonly_workflow as attach_domain_readonly_workflow_impl,
    jsonable as _jsonable,
)
from invest_evolution.application.research_services import (
    get_research_attributions_payload,
    get_research_calibration_payload,
    get_research_cases_payload,
)
from invest_evolution.common.utils import normalize_limit, safe_read_json_dict
from invest_evolution.config import (
    PROJECT_ROOT,
    config,
)
from invest_evolution.investment.governance.engine import (
    ModelAllocator,
    build_leaderboard_payload,
)
from invest_evolution.investment.runtimes import list_manager_runtime_ids
from invest_evolution.market_data.manager import MarketDataGateway, MarketQueryService

logger = logging.getLogger(__name__)

DOMAIN_TOOL_CATALOG: dict[str, tuple[str, ...]] = {
    "config": (
        "invest_control_plane_get",
        "invest_control_plane_update",
        "invest_evolution_config_get",
        "invest_evolution_config_update",
        "invest_runtime_paths_get",
        "invest_runtime_paths_update",
        "invest_agent_prompts_list",
        "invest_agent_prompts_update",
    ),
    "data": (
        "invest_data_status",
        "invest_data_download",
        "invest_data_capital_flow",
        "invest_data_dragon_tiger",
        "invest_data_intraday_60m",
    ),
    "training": (
        "invest_train",
        "invest_quick_test",
        "invest_training_plan_create",
        "invest_training_plan_list",
        "invest_training_plan_execute",
        "invest_training_runs_list",
        "invest_training_evaluations_list",
        "invest_training_lab_summary",
    ),
    "runtime": (
        "invest_quick_status",
        "invest_deep_status",
        "invest_events_tail",
        "invest_events_summary",
        "invest_runtime_diagnostics",
    ),
    "memory": (
        "invest_memory_search",
        "invest_memory_list",
        "invest_memory_get",
    ),
    "scheduler": (
        "invest_cron_add",
        "invest_cron_list",
        "invest_cron_remove",
    ),
    "analytics": (
        "invest_managers",
        "invest_leaderboard",
        "invest_allocator",
        "invest_governance_preview",
    ),
    "playbook": (
        "invest_list_playbooks",
        "invest_reload_playbooks",
    ),
    "strategy": ("invest_stock_strategies",),
    "research": (
        "invest_research_cases",
        "invest_research_attributions",
        "invest_research_calibration",
    ),
    "plugin": ("invest_plugins_reload",),
}

STATUS_OK = "ok"


def _resolve_update_evolution_config_payload() -> Any:
    commander_main = sys.modules.get("invest_evolution.application.commander_main")
    if commander_main is not None:
        compatibility_override = getattr(
            commander_main,
            "update_evolution_config_payload",
            None,
        )
        if callable(compatibility_override):
            return compatibility_override
    return update_evolution_config_payload
STATUS_CONFIRMATION_REQUIRED = "confirmation_required"
HIGH_RISK_EVOLUTION_CONFIG_KEYS = frozenset(
    {
        "default_manager_id",
        "default_manager_config_ref",
        "data_source",
        "governance_enabled",
        "governance_mode",
        "manager_active_ids",
        "manager_budget_weights",
    }
)


DOMAIN_AGENT_KIND: dict[str, str] = {
    "analytics": "bounded_analytics_agent",
    "config": "bounded_config_agent",
    "data": "bounded_data_agent",
    "memory": "bounded_memory_agent",
    "plugin": "bounded_plugin_agent",
    "research": "bounded_research_agent",
    "runtime": "bounded_runtime_agent",
    "scheduler": "bounded_scheduler_agent",
    "stock": "bounded_stock_agent",
    "playbook": "bounded_playbook_agent",
    "strategy": "bounded_strategy_agent",
    "training": "bounded_training_agent",
}


def get_domain_tools(domain: str) -> list[str]:
    return list(DOMAIN_TOOL_CATALOG.get(str(domain or ""), ()))


def get_domain_agent_kind(domain: str, default: str = "bounded_runtime_agent") -> str:
    return DOMAIN_AGENT_KIND.get(str(domain or ""), default)


@dataclass
class DataDownloadJob:
    status: str = "idle"
    started_at: str = ""
    finished_at: str = ""
    error: str = ""


@dataclass(frozen=True)
class _DomainOperationResponseSpec:
    domain: str
    writes_state: bool
    event_source: str | None = None


_ANALYTICS_READONLY_SPEC = _DomainOperationResponseSpec(
    domain="analytics",
    writes_state=False,
)
_CONFIG_READONLY_SPEC = _DomainOperationResponseSpec(
    domain="config",
    writes_state=False,
)
_CONFIG_MUTATING_SPEC = _DomainOperationResponseSpec(
    domain="config",
    writes_state=True,
    event_source="config",
)
_DATA_READONLY_SPEC = _DomainOperationResponseSpec(
    domain="data",
    writes_state=False,
)
_DATA_MUTATING_SPEC = _DomainOperationResponseSpec(
    domain="data",
    writes_state=True,
    event_source="data",
)
_MEMORY_READONLY_SPEC = _DomainOperationResponseSpec(
    domain="memory",
    writes_state=False,
)
_RESEARCH_READONLY_SPEC = _DomainOperationResponseSpec(
    domain="research",
    writes_state=False,
)


_DATA_DOWNLOAD_LOCK = threading.Lock()
_DATA_DOWNLOAD_JOB = DataDownloadJob()


def _normalized_record_limit(limit: int | None, *, default: int, maximum: int) -> int:
    normalized = normalize_limit(limit, default=default, maximum=maximum)
    return normalized if normalized > 0 else 0


def _bounded_market_frame(frame: Any, *, limit: int | None, default: int, maximum: int):
    normalized_limit = _normalized_record_limit(limit, default=default, maximum=maximum)
    if frame.empty or normalized_limit <= 0:
        return frame.head(0)
    return frame.head(normalized_limit)


def get_managers_payload(runtime: Any) -> dict[str, Any]:
    controller = runtime.body.controller
    items = list_manager_runtime_ids()
    last_governance_decision = dict(
        getattr(controller, "last_governance_decision", {}) or {}
    )
    active_manager_ids = [
        str(item).strip()
        for item in (
            getattr(controller, "manager_active_ids", None)
            or last_governance_decision.get("active_manager_ids")
            or []
        )
        if str(item).strip()
    ]
    manager_budget_weights = {
        str(key): float(value)
        for key, value in (
            getattr(controller, "manager_budget_weights", None)
            or last_governance_decision.get("manager_budget_weights")
            or {}
        ).items()
        if str(key).strip()
    }
    return {
        "count": len(items),
        "items": items,
        "execution_defaults": {
            "default_manager_id": str(
                getattr(config, "default_manager_id", "momentum") or "momentum"
            ),
            "default_manager_config_ref": str(
                getattr(
                    config,
                    "default_manager_config_ref",
                    "src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml",
                )
                or "src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml"
            ),
        },
        "governance": {
            "enabled": bool(getattr(controller, "governance_enabled", False)),
            "mode": str(getattr(controller, "governance_mode", "off") or "off"),
            "allowed_manager_ids": list(
                getattr(controller, "governance_allowed_manager_ids", []) or []
            ),
            "active_manager_ids": active_manager_ids,
            "manager_budget_weights": manager_budget_weights,
            "last_decision": last_governance_decision,
        },
    }


def get_leaderboard_payload(runtime: Any) -> dict[str, Any]:
    root_dir = Path(runtime.cfg.training_output_dir).parent
    leaderboard_path = root_dir / "leaderboard.json"
    if leaderboard_path.exists():
        try:
            payload = safe_read_json_dict(leaderboard_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            payload = {}
        if payload:
            return payload
    return build_leaderboard_payload(root_dir)


def get_allocator_payload(
    runtime: Any,
    *,
    regime: str = "oscillation",
    top_n: int = 3,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    root_dir = Path(runtime.cfg.training_output_dir).parent
    leaderboard_path = root_dir / "leaderboard.json"
    if leaderboard_path.exists():
        try:
            leaderboard = safe_read_json_dict(leaderboard_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            leaderboard = {}
    else:
        leaderboard = {}
    if not leaderboard:
        leaderboard = build_leaderboard_payload(root_dir)
    plan = ModelAllocator().allocate(
        regime,
        leaderboard,
        as_of_date=as_of_date or datetime.now().strftime("%Y%m%d"),
        top_n=max(1, min(4, int(top_n or 3))),
    )
    return {
        "leaderboard_generated_at": leaderboard.get("generated_at"),
        "allocation": plan.to_dict(),
    }


def get_governance_preview_payload(
    runtime: Any,
    *,
    cutoff_date: str | None = None,
    stock_count: int | None = None,
    min_history_days: int | None = None,
    allowed_manager_ids: list[str] | None = None,
) -> dict[str, Any]:
    controller = runtime.body.controller
    payload = controller.preview_governance(
        cutoff_date=cutoff_date,
        stock_count=stock_count,
        min_history_days=min_history_days,
        allowed_manager_ids=allowed_manager_ids or None,
    )
    return {"status": "ok", "governance": payload}


def get_data_status_payload(*, refresh: bool = False) -> dict[str, Any]:
    return MarketQueryService().get_status_summary(refresh=refresh)


def get_capital_flow_payload(
    *,
    codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    frame = MarketQueryService().get_capital_flow(
        codes=codes, start_date=start_date, end_date=end_date
    )
    frame = _bounded_market_frame(frame, limit=limit, default=200, maximum=5000)
    return {"count": int(len(frame)), "items": frame.to_dict(orient="records")}


def get_dragon_tiger_payload(
    *,
    codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    frame = MarketQueryService().get_dragon_tiger_events(
        codes=codes, start_date=start_date, end_date=end_date
    )
    frame = _bounded_market_frame(frame, limit=limit, default=200, maximum=5000)
    return {"count": int(len(frame)), "items": frame.to_dict(orient="records")}


def get_intraday_60m_payload(
    *,
    codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    frame = MarketQueryService().get_intraday_60m_bars(
        codes=codes, start_date=start_date, end_date=end_date
    )
    frame = _bounded_market_frame(frame, limit=limit, default=500, maximum=10000)
    return {"count": int(len(frame)), "items": frame.to_dict(orient="records")}


def get_data_download_status_payload() -> dict[str, Any]:
    return {
        "status": _DATA_DOWNLOAD_JOB.status,
        "started_at": _DATA_DOWNLOAD_JOB.started_at,
        "finished_at": _DATA_DOWNLOAD_JOB.finished_at,
        "error": _DATA_DOWNLOAD_JOB.error,
    }


def trigger_data_download() -> dict[str, Any]:
    def _do_download() -> None:
        global _DATA_DOWNLOAD_JOB
        try:
            _DATA_DOWNLOAD_JOB.status = "running"
            _DATA_DOWNLOAD_JOB.started_at = datetime.now().isoformat()
            _DATA_DOWNLOAD_JOB.finished_at = ""
            _DATA_DOWNLOAD_JOB.error = ""
            MarketDataGateway().sync_background_full_refresh()
            _DATA_DOWNLOAD_JOB.status = "ok"
            _DATA_DOWNLOAD_JOB.finished_at = datetime.now().isoformat()
        except Exception as exc:  # pragma: no cover
            _DATA_DOWNLOAD_JOB.status = "error"
            _DATA_DOWNLOAD_JOB.error = str(exc)
            _DATA_DOWNLOAD_JOB.finished_at = datetime.now().isoformat()
            logger.exception("Commander data download worker failed")

    with _DATA_DOWNLOAD_LOCK:
        if _DATA_DOWNLOAD_JOB.status == "running":
            return {"status": "running", "message": "后台同步已在运行"}
        thread = threading.Thread(target=_do_download, daemon=True)
        thread.start()
    return {
        "status": "started",
        "message": "后台同步任务已启动",
        "job": get_data_download_status_payload(),
    }


def read_json_file(path: Path) -> dict[str, Any]:
    return safe_read_json_dict(path)


class CommanderControlSurfaceMixin:
    """Owns config/data/research/memory/status bounded workflow surface methods."""

    cfg: Any
    memory: Any
    research_case_store: Any
    stock_analysis: Any

    def _append_runtime_event(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        source: str = "runtime",
    ) -> dict[str, Any]: ...

    @staticmethod
    def _domain_tools(domain: str) -> list[str]:
        return get_domain_tools(domain)

    @staticmethod
    def _domain_agent_kind(
        domain: str,
        default: str = "bounded_runtime_agent",
    ) -> str:
        return get_domain_agent_kind(domain, default=default)

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
        return self._attach_domain_workflow(
            payload,
            domain=domain,
            operation=operation,
            runtime_method=runtime_method,
            runtime_tool=runtime_tool,
            agent_kind=agent_kind,
            available_tools=available_tools,
            phase=phase,
            extra_phases=extra_phases,
            phase_stats=phase_stats,
            extra_policy=extra_policy,
            writes_state=False,
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
        return self._attach_domain_workflow(
            payload,
            domain=domain,
            operation=operation,
            runtime_method=runtime_method,
            runtime_tool=runtime_tool,
            agent_kind=agent_kind,
            available_tools=available_tools,
            phase=phase,
            extra_phases=extra_phases,
            phase_stats=phase_stats,
            extra_policy=extra_policy,
            writes_state=True,
        )

    def _attach_domain_workflow(
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
        writes_state: bool,
    ) -> dict[str, Any]:
        attach_impl = (
            attach_domain_mutating_workflow_impl
            if writes_state
            else attach_domain_readonly_workflow_impl
        )
        return attach_impl(
            payload=payload,
            domain=domain,
            operation=operation,
            runtime_method=runtime_method,
            runtime_tool=runtime_tool,
            agent_kind=agent_kind,
            available_tools=available_tools,
            phase=phase,
            extra_phases=extra_phases,
            phase_stats=phase_stats,
            extra_policy=extra_policy,
            workspace=str(self.cfg.workspace),
            domain_agent_kind_resolver=self._domain_agent_kind,
            domain_tools_resolver=self._domain_tools,
        )

    @staticmethod
    def _build_confirmation_required_payload(
        *,
        status: str,
        message: str,
        pending: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
        jsonable: Any,
    ) -> dict[str, Any]:
        payload = {"status": status, "message": message}
        if pending:
            payload["pending"] = jsonable(dict(pending))
        if extra_payload:
            payload.update(jsonable(dict(extra_payload)))
        return payload

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
        payload = self._build_confirmation_required_payload(
            status=STATUS_CONFIRMATION_REQUIRED,
            message=message,
            pending=pending,
            extra_payload=extra_payload,
            jsonable=_jsonable,
        )
        return self._attach_domain_operation_response(
            payload,
            spec=_DomainOperationResponseSpec(
                domain=domain,
                writes_state=True,
            ),
            operation=operation,
            runtime_tool=runtime_tool,
            phase="gate_confirmation",
            runtime_method=runtime_method,
            agent_kind=agent_kind,
            available_tools=available_tools,
            phase_stats=phase_stats,
            extra_policy={"confirmation_gate": True},
        )

    @staticmethod
    def _build_patch_confirmation_phase_stats(
        patch: dict[str, Any] | None,
        **extra_stats: Any,
    ) -> dict[str, Any]:
        return {
            "pending_key_count": len(dict(patch or {})),
            "requires_confirmation": True,
            **extra_stats,
        }

    def _build_patch_confirmation_required_workflow(
        self,
        *,
        domain: str,
        operation: str,
        runtime_tool: str,
        message: str,
        patch: dict[str, Any],
        extra_payload: dict[str, Any] | None = None,
        **phase_stats: Any,
    ) -> dict[str, Any]:
        return self._build_confirmation_required_workflow(
            domain=domain,
            operation=operation,
            runtime_tool=runtime_tool,
            message=message,
            pending={"patch": patch},
            extra_payload=extra_payload,
            phase_stats=self._build_patch_confirmation_phase_stats(
                patch,
                **phase_stats,
            ),
        )

    @staticmethod
    def _build_confirmed_update_phase_stats(
        payload: dict[str, Any],
        *,
        confirmed: bool,
        **extra_stats: Any,
    ) -> dict[str, Any]:
        return {
            "updated_count": len(list(payload.get("updated") or [])),
            "confirmed": bool(confirmed),
            **extra_stats,
        }

    def _attach_domain_operation_response(
        self,
        payload: dict[str, Any],
        *,
        spec: _DomainOperationResponseSpec,
        operation: str,
        runtime_tool: str,
        phase: str,
        runtime_method: str | None = None,
        agent_kind: str | None = None,
        available_tools: list[str] | None = None,
        phase_stats: dict[str, Any] | None,
        extra_phases: tuple[str, ...] = (),
        extra_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attach_workflow = (
            self._attach_domain_mutating_workflow
            if spec.writes_state
            else self._attach_domain_readonly_workflow
        )
        return attach_workflow(
            payload,
            domain=spec.domain,
            operation=operation,
            runtime_method=runtime_method,
            runtime_tool=runtime_tool,
            agent_kind=agent_kind,
            available_tools=available_tools,
            phase=phase,
            extra_phases=extra_phases,
            phase_stats=phase_stats,
            extra_policy=extra_policy,
        )

    def _finalize_mutating_workflow(
        self,
        payload: dict[str, Any],
        *,
        spec: _DomainOperationResponseSpec,
        runtime_event: str | None = None,
        runtime_event_payload: dict[str, Any] | None = None,
        operation: str,
        runtime_tool: str,
        phase: str,
        phase_stats: dict[str, Any] | None,
        extra_phases: tuple[str, ...] = (),
        extra_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if runtime_event:
            self._append_runtime_event(
                runtime_event,
                dict(runtime_event_payload or payload),
                source=str(spec.event_source or spec.domain),
            )
        return self._attach_domain_operation_response(
            payload,
            spec=spec,
            operation=operation,
            runtime_tool=runtime_tool,
            phase=phase,
            phase_stats=phase_stats,
            extra_phases=extra_phases,
            extra_policy=extra_policy,
        )

    def _attach_projected_readonly_bundle(self, bundle: Any) -> dict[str, Any]:
        spec = getattr(bundle, "spec", None)
        payload = dict(getattr(bundle, "payload", {}) or {})
        return self._attach_domain_operation_response(
            payload,
            spec=_DomainOperationResponseSpec(
                domain=str(getattr(spec, "domain", "") or ""),
                writes_state=False,
            ),
            operation=str(getattr(spec, "operation", "") or ""),
            runtime_method=getattr(spec, "runtime_method", None),
            runtime_tool=str(getattr(spec, "runtime_tool", "") or ""),
            agent_kind=getattr(spec, "agent_kind", None),
            available_tools=list(getattr(spec, "available_tools", ()) or ()) or None,
            phase=str(getattr(spec, "phase", "") or ""),
            phase_stats=dict(getattr(spec, "phase_stats", {}) or {}),
            extra_phases=tuple(getattr(spec, "extra_phases", ()) or ()),
            extra_policy=(
                dict(getattr(spec, "extra_policy", {}) or {})
                if getattr(spec, "extra_policy", None)
                else None
            ),
        )

    def build_training_confirmation_required(
        self,
        *,
        rounds: int,
        mock: bool,
    ) -> dict[str, Any]:
        return self._build_confirmation_required_workflow(
            domain="training",
            operation="train_once",
            runtime_tool="invest_train",
            message="多轮真实训练属于高风险操作，请使用 confirm=true 再执行。",
            pending={"rounds": int(rounds), "mock": bool(mock)},
            phase_stats={
                "rounds": int(rounds),
                "mock": bool(mock),
                "requires_confirmation": True,
            },
        )

    def get_managers(self) -> dict[str, Any]:
        payload = get_managers_payload(self)
        return self._attach_domain_operation_response(
            payload,
            spec=_ANALYTICS_READONLY_SPEC,
            operation="get_managers",
            runtime_tool="invest_managers",
            phase="manager_roster_read",
            phase_stats={
                "count": int(
                    payload.get("count", len(list(payload.get("items") or [])))
                )
            },
        )

    def get_leaderboard(self) -> dict[str, Any]:
        payload = get_leaderboard_payload(self)
        return self._attach_domain_operation_response(
            payload,
            spec=_ANALYTICS_READONLY_SPEC,
            operation="get_leaderboard",
            runtime_tool="invest_leaderboard",
            phase="leaderboard_read",
            phase_stats={
                "count": int(
                    payload.get("count", len(list(payload.get("items") or [])))
                )
            },
        )

    def get_allocator_preview(
        self,
        *,
        regime: str = "oscillation",
        top_n: int = 3,
        as_of_date: str | None = None,
    ) -> dict[str, Any]:
        payload = get_allocator_payload(
            self,
            regime=regime,
            top_n=top_n,
            as_of_date=as_of_date,
        )
        return self._attach_domain_operation_response(
            payload,
            spec=_ANALYTICS_READONLY_SPEC,
            operation="get_allocator_preview",
            runtime_tool="invest_allocator",
            phase="allocator_preview_read",
            phase_stats={"regime": regime, "top_n": int(top_n)},
        )

    def get_governance_preview(
        self,
        *,
        cutoff_date: str | None = None,
        stock_count: int | None = None,
        min_history_days: int | None = None,
        allowed_manager_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = get_governance_preview_payload(
            self,
            cutoff_date=cutoff_date,
            stock_count=stock_count,
            min_history_days=min_history_days,
            allowed_manager_ids=allowed_manager_ids,
        )
        return self._attach_domain_operation_response(
            payload,
            spec=_ANALYTICS_READONLY_SPEC,
            operation="get_governance_preview",
            runtime_tool="invest_governance_preview",
            phase="governance_preview_read",
            phase_stats={
                "cutoff_date": cutoff_date or "",
                "stock_count": stock_count,
                "min_history_days": min_history_days,
                "allowed_manager_count": len(list(allowed_manager_ids or [])),
            },
        )

    def list_agent_prompts(self) -> dict[str, Any]:
        payload = list_agent_prompts_payload()
        items = list(payload.get("configs") or []) if isinstance(payload, dict) else []
        return self._attach_domain_operation_response(
            payload,
            spec=_CONFIG_READONLY_SPEC,
            operation="list_agent_prompts",
            runtime_tool="invest_agent_prompts_list",
            phase="agent_prompts_read",
            phase_stats={"count": len(items)},
        )

    def update_agent_prompt(
        self,
        *,
        agent_name: str,
        system_prompt: str,
    ) -> dict[str, Any]:
        payload = update_agent_prompt_payload(
            agent_name=agent_name,
            system_prompt=system_prompt,
        )
        return self._finalize_mutating_workflow(
            payload,
            spec=_CONFIG_MUTATING_SPEC,
            operation="update_agent_prompt",
            runtime_tool="invest_agent_prompts_update",
            phase="agent_prompt_write",
            phase_stats={
                "agent_name": agent_name,
                "prompt_length": len(system_prompt),
            },
            runtime_event="agent_prompt_updated",
            runtime_event_payload={"agent_name": agent_name},
        )

    def get_runtime_paths(self) -> dict[str, Any]:
        payload = get_runtime_paths_payload(self, project_root=PROJECT_ROOT)
        config_payload = (
            dict(payload.get("config") or {}) if isinstance(payload, dict) else {}
        )
        return self._attach_domain_operation_response(
            payload,
            spec=_CONFIG_READONLY_SPEC,
            operation="get_runtime_paths",
            runtime_tool="invest_runtime_paths_get",
            phase="runtime_paths_read",
            phase_stats={"path_count": len(config_payload)},
        )

    def update_runtime_paths(
        self,
        patch: dict[str, Any],
        *,
        confirm: bool = False,
    ) -> dict[str, Any]:
        if not confirm:
            return self._build_patch_confirmation_required_workflow(
                domain="config",
                operation="update_runtime_paths",
                runtime_tool="invest_runtime_paths_update",
                message="runtime paths 更新会立即改变运行期产物目录，请用 confirm=true 再执行。",
                patch=patch,
            )
        payload = update_runtime_paths_payload(
            patch=patch,
            runtime=self,
            project_root=PROJECT_ROOT,
            sync_runtime=sync_runtime_path_config_bootstrap,
        )
        return self._finalize_mutating_workflow(
            payload,
            spec=_CONFIG_MUTATING_SPEC,
            operation="update_runtime_paths",
            runtime_tool="invest_runtime_paths_update",
            phase="runtime_paths_write",
            phase_stats=self._build_confirmed_update_phase_stats(
                payload,
                confirmed=True,
            ),
            runtime_event="runtime_paths_updated",
            runtime_event_payload={"updated": payload.get("updated", [])},
        )

    def get_evolution_config(self) -> dict[str, Any]:
        payload = get_evolution_config_payload(
            project_root=PROJECT_ROOT,
            live_config=config,
        )
        config_payload = (
            dict(payload.get("config") or {}) if isinstance(payload, dict) else {}
        )
        return self._attach_domain_operation_response(
            payload,
            spec=_CONFIG_READONLY_SPEC,
            operation="get_evolution_config",
            runtime_tool="invest_evolution_config_get",
            phase="evolution_config_read",
            phase_stats={"config_key_count": len(config_payload)},
        )

    def update_evolution_config(
        self,
        patch: dict[str, Any],
        *,
        confirm: bool = False,
    ) -> dict[str, Any]:
        invalid_keys = invalid_evolution_config_patch_keys(patch)
        if invalid_keys:
            raise ValueError(
                "evolution_config 不接受 llm 相关 patch；请改用 /api/control_plane 管理 provider / model / api_key 绑定"
            )
        if not confirm and any(key in patch for key in HIGH_RISK_EVOLUTION_CONFIG_KEYS):
            return self._build_patch_confirmation_required_workflow(
                domain="config",
                operation="update_evolution_config",
                runtime_tool="invest_evolution_config_update",
                message="当前 patch 会影响训练主链路，请用 confirm=true 再执行。",
                patch=patch,
            )
        payload = _resolve_update_evolution_config_payload()(
            patch=patch,
            project_root=PROJECT_ROOT,
            live_config=config,
            source="commander",
        )
        controller = getattr(getattr(self, "body", None), "controller", None)
        if controller is not None and hasattr(
            controller, "refresh_runtime_from_config"
        ):
            controller.refresh_runtime_from_config()
        return self._finalize_mutating_workflow(
            payload,
            spec=_CONFIG_MUTATING_SPEC,
            operation="update_evolution_config",
            runtime_tool="invest_evolution_config_update",
            phase="evolution_config_write",
            phase_stats=self._build_confirmed_update_phase_stats(
                payload,
                confirmed=confirm,
            ),
            runtime_event="evolution_config_updated",
            runtime_event_payload={"updated": payload.get("updated", [])},
        )

    def get_control_plane(self) -> dict[str, Any]:
        payload = get_control_plane_payload(project_root=PROJECT_ROOT)
        config_payload = (
            dict(payload.get("config") or {}) if isinstance(payload, dict) else {}
        )
        return self._attach_domain_operation_response(
            payload,
            spec=_CONFIG_READONLY_SPEC,
            operation="get_control_plane",
            runtime_tool="invest_control_plane_get",
            phase="control_plane_read",
            phase_stats={"config_section_count": len(config_payload)},
        )

    def update_control_plane(
        self,
        patch: dict[str, Any],
        *,
        confirm: bool = False,
    ) -> dict[str, Any]:
        if not confirm:
            return self._build_patch_confirmation_required_workflow(
                domain="config",
                operation="update_control_plane",
                runtime_tool="invest_control_plane_update",
                message="control plane 更新需要重启才能全局生效，请用 confirm=true 再执行。",
                patch=patch,
                extra_payload={"restart_required": True},
                restart_required=True,
            )
        payload = update_control_plane_payload(
            patch=patch,
            project_root=PROJECT_ROOT,
            source="commander",
        )
        return self._finalize_mutating_workflow(
            payload,
            spec=_CONFIG_MUTATING_SPEC,
            operation="update_control_plane",
            runtime_tool="invest_control_plane_update",
            phase="control_plane_write",
            phase_stats=self._build_confirmed_update_phase_stats(
                payload,
                confirmed=confirm,
                restart_required=True,
            ),
            runtime_event="control_plane_updated",
            runtime_event_payload={"updated": payload.get("updated", [])},
        )

    def get_data_status(self, *, refresh: bool = False) -> dict[str, Any]:
        payload = get_data_status_payload(refresh=refresh)
        quality = (
            dict(payload.get("quality") or {}) if isinstance(payload, dict) else {}
        )
        return self._attach_domain_operation_response(
            payload,
            spec=_DATA_READONLY_SPEC,
            operation="get_data_status",
            runtime_tool="invest_data_status",
            phase="data_status_refresh" if refresh else "data_status_read",
            phase_stats={
                "requested_refresh": bool(refresh),
                "health_status": quality.get("health_status", "unknown"),
            },
        )

    def get_capital_flow(
        self,
        *,
        codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        payload = get_capital_flow_payload(
            codes=codes,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        return self._attach_domain_operation_response(
            payload,
            spec=_DATA_READONLY_SPEC,
            operation="get_capital_flow",
            runtime_tool="invest_data_capital_flow",
            phase="capital_flow_query",
            phase_stats={"count": int(payload.get("count", 0)), "limit": int(limit)},
        )

    def get_dragon_tiger(
        self,
        *,
        codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        payload = get_dragon_tiger_payload(
            codes=codes,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        return self._attach_domain_operation_response(
            payload,
            spec=_DATA_READONLY_SPEC,
            operation="get_dragon_tiger",
            runtime_tool="invest_data_dragon_tiger",
            phase="dragon_tiger_query",
            phase_stats={"count": int(payload.get("count", 0)), "limit": int(limit)},
        )

    def get_intraday_60m(
        self,
        *,
        codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        payload = get_intraday_60m_payload(
            codes=codes,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        return self._attach_domain_operation_response(
            payload,
            spec=_DATA_READONLY_SPEC,
            operation="get_intraday_60m",
            runtime_tool="invest_data_intraday_60m",
            phase="intraday_60m_query",
            phase_stats={"count": int(payload.get("count", 0)), "limit": int(limit)},
        )

    def get_data_download_status(self) -> dict[str, Any]:
        payload = get_data_download_status_payload()
        return self._attach_domain_operation_response(
            payload,
            spec=_DATA_READONLY_SPEC,
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
        return self._finalize_mutating_workflow(
            payload,
            spec=_DATA_MUTATING_SPEC,
            operation="trigger_data_download",
            runtime_tool="invest_data_download",
            phase="download_job_trigger",
            phase_stats={
                "job_status": str(payload.get("status", "unknown")),
                "confirmed": True,
            },
            runtime_event="data_download_triggered",
        )

    def list_memory(self, *, query: str = "", limit: int = 20) -> dict[str, Any]:
        rows = self.memory.search(query=query, limit=limit)
        items = [memory_brief_row(row) for row in rows]
        return self._attach_domain_operation_response(
            {"count": len(items), "items": items},
            spec=_MEMORY_READONLY_SPEC,
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
        return self._attach_domain_operation_response(
            payload,
            spec=_MEMORY_READONLY_SPEC,
            operation="get_memory_detail",
            runtime_tool="invest_memory_get",
            phase="memory_detail_read",
            phase_stats={"record_id": str(record_id)},
        )

    def get_events_tail(self, *, limit: int = 50) -> dict[str, Any]:
        return self._attach_projected_readonly_bundle(
            build_events_tail_response_bundle(
                self,
                limit=limit,
            )
        )

    def get_events_summary(self, *, limit: int = 100) -> dict[str, Any]:
        return self._attach_projected_readonly_bundle(
            build_events_summary_response_bundle(
                self,
                limit=limit,
                ok_status=STATUS_OK,
            )
        )

    def get_runtime_diagnostics(
        self,
        *,
        event_limit: int = 50,
        memory_limit: int = 20,
    ) -> dict[str, Any]:
        return self._attach_projected_readonly_bundle(
            build_runtime_diagnostics_response_bundle(
                self,
                event_limit=event_limit,
                memory_limit=memory_limit,
            )
        )

    def get_training_lab_summary(self, *, limit: int = 5) -> dict[str, Any]:
        return self._attach_projected_readonly_bundle(
            build_training_lab_summary_response_bundle(
                self,
                limit=limit,
                ok_status=STATUS_OK,
            )
        )

    def list_research_cases(
        self,
        *,
        limit: int = 20,
        policy_id: str = "",
        symbol: str = "",
        as_of_date: str = "",
        horizon: str = "",
    ) -> dict[str, Any]:
        payload = get_research_cases_payload(
            case_store=self.research_case_store,
            limit=limit,
            policy_id=policy_id,
            symbol=symbol,
            as_of_date=as_of_date,
            horizon=horizon,
        )
        return self._attach_domain_operation_response(
            payload,
            spec=_RESEARCH_READONLY_SPEC,
            operation="list_research_cases",
            runtime_tool="invest_research_cases",
            phase="research_cases_read",
            extra_phases=("research_calibration_read",),
            phase_stats={
                "limit": int(limit),
                "policy_id": str(policy_id or ""),
                "symbol": str(symbol or ""),
                "as_of_date": str(as_of_date or ""),
                "horizon": str(horizon or ""),
                "count": int(payload.get("count", 0) or 0),
            },
        )

    def list_research_attributions(self, *, limit: int = 20) -> dict[str, Any]:
        payload = get_research_attributions_payload(
            case_store=self.research_case_store,
            limit=limit,
        )
        return self._attach_domain_operation_response(
            payload,
            spec=_RESEARCH_READONLY_SPEC,
            operation="list_research_attributions",
            runtime_tool="invest_research_attributions",
            phase="research_attributions_read",
            phase_stats={
                "limit": int(limit),
                "count": int(payload.get("count", 0) or 0),
            },
        )

    def get_research_calibration(self, *, policy_id: str = "") -> dict[str, Any]:
        payload = get_research_calibration_payload(
            case_store=self.research_case_store,
            policy_id=policy_id,
        )
        return self._attach_domain_operation_response(
            payload,
            spec=_RESEARCH_READONLY_SPEC,
            operation="get_research_calibration",
            runtime_tool="invest_research_calibration",
            phase="research_calibration_read",
            phase_stats={
                "policy_id": str(policy_id or ""),
                "sample_count": int(
                    dict(payload.get("report") or {}).get("sample_count") or 0
                ),
            },
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
        payload = {"status": "ok", "items": self.stock_analysis.list_strategies()}
        return self._attach_domain_readonly_workflow(
            payload,
            domain="strategy",
            operation="list_stock_strategies",
            runtime_tool="invest_stock_strategies",
            phase="stock_strategy_inventory_read",
            phase_stats={"count": len(list(payload.get("items") or []))},
        )
