"""Research bridge orchestration for stock analysis outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from invest_evolution.application.training.execution import (
    build_manager_runtime,
    controller_default_manager_config_ref,
    controller_default_manager_id,
    normalize_path_ref,
    resolve_manager_config_ref,
)
from invest_evolution.application.training.policy import TrainingGovernanceService
from invest_evolution.config import OUTPUT_DIR, config
from invest_evolution.investment.contracts import GovernanceDecision
from invest_evolution.investment.research import (
    build_dashboard_projection,
    build_research_snapshot,
    resolve_policy_snapshot,
)
from invest_evolution.market_data import DataManager

_STOCK_TOOL_EXECUTION_EXCEPTIONS = (
    RuntimeError,
    ValueError,
    TypeError,
    LookupError,
    OSError,
)

_STOCK_RESEARCH_BRIDGE_EXCEPTIONS = _STOCK_TOOL_EXECUTION_EXCEPTIONS + (ImportError,)


@dataclass(frozen=True)
class ResearchBridgeRuntimeContext:
    normalized_requested_as_of_date: str
    replay_mode: bool
    current_manager_id: str
    base_config_path: str
    current_params: dict[str, Any]
    stock_count: int
    min_history_days: int
    lookback_days: int
    parameter_source: str

    def build_data_lineage(
        self,
        *,
        repository_db_path: Path,
        data_manager: DataManager,
        effective_as_of_date: str,
        stock_data: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "db_path": str(repository_db_path),
            "requested_as_of_date": self.normalized_requested_as_of_date,
            "effective_as_of_date": effective_as_of_date,
            "data_source": str(
                getattr(data_manager, "last_source", "unknown") or "unknown"
            ),
            "data_resolution": dict(getattr(data_manager, "last_resolution", {}) or {}),
            "stock_count": len(stock_data),
            "min_history_days": self.min_history_days,
            "lookback_days": self.lookback_days,
        }

    def build_policy_data_window(
        self,
        *,
        effective_as_of_date: str,
        stock_data: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "as_of_date": effective_as_of_date,
            "lookback_days": self.lookback_days,
            "simulation_days": int(getattr(config, "simulation_days", 30) or 30),
            "universe_definition": (
                f"stock_count={self.stock_count}|min_history_days={self.min_history_days}"
            ),
            "stock_universe_size": len(stock_data),
        }

    def build_policy_metadata(
        self,
        *,
        controller: Any,
        effective_as_of_date: str,
    ) -> dict[str, Any]:
        return {
            "parameter_source": self.parameter_source,
            "controller_bound": bool(controller is not None),
            "replay_mode": self.replay_mode,
            "requested_as_of_date": self.normalized_requested_as_of_date,
            "effective_as_of_date": effective_as_of_date,
        }


@dataclass(frozen=True)
class ResearchBridgeRuntimeSelection:
    dominant_manager_id: str
    selected_config: str
    runtime_overrides: dict[str, Any]


@dataclass(frozen=True)
class ResearchBridgeDataBundle:
    data_manager: DataManager
    stock_data: dict[str, Any]


@dataclass(frozen=True)
class ResearchBridgeStageResult:
    bundle: Any | None = None
    unavailable: dict[str, Any] | None = None

    @classmethod
    def ok(cls, bundle: Any) -> "ResearchBridgeStageResult":
        return cls(bundle=bundle, unavailable=None)

    @classmethod
    def unavailable_result(
        cls, payload: dict[str, Any]
    ) -> "ResearchBridgeStageResult":
        return cls(bundle=None, unavailable=dict(payload))


@dataclass(frozen=True)
class ResearchBridgeGovernanceBundle:
    decision: GovernanceDecision
    allowed_manager_ids: list[str]
    governance_enabled: bool
    governance_mode: str


@dataclass(frozen=True)
class ResearchBridgeManagerExecution:
    runtime_selection: ResearchBridgeRuntimeSelection
    manager_runtime: Any
    manager_output: Any


@dataclass(frozen=True)
class ResearchBridgeAssemblyBundle:
    controller: Any
    runtime_context: ResearchBridgeRuntimeContext
    data_bundle: ResearchBridgeDataBundle
    governance_bundle: ResearchBridgeGovernanceBundle
    manager_execution: ResearchBridgeManagerExecution


@dataclass(frozen=True)
class ResearchBridgeOutputBundle:
    governance_context: dict[str, Any]
    data_lineage: dict[str, Any]
    snapshot: Any
    policy: Any


class StockAnalysisResearchBridgeService:
    def __init__(
        self,
        *,
        repository: Any,
        controller_provider: Callable[[], Any] | None,
        research_resolution_service: Any,
        governance_service: TrainingGovernanceService,
        normalize_as_of_date: Callable[[str | None], str],
        resolve_effective_as_of_date: Callable[[str, str], str],
        logger_instance: Any,
    ) -> None:
        self.repository = repository
        self._controller_provider = controller_provider
        self.research_resolution_service = research_resolution_service
        self.governance_service = governance_service
        self.normalize_as_of_date = normalize_as_of_date
        self.resolve_effective_as_of_date = resolve_effective_as_of_date
        self._logger = logger_instance

    def resolve_outputs(
        self,
        *,
        question: str,
        query: str,
        strategy: Any,
        strategy_source: str,
        code: str,
        security: dict[str, Any],
        requested_as_of_date: str,
        effective_as_of_date: str,
        days: int,
        execution: dict[str, Any],
        derived: dict[str, Any],
        dashboard_projection_builder: Callable[
            ..., dict[str, Any]
        ] = build_dashboard_projection,
    ) -> dict[str, Any]:
        research_bridge = self.build_research_bridge(
            code=code,
            security=security,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            days=days,
            derived=derived,
        )
        return self.research_resolution_service.resolve_outputs(
            research_bridge=research_bridge,
            question=question,
            query=query,
            strategy=strategy,
            strategy_source=strategy_source,
            code=code,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            execution=execution,
            derived=derived,
            dashboard_projection_builder=dashboard_projection_builder,
        )

    def _resolve_live_controller(self) -> Any | None:
        if self._controller_provider is None:
            return None
        try:
            return self._controller_provider()
        except _STOCK_RESEARCH_BRIDGE_EXCEPTIONS:
            self._logger.warning(
                "Failed to resolve live controller for ask_stock research bridge",
                exc_info=True,
            )
            return None

    def _ensure_query_in_stock_data(
        self,
        *,
        stock_data: dict[str, Any],
        code: str,
        cutoff_date: str,
    ) -> dict[str, Any]:
        enriched = dict(stock_data or {})
        if code in enriched:
            return enriched
        query_frame = self.repository.get_stock(code, cutoff_date=cutoff_date)
        if query_frame.empty:
            return enriched
        query_frame = query_frame.copy()
        if "trade_date" in query_frame.columns:
            query_frame["trade_date"] = query_frame["trade_date"].astype(str)
            query_frame = query_frame.sort_values("trade_date").reset_index(drop=True)
        enriched[code] = query_frame
        return enriched

    def _build_unavailable_bridge(
        self,
        *,
        stage: str,
        error: str,
        effective_as_of_date: str,
        parameter_source: str = "",
        **details: Any,
    ) -> dict[str, Any]:
        detail_payload: dict[str, Any] = {
            "stage": stage,
            "as_of_date": effective_as_of_date,
        }
        if parameter_source:
            detail_payload["parameter_source"] = parameter_source
        detail_payload.update(details)
        return {
            "status": "unavailable",
            "error": error,
            "details": detail_payload,
        }

    def _resolve_bridge_stage(
        self,
        *,
        result: ResearchBridgeStageResult,
        stage: str,
        error: str,
        effective_as_of_date: str,
        parameter_source: str,
    ) -> Any | dict[str, Any]:
        if result.unavailable is not None:
            return result.unavailable
        if result.bundle is not None:
            return result.bundle
        return self._build_unavailable_bridge(
            stage=stage,
            error=error,
            effective_as_of_date=effective_as_of_date,
            parameter_source=parameter_source,
        )

    def _resolve_bridge_runtime_context(
        self,
        *,
        controller: Any,
        code: str,
        requested_as_of_date: str,
        effective_as_of_date: str,
        days: int,
    ) -> ResearchBridgeRuntimeContext:
        normalized_requested_as_of_date = self.normalize_as_of_date(
            requested_as_of_date
        )
        latest_live_date = self.resolve_effective_as_of_date(code, "")
        replay_mode = (
            bool(normalized_requested_as_of_date)
            and bool(latest_live_date)
            and str(effective_as_of_date) < str(latest_live_date)
        )
        current_manager_id = str(
            controller_default_manager_id(
                controller,
                default=str(
                    getattr(config, "default_manager_id", "momentum") or "momentum"
                ),
            )
            or "momentum"
        )
        fallback_config_path = resolve_manager_config_ref(
            current_manager_id,
            getattr(config, "default_manager_config_ref", ""),
        )
        base_config_path = str(
            controller_default_manager_config_ref(controller)
            or fallback_config_path
            or ""
        )
        query_history_frame = self.repository.get_stock(
            code, cutoff_date=effective_as_of_date
        )
        query_history_days = int(len(query_history_frame))
        return ResearchBridgeRuntimeContext(
            normalized_requested_as_of_date=normalized_requested_as_of_date,
            replay_mode=replay_mode,
            current_manager_id=current_manager_id,
            base_config_path=normalize_path_ref(base_config_path)
            or fallback_config_path,
            current_params={}
            if replay_mode
            else dict(getattr(controller, "current_params", {}) or {}),
            stock_count=max(10, int(getattr(config, "max_stocks", 50) or 50)),
            min_history_days=max(
                30,
                min(
                    60,
                    query_history_days if query_history_days > 0 else int(days or 60),
                ),
            ),
            lookback_days=max(60, int(days or 60)),
            parameter_source=(
                "config_default_replay_safe"
                if replay_mode
                else "live_controller"
                if controller is not None
                else "config_default"
            ),
        )

    def _load_bridge_stock_data(
        self,
        *,
        code: str,
        effective_as_of_date: str,
        stock_count: int,
        min_history_days: int,
        parameter_source: str,
    ) -> ResearchBridgeStageResult:
        data_manager = DataManager(
            db_path=str(self.repository.db_path),
            prefer_offline=True,
            allow_mock_fallback=False,
        )
        try:
            stock_data = data_manager.load_stock_data(
                cutoff_date=effective_as_of_date,
                stock_count=stock_count,
                min_history_days=min_history_days,
                include_capital_flow=False,
            )
        except _STOCK_RESEARCH_BRIDGE_EXCEPTIONS as exc:
            self._logger.warning(
                "Research bridge data load failed for %s", code, exc_info=True
            )
            return ResearchBridgeStageResult.unavailable_result(
                self._build_unavailable_bridge(
                    stage="load_stock_data",
                    error=str(exc),
                    effective_as_of_date=effective_as_of_date,
                    parameter_source=parameter_source,
                )
            )
        stock_data = self._ensure_query_in_stock_data(
            stock_data=stock_data,
            code=code,
            cutoff_date=effective_as_of_date,
        )
        if not stock_data:
            return ResearchBridgeStageResult.unavailable_result(
                self._build_unavailable_bridge(
                    stage="empty_universe",
                    error="research bridge returned empty stock universe",
                    effective_as_of_date=effective_as_of_date,
                    parameter_source=parameter_source,
                )
            )
        return ResearchBridgeStageResult.ok(
            ResearchBridgeDataBundle(
                data_manager=data_manager,
                stock_data=stock_data,
            )
        )

    def _resolve_allowed_manager_ids(self, controller: Any) -> list[str]:
        return [
            str(item).strip()
            for item in (
                getattr(controller, "experiment_allowed_manager_ids", None)
                or getattr(controller, "governance_allowed_manager_ids", None)
                or getattr(config, "governance_allowed_manager_ids", None)
                or []
            )
            if str(item).strip()
        ]

    def _resolve_governance_settings(
        self, controller: Any
    ) -> tuple[list[str], bool, str]:
        allowed_manager_ids = self._resolve_allowed_manager_ids(controller)
        governance_enabled = bool(
            getattr(
                controller,
                "governance_enabled",
                getattr(config, "governance_enabled", True),
            )
        )
        governance_mode = (
            str(
                getattr(
                    controller,
                    "governance_mode",
                    getattr(config, "governance_mode", "rule"),
                )
                or "rule"
            )
            .strip()
            .lower()
        )
        return allowed_manager_ids, governance_enabled, governance_mode

    def _resolve_governance_bundle(
        self,
        *,
        controller: Any,
        data_bundle: ResearchBridgeDataBundle,
        effective_as_of_date: str,
        runtime_context: ResearchBridgeRuntimeContext,
        code: str,
    ) -> ResearchBridgeStageResult:
        allowed_manager_ids, governance_enabled, governance_mode = (
            self._resolve_governance_settings(controller)
        )
        decision, unavailable = self._resolve_governance_decision(
            controller=controller,
            stock_data=data_bundle.stock_data,
            effective_as_of_date=effective_as_of_date,
            current_manager_id=runtime_context.current_manager_id,
            data_manager=data_bundle.data_manager,
            allowed_manager_ids=allowed_manager_ids,
            parameter_source=runtime_context.parameter_source,
            code=code,
        )
        if unavailable is not None:
            return ResearchBridgeStageResult.unavailable_result(unavailable)
        if decision is None:
            return ResearchBridgeStageResult.unavailable_result(
                self._build_unavailable_bridge(
                    stage="governance",
                    error="governance decision unavailable",
                    effective_as_of_date=effective_as_of_date,
                    parameter_source=runtime_context.parameter_source,
                )
            )
        return ResearchBridgeStageResult.ok(
            ResearchBridgeGovernanceBundle(
                decision=decision,
                allowed_manager_ids=allowed_manager_ids,
                governance_enabled=governance_enabled,
                governance_mode=governance_mode,
            )
        )

    def _resolve_governance_decision(
        self,
        *,
        controller: Any,
        stock_data: dict[str, Any],
        effective_as_of_date: str,
        current_manager_id: str,
        data_manager: DataManager,
        allowed_manager_ids: list[str],
        parameter_source: str,
        code: str,
    ) -> tuple[GovernanceDecision | None, dict[str, Any] | None]:
        try:
            decision = self.governance_service.decide_governance(
                controller,
                stock_data=stock_data,
                cutoff_date=effective_as_of_date,
                current_manager_id=current_manager_id,
                data_manager=data_manager,
                output_dir=getattr(controller, "output_dir", OUTPUT_DIR),
                allowed_manager_ids=allowed_manager_ids or None,
                current_cycle_id=getattr(controller, "current_cycle_id", None),
                safe_leaderboard_refresh=True,
            )
        except _STOCK_RESEARCH_BRIDGE_EXCEPTIONS as exc:
            self._logger.warning(
                "Research bridge governance failed for %s", code, exc_info=True
            )
            return None, self._build_unavailable_bridge(
                stage="governance",
                error=str(exc),
                effective_as_of_date=effective_as_of_date,
                parameter_source=parameter_source,
            )
        return decision, None

    @staticmethod
    def _resolve_runtime_selection(
        *,
        decision: GovernanceDecision,
        current_manager_id: str,
        base_config_path: str,
        current_params: dict[str, Any],
        replay_mode: bool,
    ) -> ResearchBridgeRuntimeSelection:
        dominant_manager_id = str(
            getattr(decision, "dominant_manager_id", "")
            or current_manager_id
            or "momentum"
        )
        allocation_plan = dict(getattr(decision, "allocation_plan", {}) or {})
        decision_metadata = dict(getattr(decision, "metadata", {}) or {})
        selected_config = str(
            dict(allocation_plan.get("selected_manager_config_refs") or {}).get(
                dominant_manager_id
            )
            or decision_metadata.get("dominant_manager_config")
            or resolve_manager_config_ref(dominant_manager_id)
        )
        selected_config = normalize_path_ref(selected_config) or str(selected_config)
        runtime_overrides = (
            current_params
            if (
                not replay_mode
                and dominant_manager_id == current_manager_id
                and selected_config == base_config_path
            )
            else {}
        )
        return ResearchBridgeRuntimeSelection(
            dominant_manager_id=dominant_manager_id,
            selected_config=selected_config,
            runtime_overrides=runtime_overrides,
        )

    def _build_governance_context(
        self,
        *,
        decision: GovernanceDecision,
        effective_as_of_date: str,
        requested_as_of_date: str,
        governance_mode: str,
        governance_enabled: bool,
        dominant_manager_id: str,
        allowed_manager_ids: list[str],
    ) -> dict[str, Any]:
        return {
            "as_of_date": effective_as_of_date,
            "requested_as_of_date": requested_as_of_date,
            "governance_mode": governance_mode if governance_enabled else "off",
            "dominant_manager_id": dominant_manager_id,
            "active_manager_ids": list(
                getattr(decision, "active_manager_ids", []) or [dominant_manager_id]
            ),
            "manager_budget_weights": dict(
                getattr(decision, "manager_budget_weights", {}) or {}
            ),
            "portfolio_constraints": dict(
                getattr(decision, "portfolio_constraints", {}) or {}
            ),
            "decision_source": str(getattr(decision, "decision_source", "") or ""),
            "regime": str(getattr(decision, "regime", "") or "unknown"),
            "regime_confidence": float(
                getattr(decision, "regime_confidence", 0.0) or 0.0
            ),
            "decision_confidence": float(
                getattr(decision, "decision_confidence", 0.0) or 0.0
            ),
            "allowed_manager_ids": allowed_manager_ids or [dominant_manager_id],
            "cash_reserve_hint": float(
                getattr(decision, "cash_reserve_hint", 0.0) or 0.0
            ),
        }

    def _execute_manager_bridge(
        self,
        *,
        code: str,
        effective_as_of_date: str,
        runtime_context: ResearchBridgeRuntimeContext,
        data_bundle: ResearchBridgeDataBundle,
        governance_bundle: ResearchBridgeGovernanceBundle,
    ) -> ResearchBridgeStageResult:
        runtime_selection = self._resolve_runtime_selection(
            decision=governance_bundle.decision,
            current_manager_id=runtime_context.current_manager_id,
            base_config_path=runtime_context.base_config_path,
            current_params=dict(runtime_context.current_params),
            replay_mode=runtime_context.replay_mode,
        )
        try:
            manager_runtime = build_manager_runtime(
                manager_id=runtime_selection.dominant_manager_id,
                manager_config_ref=runtime_selection.selected_config,
                runtime_overrides=dict(runtime_selection.runtime_overrides),
            )
            manager_output = manager_runtime.process(
                data_bundle.stock_data,
                effective_as_of_date,
            )
        except _STOCK_RESEARCH_BRIDGE_EXCEPTIONS as exc:
            self._logger.warning(
                "Research bridge model execution failed for %s", code, exc_info=True
            )
            return ResearchBridgeStageResult.unavailable_result(
                self._build_unavailable_bridge(
                    stage="model_process",
                    error=str(exc),
                    effective_as_of_date=effective_as_of_date,
                    dominant_manager_id=runtime_selection.dominant_manager_id,
                    selected_config=runtime_selection.selected_config,
                )
            )
        return ResearchBridgeStageResult.ok(
            ResearchBridgeManagerExecution(
                runtime_selection=runtime_selection,
                manager_runtime=manager_runtime,
                manager_output=manager_output,
            )
        )

    def _run_bridge_output_stage(
        self,
        *,
        controller: Any,
        security: dict[str, Any],
        code: str,
        effective_as_of_date: str,
        derived: dict[str, Any],
        runtime_context: ResearchBridgeRuntimeContext,
        data_bundle: ResearchBridgeDataBundle,
        governance_bundle: ResearchBridgeGovernanceBundle,
        manager_execution: ResearchBridgeManagerExecution,
    ) -> ResearchBridgeOutputBundle:
        governance_context = self._build_governance_context(
            decision=governance_bundle.decision,
            effective_as_of_date=effective_as_of_date,
            requested_as_of_date=runtime_context.normalized_requested_as_of_date,
            governance_mode=governance_bundle.governance_mode,
            governance_enabled=governance_bundle.governance_enabled,
            dominant_manager_id=manager_execution.runtime_selection.dominant_manager_id,
            allowed_manager_ids=governance_bundle.allowed_manager_ids,
        )
        data_lineage = runtime_context.build_data_lineage(
            repository_db_path=self.repository.db_path,
            data_manager=data_bundle.data_manager,
            effective_as_of_date=effective_as_of_date,
            stock_data=data_bundle.stock_data,
        )
        snapshot = build_research_snapshot(
            manager_output=manager_execution.manager_output,
            security=security,
            query_code=code,
            stock_data=data_bundle.stock_data,
            governance_context=governance_context,
            data_lineage=data_lineage,
            derived_signals=derived,
        )
        policy = resolve_policy_snapshot(
            manager_runtime=manager_execution.manager_runtime,
            manager_id=manager_execution.runtime_selection.dominant_manager_id,
            governance_context=governance_context,
            data_window=runtime_context.build_policy_data_window(
                effective_as_of_date=effective_as_of_date,
                stock_data=data_bundle.stock_data,
            ),
            metadata=runtime_context.build_policy_metadata(
                controller=controller,
                effective_as_of_date=effective_as_of_date,
            ),
        )
        return ResearchBridgeOutputBundle(
            governance_context=governance_context,
            data_lineage=data_lineage,
            snapshot=snapshot,
            policy=policy,
        )

    def _resolve_bridge_assembly(
        self,
        *,
        controller: Any,
        code: str,
        effective_as_of_date: str,
        runtime_context: ResearchBridgeRuntimeContext,
    ) -> ResearchBridgeAssemblyBundle | dict[str, Any]:
        data_bundle_result = self._resolve_bridge_stage(
            result=self._load_bridge_stock_data(
                code=code,
                effective_as_of_date=effective_as_of_date,
                stock_count=runtime_context.stock_count,
                min_history_days=runtime_context.min_history_days,
                parameter_source=runtime_context.parameter_source,
            ),
            stage="load_stock_data",
            error="research bridge data bundle unavailable",
            effective_as_of_date=effective_as_of_date,
            parameter_source=runtime_context.parameter_source,
        )
        if isinstance(data_bundle_result, dict):
            return data_bundle_result
        governance_bundle_result = self._resolve_bridge_stage(
            result=self._resolve_governance_bundle(
                controller=controller,
                data_bundle=data_bundle_result,
                effective_as_of_date=effective_as_of_date,
                runtime_context=runtime_context,
                code=code,
            ),
            stage="governance",
            error="governance decision unavailable",
            effective_as_of_date=effective_as_of_date,
            parameter_source=runtime_context.parameter_source,
        )
        if isinstance(governance_bundle_result, dict):
            return governance_bundle_result
        manager_execution_result = self._resolve_bridge_stage(
            result=self._execute_manager_bridge(
                code=code,
                effective_as_of_date=effective_as_of_date,
                runtime_context=runtime_context,
                data_bundle=data_bundle_result,
                governance_bundle=governance_bundle_result,
            ),
            stage="model_process",
            error="research bridge manager execution unavailable",
            effective_as_of_date=effective_as_of_date,
            parameter_source=runtime_context.parameter_source,
        )
        if isinstance(manager_execution_result, dict):
            return manager_execution_result
        return ResearchBridgeAssemblyBundle(
            controller=controller,
            runtime_context=runtime_context,
            data_bundle=data_bundle_result,
            governance_bundle=governance_bundle_result,
            manager_execution=manager_execution_result,
        )

    @staticmethod
    def _run_bridge_finalize_stage(
        *,
        controller: Any,
        runtime_context: ResearchBridgeRuntimeContext,
        governance_bundle: ResearchBridgeGovernanceBundle,
        manager_execution: ResearchBridgeManagerExecution,
        bridge_outputs: ResearchBridgeOutputBundle,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "controller_bound": bool(controller is not None),
            "replay_mode": runtime_context.replay_mode,
            "parameter_source": runtime_context.parameter_source,
            "governance_decision": governance_bundle.decision.to_dict(),
            "manager_output": manager_execution.manager_output,
            "snapshot": bridge_outputs.snapshot,
            "policy": bridge_outputs.policy,
        }

    def build_research_bridge(
        self,
        *,
        code: str,
        security: dict[str, Any],
        requested_as_of_date: str,
        effective_as_of_date: str,
        days: int,
        derived: dict[str, Any],
    ) -> dict[str, Any]:
        controller = self._resolve_live_controller()
        runtime_context = self._resolve_bridge_runtime_context(
            controller=controller,
            code=code,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            days=days,
        )
        assembly = self._resolve_bridge_assembly(
            controller=controller,
            code=code,
            effective_as_of_date=effective_as_of_date,
            runtime_context=runtime_context,
        )
        if isinstance(assembly, dict):
            return assembly
        bridge_outputs = self._run_bridge_output_stage(
            controller=assembly.controller,
            security=security,
            code=code,
            effective_as_of_date=effective_as_of_date,
            derived=derived,
            runtime_context=assembly.runtime_context,
            data_bundle=assembly.data_bundle,
            governance_bundle=assembly.governance_bundle,
            manager_execution=assembly.manager_execution,
        )
        return self._run_bridge_finalize_stage(
            controller=assembly.controller,
            runtime_context=assembly.runtime_context,
            governance_bundle=assembly.governance_bundle,
            manager_execution=assembly.manager_execution,
            bridge_outputs=bridge_outputs,
        )

