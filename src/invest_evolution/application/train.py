"""Canonical training facade and CLI entrypoint."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, cast
from uuid import uuid4

from invest_evolution.config import (
    PROJECT_ROOT,
    config,
)
from invest_evolution.config.control_plane import RuntimePathConfigService
from invest_evolution.investment.shared.policy import (
    evaluate_optimization_event_contract,
    resolve_governance_matrix,
)
from invest_evolution.market_data import DataSourceUnavailableError
from invest_evolution.investment.runtimes.catalog import COMMON_PARAM_DEFAULTS
from invest_evolution.application.training.bootstrap import (
    initialize_agents_and_runtime_support,
    initialize_callbacks,
    initialize_config_service,
    initialize_core_runtime,
    initialize_llm_runtime,
    initialize_model_runtime,
    initialize_output_runtime,
    initialize_training_services,
    initialize_training_state,
)
from invest_evolution.application.training.bootstrap import (
    build_default_training_diagnostics as _default_training_diagnostics,
)
from invest_evolution.application.training.bootstrap import (
    build_mock_provider as _build_mock_provider,
)
from invest_evolution.application.training.policy import execution_defaults_payload, normalize_governance_decision
from invest_evolution.application.training.execution import (
    trigger_loss_optimization,
)
from invest_evolution.application.training import review_contracts as training_review_contracts
from invest_evolution.application.training.policy import (
    TrainingPolicyService,
)
from invest_evolution.application.training import observability as training_observability
from invest_evolution.application.training.research import (
    TrainingFeedbackService,
)
from invest_evolution.application.training.controller import (
    session_consecutive_losses,
    session_current_params,
    session_cycle_history,
    session_cycle_records,
    session_default_manager_config_ref,
    session_default_manager_id,
    session_last_feedback_optimization,
    session_last_feedback_optimization_cycle_id,
    session_last_governance_decision,
    session_manager_budget_weights,
    set_session_consecutive_losses,
    set_session_current_params,
    set_session_cycle_history,
    set_session_cycle_records,
    set_session_default_manager,
    set_session_last_feedback_optimization,
    set_session_last_feedback_optimization_cycle_id,
    set_session_last_governance_decision,
    set_session_manager_budget_weights,
)

logger = logging.getLogger(__name__)

SelfAssessmentSnapshot = training_observability.SelfAssessmentSnapshot
_event_callback_state = training_observability._event_callback_state
emit_event = training_observability.emit_event
set_event_callback = training_observability.set_event_callback


def _empty_review_applied_effects_payload() -> training_review_contracts.ReviewAppliedEffectsPayload:
    return {}


def _empty_review_decision_stage_payload() -> training_review_contracts.ReviewDecisionOptimizationStagePayload:
    return {}


def _empty_research_feedback_stage_payload() -> training_review_contracts.ResearchFeedbackOptimizationStagePayload:
    return {}


def _empty_llm_analysis_stage_payload() -> training_review_contracts.LlmAnalysisOptimizationStagePayload:
    return {}


def _empty_evolution_engine_stage_payload() -> training_review_contracts.EvolutionEngineOptimizationStagePayload:
    return {}


def _empty_runtime_config_mutation_stage_payload() -> (
    training_review_contracts.RuntimeConfigMutationOptimizationStagePayload
):
    return {}


def _empty_runtime_config_mutation_skipped_stage_payload() -> (
    training_review_contracts.RuntimeConfigMutationSkippedOptimizationStagePayload
):
    return {}


def _empty_optimization_error_stage_payload() -> training_review_contracts.OptimizationErrorStagePayload:
    return {}


# ============================================================
# Part 1: 遗传算法
# ============================================================

# ============================================================
# Part 2: Q-Learning 参数调整器（轻量级 RL）
# ============================================================

class ReinforcementLearningOptimizer:
    """
    简单 Q-Learning 参数调整器

    状态空间：连续亏损次数 / 收益率段
    动作空间：increase_position / decrease_position / keep
    """

    def __init__(self, learning_rate: float = 0.10, discount_factor: float = 0.90):
        self.lr   = learning_rate
        self.gamma = discount_factor
        self.q_table: Dict[str, Dict[str, float]] = {}

    def get_action(self, state: str, params: Dict) -> Dict:
        """选择动作并返回调整后的参数"""
        if state not in self.q_table:
            self.q_table[state] = {"increase": 0.0, "decrease": 0.0, "keep": 0.0}

        action = max(self.q_table[state].items(), key=lambda item: item[1])[0]
        new_params = params.copy()

        if action == "increase":
            new_params["position_size"]   = min(new_params.get("position_size", COMMON_PARAM_DEFAULTS["position_size"]) * 1.1, 0.5)
            new_params["take_profit_pct"] = min(new_params.get("take_profit_pct", COMMON_PARAM_DEFAULTS["take_profit_pct"]) * 1.1, 0.5)
        elif action == "decrease":
            new_params["position_size"]   = max(new_params.get("position_size", COMMON_PARAM_DEFAULTS["position_size"]) * 0.9, 0.05)
            new_params["take_profit_pct"] = max(new_params.get("take_profit_pct", COMMON_PARAM_DEFAULTS["take_profit_pct"]) * 0.9, 0.05)

        return new_params

    def update(self, state: str, action: str, reward: float, next_state: str):
        """更新 Q 值"""
        for s in (state, next_state):
            if s not in self.q_table:
                self.q_table[s] = {"increase": 0.0, "decrease": 0.0, "keep": 0.0}

        old_q      = self.q_table[state][action]
        max_next_q = max(self.q_table[next_state].values())
        self.q_table[state][action] = old_q + self.lr * (reward + self.gamma * max_next_q - old_q)


# ============================================================
# Part 3: 训练数据类
# ============================================================


@dataclass
class TrainingResult:
    """单次训练周期结果"""
    cycle_id: int
    cutoff_date: str
    selected_stocks: List[str]
    initial_capital: float
    final_value: float
    return_pct: float
    is_profit: bool
    trade_history: List[Dict]
    params: Dict
    analysis: str = ""
    data_mode: str = "unknown"
    requested_data_mode: str = "live"
    effective_data_mode: str = "unknown"
    llm_mode: str = "live"
    degraded: bool = False
    degrade_reason: str = ""
    selection_mode: str = "unknown"
    agent_used: bool = False
    llm_used: bool = False
    benchmark_passed: bool = False
    strategy_scores: (
        training_review_contracts.StrategyScoresPayload
        | training_review_contracts.StrategyScoresInputPayload
    ) = field(
        default_factory=lambda: cast(
            training_review_contracts.StrategyScoresInputPayload,
            {},
        )
    )
    review_applied: bool = False
    config_snapshot_path: str = ""
    optimization_events: List[training_review_contracts.OptimizationEventPayload | dict[str, Any]] = field(default_factory=list)
    audit_tags: Dict[str, Any] = field(default_factory=dict)
    execution_defaults: training_review_contracts.ExecutionDefaultsPayload | dict[str, Any] = field(default_factory=dict)
    governance_decision: (
        training_review_contracts.GovernanceDecisionPayload
        | training_review_contracts.GovernanceDecisionInputPayload
    ) = field(
        default_factory=lambda: cast(
            training_review_contracts.GovernanceDecisionInputPayload,
            {},
        )
    )
    research_feedback: training_review_contracts.ResearchFeedbackPayload | dict[str, Any] = field(default_factory=dict)
    research_artifacts: Dict[str, Any] = field(default_factory=dict)
    ab_comparison: Dict[str, Any] = field(default_factory=dict)
    experiment_spec: Dict[str, Any] = field(default_factory=dict)
    execution_snapshot: training_review_contracts.ExecutionSnapshotPayload | dict[str, Any] = field(default_factory=dict)
    run_context: training_review_contracts.RunContextPayload | dict[str, Any] = field(default_factory=dict)
    promotion_record: training_review_contracts.PromotionRecordPayload | dict[str, Any] = field(default_factory=dict)
    lineage_record: training_review_contracts.LineageRecordPayload | dict[str, Any] = field(default_factory=dict)
    manager_results: List[Dict[str, Any]] = field(default_factory=list)
    portfolio_plan: Dict[str, Any] = field(default_factory=dict)
    portfolio_attribution: Dict[str, Any] = field(default_factory=dict)
    manager_review_report: (
        training_review_contracts.ManagerReviewDigestPayload | dict[str, Any]
    ) = field(default_factory=dict)
    allocation_review_report: (
        training_review_contracts.AllocationReviewDigestPayload | dict[str, Any]
    ) = field(default_factory=dict)
    dominant_manager_id: str = ""
    compatibility_fields: Dict[str, Any] = field(default_factory=dict)
    review_decision: (
        training_review_contracts.ReviewDecisionPayload
        | training_review_contracts.ReviewDecisionInputPayload
    ) = field(
        default_factory=lambda: cast(
            training_review_contracts.ReviewDecisionInputPayload,
            {},
        )
    )
    causal_diagnosis: Dict[str, Any] = field(default_factory=dict)
    similarity_summary: (
        training_review_contracts.SimilaritySummaryPayload
        | training_review_contracts.SimilaritySummaryInputPayload
    ) = field(
        default_factory=lambda: cast(
            training_review_contracts.SimilaritySummaryInputPayload,
            {},
        )
    )
    similar_results: list[
        training_review_contracts.SimilarResultCompactPayload
        | training_review_contracts.SimilarResultCompactInputPayload
    ] = field(default_factory=list)
    realism_metrics: Dict[str, Any] = field(default_factory=dict)
    stage_snapshots: training_review_contracts.StageSnapshotsPayload | dict[str, Any] = field(default_factory=dict)
    validation_report: training_review_contracts.ValidationReportPayload | dict[str, Any] = field(default_factory=dict)
    validation_summary: training_review_contracts.ValidationSummaryPayload | dict[str, Any] = field(default_factory=dict)
    peer_comparison_report: Dict[str, Any] = field(default_factory=dict)
    judge_report: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (not self.effective_data_mode or self.effective_data_mode == "unknown") and self.data_mode:
            self.effective_data_mode = self.data_mode
        if (not self.data_mode or self.data_mode == "unknown") and self.effective_data_mode:
            self.data_mode = self.effective_data_mode
        if self.requested_data_mode in {"", "unknown", "live"} and self.effective_data_mode == "mock":
            self.requested_data_mode = "mock"
        if not self.llm_mode:
            self.llm_mode = "live"
        self.strategy_scores = cast(
            training_review_contracts.StrategyScoresInputPayload,
            dict(self.strategy_scores or {}),
        )
        normalized_governance = normalize_governance_decision(dict(self.governance_decision or {}))
        self.governance_decision = cast(training_review_contracts.GovernanceDecisionPayload, normalized_governance)
        self.review_decision = cast(
            training_review_contracts.ReviewDecisionInputPayload,
            dict(self.review_decision or {}),
        )
        self.similarity_summary = cast(
            training_review_contracts.SimilaritySummaryInputPayload,
            dict(self.similarity_summary or {}),
        )
        self.execution_defaults = cast(training_review_contracts.ExecutionDefaultsPayload, execution_defaults_payload(
            normalized_governance,
            portfolio_plan=dict(self.portfolio_plan or {}),
            manager_results=list(self.manager_results or []),
            execution_snapshot=dict(self.execution_snapshot or {}),
            fallback=dict(self.execution_defaults or {}),
        ))


@dataclass
class OptimizationEvent:
    trigger: str
    stage: str
    cycle_id: int | None = None
    status: str = "ok"
    suggestions: List[str] = field(default_factory=list)
    decision: Dict[str, Any] = field(default_factory=dict)
    applied_change: Dict[str, Any] = field(default_factory=dict)
    lineage: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    review_applied_effects_payload: training_review_contracts.ReviewAppliedEffectsPayload = (
        field(default_factory=_empty_review_applied_effects_payload)
    )
    review_decision_payload: training_review_contracts.ReviewDecisionOptimizationStagePayload = (
        field(default_factory=_empty_review_decision_stage_payload)
    )
    research_feedback_payload: training_review_contracts.ResearchFeedbackOptimizationStagePayload = (
        field(default_factory=_empty_research_feedback_stage_payload)
    )
    llm_analysis_payload: training_review_contracts.LlmAnalysisOptimizationStagePayload = (
        field(default_factory=_empty_llm_analysis_stage_payload)
    )
    evolution_engine_payload: training_review_contracts.EvolutionEngineOptimizationStagePayload = (
        field(default_factory=_empty_evolution_engine_stage_payload)
    )
    runtime_config_mutation_payload: training_review_contracts.RuntimeConfigMutationOptimizationStagePayload = (
        field(default_factory=_empty_runtime_config_mutation_stage_payload)
    )
    runtime_config_mutation_skipped_payload: training_review_contracts.RuntimeConfigMutationSkippedOptimizationStagePayload = (
        field(default_factory=_empty_runtime_config_mutation_skipped_stage_payload)
    )
    optimization_error_payload: training_review_contracts.OptimizationErrorStagePayload = (
        field(default_factory=_empty_optimization_error_stage_payload)
    )
    event_id: str = field(default_factory=lambda: f"opt_{uuid4().hex[:12]}")
    contract_version: str = field(default_factory=lambda: str(resolve_governance_matrix().get("optimization", {}).get("contract_version", "optimization_event.v2")))
    ts: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> training_review_contracts.OptimizationEventPayload:
        payload = cast(training_review_contracts.OptimizationEventPayload, {
            "event_id": self.event_id,
            "contract_version": self.contract_version,
            "cycle_id": self.cycle_id,
            "trigger": self.trigger,
            "stage": self.stage,
            "status": self.status,
            "suggestions": list(self.suggestions),
            "decision": dict(self.decision),
            "applied_change": dict(self.applied_change),
            "lineage": dict(self.lineage),
            "evidence": dict(self.evidence),
            "notes": self.notes,
            "ts": self.ts,
        })
        if self.review_applied_effects_payload:
            payload["review_applied_effects_payload"] = cast(
                training_review_contracts.ReviewAppliedEffectsPayload,
                dict(self.review_applied_effects_payload),
            )
        if self.review_decision_payload:
            payload["review_decision_payload"] = cast(
                training_review_contracts.ReviewDecisionOptimizationStagePayload,
                dict(self.review_decision_payload),
            )
        if self.research_feedback_payload:
            payload["research_feedback_payload"] = cast(
                training_review_contracts.ResearchFeedbackOptimizationStagePayload,
                dict(self.research_feedback_payload),
            )
        if self.llm_analysis_payload:
            payload["llm_analysis_payload"] = cast(
                training_review_contracts.LlmAnalysisOptimizationStagePayload,
                dict(self.llm_analysis_payload),
            )
        if self.evolution_engine_payload:
            payload["evolution_engine_payload"] = cast(
                training_review_contracts.EvolutionEngineOptimizationStagePayload,
                dict(self.evolution_engine_payload),
            )
        if self.runtime_config_mutation_payload:
            payload["runtime_config_mutation_payload"] = cast(
                training_review_contracts.RuntimeConfigMutationOptimizationStagePayload,
                dict(self.runtime_config_mutation_payload),
            )
        if self.runtime_config_mutation_skipped_payload:
            payload["runtime_config_mutation_skipped_payload"] = cast(
                training_review_contracts.RuntimeConfigMutationSkippedOptimizationStagePayload,
                dict(self.runtime_config_mutation_skipped_payload),
            )
        if self.optimization_error_payload:
            payload["optimization_error_payload"] = cast(
                training_review_contracts.OptimizationErrorStagePayload,
                dict(self.optimization_error_payload),
            )
        return payload


# ============================================================
# Part 4: 自我学习主控制器
# ============================================================

class SelfLearningController:
    """
    自我学习主控制器

    核心职责：
    1. 协调数据加载、选股、模拟交易
    2. 监控亏损并触发 LLM + 遗传算法优化
    3. 管理训练周期历史
    4. 判断是否满足固化条件（连续 N 轮中 M 轮盈利）

    固化条件（默认）：近 10 轮中有 7 轮盈利
    优化触发（默认）：连续 3 轮亏损
    """

    DEFAULT_PARAMS = dict(COMMON_PARAM_DEFAULTS)
    session_state: Any
    aggregate_leaderboard_enabled: bool
    runtime_evolution_optimizer: Any
    evolution_engine: Any
    evolution_service: Any
    strategy_evaluator: Any
    benchmark_evaluator: Any
    training_experiment_service: Any
    training_llm_runtime_service: Any
    training_observability_service: Any
    training_feedback_service: Any
    freeze_gate_service: Any
    training_persistence_service: Any
    training_cycle_data_service: Any
    training_execution_service: Any
    training_lifecycle_service: Any
    training_outcome_service: Any
    training_research_service: Any
    training_ab_service: Any
    training_review_service: Any
    training_review_stage_service: Any
    training_manager_review_stage_service: Any
    training_allocation_review_stage_service: Any
    training_selection_service: Any
    training_simulation_service: Any
    training_manager_execution_service: Any
    training_policy_service: Any
    training_governance_service: Any
    llm_caller: Any
    llm_optimizer: Any
    llm_mode: str
    agents: dict[str, Any]
    agent_tracker: Any
    artifact_recorder: Any
    config_service: Any
    data_manager: Any
    requested_data_mode: str
    auto_apply_mutation: bool
    quality_gate_matrix: dict[str, Any]
    selection_agent_weights: dict[str, float]
    selection_debate_enabled: bool
    review_risk_debate_enabled: bool
    max_selection_debate_rounds: int
    max_review_risk_rounds: int
    execution_policy: dict[str, Any]
    train_policy: dict[str, Any]
    freeze_gate_policy: dict[str, Any]
    promotion_gate_policy: dict[str, Any]
    risk_policy: dict[str, Any]
    evaluation_policy: dict[str, Any]
    review_policy: dict[str, Any]
    freeze_total_cycles: int
    freeze_profit_required: int
    max_losses_before_optimize: int
    allocator_enabled: bool
    allocator_top_n: int
    manager_arch_enabled: bool
    manager_shadow_mode: bool
    manager_allocator_enabled: bool
    portfolio_assembly_enabled: bool
    dual_review_enabled: bool
    manager_persistence_enabled: bool
    governance_enabled: bool
    governance_mode: str
    governance_allowed_manager_ids: list[str]
    governance_cooldown_cycles: int
    governance_min_confidence: float
    governance_hysteresis_margin: float
    governance_agent_override_enabled: bool
    governance_agent_override_max_gap: float
    governance_policy: dict[str, Any]
    effective_runtime_mode: str
    runtime_contract_version: int
    last_allocation_plan: dict[str, Any]
    governance_history: list[dict[str, Any]]
    last_governance_change_cycle_id: int | None
    stop_on_freeze: bool
    runtime_config_mutator: Any
    manager_runtime: Any
    current_cycle_id: int
    total_cycle_attempts: int
    skipped_cycle_count: int
    assessment_history: list[Any]
    optimization_events_history: list[Any]
    last_cycle_meta: dict[str, Any]
    experiment_spec: dict[str, Any]
    experiment_seed: int | None
    experiment_min_date: str | None
    experiment_max_date: str | None
    experiment_allowed_manager_ids: list[str]
    experiment_min_history_days: int | None
    experiment_simulation_days: int | None
    experiment_llm: dict[str, Any]
    experiment_protocol: dict[str, Any]
    experiment_cutoff_policy: dict[str, Any]
    experiment_review_window: dict[str, Any]
    experiment_promotion_policy: dict[str, Any]
    on_cycle_complete: Any
    on_optimize: Any
    output_dir: Any
    runtime_state_dir: Any
    research_case_store: Any
    research_market_repository: Any
    research_scenario_engine: Any
    research_attribution_engine: Any
    last_research_feedback: dict[str, Any]
    last_freeze_gate_evaluation: dict[str, Any]
    last_cutoff_policy_context: dict[str, Any]
    research_feedback_policy: dict[str, Any]
    research_feedback_optimization_policy: dict[str, Any]
    research_feedback_freeze_policy: dict[str, Any]

    # Compatibility property proxies live on the controller boundary; training core
    # should prefer invest_evolution.application.training.controller helpers instead of touching these directly.
    @property
    def current_params(self) -> Dict[str, Any]:
        return session_current_params(self)

    @current_params.setter
    def current_params(self, value: Dict[str, Any] | None) -> None:
        set_session_current_params(self, value)

    @property
    def consecutive_losses(self) -> int:
        return session_consecutive_losses(self)

    @consecutive_losses.setter
    def consecutive_losses(self, value: int) -> None:
        set_session_consecutive_losses(self, value)

    @property
    def default_manager_id(self) -> str:
        return session_default_manager_id(self)

    @default_manager_id.setter
    def default_manager_id(self, value: str) -> None:
        set_session_default_manager(
            self,
            manager_id=value,
            manager_config_ref=session_default_manager_config_ref(self),
        )

    @property
    def default_manager_config_ref(self) -> str:
        return session_default_manager_config_ref(self)

    @default_manager_config_ref.setter
    def default_manager_config_ref(self, value: str) -> None:
        set_session_default_manager(
            self,
            manager_id=session_default_manager_id(self),
            manager_config_ref=value,
        )

    @property
    def manager_budget_weights(self) -> Dict[str, float]:
        return session_manager_budget_weights(self)

    @manager_budget_weights.setter
    def manager_budget_weights(self, value: Dict[str, Any] | None) -> None:
        set_session_manager_budget_weights(self, value)

    @property
    def last_governance_decision(self) -> Dict[str, Any]:
        return session_last_governance_decision(self)

    @last_governance_decision.setter
    def last_governance_decision(self, value: Dict[str, Any] | None) -> None:
        set_session_last_governance_decision(self, value)

    @property
    def last_feedback_optimization(self) -> Dict[str, Any]:
        return session_last_feedback_optimization(self)

    @last_feedback_optimization.setter
    def last_feedback_optimization(self, value: Dict[str, Any] | None) -> None:
        set_session_last_feedback_optimization(self, value)

    @property
    def last_feedback_optimization_cycle_id(self) -> int:
        return session_last_feedback_optimization_cycle_id(self)

    @last_feedback_optimization_cycle_id.setter
    def last_feedback_optimization_cycle_id(self, value: int) -> None:
        set_session_last_feedback_optimization_cycle_id(self, value)

    @property
    def cycle_history(self) -> List[TrainingResult]:
        return session_cycle_history(self)

    @cycle_history.setter
    def cycle_history(self, value: List[TrainingResult] | None) -> None:
        set_session_cycle_history(self, value)

    @property
    def cycle_records(self) -> List[Dict]:
        return session_cycle_records(self)

    @cycle_records.setter
    def cycle_records(self, value: List[Dict] | None) -> None:
        set_session_cycle_records(self, value)

    def __init__(
        self,
        output_dir: Optional[str] = None,
        artifact_log_dir: Optional[str] = None,
        config_audit_log_path: Optional[str] = None,
        config_snapshot_dir: Optional[str] = None,
        runtime_state_dir: Optional[str] = None,
        freeze_total_cycles:    int = 10,
        freeze_profit_required: int = 7,
        max_losses_before_optimize: int = 3,
        data_provider=None,
    ):
        initialize_core_runtime(self, data_provider=data_provider)
        build_llm = initialize_llm_runtime(self)
        initialize_agents_and_runtime_support(
            self,
            build_llm=build_llm,
            artifact_log_dir=artifact_log_dir,
        )
        initialize_config_service(
            self,
            config_audit_log_path=config_audit_log_path,
            config_snapshot_dir=config_snapshot_dir,
        )
        initialize_model_runtime(
            self,
            freeze_total_cycles=freeze_total_cycles,
            freeze_profit_required=freeze_profit_required,
            max_losses_before_optimize=max_losses_before_optimize,
        )
        initialize_training_state(self)
        initialize_callbacks(self)
        initialize_output_runtime(
            self,
            output_dir=output_dir,
            runtime_state_dir=runtime_state_dir,
            config_audit_log_path=config_audit_log_path,
        )
        initialize_training_services(self)

        logger.info("自我学习控制器初始化完成")

    @staticmethod
    def _research_feedback_brief(feedback: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return TrainingFeedbackService.research_feedback_brief(feedback)

    def _load_research_feedback(
        self,
        *,
        cutoff_date: str,
        manager_id: str,
        manager_config_ref: str,
        regime: str = "",
    ) -> Dict[str, Any]:
        return self.training_feedback_service.load_research_feedback(
            self,
            cutoff_date=cutoff_date,
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            regime=regime,
        )

    @staticmethod
    def _policy_lookup(policy: Dict[str, Any] | None, path: str, default: Any) -> Any:
        return TrainingPolicyService.policy_lookup(policy, path, default)

    def _sanitize_runtime_param_adjustments(self, adjustments: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.training_policy_service.sanitize_runtime_param_adjustments(self, adjustments)

    @staticmethod
    def _feedback_optimization_brief(plan: Dict[str, Any] | None = None, *, triggered: bool = False) -> Dict[str, Any]:
        return TrainingFeedbackService.feedback_brief(plan, triggered=triggered)

    def _build_feedback_optimization_plan(self, feedback: Dict[str, Any] | None, *, cycle_id: int) -> Dict[str, Any]:
        return self.training_feedback_service.build_feedback_optimization_plan(self, feedback, cycle_id=cycle_id)

    def _evaluate_freeze_gate(self, rolling: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.freeze_gate_service.evaluate_freeze_gate(self, rolling)

    def configure_experiment(self, spec: Dict[str, Any] | None = None) -> None:
        self.training_experiment_service.configure_experiment(self, spec)


    def _apply_experiment_llm_overrides(self, llm_spec: Dict[str, Any] | None = None) -> None:
        self.training_llm_runtime_service.apply_experiment_overrides(self, llm_spec)

    def set_llm_dry_run(self, enabled: bool = True) -> None:
        """统一切换 LLM 调用 dry-run 模式。"""
        self.training_llm_runtime_service.set_dry_run(self, enabled)

    def set_mock_mode(self, enabled: bool = True) -> None:
        """兼容别名：mock mode 仅表示 LLM dry-run，不再代表数据源选择。"""
        self.training_llm_runtime_service.set_dry_run(self, enabled)

    def _refresh_governance_coordinator(self) -> None:
        self.training_governance_service.refresh_governance_coordinator(self)

    def refresh_runtime_from_config(self) -> None:
        self.training_governance_service.sync_runtime_from_config(self)

    def preview_governance(
        self,
        *,
        cutoff_date: Optional[str] = None,
        stock_count: Optional[int] = None,
        min_history_days: Optional[int] = None,
        allowed_manager_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return self.training_governance_service.preview_governance(
            self,
            cutoff_date=cutoff_date,
            stock_count=stock_count,
            min_history_days=min_history_days,
            allowed_manager_ids=allowed_manager_ids or None,
        )

    def _reload_manager_runtime(self, runtime_config_ref: Optional[str] = None) -> None:
        self.training_governance_service.reload_manager_runtime(self, runtime_config_ref)

    def _sync_runtime_policy_from_manager_runtime(self) -> None:
        self.training_policy_service.sync_runtime_policy(self)


    def _maybe_apply_allocator(self, stock_data: Dict[str, Any], cutoff_date: str, cycle_id: int) -> None:
        self.training_governance_service.apply_governance(
            self,
            stock_data=stock_data,
            cutoff_date=cutoff_date,
            cycle_id=cycle_id,
            event_emitter=emit_event,
        )

    def _thinking_excerpt(self, reasoning: Any, limit: int = 200) -> str:
        """将多种推理结果安全转换为前端展示文本。"""
        return self.training_observability_service.thinking_excerpt(reasoning, limit=limit)

    def _event_context(self, cycle_id: int | None = None) -> Dict[str, Any]:
        return self.training_observability_service.event_context(self, cycle_id)

    @staticmethod
    def _emit_runtime_event(event_type: str, data: dict) -> None:
        emit_event(event_type, data)

    def _emit_agent_status(
        self,
        agent: str,
        status: str,
        message: str,
        *,
        cycle_id: int | None = None,
        stage: str = "",
        progress_pct: int | None = None,
        step: int | None = None,
        total_steps: int | None = None,
        thinking: str = "",
        selected_stocks: List[str] | None = None,
        details: Any = None,
        **extra: Any,
    ) -> None:
        self.training_observability_service.emit_agent_status(
            self,
            event_emitter=emit_event,
            agent=agent,
            status=status,
            message=message,
            cycle_id=cycle_id,
            stage=stage,
            progress_pct=progress_pct,
            step=step,
            total_steps=total_steps,
            thinking=thinking,
            selected_stocks=selected_stocks,
            details=details,
            **extra,
        )

    def _emit_module_log(
        self,
        module: str,
        title: str,
        message: str = "",
        *,
        cycle_id: int | None = None,
        kind: str = "log",
        level: str = "info",
        details: Any = None,
        metrics: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        self.training_observability_service.emit_module_log(
            self,
            event_emitter=emit_event,
            module=module,
            title=title,
            message=message,
            cycle_id=cycle_id,
            kind=kind,
            level=level,
            details=details,
            metrics=metrics,
            **extra,
        )

    def _emit_meeting_speech(
        self,
        meeting: str,
        speaker: str,
        speech: str,
        *,
        cycle_id: int | None = None,
        role: str = "",
        picks: List[dict[str, Any]] | List[str] | None = None,
        suggestions: List[str] | None = None,
        decision: dict[str, Any] | None = None,
        confidence: Any = None,
        **extra: Any,
    ) -> None:
        self.training_observability_service.emit_meeting_speech(
            self,
            event_emitter=emit_event,
            meeting=meeting,
            speaker=speaker,
            speech=speech,
            cycle_id=cycle_id,
            role=role,
            picks=picks,
            suggestions=suggestions,
            decision=decision,
            confidence=confidence,
            **extra,
        )

    def _handle_selection_progress(self, payload: dict[str, Any]) -> None:
        self.training_observability_service.handle_selection_progress(self, payload)

    def _handle_review_progress(self, payload: dict[str, Any]) -> None:
        self.training_observability_service.handle_review_progress(self, payload)

    def _mark_cycle_skipped(self, cycle_id: int, cutoff_date: str, stage: str, reason: str, **extra: Any) -> None:
        self.training_observability_service.mark_cycle_skipped(
            self,
            event_emitter=emit_event,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            stage=stage,
            reason=reason,
            **extra,
        )

    def run_training_cycle(self) -> Optional[TrainingResult]:
        """
        执行一个完整的训练周期

        Returns:
            TrainingResult 或 None（数据不足时）
        """
        cycle_context = self.training_cycle_data_service.prepare_cycle_context(self)
        cycle_id = cycle_context.cycle_id
        logger.info(f"\n{'='*60}")
        logger.info(f"训练周期 #{cycle_id}")
        logger.info(f"{'='*60}")

        cutoff_date = cycle_context.cutoff_date
        logger.info(f"截断日期: {cutoff_date}")
        requested_data_mode = cycle_context.requested_data_mode
        llm_mode = cycle_context.llm_mode

        # 发射周期开始事件
        emit_event("cycle_start", {
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "phase": "cycle_start",
            "requested_data_mode": requested_data_mode,
            "llm_mode": llm_mode,
            "timestamp": datetime.now().isoformat()
        })

        optimization_events: list[dict[str, Any]] = []
        llm_used = bool(getattr(self.llm_caller.gateway, "available", False))
        self.last_research_feedback = {}
        self.last_cycle_meta = {
            "status": "running",
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "timestamp": datetime.now().isoformat(),
        }

        logger.info("加载数据...")
        self._emit_agent_status("DataLoader", "loading", f"正在加载 {cutoff_date} 的历史数据...", cycle_id=cycle_id, stage="data_loading", progress_pct=8, step=1, total_steps=6)
        self._emit_module_log("data_loading", "开始加载训练数据", f"截断日期 {cutoff_date}", cycle_id=cycle_id, kind="phase_start")
        data_load_result = None
        diagnostics: dict[str, Any] = _default_training_diagnostics(
            cutoff_date,
            config.max_stocks,
            max(30, int(self.experiment_min_history_days or getattr(config, "min_history_days", 200))),
        )

        try:
            data_load_result = self.training_cycle_data_service.load_training_data(
                self,
                cutoff_date=cutoff_date,
                requested_data_mode=requested_data_mode,
            )
            diagnostics = data_load_result.diagnostics or diagnostics
            if not diagnostics.get("ready", False):
                logger.warning(
                    "训练前数据诊断: eligible=%s target=%s range=%s~%s issues=%s",
                    diagnostics.get("eligible_stock_count", 0),
                    diagnostics.get("target_stock_count", 0),
                    diagnostics.get("date_range", {}).get("min"),
                    diagnostics.get("date_range", {}).get("max"),
                    "；".join(diagnostics.get("issues", [])) or "none",
                )
                self._emit_module_log(
                    "data_loading",
                    "训练前数据诊断预警",
                    "可用数据不足，可能跳过本轮",
                    cycle_id=cycle_id,
                    kind="diagnostics",
                    level="warn",
                    details=diagnostics.get("issues", []),
                    metrics={
                        "eligible_stock_count": diagnostics.get("eligible_stock_count", 0),
                        "target_stock_count": diagnostics.get("target_stock_count", 0),
                    },
                )
            stock_data = data_load_result.stock_data
        except DataSourceUnavailableError as exc:
            error_payload = exc.to_dict()
            self.last_cycle_meta = {
                "status": "error",
                "cycle_id": cycle_id,
                "cutoff_date": cutoff_date,
                "stage": "data_loading",
                "reason": error_payload["error"],
                "error_code": error_payload["error_code"],
                "requested_data_mode": requested_data_mode,
                "effective_data_mode": "unavailable",
                "llm_mode": llm_mode,
                "degraded": True,
                "degrade_reason": error_payload["error"],
                "timestamp": datetime.now().isoformat(),
            }
            self._emit_module_log(
                "data_loading",
                "训练数据源不可用",
                str(error_payload.get("error") or ""),
                cycle_id=cycle_id,
                kind="data_source_unavailable",
                level="error",
                details=error_payload,
                metrics={
                    "requested_data_mode": requested_data_mode,
                    "effective_data_mode": "unavailable",
                },
            )
            raise

        effective_data_mode = data_load_result.effective_data_mode if data_load_result else "unknown"
        degrade_reason = data_load_result.degrade_reason if data_load_result else ""
        degraded = data_load_result.degraded if data_load_result else False
        data_mode = data_load_result.data_mode if data_load_result else effective_data_mode
        self._emit_agent_status(
            "DataLoader",
            "completed",
            f"数据加载完成，共载入 {len(stock_data)} 只股票，数据源 {data_mode}",
            cycle_id=cycle_id,
            stage="data_loading",
            progress_pct=18,
            step=1,
            total_steps=6,
            details={
                "requested_data_mode": requested_data_mode,
                "effective_data_mode": effective_data_mode,
                "degraded": degraded,
                "degrade_reason": degrade_reason,
                "stock_count": len(stock_data),
            },
        )
        self._emit_module_log(
            "data_loading",
            "数据加载完成",
            f"请求模式 {requested_data_mode}，实际数据源 {data_mode}，载入 {len(stock_data)} 只股票",
            cycle_id=cycle_id,
            kind="data_ready",
            details={
                "requested_data_mode": requested_data_mode,
                "effective_data_mode": effective_data_mode,
                "degraded": degraded,
                "degrade_reason": degrade_reason,
            },
            metrics={
                "stock_count": len(stock_data),
                "data_mode": data_mode,
                "requested_data_mode": requested_data_mode,
            },
        )

        if not stock_data:
            latest = getattr(self.data_manager, "last_diagnostics", diagnostics)
            logger.error("没有加载到数据: %s", "；".join(latest.get("issues", [])) or "未知原因")
            for suggestion in latest.get("suggestions", []):
                logger.error("建议: %s", suggestion)
            self._mark_cycle_skipped(
                cycle_id,
                cutoff_date,
                stage="data_loading",
                reason="没有加载到可用训练数据",
                suggestions=list(latest.get("suggestions", [])),
                requested_data_mode=requested_data_mode,
                effective_data_mode=effective_data_mode,
                llm_mode=llm_mode,
                degraded=degraded,
                degrade_reason=degrade_reason,
            )
            return None

        return self.training_execution_service.execute_loaded_cycle(
            self,
            result_factory=TrainingResult,
            optimization_event_factory=OptimizationEvent,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            stock_data=stock_data,
            diagnostics=diagnostics,
            requested_data_mode=requested_data_mode,
            effective_data_mode=effective_data_mode,
            llm_mode=llm_mode,
            degraded=degraded,
            degrade_reason=degrade_reason,
            data_mode=data_mode,
            llm_used=llm_used,
            optimization_events=optimization_events,
        )

    def _trigger_optimization(self, optimization_input: Any, trade_dicts: List[Dict], *, trigger_reason: str = "consecutive_losses", feedback_plan: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        return trigger_loss_optimization(
            self,
            optimization_input,
            trade_dicts,
            event_factory=OptimizationEvent,
            trigger_reason=trigger_reason,
            feedback_plan=feedback_plan,
        )

    def _append_optimization_event(self, event: OptimizationEvent) -> None:
        self.optimization_events_history.append(event)
        payload = event.to_dict()
        contract = evaluate_optimization_event_contract(
            cast(dict[str, Any], payload),
            policy=dict((self.quality_gate_matrix or {}).get("optimization") or {}),
        )
        log_payload = cast(training_review_contracts.OptimizationEventLogPayload, {
            **payload,
            "contract_check": contract,
        })
        if not contract.get("passed", False):
            logger.warning(
                "Optimization event contract check failed for cycle=%s stage=%s: %s",
                payload.get("cycle_id"),
                payload.get("stage"),
                [item.get("name") for item in contract.get("failed_checks", [])],
            )
        path = self.output_dir / "optimization_events.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_payload, ensure_ascii=False) + "\n")

    def run_continuous(
        self,
        max_cycles: int = 100,
        successful_cycles_target: Optional[int] = None,
    ) -> Dict:
        """
        持续训练主循环

        Args:
            max_cycles: 最大尝试周期数
            successful_cycles_target: 成功周期目标数；达到后提前结束

        Returns:
            训练报告字典
        """
        kwargs: Dict[str, Any] = {"max_cycles": max_cycles}
        if successful_cycles_target is not None:
            kwargs["successful_cycles_target"] = successful_cycles_target
        return self.training_lifecycle_service.run_continuous(
            self,
            **kwargs,
        )

    def _record_self_assessment(self, cycle_result: TrainingResult, assessment_payload: Dict):
        """记录单周期自我评估快照"""
        self.training_persistence_service.record_self_assessment(
            self,
            SelfAssessmentSnapshot,
            cycle_result,
            assessment_payload,
        )

    def _rolling_self_assessment(self, window: Optional[int] = None) -> Dict:
        """滚动自我评估摘要（用于冻结门控）"""
        return self.freeze_gate_service.rolling_self_assessment(self, window=window)

    def should_freeze(self) -> bool:
        """
        是否满足固化条件

        冻结门控（训练阶段）：
        1. 最近N轮胜率达到阈值（默认 10轮7胜）
        2. 近窗平均收益 > 0
        3. 近窗平均 Sharpe >= 0.8
        4. 近窗平均最大回撤 < 15%
        5. 基准评估通过率 >= 60%
        """
        return self.freeze_gate_service.should_freeze(self)

    def _freeze_runtime_state(self) -> Dict:
        """固化当前 runtime 状态并保存"""
        return self.freeze_gate_service.freeze_runtime_state(self)

    def _generate_report(self) -> Dict:
        return self.freeze_gate_service.generate_training_report(self)

    def _save_cycle_result(self, result: TrainingResult):
        """将周期结果写入 JSON"""
        self.training_persistence_service.save_cycle_result(self, result)


# ============================================================
# Part 5: 命令行入口
# ============================================================

def train_main():
    parser = build_train_parser()
    args = parser.parse_args()
    run_train_cli(
        args,
        controller_cls=SelfLearningController,
        build_mock_provider=_build_mock_provider,
        runtime_path_config_service_cls=RuntimePathConfigService,
        project_root=PROJECT_ROOT,
        live_config=config,
        logger=logger,
        logging_module=logging,
    )


if __name__ == "__main__":
    train_main()


def build_train_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="投资进化系统 - 训练批处理/兼容入口（人类主入口请优先使用 Commander）"
    )
    parser.add_argument("--cycles", type=int, default=20, help="最大尝试周期数")
    parser.add_argument("--mock", action="store_true", help="使用模拟数据（无需数据库）")
    parser.add_argument("--output", type=str, default=None, help="训练输出目录")
    parser.add_argument("--artifact-log-dir", type=str, default=None, help="训练工件输出目录")
    parser.add_argument(
        "--config-audit-log-path",
        type=str,
        default=None,
        help="配置变更审计日志路径",
    )
    parser.add_argument(
        "--config-snapshot-dir",
        type=str,
        default=None,
        help="配置快照输出目录",
    )
    parser.add_argument("--freeze-n", type=int, default=10, help="固化评估窗口大小")
    parser.add_argument("--freeze-m", type=int, default=7, help="固化要求最低盈利次数")
    parser.add_argument("--log-level", type=str, default="INFO", help="日志级别")
    parser.add_argument("--use-allocator", action="store_true", help="启用 market regime allocator")
    parser.add_argument(
        "--allocator-top-n",
        type=int,
        default=None,
        help="allocator 参与分配的前 N 个模型",
    )
    parser.add_argument(
        "--shadow-mode",
        action="store_true",
        help="启用影子裁决模式，只生成验证与裁决结果，不执行自动动作",
    )
    parser.add_argument(
        "--llm-dry-run",
        action="store_true",
        help="以 dry-run 模式运行 LLM，避免真实外部调用",
    )
    parser.add_argument(
        "--successful-cycles-target",
        type=int,
        default=None,
        help="成功周期目标数；达到后提前结束",
    )
    parser.add_argument(
        "--force-full-cycles",
        action="store_true",
        help="即使达到冻结条件也继续跑满 cycles",
    )
    return parser


def run_train_cli(
    args: argparse.Namespace,
    *,
    controller_cls: type[Any],
    build_mock_provider: Callable[[], Any],
    runtime_path_config_service_cls: type[Any],
    project_root: Any,
    live_config: Any,
    logger: logging.Logger,
    logging_module: Any,
) -> dict[str, Any]:
    logging_module.basicConfig(
        level=getattr(logging_module, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    logger.info(
        "训练参数: cycles=%s, mock=%s, shadow_mode=%s, llm_dry_run=%s",
        args.cycles,
        args.mock,
        args.shadow_mode,
        args.llm_dry_run,
    )
    if args.successful_cycles_target is not None:
        logger.info(
            "成功周期目标: %s（最大尝试数=%s）",
            args.successful_cycles_target,
            args.cycles,
        )
    if args.use_allocator:
        live_config.allocator_enabled = True
        live_config.governance_enabled = True
        live_config.governance_mode = "rule"
    if args.allocator_top_n is not None:
        live_config.allocator_top_n = max(1, int(args.allocator_top_n))
    if args.force_full_cycles:
        live_config.stop_on_freeze = False

    runtime_paths = runtime_path_config_service_cls(project_root=project_root).get_payload()
    output_dir = args.output or runtime_paths["training_output_dir"]
    artifact_log_dir = args.artifact_log_dir or runtime_paths["artifact_log_dir"]
    config_audit_log_path = (
        args.config_audit_log_path or runtime_paths["config_audit_log_path"]
    )
    config_snapshot_dir = args.config_snapshot_dir or runtime_paths["config_snapshot_dir"]

    data_provider = None
    if args.mock:
        logger.info("使用模拟数据模式")
        data_provider = build_mock_provider()

    controller = controller_cls(
        output_dir=output_dir,
        artifact_log_dir=artifact_log_dir,
        config_audit_log_path=config_audit_log_path,
        config_snapshot_dir=config_snapshot_dir,
        freeze_total_cycles=args.freeze_n,
        freeze_profit_required=args.freeze_m,
        data_provider=data_provider,
    )

    if args.mock:
        controller.set_llm_dry_run(True)
    if args.shadow_mode or args.llm_dry_run:
        experiment_spec: dict[str, Any] = {}
        if args.shadow_mode:
            experiment_spec["protocol"] = {"shadow_mode": True}
        if args.llm_dry_run:
            experiment_spec["llm"] = {"dry_run": True}
        controller.configure_experiment(experiment_spec)
        logger.info(
            "实验协议已启用: shadow_mode=%s, llm_dry_run=%s",
            args.shadow_mode,
            args.llm_dry_run or args.mock,
        )

    report = controller.run_continuous(
        max_cycles=args.cycles,
        successful_cycles_target=args.successful_cycles_target,
    )
    logger.info("\n训练完成: %s", report)
    return report
