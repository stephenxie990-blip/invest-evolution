"""Training review stage contracts, envelopes, and run-context builders."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, NotRequired, TypeAlias, TypedDict, cast

from invest_evolution.application.training.controller import (
    session_current_params,
    session_cycle_history,
)
from invest_evolution.application.training.policy import (
    _normalize_config_ref,
    governance_from_controller,
    normalize_governance_decision,
    normalize_review_window,
)
from invest_evolution.investment.shared.policy import (
    evaluate_promotion_discipline,
    infer_deployment_stage,
    normalize_freeze_gate_policy,
    normalize_promotion_gate_policy,
    resolve_governance_matrix,
)

CYCLE_STAGE_SNAPSHOT_CONTRACT_VERSION = "training_cycle_stage_snapshots.v1"


class ReviewBasisWindowPayload(TypedDict):
    mode: str
    size: int
    cycle_ids: list[int]
    current_cycle_id: int


class ExecutionDefaultsPayload(TypedDict):
    default_manager_id: str
    default_manager_config_ref: str
    dominant_manager_id: NotRequired[str]
    active_manager_ids: NotRequired[list[str]]
    manager_budget_weights: NotRequired[dict[str, float]]
    regime: NotRequired[str]
    subject_type: NotRequired[str]


class GovernanceDecisionPayload(TypedDict):
    as_of_date: str
    regime: str
    regime_confidence: float
    decision_confidence: float
    active_manager_ids: list[str]
    manager_budget_weights: dict[str, float]
    dominant_manager_id: str
    cash_reserve_hint: float
    portfolio_constraints: dict[str, Any]
    decision_source: str
    regime_source: str
    reasoning: str
    evidence: dict[str, Any]
    agent_advice: dict[str, Any]
    allocation_plan: dict[str, Any]
    guardrail_checks: list[dict[str, Any]]
    metadata: dict[str, Any]


class GovernanceDecisionInputPayload(TypedDict, total=False):
    as_of_date: str
    regime: str
    regime_confidence: float
    decision_confidence: float
    active_manager_ids: list[str]
    manager_budget_weights: dict[str, float]
    dominant_manager_id: str
    cash_reserve_hint: float
    portfolio_constraints: dict[str, Any]
    decision_source: str
    regime_source: str
    reasoning: str
    evidence: dict[str, Any]
    agent_advice: dict[str, Any]
    allocation_plan: dict[str, Any]
    guardrail_checks: list[dict[str, Any]]
    metadata: dict[str, Any]


class ResearchFeedbackRecommendationPayload(TypedDict):
    bias: str
    summary: str


class ResearchFeedbackScopePayload(TypedDict):
    effective_scope: str
    requested_regime: str
    regime_sample_count: int
    overall_sample_count: int


class ResearchFeedbackHorizonPayload(TypedDict):
    hit_rate: float
    invalidation_rate: float
    interval_hit_rate: float
    label: str


class ResearchFeedbackPayload(TypedDict):
    enabled: bool
    passed: bool
    summary: str
    trigger: str
    sample_count: int
    brier_like_direction_score: float
    recommendation: ResearchFeedbackRecommendationPayload | dict[str, Any]
    horizons: dict[str, ResearchFeedbackHorizonPayload | dict[str, Any]]
    scope: ResearchFeedbackScopePayload | dict[str, Any]
    overall_feedback: dict[str, Any]


class StrategyScoresPayload(TypedDict):
    signal_accuracy: float
    timing_score: float
    risk_control_score: float
    overall_score: float


class StrategyScoresInputPayload(TypedDict, total=False):
    signal_accuracy: float
    timing_score: float
    risk_control_score: float
    overall_score: float


class PromotionDecisionPayload(TypedDict):
    status: str
    source: str
    reason: str
    applied_to_active: bool
    active_runtime_config_ref: str
    candidate_runtime_config_ref: str
    policy: dict[str, Any]


class PromotionRecordPayload(TypedDict):
    cycle_id: int
    basis_stage: str
    subject_type: str
    dominant_manager_id: str
    active_manager_ids: list[str]
    status: str
    source: str
    reason: str
    applied_to_active: bool
    attempted: bool
    gate_status: str
    deployment_stage: str
    shadow_mode: bool
    discipline: dict[str, Any]
    active_runtime_config_ref: str
    candidate_runtime_config_ref: str
    candidate_runtime_config_meta_ref: str
    policy: dict[str, Any]
    mutation_trigger: str
    mutation_stage: str
    mutation_notes: str


class ExecutionSnapshotPayload(TypedDict):
    basis_stage: str
    cycle_id: int
    active_runtime_config_ref: str
    manager_id: str
    manager_config_ref: str
    dominant_manager_id: str
    subject_type: str
    selection_mode: str
    benchmark_passed: bool
    execution_defaults: ExecutionDefaultsPayload | dict[str, Any]
    runtime_overrides: dict[str, Any]
    governance_decision: GovernanceDecisionPayload | dict[str, Any]
    manager_results: list[dict[str, Any]]
    portfolio_plan: dict[str, Any]
    compatibility_fields: dict[str, Any]
    contract_stage_snapshots: NotRequired[StageSnapshotsPayload | dict[str, Any]]
    cutoff_policy_context: NotRequired[dict[str, Any]]


class ExecutionSnapshotProjectionPayload(TypedDict):
    basis_stage: str
    active_runtime_config_ref: str
    manager_config_ref: str
    dominant_manager_id: str
    subject_type: str
    execution_defaults: ExecutionDefaultsPayload | dict[str, Any]


class RunContextPayload(TypedDict):
    basis_stage: str
    active_runtime_config_ref: str
    candidate_runtime_config_ref: str
    shadow_mode: bool
    runtime_overrides: dict[str, Any]
    review_basis_window: ReviewBasisWindowPayload | dict[str, Any]
    fitness_source_cycles: list[int]
    ab_comparison: dict[str, Any]
    research_feedback: ResearchFeedbackPayload | dict[str, Any]
    promotion_decision: PromotionDecisionPayload | dict[str, Any]
    subject_type: str
    dominant_manager_id: str
    manager_config_ref: str
    execution_defaults: ExecutionDefaultsPayload | dict[str, Any]
    manager_results: list[dict[str, Any]]
    portfolio_plan: dict[str, Any]
    active_manager_ids: list[str]
    governance_decision: GovernanceDecisionPayload | dict[str, Any]
    portfolio_attribution: dict[str, Any]
    manager_review_report: "ManagerReviewDigestPayload | dict[str, Any]"
    allocation_review_report: "AllocationReviewDigestPayload | dict[str, Any]"
    compatibility_fields: dict[str, Any]
    deployment_stage: str
    promotion_discipline: dict[str, Any]
    quality_gate_matrix: dict[str, Any]
    resolved_train_policy: dict[str, Any]
    governance_stage: str
    contract_stage_snapshots: NotRequired[StageSnapshotsPayload | dict[str, Any]]
    experiment_protocol: NotRequired[dict[str, Any]]


class ValidationReviewResultPayload(TypedDict):
    cycle_id: int
    regime: str
    failure_signature: dict[str, Any]
    regime_summary: dict[str, Any]
    research_feedback: ResearchFeedbackPayload | dict[str, Any]
    causal_diagnosis: dict[str, Any]
    manager_review_report: "ManagerReviewDigestPayload | dict[str, Any]"
    allocation_review_report: "AllocationReviewDigestPayload | dict[str, Any]"


class LineageRecordPayload(TypedDict):
    cycle_id: int
    basis_stage: str
    subject_type: str
    dominant_manager_id: str
    manager_config_ref: str
    active_manager_ids: list[str]
    active_runtime_config_ref: str
    candidate_runtime_config_ref: str
    candidate_runtime_config_meta_ref: str
    deployment_stage: str
    lineage_status: str
    shadow_mode: bool
    runtime_overrides: dict[str, Any]
    fitness_source_cycles: list[int]
    review_basis_window: ReviewBasisWindowPayload | dict[str, Any]
    compatibility_fields: dict[str, Any]
    promotion_discipline: dict[str, Any]
    mutation_trigger: str
    mutation_stage: str
    mutation_notes: str
    promotion_status: str


class ReviewDecisionOptimizationStagePayload(TypedDict, total=False):
    strategy_suggestions: list[str]
    param_adjustments: dict[str, Any]
    agent_weight_adjustments: dict[str, Any]
    manager_budget_adjustments: dict[str, Any]
    return_pct: float
    benchmark_passed: bool
    manager_review_count: int
    allocation_review_verdict: str
    reasoning: str


class ReviewAppliedEffectsPayload(TypedDict, total=False):
    param_adjustments: dict[str, Any]
    manager_budget_adjustments: dict[str, float]
    agent_weight_adjustments: dict[str, float]


class ResearchFeedbackOptimizationStagePayload(TypedDict, total=False):
    bias: str
    failed_horizons: list[str]
    failed_checks: list[str]
    suggestions: list[str]
    param_adjustments: dict[str, Any]
    scoring_adjustments: dict[str, Any]
    sample_count: int
    severity: float
    benchmark_context: dict[str, Any]
    summary: str


class LlmAnalysisOptimizationStagePayload(TypedDict, total=False):
    cause: str
    suggestions: list[str]
    consecutive_losses: int
    trade_record_count: int


class EvolutionEngineOptimizationStagePayload(TypedDict, total=False):
    fitness_scores: list[float]
    fitness_policy: str
    best_params: dict[str, Any]
    fitness_sample_count: int
    population_size: int


class RuntimeConfigMutationOptimizationStagePayload(TypedDict, total=False):
    runtime_config_ref: str
    auto_applied: bool
    param_adjustments: dict[str, Any]
    scoring_adjustments: dict[str, Any]
    mutation_meta: dict[str, Any]


class RuntimeConfigMutationSkippedOptimizationStagePayload(TypedDict, total=False):
    skipped: bool
    pending_candidate_runtime_config_ref: str
    auto_applied: bool
    param_adjustments: dict[str, Any]
    scoring_adjustments: dict[str, Any]
    skip_reason: str


class OptimizationErrorStagePayload(TypedDict, total=False):
    exception_type: str
    message: str


class OptimizationEventPayload(TypedDict):
    event_id: str
    contract_version: str
    cycle_id: int | None
    trigger: str
    stage: str
    status: str
    suggestions: list[str]
    decision: dict[str, Any]
    applied_change: dict[str, Any]
    lineage: dict[str, Any]
    evidence: dict[str, Any]
    notes: str
    ts: str
    review_decision_payload: NotRequired[ReviewDecisionOptimizationStagePayload]
    review_applied_effects_payload: NotRequired[ReviewAppliedEffectsPayload]
    research_feedback_payload: NotRequired[ResearchFeedbackOptimizationStagePayload]
    llm_analysis_payload: NotRequired[LlmAnalysisOptimizationStagePayload]
    evolution_engine_payload: NotRequired[EvolutionEngineOptimizationStagePayload]
    runtime_config_mutation_payload: NotRequired[RuntimeConfigMutationOptimizationStagePayload]
    runtime_config_mutation_skipped_payload: NotRequired[
        RuntimeConfigMutationSkippedOptimizationStagePayload
    ]
    optimization_error_payload: NotRequired[OptimizationErrorStagePayload]


class OptimizationEventLogPayload(OptimizationEventPayload):
    contract_check: dict[str, Any]


class PersistedOptimizationEventPayload(TypedDict, total=False):
    event_id: str
    contract_version: str
    cycle_id: int | None
    trigger: str
    stage: str
    status: str
    notes: str
    ts: str
    lineage: dict[str, Any]
    review_decision_payload: ReviewDecisionOptimizationStagePayload
    review_applied_effects_payload: ReviewAppliedEffectsPayload
    research_feedback_payload: ResearchFeedbackOptimizationStagePayload
    llm_analysis_payload: LlmAnalysisOptimizationStagePayload
    evolution_engine_payload: EvolutionEngineOptimizationStagePayload
    runtime_config_mutation_payload: RuntimeConfigMutationOptimizationStagePayload
    runtime_config_mutation_skipped_payload: RuntimeConfigMutationSkippedOptimizationStagePayload
    optimization_error_payload: OptimizationErrorStagePayload


class SimulationStageSnapshotPayload(TypedDict):
    contract_version: str
    stage: str
    cycle_id: int
    cutoff_date: str
    regime: str
    selection_mode: str
    selected_stocks: list[str]
    return_pct: float
    benchmark_passed: bool
    benchmark_strict_passed: bool
    strategy_scores: StrategyScoresPayload
    governance_decision: GovernanceDecisionPayload
    execution_snapshot: ExecutionSnapshotProjectionPayload | dict[str, Any]


class ReviewDecisionPayload(TypedDict):
    reasoning: str
    verdict: str
    subject_type: str
    regime_summary: dict[str, Any]
    causal_diagnosis: dict[str, Any]
    param_adjustments: dict[str, Any]
    manager_budget_adjustments: dict[str, Any]
    agent_weight_adjustments: dict[str, Any]


class SimilaritySummaryPayload(TypedDict):
    target_cycle_id: int
    matched_cycle_ids: list[int]
    matched_cycle_ids_truncated: bool
    match_count: int
    match_features: list[str]
    dominant_regime: str
    similarity_band: str
    summary: str
    compared_history_size: int
    strict_failure_match_count: int
    matched_primary_driver: str
    matched_feedback_bias: str
    avg_evidence_score: float


class SimilaritySummaryInputPayload(TypedDict, total=False):
    target_cycle_id: int
    matched_cycle_ids: list[int]
    matched_cycle_ids_truncated: bool
    match_count: int
    match_features: list[str]
    dominant_regime: str
    similarity_band: str
    summary: str
    compared_history_size: int
    strict_failure_match_count: int
    matched_primary_driver: str
    matched_feedback_bias: str
    avg_evidence_score: float


class SimilarResultCompactPayload(TypedDict):
    cycle_id: int
    cutoff_date: str
    return_pct: float
    is_profit: bool
    benchmark_passed: bool
    review_applied: bool
    regime: str
    selection_mode: str
    manager_id: str
    manager_config_ref: str
    similarity_score: int
    matched_features: list[str]
    strict_failure_match: bool
    evidence_score: int
    failure_signature: dict[str, Any]


class SimilarResultCompactInputPayload(TypedDict, total=False):
    cycle_id: int
    cutoff_date: str
    return_pct: float
    is_profit: bool
    benchmark_passed: bool
    review_applied: bool
    regime: str
    selection_mode: str
    manager_id: str
    manager_config_ref: str
    similarity_score: int
    score: int
    matched_features: list[str]
    strict_failure_match: bool
    evidence_score: int
    failure_signature: dict[str, Any]


class ReviewDecisionInputPayload(TypedDict, total=False):
    reasoning: str
    verdict: str
    subject_type: str
    decision_source: str
    strategy_suggestions: list[str]
    regime_summary: dict[str, Any]
    causal_diagnosis: dict[str, Any]
    param_adjustments: dict[str, Any]
    manager_budget_adjustments: dict[str, Any]
    agent_weight_adjustments: dict[str, Any]
    similarity_summary: SimilaritySummaryInputPayload
    similar_results: list[SimilarResultCompactInputPayload]


class ReviewStageSnapshotPayload(TypedDict):
    contract_version: str
    stage: str
    cycle_id: int
    analysis: str
    review_decision: ReviewDecisionPayload
    causal_diagnosis: dict[str, Any]
    similarity_summary: SimilaritySummaryPayload
    similar_results: list[SimilarResultCompactPayload]
    manager_review_report: "ManagerReviewDigestPayload"
    allocation_review_report: "AllocationReviewDigestPayload"
    ab_comparison: dict[str, Any]


class ValidationSummaryPayload(TypedDict):
    contract_version: str
    validation_task_id: str
    status: str
    shadow_mode: bool
    review_required: bool
    confidence_score: float
    validation_tags: list[str]
    reason_codes: list[str]
    checks: list[dict[str, Any]]
    failed_checks: list[dict[str, Any]]
    raw_evidence: dict[str, Any]
    summary: str


class ValidationReportPayload(TypedDict):
    contract_version: str
    validation_task_id: str
    shadow_mode: bool
    market_tagging: dict[str, Any]
    failure_tagging: dict[str, Any]
    validation_tagging: dict[str, Any]
    summary: ValidationSummaryPayload | dict[str, Any]
    checks: list[dict[str, Any]]
    failed_checks: list[dict[str, Any]]
    checkpoint: dict[str, Any]


class ValidationReportInputPayload(TypedDict, total=False):
    contract_version: str
    validation_task_id: str
    shadow_mode: bool
    market_tagging: dict[str, Any]
    failure_tagging: dict[str, Any]
    validation_tagging: dict[str, Any]
    summary: ValidationSummaryPayload | dict[str, Any]
    checks: list[dict[str, Any]]
    failed_checks: list[dict[str, Any]]
    checkpoint: dict[str, Any]


class ValidationStageSnapshotPayload(TypedDict):
    contract_version: str
    stage: str
    cycle_id: int
    validation_task_id: str
    shadow_mode: bool
    validation_summary: ValidationSummaryPayload | dict[str, Any]
    market_tagging: dict[str, Any]
    failure_tagging: dict[str, Any]
    validation_tagging: dict[str, Any]
    judge_report: dict[str, Any]


class RealismMetricsPayload(TypedDict, total=False):
    trade_record_count: int
    selection_mode: str
    optimization_event_count: int
    avg_trade_amount: float
    avg_turnover_rate: float
    high_turnover_trade_count: int
    avg_holding_days: float
    source_mix: dict[str, float]
    exit_trigger_mix: dict[str, float]


class OutcomeStageSnapshotPayload(TypedDict):
    contract_version: str
    stage: str
    cycle_id: int
    execution_snapshot: ExecutionSnapshotPayload | ExecutionSnapshotProjectionPayload | dict[str, Any]
    run_context: RunContextPayload | dict[str, Any]
    promotion_record: PromotionRecordPayload | dict[str, Any]
    lineage_record: LineageRecordPayload | dict[str, Any]
    realism_metrics: RealismMetricsPayload


class StageSnapshotsPayload(TypedDict):
    simulation: SimulationStageSnapshotPayload | dict[str, Any]
    review: ReviewStageSnapshotPayload | dict[str, Any]
    validation: ValidationStageSnapshotPayload | dict[str, Any]
    outcome: OutcomeStageSnapshotPayload | dict[str, Any]


class StageSnapshotsInputPayload(TypedDict, total=False):
    simulation: SimulationStageSnapshotPayload | dict[str, Any]
    review: ReviewStageSnapshotPayload | dict[str, Any]
    validation: ValidationStageSnapshotPayload | dict[str, Any]
    outcome: OutcomeStageSnapshotPayload | dict[str, Any]


class StageSnapshotRefsPayload(TypedDict):
    stage_names: list[str]
    count: int


class SimilaritySummaryCompactPayload(TypedDict):
    match_count: int
    similarity_band: str
    summary: str


class SimilaritySummaryPersistedPayload(SimilaritySummaryCompactPayload):
    matched_cycle_ids: list[int]
    matched_cycle_ids_truncated: bool


class ManagerReviewDigestSummaryPayload(TypedDict):
    manager_count: int
    active_manager_ids: list[str]
    verdict_counts: dict[str, int]


class ManagerReviewDigestPayload(TypedDict):
    subject_type: str
    dominant_manager_id: str
    summary: ManagerReviewDigestSummaryPayload
    manager_budget_adjustments: dict[str, float]


class PersistedManagerReviewDigestPayload(ManagerReviewDigestPayload, total=False):
    as_of_date: str
    regime: str
    recommended_actions: list[str]
    review_basis_window: ReviewBasisWindowPayload | dict[str, Any]
    reasoning: str


class AllocationReviewDigestSummaryPayload(TypedDict):
    portfolio_selected_count: int
    overlap_code_count: int
    max_position_weight: float


class AllocationReviewDigestPayload(TypedDict):
    subject_type: str
    verdict: str
    active_manager_ids: list[str]
    risk_flags: list[str]


class PersistedAllocationReviewDigestPayload(AllocationReviewDigestPayload, total=False):
    as_of_date: str
    regime: str
    summary: AllocationReviewDigestSummaryPayload


class TaggingDigestPayload(TypedDict):
    primary_tag: str
    confidence_score: float
    review_required: bool
    reason_codes: list[str]


class PersistedTaggingDigestPayload(TaggingDigestPayload):
    contract_version: str
    tag_family: str
    normalized_tags: list[str]


class ValidationSummaryCompactPayload(TypedDict):
    contract_version: str
    validation_task_id: str
    status: str
    shadow_mode: bool
    review_required: bool
    confidence_score: float
    reason_codes: list[str]
    check_count: int
    failed_check_count: int


class ValidationCheckSummaryPayload(TypedDict):
    name: str
    passed: bool
    reason_code: str
    actual: Any
    threshold: Any


class ValidationRawEvidenceCycleResultPayload(TypedDict):
    return_pct: float
    benchmark_passed: bool
    strategy_scores: dict[str, Any]
    ab_comparison: dict[str, Any]
    research_feedback: dict[str, Any]


class ValidationRawEvidenceSummaryPayload(TypedDict):
    run_context: dict[str, Any]
    review_result: dict[str, Any]
    cycle_result: ValidationRawEvidenceCycleResultPayload


class PersistedValidationSummaryPayload(ValidationSummaryCompactPayload):
    validation_tags: list[str]
    summary: str
    checks: list[ValidationCheckSummaryPayload]
    failed_checks: list[ValidationCheckSummaryPayload]
    raw_evidence: NotRequired[ValidationRawEvidenceSummaryPayload]


class ValidationReportSummaryPayload(TypedDict):
    validation_task_id: str
    shadow_mode: bool
    summary: PersistedValidationSummaryPayload
    market_tagging: PersistedTaggingDigestPayload
    failure_tagging: PersistedTaggingDigestPayload
    validation_tagging: PersistedTaggingDigestPayload


class PeerComparisonCompactPayload(TypedDict):
    compared_market_tag: str
    comparable: bool
    compared_count: int
    dominant_peer: str
    peer_dominated: bool
    candidate_outperformed_peers: bool
    reason_codes: list[str]


class PeerComparisonPeerSummaryPayload(TypedDict):
    manager_id: str
    market_tag: str
    score: Any
    sample_count: Any
    cycle_id: Any


class PersistedPeerComparisonPayload(PeerComparisonCompactPayload):
    ranked_peers: list[PeerComparisonPeerSummaryPayload]


class ReviewDecisionSummaryCompactPayload(TypedDict):
    reasoning: str
    analysis: str
    verdict: str
    subject_type: str
    regime_summary: dict[str, Any]
    causal_diagnosis: dict[str, Any]
    param_adjustments: dict[str, Any]
    manager_budget_adjustments: dict[str, Any]
    agent_weight_adjustments: dict[str, Any]


class PersistedReviewDecisionSummaryPayload(ReviewDecisionSummaryCompactPayload):
    similarity_summary: NotRequired[SimilaritySummaryPersistedPayload]


class JudgeReportSummaryPayload(TypedDict, total=False):
    decision: str
    validation_status: str
    reason_codes: list[str]
    summary: str
    actionable: bool
    review_required: bool
    shadow_mode: bool
    next_actions: list[str]
    next_actions_truncated: bool


class RealismMetricsSummaryPayload(RealismMetricsPayload, total=False):
    pass


class PromotionRecordPersistedPayload(TypedDict, total=False):
    status: str
    gate_status: str
    applied_to_active: bool
    review_applied: bool
    active_runtime_config_ref: str
    candidate_runtime_config_ref: str
    applied_runtime_config_ref: str


class LineageRecordPersistedPayload(TypedDict, total=False):
    lineage_status: str
    active_runtime_config_ref: str
    candidate_runtime_config_ref: str
    parent_cycle_id: Any
    candidate_cycle_id: Any
    generation: Any


class PromotionRecordCompactPayload(TypedDict):
    status: str
    gate_status: str
    applied_to_active: bool


class LineageRecordCompactPayload(TypedDict):
    lineage_status: str


class SimulationContractStageSnapshotSummaryPayload(TypedDict):
    stage: str
    cycle_id: int
    cutoff_date: str
    regime: str
    selection_mode: str
    return_pct: float
    benchmark_passed: bool
    benchmark_strict_passed: bool


class ReviewContractStageSnapshotSummaryPayload(TypedDict):
    stage: str
    cycle_id: int
    analysis: str
    similarity_summary: SimilaritySummaryCompactPayload


class ValidationContractStageSnapshotSummaryPayload(TypedDict):
    stage: str
    cycle_id: int
    validation_task_id: str
    shadow_mode: bool
    validation_summary: ValidationSummaryCompactPayload
    judge_decision: str


class OutcomeContractStageSnapshotSummaryPayload(TypedDict):
    stage: str
    cycle_id: int
    promotion_record: PromotionRecordCompactPayload
    lineage_record: LineageRecordCompactPayload


ContractStageSnapshotSummaryPayload: TypeAlias = (
    SimulationContractStageSnapshotSummaryPayload
    | ReviewContractStageSnapshotSummaryPayload
    | ValidationContractStageSnapshotSummaryPayload
    | OutcomeContractStageSnapshotSummaryPayload
)


class ContractStageSnapshotsSummaryPayload(TypedDict, total=False):
    simulation: SimulationContractStageSnapshotSummaryPayload
    review: ReviewContractStageSnapshotSummaryPayload
    validation: ValidationContractStageSnapshotSummaryPayload
    outcome: OutcomeContractStageSnapshotSummaryPayload


class SimulationStageSnapshotPersistedPayload(TypedDict, total=False):
    stage: str
    cycle_id: int
    cutoff_date: str
    regime: str
    selection_mode: str
    selected_stocks: list[str]
    return_pct: float
    benchmark_passed: bool
    benchmark_strict_passed: bool


class ReviewStageSnapshotPersistedPayload(TypedDict, total=False):
    stage: str
    cycle_id: int
    analysis: str
    review_applied: bool
    similarity_summary: SimilaritySummaryPersistedPayload
    manager_review_report: PersistedManagerReviewDigestPayload
    allocation_review_report: PersistedAllocationReviewDigestPayload


class ValidationStageSnapshotPersistedPayload(TypedDict, total=False):
    stage: str
    cycle_id: int
    validation_task_id: str
    shadow_mode: bool
    validation_summary: PersistedValidationSummaryPayload
    market_tagging: PersistedTaggingDigestPayload
    failure_tagging: PersistedTaggingDigestPayload
    validation_tagging: PersistedTaggingDigestPayload
    judge_report: JudgeReportSummaryPayload


class OutcomeStageSnapshotPersistedPayload(TypedDict, total=False):
    stage: str
    cycle_id: int
    promotion_record: PromotionRecordPersistedPayload
    lineage_record: LineageRecordPersistedPayload
    realism_metrics: RealismMetricsSummaryPayload


StageSnapshotPersistedPayload: TypeAlias = (
    SimulationStageSnapshotPersistedPayload
    | ReviewStageSnapshotPersistedPayload
    | ValidationStageSnapshotPersistedPayload
    | OutcomeStageSnapshotPersistedPayload
)


class StageSnapshotsPersistedPayload(TypedDict, total=False):
    simulation: SimulationStageSnapshotPersistedPayload
    review: ReviewStageSnapshotPersistedPayload
    validation: ValidationStageSnapshotPersistedPayload
    outcome: OutcomeStageSnapshotPersistedPayload


def project_manager_compatibility(*args: Any, **kwargs: Any) -> Any:
    from invest_evolution.application.training.execution import (
        project_manager_compatibility as _project_manager_compatibility,
    )

    return _project_manager_compatibility(*args, **kwargs)


def _finite_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _finite_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _dict_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    try:
        return dict(value)
    except Exception:
        return {}


def _list_payload(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value is None:
        return []
    try:
        return list(value)
    except Exception:
        return []


def _normalize_governance_payload(
    payload: GovernanceDecisionInputPayload | dict[str, Any] | None = None,
    *,
    fallback: dict[str, Any] | None = None,
) -> GovernanceDecisionPayload:
    normalized = dict(
        normalize_governance_decision(_dict_payload(payload), fallback=fallback) or {}
    )
    dominant_manager_id = str(normalized.get("dominant_manager_id") or "").strip()
    active_manager_ids = [
        str(item).strip()
        for item in list(normalized.get("active_manager_ids") or [])
        if str(item).strip()
    ]
    if not active_manager_ids and dominant_manager_id:
        active_manager_ids = [dominant_manager_id]
    if not dominant_manager_id and active_manager_ids:
        dominant_manager_id = active_manager_ids[0]
    manager_budget_weights = {
        str(key): float(value)
        for key, value in dict(normalized.get("manager_budget_weights") or {}).items()
        if str(key).strip()
    }
    if not manager_budget_weights and dominant_manager_id:
        manager_budget_weights = {dominant_manager_id: 1.0}
    return cast(
        GovernanceDecisionPayload,
        {
            "as_of_date": str(normalized.get("as_of_date") or ""),
            "regime": str(normalized.get("regime") or "unknown"),
            "regime_confidence": _finite_float(normalized.get("regime_confidence")) or 0.0,
            "decision_confidence": _finite_float(normalized.get("decision_confidence")) or 0.0,
            "active_manager_ids": active_manager_ids,
            "manager_budget_weights": manager_budget_weights,
            "dominant_manager_id": dominant_manager_id,
            "cash_reserve_hint": _finite_float(normalized.get("cash_reserve_hint")) or 0.0,
            "portfolio_constraints": deepcopy(dict(normalized.get("portfolio_constraints") or {})),
            "decision_source": str(normalized.get("decision_source") or ""),
            "regime_source": str(normalized.get("regime_source") or ""),
            "reasoning": str(normalized.get("reasoning") or ""),
            "evidence": deepcopy(dict(normalized.get("evidence") or {})),
            "agent_advice": deepcopy(dict(normalized.get("agent_advice") or {})),
            "allocation_plan": deepcopy(dict(normalized.get("allocation_plan") or {})),
            "guardrail_checks": deepcopy(
                [dict(item) for item in list(normalized.get("guardrail_checks") or [])]
            ),
            "metadata": deepcopy(dict(normalized.get("metadata") or {})),
        },
    )


def _normalize_research_feedback_recommendation_payload(
    payload: dict[str, Any] | None = None,
) -> ResearchFeedbackRecommendationPayload:
    recommendation = dict(payload or {})
    return cast(
        ResearchFeedbackRecommendationPayload,
        {
            "bias": str(recommendation.get("bias") or ""),
            "summary": str(recommendation.get("summary") or ""),
        },
    )


def _normalize_research_feedback_scope_payload(
    payload: dict[str, Any] | None = None,
) -> ResearchFeedbackScopePayload:
    scope = dict(payload or {})
    return cast(
        ResearchFeedbackScopePayload,
        {
            "effective_scope": str(scope.get("effective_scope") or ""),
            "requested_regime": str(scope.get("requested_regime") or ""),
            "regime_sample_count": int(scope.get("regime_sample_count") or 0),
            "overall_sample_count": int(scope.get("overall_sample_count") or 0),
        },
    )


def _normalize_research_feedback_horizon_payload(
    payload: dict[str, Any] | None = None,
    *,
    label: str = "",
) -> ResearchFeedbackHorizonPayload:
    horizon = dict(payload or {})
    return cast(
        ResearchFeedbackHorizonPayload,
        {
            "hit_rate": _finite_float(horizon.get("hit_rate")) or 0.0,
            "invalidation_rate": _finite_float(horizon.get("invalidation_rate")) or 0.0,
            "interval_hit_rate": _finite_float(horizon.get("interval_hit_rate")) or 0.0,
            "label": str(horizon.get("label") or label or ""),
        },
    )


def _normalize_research_feedback_payload(
    payload: dict[str, Any] | None = None,
) -> ResearchFeedbackPayload:
    feedback = dict(payload or {})
    horizons = {
        str(key): _normalize_research_feedback_horizon_payload(dict(value or {}), label=str(key))
        for key, value in dict(feedback.get("horizons") or {}).items()
        if str(key).strip()
    }
    return cast(
        ResearchFeedbackPayload,
        {
            "enabled": bool(feedback.get("enabled", bool(feedback))),
            "passed": bool(feedback.get("passed", False)),
            "summary": str(feedback.get("summary") or ""),
            "trigger": str(feedback.get("trigger") or ""),
            "sample_count": int(feedback.get("sample_count") or 0),
            "brier_like_direction_score": _finite_float(
                feedback.get("brier_like_direction_score")
            )
            or 0.0,
            "recommendation": _normalize_research_feedback_recommendation_payload(
                dict(feedback.get("recommendation") or {})
            ),
            "horizons": horizons,
            "scope": _normalize_research_feedback_scope_payload(
                dict(feedback.get("scope") or {})
            ),
            "overall_feedback": deepcopy(dict(feedback.get("overall_feedback") or {})),
        },
    )


def _project_strategy_scores_payload(
    payload: StrategyScoresInputPayload | dict[str, Any] | None = None,
) -> StrategyScoresPayload:
    scores = dict(payload or {})
    return cast(
        StrategyScoresPayload,
        {
            "signal_accuracy": _finite_float(scores.get("signal_accuracy")) or 0.0,
            "timing_score": _finite_float(scores.get("timing_score")) or 0.0,
            "risk_control_score": _finite_float(scores.get("risk_control_score")) or 0.0,
            "overall_score": _finite_float(scores.get("overall_score")) or 0.0,
        },
    )


def _project_review_decision_payload(
    payload: ReviewDecisionInputPayload | dict[str, Any] | None = None,
) -> ReviewDecisionPayload:
    decision = dict(payload or {})
    return cast(
        ReviewDecisionPayload,
        {
            "reasoning": str(decision.get("reasoning") or ""),
            "verdict": str(decision.get("verdict") or ""),
            "subject_type": str(decision.get("subject_type") or ""),
            "regime_summary": deepcopy(_dict_payload(decision.get("regime_summary"))),
            "causal_diagnosis": deepcopy(_dict_payload(decision.get("causal_diagnosis"))),
            "param_adjustments": deepcopy(_dict_payload(decision.get("param_adjustments"))),
            "manager_budget_adjustments": deepcopy(
                _dict_payload(decision.get("manager_budget_adjustments"))
            ),
            "agent_weight_adjustments": deepcopy(
                _dict_payload(decision.get("agent_weight_adjustments"))
            ),
        },
    )


def _project_similarity_summary_payload(
    payload: SimilaritySummaryInputPayload | dict[str, Any] | None = None,
) -> SimilaritySummaryPayload:
    summary = dict(payload or {})
    matched_cycle_ids: list[int] = []
    for item in _list_payload(summary.get("matched_cycle_ids")):
        try:
            matched_cycle_ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return cast(
        SimilaritySummaryPayload,
        {
            "target_cycle_id": _finite_int(summary.get("target_cycle_id")) or 0,
            "matched_cycle_ids": matched_cycle_ids,
            "matched_cycle_ids_truncated": bool(
                summary.get("matched_cycle_ids_truncated", False)
            ),
            "match_count": _finite_int(summary.get("match_count")) or 0,
            "match_features": [
                str(item)
                for item in _list_payload(summary.get("match_features"))
                if str(item or "").strip()
            ],
            "dominant_regime": str(summary.get("dominant_regime") or ""),
            "similarity_band": str(summary.get("similarity_band") or ""),
            "summary": str(summary.get("summary") or ""),
            "compared_history_size": _finite_int(summary.get("compared_history_size")) or 0,
            "strict_failure_match_count": _finite_int(
                summary.get("strict_failure_match_count")
            )
            or 0,
            "matched_primary_driver": str(summary.get("matched_primary_driver") or ""),
            "matched_feedback_bias": str(summary.get("matched_feedback_bias") or ""),
            "avg_evidence_score": _finite_float(summary.get("avg_evidence_score")) or 0.0,
        },
    )


def _project_manager_review_digest(
    payload: dict[str, Any] | None = None,
) -> ManagerReviewDigestPayload:
    report = dict(payload or {})
    summary = dict(report.get("summary") or {})
    active_manager_ids = [
        str(item).strip()
        for item in list(summary.get("active_manager_ids") or [])
        if str(item).strip()
    ]
    verdict_counts = {
        str(key): int(value or 0)
        for key, value in dict(summary.get("verdict_counts") or {}).items()
    }
    return cast(
        ManagerReviewDigestPayload,
        {
            "subject_type": str(report.get("subject_type") or "manager_review"),
            "dominant_manager_id": str(report.get("dominant_manager_id") or ""),
            "summary": {
                "manager_count": int(summary.get("manager_count") or len(active_manager_ids)),
                "active_manager_ids": active_manager_ids,
                "verdict_counts": verdict_counts,
            },
            "manager_budget_adjustments": {
                str(key): _finite_float(value) or 0.0
                for key, value in dict(report.get("manager_budget_adjustments") or {}).items()
            },
        },
    )


def _project_persisted_manager_review_digest(
    payload: dict[str, Any] | None = None,
) -> PersistedManagerReviewDigestPayload:
    report = dict(payload or {})
    summary = dict(report.get("summary") or {})
    digest = cast(
        PersistedManagerReviewDigestPayload,
        {
            **_project_manager_review_digest(report),
            "as_of_date": str(report.get("as_of_date") or ""),
            "regime": str(report.get("regime") or ""),
            "recommended_actions": [
                str(item).strip()
                for item in list(summary.get("recommended_actions") or [])
                if str(item).strip()
            ],
            "review_basis_window": deepcopy(dict(summary.get("review_basis_window") or {})),
            "reasoning": str(summary.get("reasoning") or ""),
        },
    )
    return digest


def _project_allocation_review_digest(
    payload: dict[str, Any] | None = None,
) -> AllocationReviewDigestPayload:
    report = dict(payload or {})
    return cast(
        AllocationReviewDigestPayload,
        {
            "subject_type": str(report.get("subject_type") or "allocation_review"),
            "verdict": str(report.get("verdict") or ""),
            "active_manager_ids": [
                str(item).strip()
                for item in list(report.get("active_manager_ids") or [])
                if str(item).strip()
            ],
            "risk_flags": [
                str(item).strip()
                for item in list(report.get("risk_flags") or [])
                if str(item).strip()
            ],
        },
    )


def _project_persisted_allocation_review_digest(
    payload: dict[str, Any] | None = None,
) -> PersistedAllocationReviewDigestPayload:
    report = dict(payload or {})
    summary = dict(report.get("summary") or {})
    return cast(
        PersistedAllocationReviewDigestPayload,
        {
            **_project_allocation_review_digest(report),
            "as_of_date": str(report.get("as_of_date") or ""),
            "regime": str(report.get("regime") or ""),
            "summary": {
                "portfolio_selected_count": int(summary.get("portfolio_selected_count") or 0),
                "overlap_code_count": int(summary.get("overlap_code_count") or 0),
                "max_position_weight": _finite_float(summary.get("max_position_weight")) or 0.0,
            },
        },
    )


def _project_review_applied_effects_payload(
    payload: dict[str, Any] | None = None,
) -> ReviewAppliedEffectsPayload:
    effects = dict(payload or {})
    projected = cast(ReviewAppliedEffectsPayload, {})
    param_adjustments = deepcopy(_dict_payload(effects.get("param_adjustments")))
    if param_adjustments:
        projected["param_adjustments"] = param_adjustments
    manager_budget_adjustments = {
        str(key): _finite_float(value) or 0.0
        for key, value in dict(
            effects.get("manager_budget_adjustments") or {}
        ).items()
    }
    if manager_budget_adjustments:
        projected["manager_budget_adjustments"] = manager_budget_adjustments
    agent_weight_adjustments = {
        str(key): _finite_float(value) or 0.0
        for key, value in dict(
            effects.get("agent_weight_adjustments") or {}
        ).items()
    }
    if agent_weight_adjustments:
        projected["agent_weight_adjustments"] = agent_weight_adjustments
    return projected


def _project_persisted_optimization_event(
    payload: dict[str, Any] | None = None,
) -> PersistedOptimizationEventPayload:
    event = dict(payload or {})
    persisted = cast(
        PersistedOptimizationEventPayload,
        {
            "event_id": str(event.get("event_id") or ""),
            "contract_version": str(event.get("contract_version") or ""),
            "cycle_id": _finite_int(event.get("cycle_id")),
            "trigger": str(event.get("trigger") or ""),
            "stage": str(event.get("stage") or ""),
            "status": str(event.get("status") or ""),
            "notes": str(event.get("notes") or ""),
            "ts": str(event.get("ts") or ""),
            "lineage": deepcopy(_dict_payload(event.get("lineage"))),
        },
    )
    review_decision_payload = dict(event.get("review_decision_payload") or {})
    if review_decision_payload:
        persisted["review_decision_payload"] = cast(
            ReviewDecisionOptimizationStagePayload,
            deepcopy(review_decision_payload),
        )
    review_applied_effects_payload = dict(event.get("review_applied_effects_payload") or {})
    if review_applied_effects_payload:
        persisted["review_applied_effects_payload"] = _project_review_applied_effects_payload(
            review_applied_effects_payload
        )
    research_feedback_payload = dict(event.get("research_feedback_payload") or {})
    if research_feedback_payload:
        persisted["research_feedback_payload"] = cast(
            ResearchFeedbackOptimizationStagePayload,
            deepcopy(research_feedback_payload),
        )
    llm_analysis_payload = dict(event.get("llm_analysis_payload") or {})
    if llm_analysis_payload:
        persisted["llm_analysis_payload"] = cast(
            LlmAnalysisOptimizationStagePayload,
            deepcopy(llm_analysis_payload),
        )
    evolution_engine_payload = dict(event.get("evolution_engine_payload") or {})
    if evolution_engine_payload:
        persisted["evolution_engine_payload"] = cast(
            EvolutionEngineOptimizationStagePayload,
            deepcopy(evolution_engine_payload),
        )
    runtime_config_mutation_payload = dict(event.get("runtime_config_mutation_payload") or {})
    if runtime_config_mutation_payload:
        persisted["runtime_config_mutation_payload"] = cast(
            RuntimeConfigMutationOptimizationStagePayload,
            deepcopy(runtime_config_mutation_payload),
        )
    runtime_config_mutation_skipped_payload = dict(
        event.get("runtime_config_mutation_skipped_payload") or {}
    )
    if runtime_config_mutation_skipped_payload:
        persisted["runtime_config_mutation_skipped_payload"] = cast(
            RuntimeConfigMutationSkippedOptimizationStagePayload,
            deepcopy(runtime_config_mutation_skipped_payload),
        )
    optimization_error_payload = dict(event.get("optimization_error_payload") or {})
    if optimization_error_payload:
        persisted["optimization_error_payload"] = cast(
            OptimizationErrorStagePayload,
            deepcopy(optimization_error_payload),
        )
    return persisted


def project_manager_review_digest(
    payload: dict[str, Any] | None = None,
) -> ManagerReviewDigestPayload:
    return _project_manager_review_digest(payload)


def project_persisted_manager_review_digest(
    payload: dict[str, Any] | None = None,
) -> PersistedManagerReviewDigestPayload:
    return _project_persisted_manager_review_digest(payload)


def project_allocation_review_digest(
    payload: dict[str, Any] | None = None,
) -> AllocationReviewDigestPayload:
    return _project_allocation_review_digest(payload)


def project_persisted_allocation_review_digest(
    payload: dict[str, Any] | None = None,
) -> PersistedAllocationReviewDigestPayload:
    return _project_persisted_allocation_review_digest(payload)


def project_review_applied_effects_payload(
    payload: dict[str, Any] | None = None,
) -> ReviewAppliedEffectsPayload:
    return _project_review_applied_effects_payload(payload)


def project_persisted_optimization_event(
    payload: dict[str, Any] | None = None,
) -> PersistedOptimizationEventPayload:
    return _project_persisted_optimization_event(payload)


def _project_similar_result_payload(
    payload: dict[str, Any] | SimilarResultCompactInputPayload | SimilarResultCompactPayload | None = None,
) -> SimilarResultCompactPayload:
    result = dict(payload or {})
    failure_signature = _dict_payload(result.get("failure_signature"))
    return cast(
        SimilarResultCompactPayload,
        {
            "cycle_id": _finite_int(result.get("cycle_id")) or 0,
            "cutoff_date": str(result.get("cutoff_date") or ""),
            "return_pct": _finite_float(result.get("return_pct")) or 0.0,
            "is_profit": bool(result.get("is_profit", False)),
            "benchmark_passed": bool(result.get("benchmark_passed", False)),
            "review_applied": bool(result.get("review_applied", False)),
            "regime": str(result.get("regime") or "unknown"),
            "selection_mode": str(result.get("selection_mode") or "unknown"),
            "manager_id": str(result.get("manager_id") or ""),
            "manager_config_ref": str(result.get("manager_config_ref") or ""),
            "similarity_score": _finite_int(
                result.get("similarity_score") or result.get("score")
            )
            or 0,
            "matched_features": [
                str(item)
                for item in _list_payload(result.get("matched_features"))
                if str(item or "").strip()
            ],
            "strict_failure_match": bool(result.get("strict_failure_match", False)),
            "evidence_score": _finite_int(result.get("evidence_score")) or 0,
            "failure_signature": failure_signature,
        },
    )


def _project_validation_summary_payload(
    payload: dict[str, Any] | None = None,
) -> ValidationSummaryPayload:
    summary = dict(payload or {})
    return cast(
        ValidationSummaryPayload,
        {
            "contract_version": str(summary.get("contract_version") or "validation.v1"),
            "validation_task_id": str(summary.get("validation_task_id") or ""),
            "status": str(summary.get("status") or "hold"),
            "shadow_mode": bool(summary.get("shadow_mode", False)),
            "review_required": bool(summary.get("review_required", False)),
            "confidence_score": _finite_float(summary.get("confidence_score")) or 0.0,
            "validation_tags": deepcopy(list(summary.get("validation_tags") or [])),
            "reason_codes": deepcopy(list(summary.get("reason_codes") or [])),
            "checks": deepcopy([dict(item) for item in list(summary.get("checks") or [])]),
            "failed_checks": deepcopy(
                [dict(item) for item in list(summary.get("failed_checks") or [])]
            ),
            "raw_evidence": deepcopy(dict(summary.get("raw_evidence") or {})),
            "summary": str(summary.get("summary") or ""),
        },
    )


def _coerce_stage_snapshots_input(
    payload: StageSnapshotsInputPayload | dict[str, Any] | None = None,
) -> StageSnapshotsInputPayload:
    return cast(StageSnapshotsInputPayload, deepcopy(dict(payload or {})))


def _coerce_strategy_scores_input(
    payload: StrategyScoresInputPayload | dict[str, Any] | None = None,
) -> StrategyScoresInputPayload:
    return cast(StrategyScoresInputPayload, deepcopy(dict(payload or {})))


def _coerce_governance_decision_input(
    payload: GovernanceDecisionInputPayload | dict[str, Any] | None = None,
) -> GovernanceDecisionInputPayload:
    return cast(GovernanceDecisionInputPayload, deepcopy(dict(payload or {})))


def _coerce_review_decision_input(
    payload: ReviewDecisionInputPayload | dict[str, Any] | None = None,
) -> ReviewDecisionInputPayload:
    return cast(ReviewDecisionInputPayload, deepcopy(dict(payload or {})))


def _coerce_similar_results_input(
    payload: Any = None,
) -> list[SimilarResultCompactPayload]:
    return [
        _project_similar_result_payload(item)
        for item in _list_payload(payload)
    ]


def _coerce_similarity_summary_input(
    payload: SimilaritySummaryInputPayload | dict[str, Any] | None = None,
) -> SimilaritySummaryInputPayload:
    return cast(SimilaritySummaryInputPayload, deepcopy(dict(payload or {})))


def _coerce_validation_report_input(
    payload: ValidationReportInputPayload | dict[str, Any] | None = None,
) -> ValidationReportInputPayload:
    return cast(ValidationReportInputPayload, deepcopy(dict(payload or {})))


def _resolve_cycle_payload(
    *,
    cycle_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return deepcopy(dict(cycle_payload or {}))


@dataclass(frozen=True)
class _RuntimeMutationProjection:
    mutation_event: dict[str, Any]
    candidate_runtime_config_ref: str
    auto_applied: bool


@dataclass(frozen=True)
class _ManagerScopeProjectionState:
    projection: Any
    active_runtime_config_ref: str
    manager_config_ref: str
    dominant_manager_id: str
    subject_type: str
    execution_defaults: ExecutionDefaultsPayload


@dataclass(frozen=True)
class _ContractStageInputs:
    payload: dict[str, Any]
    execution_snapshot: dict[str, Any]
    stage_snapshots: StageSnapshotsInputPayload
    simulation_envelope: "SimulationStageEnvelope"
    review_envelope: "ReviewStageEnvelope"


def _normalize_execution_defaults_payload(
    payload: dict[str, Any] | None = None,
) -> ExecutionDefaultsPayload:
    execution_defaults = deepcopy(dict(payload or {}))
    if execution_defaults.get("default_manager_config_ref"):
        execution_defaults["default_manager_config_ref"] = _normalize_config_ref(
            execution_defaults.get("default_manager_config_ref")
        )
    return cast(ExecutionDefaultsPayload, execution_defaults)


def _resolve_runtime_mutation_projection(
    optimization_events: list[dict[str, Any]] | None = None,
) -> _RuntimeMutationProjection:
    mutation_event = latest_runtime_config_mutation_event(optimization_events)
    return _RuntimeMutationProjection(
        mutation_event=mutation_event,
        candidate_runtime_config_ref=_runtime_config_mutation_candidate_runtime_config_ref(
            mutation_event
        ),
        auto_applied=_runtime_config_mutation_auto_applied(mutation_event),
    )


def _resolve_manager_scope_projection_state(
    *,
    manager_output: Any | None,
    governance_decision: (
        GovernanceDecisionPayload
        | GovernanceDecisionInputPayload
        | dict[str, Any]
        | None
    ) = None,
    portfolio_plan: dict[str, Any] | None = None,
    manager_results: list[dict[str, Any]] | list[Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    dominant_manager_id_hint: str = "",
) -> _ManagerScopeProjectionState:
    projection = project_manager_compatibility(
        None,
        manager_output=manager_output,
        governance_decision=dict(governance_decision or {}),
        portfolio_plan=dict(portfolio_plan or {}),
        manager_results=list(manager_results or []),
        execution_snapshot=dict(execution_snapshot or {}),
        dominant_manager_id_hint=str(dominant_manager_id_hint or ""),
    )
    active_runtime_config_ref = _normalize_config_ref(
        getattr(projection, "active_runtime_config_ref", "")
    )
    manager_config_ref = (
        _normalize_config_ref(getattr(projection, "manager_config_ref", ""))
        or active_runtime_config_ref
    )
    dominant_manager_id = str(
        getattr(projection, "dominant_manager_id", "")
        or getattr(projection, "manager_id", "")
        or ""
    )
    subject_type = str(
        getattr(projection, "subject_type", "") or "single_manager"
    )
    return _ManagerScopeProjectionState(
        projection=projection,
        active_runtime_config_ref=active_runtime_config_ref,
        manager_config_ref=manager_config_ref,
        dominant_manager_id=dominant_manager_id,
        subject_type=subject_type,
        execution_defaults=_normalize_execution_defaults_payload(
            dict(getattr(projection, "execution_defaults", {}) or {})
        ),
    )


def build_review_basis_window(
    controller: Any,
    *,
    cycle_id: int,
    review_window: dict[str, Any] | None = None,
) -> ReviewBasisWindowPayload:
    normalized = normalize_review_window(review_window)
    size = max(1, int(normalized.get("size") or 1))
    if str(normalized.get("mode") or "single_cycle") == "single_cycle":
        return cast(ReviewBasisWindowPayload, {
            "mode": "single_cycle",
            "size": 1,
            "cycle_ids": [int(cycle_id)],
            "current_cycle_id": int(cycle_id),
        })
    previous_cycle_ids = [
        int(getattr(item, "cycle_id"))
        for item in list(session_cycle_history(controller) or [])
        if getattr(item, "cycle_id", None) is not None
    ]
    basis_cycle_ids = (previous_cycle_ids[-max(0, size - 1):] + [int(cycle_id)])[-size:]
    return cast(ReviewBasisWindowPayload, {
        "mode": str(normalized.get("mode") or "single_cycle"),
        "size": size,
        "cycle_ids": basis_cycle_ids,
        "current_cycle_id": int(cycle_id),
    })


def _fitness_source_cycles(
    controller: Any,
    optimization_events: list[dict[str, Any]] | None = None,
) -> list[int]:
    if not latest_runtime_config_mutation_event(optimization_events):
        return []
    return [
        int(getattr(item, "cycle_id"))
        for item in list(session_cycle_history(controller) or [])[-10:]
        if getattr(item, "cycle_id", None) is not None
    ]


def latest_runtime_config_mutation_event(
    optimization_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    for event in reversed(list(optimization_events or [])):
        if str(event.get("stage") or "") in {
            "runtime_config_mutation",
            "runtime_config_mutation_skipped",
        }:
            return dict(event)
    return {}


def _runtime_config_mutation_stage_payload(
    mutation_event: dict[str, Any] | None = None,
) -> (
    RuntimeConfigMutationOptimizationStagePayload
    | RuntimeConfigMutationSkippedOptimizationStagePayload
):
    event = dict(mutation_event or {})
    runtime_payload = dict(event.get("runtime_config_mutation_payload") or {})
    if runtime_payload:
        return cast(RuntimeConfigMutationOptimizationStagePayload, runtime_payload)
    skipped_payload = dict(event.get("runtime_config_mutation_skipped_payload") or {})
    if skipped_payload:
        return cast(RuntimeConfigMutationSkippedOptimizationStagePayload, skipped_payload)
    return cast(
        RuntimeConfigMutationOptimizationStagePayload
        | RuntimeConfigMutationSkippedOptimizationStagePayload,
        {},
    )


def _runtime_config_mutation_candidate_runtime_config_ref(
    mutation_event: dict[str, Any] | None = None,
) -> str:
    stage_payload = dict(_runtime_config_mutation_stage_payload(mutation_event))
    raw_ref = str(
        stage_payload.get("runtime_config_ref")
        or stage_payload.get("pending_candidate_runtime_config_ref")
        or ""
    ).strip()
    return _normalize_config_ref(raw_ref) or raw_ref


def _runtime_config_mutation_auto_applied(
    mutation_event: dict[str, Any] | None = None,
) -> bool:
    stage_payload = dict(_runtime_config_mutation_stage_payload(mutation_event))
    return bool(stage_payload.get("auto_applied", False))


def _promotion_decision(
    *,
    controller: Any,
    active_runtime_config_ref: str,
    candidate_runtime_config_ref: str,
    auto_applied: bool,
    mutation_event: dict[str, Any] | None = None,
) -> PromotionDecisionPayload:
    policy = dict(getattr(controller, "experiment_promotion_policy", {}) or {})
    if not candidate_runtime_config_ref:
        return cast(PromotionDecisionPayload, {
            "status": "not_evaluated",
            "source": "controller_cycle",
            "reason": "no_candidate_runtime_config_generated",
            "applied_to_active": False,
            "active_runtime_config_ref": active_runtime_config_ref,
            "candidate_runtime_config_ref": "",
            "policy": policy,
        })

    status = "candidate_auto_applied" if auto_applied else "candidate_generated"
    reason = (
        "candidate runtime config auto-applied"
        if auto_applied
        else "candidate runtime config generated; active runtime config unchanged"
    )
    return cast(PromotionDecisionPayload, {
        "status": status,
        "source": "runtime_config_mutation",
        "reason": str((mutation_event or {}).get("notes") or reason),
        "applied_to_active": bool(auto_applied),
        "active_runtime_config_ref": active_runtime_config_ref,
        "candidate_runtime_config_ref": candidate_runtime_config_ref,
        "policy": policy,
    })


def _run_context_scope_snapshot(
    *,
    snapshot: dict[str, Any],
    manager_output: Any | None,
    candidate_runtime_config_ref: str,
    auto_applied: bool,
) -> dict[str, Any]:
    execution_defaults = dict(snapshot.get("execution_defaults") or {})
    return {
        **snapshot,
        "active_runtime_config_ref": _normalize_config_ref(
            (
                candidate_runtime_config_ref
                if auto_applied and candidate_runtime_config_ref
                else snapshot.get("active_runtime_config_ref")
            )
            or snapshot.get("manager_config_ref")
            or execution_defaults.get("default_manager_config_ref")
            or getattr(manager_output, "manager_config_ref", "")
            or ""
        ),
        "manager_config_ref": _normalize_config_ref(
            snapshot.get("manager_config_ref")
            or execution_defaults.get("default_manager_config_ref")
            or getattr(manager_output, "manager_config_ref", "")
            or ""
        ),
    }


def _execution_snapshot_scope_seed(
    *,
    compatibility_fields: dict[str, Any],
    portfolio_plan: dict[str, Any],
    manager_output: Any | None,
) -> dict[str, Any]:
    resolved_manager_config_ref = _normalize_config_ref(
        dict(compatibility_fields or {}).get("manager_config_ref")
        or dict(portfolio_plan or {}).get("metadata", {}).get("dominant_manager_config")
        or getattr(manager_output, "manager_config_ref", "")
        or ""
    )
    return {
        "active_runtime_config_ref": resolved_manager_config_ref,
        "manager_config_ref": resolved_manager_config_ref,
    }


def build_cycle_run_context(
    controller: Any,
    *,
    cycle_id: int,
    manager_output: Any | None,
    optimization_events: list[dict[str, Any]] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    evaluation_context: dict[str, Any] | None = None,
) -> RunContextPayload:
    snapshot = dict(execution_snapshot or {})
    evaluation = dict(evaluation_context or {})
    raw_governance_payload = dict(snapshot.get("governance_decision", {}) or {})
    governance_payload = _normalize_governance_payload(
        raw_governance_payload,
        fallback=governance_from_controller(controller),
    )
    mutation_projection = _resolve_runtime_mutation_projection(optimization_events)
    scope_state = _resolve_manager_scope_projection_state(
        manager_output=manager_output,
        governance_decision=raw_governance_payload,
        portfolio_plan=dict(snapshot.get("portfolio_plan") or {}),
        manager_results=list(snapshot.get("manager_results") or []),
        execution_snapshot=_run_context_scope_snapshot(
            snapshot=snapshot,
            manager_output=manager_output,
            candidate_runtime_config_ref=mutation_projection.candidate_runtime_config_ref,
            auto_applied=mutation_projection.auto_applied,
        ),
        dominant_manager_id_hint=str(
            snapshot.get("dominant_manager_id")
            or getattr(manager_output, "manager_id", "")
            or ""
        ),
    )
    active_runtime_config_ref = scope_state.active_runtime_config_ref
    execution_defaults = _normalize_execution_defaults_payload(
        dict(snapshot.get("execution_defaults") or scope_state.execution_defaults or {})
    )
    review_window = dict(getattr(controller, "experiment_review_window", {}) or {})

    context: dict[str, Any] = {
        "basis_stage": str(snapshot.get("basis_stage") or "post_cycle_result"),
        "active_runtime_config_ref": active_runtime_config_ref,
        "candidate_runtime_config_ref": mutation_projection.candidate_runtime_config_ref,
        "shadow_mode": bool(
            dict(getattr(controller, "experiment_protocol", {}) or {})
            .get("protocol", {})
            .get("shadow_mode", False)
        ),
        "runtime_overrides": deepcopy(
            dict(snapshot.get("runtime_overrides") or session_current_params(controller) or {})
        ),
        "review_basis_window": build_review_basis_window(
            controller,
            cycle_id=int(cycle_id),
            review_window=review_window,
        ),
        "fitness_source_cycles": _fitness_source_cycles(
            controller,
            optimization_events=optimization_events,
        ),
        "ab_comparison": deepcopy(dict(evaluation.get("ab_comparison") or {})),
        "research_feedback": cast(
            ResearchFeedbackPayload,
            _normalize_research_feedback_payload(
                deepcopy(dict(evaluation.get("research_feedback") or {}))
            ),
        ),
        "promotion_decision": _promotion_decision(
            controller=controller,
            active_runtime_config_ref=active_runtime_config_ref,
            candidate_runtime_config_ref=mutation_projection.candidate_runtime_config_ref,
            auto_applied=mutation_projection.auto_applied,
            mutation_event=mutation_projection.mutation_event,
        ),
        "subject_type": str(snapshot.get("subject_type") or scope_state.subject_type or "single_manager"),
        "dominant_manager_id": str(
            snapshot.get("dominant_manager_id")
            or scope_state.dominant_manager_id
            or getattr(scope_state.projection, "manager_id", "")
            or ""
        ),
        "manager_config_ref": (
            _normalize_config_ref(snapshot.get("manager_config_ref"))
            or scope_state.manager_config_ref
            or active_runtime_config_ref
        ),
        "execution_defaults": cast(ExecutionDefaultsPayload, execution_defaults),
        "manager_results": deepcopy(list(snapshot.get("manager_results") or [])),
        "portfolio_plan": deepcopy(dict(snapshot.get("portfolio_plan") or {})),
        "active_manager_ids": deepcopy(
            list(dict(snapshot.get("portfolio_plan") or {}).get("active_manager_ids") or [])
        ),
        "governance_decision": cast(GovernanceDecisionPayload, deepcopy(governance_payload)),
        "portfolio_attribution": deepcopy(dict(evaluation.get("portfolio_attribution") or {})),
        "manager_review_report": _project_manager_review_digest(
            deepcopy(dict(evaluation.get("manager_review_report") or {}))
        ),
        "allocation_review_report": _project_allocation_review_digest(
            deepcopy(dict(evaluation.get("allocation_review_report") or {}))
        ),
        "compatibility_fields": deepcopy(dict(snapshot.get("compatibility_fields") or {})),
    }
    discipline = evaluate_promotion_discipline(
        run_context=cast(dict[str, Any], context),
        cycle_history=list(session_cycle_history(controller) or []),
        policy=dict((getattr(controller, "quality_gate_matrix", {}) or {}).get("promotion") or {}),
        optimization_events=optimization_events,
    )
    context["deployment_stage"] = str(discipline.get("deployment_stage") or "active")
    context["promotion_discipline"] = discipline
    context["quality_gate_matrix"] = cast(
        dict[str, Any],
        resolve_governance_matrix(dict(getattr(controller, "quality_gate_matrix", {}) or {})),
    )
    context["resolved_train_policy"] = cast(dict[str, Any], {
        "promotion_gate": normalize_promotion_gate_policy(
            dict(getattr(controller, "promotion_gate_policy", {}) or {})
        ),
        "freeze_gate": normalize_freeze_gate_policy(
            dict(getattr(controller, "freeze_gate_policy", {}) or {})
        ),
        "quality_gate_matrix": dict(cast(dict[str, Any], context["quality_gate_matrix"])),
    })
    context["governance_stage"] = str(
        infer_deployment_stage(
            run_context=cast(dict[str, Any], context),
            optimization_events=optimization_events,
        )
        or ""
    )
    if getattr(controller, "experiment_protocol", None):
        context["experiment_protocol"] = deepcopy(
            dict(getattr(controller, "experiment_protocol", {}) or {})
        )
    return cast(RunContextPayload, context)


def build_execution_snapshot(
    controller: Any,
    *,
    cycle_id: int,
    manager_output: Any | None,
    selection_mode: str = "",
    benchmark_passed: bool = False,
    basis_stage: str = "pre_optimization",
    manager_results: list[dict[str, Any]] | None = None,
    portfolio_plan: dict[str, Any] | None = None,
    dominant_manager_id: str = "",
    compatibility_fields: dict[str, Any] | None = None,
) -> ExecutionSnapshotPayload:
    governance_payload = _normalize_governance_payload(governance_from_controller(controller))
    scope_state = _resolve_manager_scope_projection_state(
        manager_output=manager_output,
        governance_decision=governance_payload,
        portfolio_plan=dict(portfolio_plan or {}),
        manager_results=list(manager_results or []),
        execution_snapshot=_execution_snapshot_scope_seed(
            compatibility_fields=dict(compatibility_fields or {}),
            portfolio_plan=dict(portfolio_plan or {}),
            manager_output=manager_output,
        ),
        dominant_manager_id_hint=str(
            dominant_manager_id or getattr(manager_output, "manager_id", "") or ""
        ),
    )
    return cast(ExecutionSnapshotPayload, {
        "basis_stage": str(basis_stage or "pre_optimization"),
        "cycle_id": int(cycle_id),
        "active_runtime_config_ref": scope_state.active_runtime_config_ref,
        "manager_id": getattr(scope_state.projection, "manager_id", ""),
        "manager_config_ref": scope_state.manager_config_ref,
        "execution_defaults": cast(ExecutionDefaultsPayload, scope_state.execution_defaults),
        "runtime_overrides": deepcopy(dict(session_current_params(controller) or {})),
        "governance_decision": cast(GovernanceDecisionPayload, deepcopy(governance_payload)),
        "selection_mode": str(selection_mode or ""),
        "benchmark_passed": bool(benchmark_passed),
        "subject_type": scope_state.subject_type,
        "dominant_manager_id": scope_state.dominant_manager_id or getattr(scope_state.projection, "manager_id", ""),
        "manager_results": deepcopy(list(manager_results or [])),
        "portfolio_plan": deepcopy(dict(portfolio_plan or {})),
        "compatibility_fields": deepcopy(dict(compatibility_fields or {})),
    })


def _resolve_contract_stage_inputs(
    *,
    cycle_payload: dict[str, Any] | None = None,
    simulation_envelope: "SimulationStageEnvelope | None" = None,
    review_envelope: "ReviewStageEnvelope | None" = None,
    execution_snapshot: dict[str, Any] | None = None,
    review_decision: ReviewDecisionInputPayload | dict[str, Any] | None = None,
) -> _ContractStageInputs:
    payload = _resolve_cycle_payload(cycle_payload=cycle_payload)
    if review_decision and not payload.get("review_decision"):
        payload["review_decision"] = dict(review_decision)
    resolved_execution_snapshot = dict(
        execution_snapshot or payload.get("execution_snapshot") or {}
    )
    resolved_stage_snapshots = _coerce_stage_snapshots_input(
        payload.get("stage_snapshots") or {}
    )
    resolved_simulation = simulation_envelope
    if resolved_simulation is None:
        resolved_simulation = SimulationStageEnvelope.from_cycle_payload(
            cycle_payload=payload,
            execution_snapshot=resolved_execution_snapshot,
            stage_snapshots=resolved_stage_snapshots,
        )
    resolved_review = review_envelope
    if resolved_review is None:
        resolved_review = ReviewStageEnvelope.from_cycle_payload(
            cycle_payload=payload,
            simulation_envelope=resolved_simulation,
            stage_snapshots=resolved_stage_snapshots,
        )
    resolved_execution_snapshot = dict(
        resolved_execution_snapshot
        or resolved_simulation.execution_snapshot
        or {}
    )
    return _ContractStageInputs(
        payload=payload,
        execution_snapshot=resolved_execution_snapshot,
        stage_snapshots=resolved_stage_snapshots,
        simulation_envelope=resolved_simulation,
        review_envelope=resolved_review,
    )


def _simulation_stage_snapshot_from_envelope(
    simulation_envelope: "SimulationStageEnvelope",
    *,
    execution_snapshot: dict[str, Any],
) -> SimulationStageSnapshotPayload:
    existing_snapshot = deepcopy(
        dict(simulation_envelope.stage_snapshots.get("simulation") or {})
    )
    if existing_snapshot:
        return cast(SimulationStageSnapshotPayload, existing_snapshot)
    return build_simulation_stage_snapshot_from_fields(
        cycle_id=int(simulation_envelope.cycle_id),
        cutoff_date=str(simulation_envelope.cutoff_date or ""),
        regime=str(simulation_envelope.regime or "unknown"),
        selection_mode=str(
            dict(simulation_envelope.stage_snapshots.get("simulation") or {}).get(
                "selection_mode"
            )
            or ""
        ),
        selected_stocks=list(simulation_envelope.selected_stocks or []),
        return_pct=float(simulation_envelope.return_pct or 0.0),
        benchmark_passed=bool(simulation_envelope.benchmark_passed),
        benchmark_strict_passed=bool(simulation_envelope.benchmark_strict_passed),
        strategy_scores=deepcopy(dict(simulation_envelope.strategy_scores or {})),
        governance_decision=deepcopy(
            dict(simulation_envelope.governance_decision or {})
        ),
        execution_snapshot=execution_snapshot,
    )


def _review_stage_snapshot_from_envelope(
    review_envelope: "ReviewStageEnvelope",
) -> ReviewStageSnapshotPayload:
    existing_snapshot = deepcopy(
        dict(review_envelope.stage_snapshots.get("review") or {})
    )
    if existing_snapshot:
        return cast(ReviewStageSnapshotPayload, existing_snapshot)
    return build_review_stage_snapshot_from_fields(
        cycle_id=int(review_envelope.simulation.cycle_id),
        analysis=str(review_envelope.analysis or ""),
        review_decision=deepcopy(dict(review_envelope.review_decision or {})),
        causal_diagnosis=deepcopy(dict(review_envelope.causal_diagnosis or {})),
        similarity_summary=deepcopy(dict(review_envelope.similarity_summary or {})),
        similar_results=_coerce_similar_results_input(
            list(review_envelope.similar_results or [])
        ),
        manager_review_report=deepcopy(
            dict(review_envelope.manager_review_report or {})
        ),
        allocation_review_report=deepcopy(
            dict(review_envelope.allocation_review_report or {})
        ),
        ab_comparison=deepcopy(dict(review_envelope.ab_comparison or {})),
    )


def build_cycle_contract_stage_snapshots(
    *,
    cycle_payload: dict[str, Any] | None = None,
    simulation_envelope: SimulationStageEnvelope | None = None,
    review_envelope: ReviewStageEnvelope | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    review_decision: ReviewDecisionInputPayload | dict[str, Any] | None = None,
    validation_report: ValidationReportInputPayload | None = None,
    run_context: dict[str, Any] | None = None,
) -> StageSnapshotsPayload:
    inputs = _resolve_contract_stage_inputs(
        cycle_payload=cycle_payload,
        simulation_envelope=simulation_envelope,
        review_envelope=review_envelope,
        execution_snapshot=execution_snapshot,
        review_decision=review_decision,
    )
    return cast(StageSnapshotsPayload, {
        "simulation": _simulation_stage_snapshot_from_envelope(
            inputs.simulation_envelope,
            execution_snapshot=inputs.execution_snapshot,
        ),
        "review": _review_stage_snapshot_from_envelope(inputs.review_envelope),
        "validation": build_validation_stage_snapshot(
            cycle_id=int(inputs.simulation_envelope.cycle_id),
            validation_report=validation_report,
        ),
        "outcome": build_outcome_stage_snapshot(
            cycle_id=int(
                inputs.simulation_envelope.cycle_id
                or inputs.execution_snapshot.get("cycle_id")
                or 0
            ),
            execution_snapshot=inputs.execution_snapshot,
            run_context=run_context,
        ),
    })


def _project_execution_snapshot(
    snapshot: dict[str, Any] | None = None,
) -> ExecutionSnapshotProjectionPayload:
    payload = dict(snapshot or {})
    return cast(ExecutionSnapshotProjectionPayload, {
        "basis_stage": str(payload.get("basis_stage") or ""),
        "active_runtime_config_ref": str(payload.get("active_runtime_config_ref") or ""),
        "manager_config_ref": str(payload.get("manager_config_ref") or ""),
        "dominant_manager_id": str(payload.get("dominant_manager_id") or payload.get("manager_id") or ""),
        "subject_type": str(payload.get("subject_type") or ""),
        "execution_defaults": deepcopy(dict(payload.get("execution_defaults") or {})),
    })


def build_simulation_stage_snapshot(
    cycle_payload: dict[str, Any] | None = None,
    *,
    execution_snapshot: dict[str, Any] | None = None,
) -> SimulationStageSnapshotPayload:
    payload = _resolve_cycle_payload(cycle_payload=cycle_payload)
    return cast(
        SimulationStageSnapshotPayload,
        {
        "contract_version": CYCLE_STAGE_SNAPSHOT_CONTRACT_VERSION,
        "stage": "simulation",
        "cycle_id": int(payload.get("cycle_id") or 0),
        "cutoff_date": str(payload.get("cutoff_date") or ""),
        "regime": str(payload.get("regime") or "unknown"),
        "selection_mode": str(payload.get("selection_mode") or ""),
        "selected_stocks": deepcopy(list(payload.get("selected_stocks") or [])),
        "return_pct": float(payload.get("return_pct") or 0.0),
        "benchmark_passed": bool(payload.get("benchmark_passed", False)),
        "benchmark_strict_passed": bool(payload.get("benchmark_strict_passed", False)),
        "strategy_scores": _project_strategy_scores_payload(
            deepcopy(dict(payload.get("strategy_scores") or {}))
        ),
        "governance_decision": _normalize_governance_payload(
            deepcopy(dict(payload.get("governance_decision") or {}))
        ),
        "execution_snapshot": _project_execution_snapshot(execution_snapshot),
        },
    )


def build_simulation_stage_snapshot_from_fields(
    *,
    cycle_id: int,
    cutoff_date: str,
    regime: str = "unknown",
    selection_mode: str = "",
    selected_stocks: list[str] | None = None,
    return_pct: float = 0.0,
    benchmark_passed: bool = False,
    benchmark_strict_passed: bool = False,
    strategy_scores: StrategyScoresInputPayload | dict[str, Any] | None = None,
    governance_decision: GovernanceDecisionInputPayload | dict[str, Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
) -> SimulationStageSnapshotPayload:
    payload = {
        "cycle_id": int(cycle_id),
        "cutoff_date": str(cutoff_date or ""),
        "regime": str(regime or "unknown"),
        "selection_mode": str(selection_mode or ""),
        "selected_stocks": deepcopy(list(selected_stocks or [])),
        "return_pct": float(return_pct or 0.0),
        "benchmark_passed": bool(benchmark_passed),
        "benchmark_strict_passed": bool(benchmark_strict_passed),
        "strategy_scores": _coerce_strategy_scores_input(strategy_scores),
        "governance_decision": _coerce_governance_decision_input(governance_decision),
    }
    return build_simulation_stage_snapshot(
        cycle_payload=payload,
        execution_snapshot=execution_snapshot,
    )


@dataclass(frozen=True)
class SimulationStageEnvelope:
    """Canonical simulation-stage envelope.

    Repository callers should prefer ``from_cycle_payload`` or
    ``from_structured_inputs``. ``from_cycle_dict`` is retained only as a
    legacy boundary adapter.
    """

    cycle_id: int
    cutoff_date: str
    regime: str = "unknown"
    selected_stocks: list[str] = field(default_factory=list)
    return_pct: float = 0.0
    benchmark_passed: bool = False
    benchmark_strict_passed: bool = False
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    excess_return: float = 0.0
    strategy_scores: StrategyScoresInputPayload = field(
        default_factory=lambda: cast(StrategyScoresInputPayload, {})
    )
    governance_decision: GovernanceDecisionInputPayload = field(
        default_factory=lambda: cast(GovernanceDecisionInputPayload, {})
    )
    execution_snapshot: dict[str, Any] = field(default_factory=dict)
    stage_snapshots: StageSnapshotsInputPayload = field(
        default_factory=lambda: cast(StageSnapshotsInputPayload, {})
    )

    @classmethod
    def from_structured_inputs(
        cls,
        *,
        cycle_id: int,
        cutoff_date: str,
        regime: str = "unknown",
        selection_mode: str = "",
        selected_stocks: list[str] | None = None,
        return_pct: float = 0.0,
        benchmark_passed: bool = False,
        benchmark_strict_passed: bool = False,
        sharpe_ratio: float = 0.0,
        max_drawdown: float = 0.0,
        excess_return: float = 0.0,
        strategy_scores: StrategyScoresInputPayload | dict[str, Any] | None = None,
        governance_decision: GovernanceDecisionInputPayload | dict[str, Any] | None = None,
        execution_snapshot: dict[str, Any] | None = None,
        stage_snapshots: StageSnapshotsInputPayload | None = None,
    ) -> "SimulationStageEnvelope":
        execution_payload = deepcopy(dict(execution_snapshot or {}))
        snapshots = _coerce_stage_snapshots_input(stage_snapshots)
        snapshots["simulation"] = build_simulation_stage_snapshot_from_fields(
            cycle_id=int(cycle_id),
            cutoff_date=str(cutoff_date or ""),
            regime=str(regime or "unknown"),
            selection_mode=str(selection_mode or ""),
            selected_stocks=deepcopy(list(selected_stocks or [])),
            return_pct=float(return_pct or 0.0),
            benchmark_passed=bool(benchmark_passed),
            benchmark_strict_passed=bool(benchmark_strict_passed),
            strategy_scores=_coerce_strategy_scores_input(strategy_scores),
            governance_decision=_coerce_governance_decision_input(governance_decision),
            execution_snapshot=execution_payload,
        )
        return cls(
            cycle_id=int(cycle_id),
            cutoff_date=str(cutoff_date or ""),
            regime=str(regime or "unknown"),
            selected_stocks=deepcopy(list(selected_stocks or [])),
            return_pct=float(return_pct or 0.0),
            benchmark_passed=bool(benchmark_passed),
            benchmark_strict_passed=bool(benchmark_strict_passed),
            sharpe_ratio=float(sharpe_ratio or 0.0),
            max_drawdown=float(max_drawdown or 0.0),
            excess_return=float(excess_return or 0.0),
            strategy_scores=_coerce_strategy_scores_input(strategy_scores),
            governance_decision=_coerce_governance_decision_input(governance_decision),
            execution_snapshot=execution_payload,
            stage_snapshots=cast(StageSnapshotsInputPayload, snapshots),
        )

    @classmethod
    def from_cycle_dict(
        cls,
        cycle_dict: dict[str, Any] | None = None,
        *,
        execution_snapshot: dict[str, Any] | None = None,
        stage_snapshots: StageSnapshotsInputPayload | None = None,
    ) -> "SimulationStageEnvelope":
        # Compat-only adapter. Internal callers should prefer from_cycle_payload().
        return cls.from_cycle_payload(
            cycle_payload=deepcopy(dict(cycle_dict or {})),
            execution_snapshot=execution_snapshot,
            stage_snapshots=stage_snapshots,
        )

    @classmethod
    def from_cycle_payload(
        cls,
        cycle_payload: dict[str, Any] | None = None,
        *,
        execution_snapshot: dict[str, Any] | None = None,
        stage_snapshots: StageSnapshotsInputPayload | None = None,
    ) -> "SimulationStageEnvelope":
        payload = _resolve_cycle_payload(cycle_payload=cycle_payload)
        return cls.from_structured_inputs(
            cycle_id=int(payload.get("cycle_id") or 0),
            cutoff_date=str(payload.get("cutoff_date") or ""),
            regime=str(payload.get("regime") or "unknown"),
            selection_mode=str(payload.get("selection_mode") or ""),
            selected_stocks=deepcopy(list(payload.get("selected_stocks") or [])),
            return_pct=float(payload.get("return_pct") or 0.0),
            benchmark_passed=bool(payload.get("benchmark_passed", False)),
            benchmark_strict_passed=bool(payload.get("benchmark_strict_passed", False)),
            sharpe_ratio=float(payload.get("sharpe_ratio") or 0.0),
            max_drawdown=float(payload.get("max_drawdown") or 0.0),
            excess_return=float(payload.get("excess_return") or 0.0),
            strategy_scores=deepcopy(dict(payload.get("strategy_scores") or {})),
            governance_decision=deepcopy(dict(payload.get("governance_decision") or {})),
            execution_snapshot=deepcopy(dict(execution_snapshot or payload.get("execution_snapshot") or {})),
            stage_snapshots=_coerce_stage_snapshots_input(
                stage_snapshots or payload.get("stage_snapshots") or {}
            ),
        )

    def to_cycle_payload(
        self,
        *,
        base_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = deepcopy(dict(base_payload or {}))
        payload.update(
            {
                "cycle_id": int(self.cycle_id),
                "cutoff_date": str(self.cutoff_date or ""),
                "regime": str(self.regime or "unknown"),
                "selected_stocks": deepcopy(list(self.selected_stocks or [])),
                "return_pct": float(self.return_pct or 0.0),
                "benchmark_passed": bool(self.benchmark_passed),
                "benchmark_strict_passed": bool(self.benchmark_strict_passed),
                "sharpe_ratio": float(self.sharpe_ratio or 0.0),
                "max_drawdown": float(self.max_drawdown or 0.0),
                "excess_return": float(self.excess_return or 0.0),
                "strategy_scores": deepcopy(dict(self.strategy_scores or {})),
                "governance_decision": deepcopy(dict(self.governance_decision or {})),
                "execution_snapshot": deepcopy(dict(self.execution_snapshot or {})),
            }
        )
        stage_snapshots = deepcopy(dict(self.stage_snapshots or {}))
        stage_snapshots["simulation"] = build_simulation_stage_snapshot_from_fields(
            cycle_id=int(self.cycle_id),
            cutoff_date=str(self.cutoff_date or ""),
            regime=str(self.regime or "unknown"),
            selection_mode=str(payload.get("selection_mode") or ""),
            selected_stocks=deepcopy(list(self.selected_stocks or [])),
            return_pct=float(self.return_pct or 0.0),
            benchmark_passed=bool(self.benchmark_passed),
            benchmark_strict_passed=bool(self.benchmark_strict_passed),
            strategy_scores=deepcopy(dict(self.strategy_scores or {})),
            governance_decision=deepcopy(dict(self.governance_decision or {})),
            execution_snapshot=deepcopy(dict(self.execution_snapshot or {})),
        )
        payload["stage_snapshots"] = stage_snapshots
        return payload


def build_review_stage_snapshot(
    cycle_payload: dict[str, Any] | None = None,
) -> ReviewStageSnapshotPayload:
    payload = _resolve_cycle_payload(cycle_payload=cycle_payload)
    return cast(ReviewStageSnapshotPayload, {
        "contract_version": CYCLE_STAGE_SNAPSHOT_CONTRACT_VERSION,
        "stage": "review",
        "cycle_id": int(payload.get("cycle_id") or 0),
        "analysis": str(payload.get("analysis") or ""),
        "review_decision": _project_review_decision_payload(
            deepcopy(dict(payload.get("review_decision") or {}))
        ),
        "causal_diagnosis": deepcopy(dict(payload.get("causal_diagnosis") or {})),
        "similarity_summary": _project_similarity_summary_payload(
            deepcopy(dict(payload.get("similarity_summary") or {}))
        ),
        "similar_results": _coerce_similar_results_input(payload.get("similar_results")),
        "manager_review_report": _project_manager_review_digest(
            deepcopy(dict(payload.get("manager_review_report") or {}))
        ),
        "allocation_review_report": _project_allocation_review_digest(
            deepcopy(dict(payload.get("allocation_review_report") or {}))
        ),
        "ab_comparison": deepcopy(dict(payload.get("ab_comparison") or {})),
    })


def build_review_stage_snapshot_from_fields(
    *,
    cycle_id: int,
    analysis: str = "",
    review_decision: ReviewDecisionInputPayload | dict[str, Any] | None = None,
    causal_diagnosis: dict[str, Any] | None = None,
    similarity_summary: SimilaritySummaryInputPayload | dict[str, Any] | None = None,
    similar_results: (
        list[SimilarResultCompactPayload]
        | list[SimilarResultCompactInputPayload]
        | list[dict[str, Any]]
        | None
    ) = None,
    manager_review_report: ManagerReviewDigestPayload | dict[str, Any] | None = None,
    allocation_review_report: AllocationReviewDigestPayload | dict[str, Any] | None = None,
    ab_comparison: dict[str, Any] | None = None,
) -> ReviewStageSnapshotPayload:
    payload = {
        "cycle_id": int(cycle_id),
        "analysis": str(analysis or ""),
        "review_decision": _coerce_review_decision_input(review_decision),
        "causal_diagnosis": deepcopy(dict(causal_diagnosis or {})),
        "similarity_summary": _coerce_similarity_summary_input(similarity_summary),
        "similar_results": _coerce_similar_results_input(similar_results),
        "manager_review_report": _project_manager_review_digest(
            deepcopy(dict(manager_review_report or {}))
        ),
        "allocation_review_report": _project_allocation_review_digest(
            deepcopy(dict(allocation_review_report or {}))
        ),
        "ab_comparison": deepcopy(dict(ab_comparison or {})),
    }
    return build_review_stage_snapshot(cycle_payload=payload)


@dataclass(frozen=True)
class ReviewStageEnvelope:
    """Canonical review-stage envelope.

    Repository callers should prefer ``from_cycle_payload`` or
    ``from_structured_inputs``. ``from_cycle_dict`` is retained only as a
    legacy boundary adapter.
    """

    simulation: SimulationStageEnvelope
    analysis: str = ""
    review_decision: ReviewDecisionInputPayload = field(
        default_factory=lambda: cast(ReviewDecisionInputPayload, {})
    )
    causal_diagnosis: dict[str, Any] = field(default_factory=dict)
    similarity_summary: SimilaritySummaryInputPayload = field(
        default_factory=lambda: cast(SimilaritySummaryInputPayload, {})
    )
    similar_results: list[SimilarResultCompactPayload] = field(default_factory=list)
    manager_review_report: ManagerReviewDigestPayload = field(
        default_factory=lambda: cast(ManagerReviewDigestPayload, {})
    )
    allocation_review_report: AllocationReviewDigestPayload = field(
        default_factory=lambda: cast(AllocationReviewDigestPayload, {})
    )
    ab_comparison: dict[str, Any] = field(default_factory=dict)
    review_applied: bool = False
    stage_snapshots: StageSnapshotsInputPayload = field(
        default_factory=lambda: cast(StageSnapshotsInputPayload, {})
    )

    @classmethod
    def from_structured_inputs(
        cls,
        *,
        simulation: SimulationStageEnvelope,
        analysis: str = "",
        review_decision: ReviewDecisionInputPayload | dict[str, Any] | None = None,
        causal_diagnosis: dict[str, Any] | None = None,
        similarity_summary: SimilaritySummaryInputPayload | dict[str, Any] | None = None,
        similar_results: (
            list[SimilarResultCompactPayload]
            | list[SimilarResultCompactInputPayload]
            | list[dict[str, Any]]
            | None
        ) = None,
        manager_review_report: ManagerReviewDigestPayload | dict[str, Any] | None = None,
        allocation_review_report: AllocationReviewDigestPayload | dict[str, Any] | None = None,
        ab_comparison: dict[str, Any] | None = None,
        review_applied: bool = False,
        stage_snapshots: StageSnapshotsInputPayload | None = None,
    ) -> "ReviewStageEnvelope":
        snapshots = _coerce_stage_snapshots_input(stage_snapshots)
        snapshots["simulation"] = deepcopy(dict(simulation.stage_snapshots.get("simulation") or {}))
        snapshots["review"] = build_review_stage_snapshot_from_fields(
            cycle_id=int(simulation.cycle_id),
            analysis=str(analysis or ""),
            review_decision=_coerce_review_decision_input(review_decision),
            causal_diagnosis=deepcopy(dict(causal_diagnosis or {})),
            similarity_summary=_coerce_similarity_summary_input(similarity_summary),
            similar_results=_coerce_similar_results_input(similar_results),
            manager_review_report=_project_manager_review_digest(
                deepcopy(dict(manager_review_report or {}))
            ),
            allocation_review_report=_project_allocation_review_digest(
                deepcopy(dict(allocation_review_report or {}))
            ),
            ab_comparison=deepcopy(dict(ab_comparison or {})),
        )
        return cls(
            simulation=simulation,
            analysis=str(analysis or ""),
            review_decision=_coerce_review_decision_input(review_decision),
            causal_diagnosis=deepcopy(dict(causal_diagnosis or {})),
            similarity_summary=_coerce_similarity_summary_input(similarity_summary),
            similar_results=_coerce_similar_results_input(similar_results),
            manager_review_report=_project_manager_review_digest(
                deepcopy(dict(manager_review_report or {}))
            ),
            allocation_review_report=_project_allocation_review_digest(
                deepcopy(dict(allocation_review_report or {}))
            ),
            ab_comparison=deepcopy(dict(ab_comparison or {})),
            review_applied=bool(review_applied),
            stage_snapshots=cast(StageSnapshotsInputPayload, snapshots),
        )

    @classmethod
    def from_cycle_dict(
        cls,
        cycle_dict: dict[str, Any] | None = None,
        *,
        simulation_envelope: SimulationStageEnvelope | None = None,
        stage_snapshots: StageSnapshotsInputPayload | None = None,
    ) -> "ReviewStageEnvelope":
        # Compat-only adapter. Internal callers should prefer from_cycle_payload().
        return cls.from_cycle_payload(
            cycle_payload=deepcopy(dict(cycle_dict or {})),
            simulation_envelope=simulation_envelope,
            stage_snapshots=stage_snapshots,
        )

    @classmethod
    def from_cycle_payload(
        cls,
        cycle_payload: dict[str, Any] | None = None,
        *,
        simulation_envelope: SimulationStageEnvelope | None = None,
        stage_snapshots: StageSnapshotsInputPayload | None = None,
    ) -> "ReviewStageEnvelope":
        payload = _resolve_cycle_payload(cycle_payload=cycle_payload)
        simulation = simulation_envelope or SimulationStageEnvelope.from_cycle_payload(payload)
        return cls.from_structured_inputs(
            simulation=simulation,
            analysis=str(payload.get("analysis") or ""),
            review_decision=deepcopy(dict(payload.get("review_decision") or {})),
            causal_diagnosis=deepcopy(dict(payload.get("causal_diagnosis") or {})),
            similarity_summary=deepcopy(dict(payload.get("similarity_summary") or {})),
            similar_results=_coerce_similar_results_input(payload.get("similar_results")),
            manager_review_report=_project_manager_review_digest(
                deepcopy(dict(payload.get("manager_review_report") or {}))
            ),
            allocation_review_report=_project_allocation_review_digest(
                deepcopy(dict(payload.get("allocation_review_report") or {}))
            ),
            ab_comparison=deepcopy(dict(payload.get("ab_comparison") or {})),
            review_applied=bool(payload.get("review_applied", False)),
            stage_snapshots=_coerce_stage_snapshots_input(
                stage_snapshots or payload.get("stage_snapshots") or {}
            ),
        )

    def to_cycle_payload(
        self,
        *,
        base_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self.simulation.to_cycle_payload(base_payload=base_payload)
        payload.update(
            {
                "analysis": str(self.analysis or ""),
                "review_decision": deepcopy(dict(self.review_decision or {})),
                "causal_diagnosis": deepcopy(dict(self.causal_diagnosis or {})),
                "similarity_summary": deepcopy(dict(self.similarity_summary or {})),
                "similar_results": _coerce_similar_results_input(self.similar_results),
                "manager_review_report": _project_manager_review_digest(
                    deepcopy(dict(self.manager_review_report or {}))
                ),
                "allocation_review_report": _project_allocation_review_digest(
                    deepcopy(dict(self.allocation_review_report or {}))
                ),
                "ab_comparison": deepcopy(dict(self.ab_comparison or {})),
                "review_applied": bool(self.review_applied),
            }
        )
        stage_snapshots = deepcopy(dict(self.stage_snapshots or {}))
        stage_snapshots["simulation"] = deepcopy(
            dict(self.simulation.stage_snapshots.get("simulation") or {})
        )
        stage_snapshots["review"] = build_review_stage_snapshot_from_fields(
            cycle_id=int(self.simulation.cycle_id),
            analysis=str(self.analysis or ""),
            review_decision=deepcopy(dict(self.review_decision or {})),
            causal_diagnosis=deepcopy(dict(self.causal_diagnosis or {})),
            similarity_summary=deepcopy(dict(self.similarity_summary or {})),
            similar_results=_coerce_similar_results_input(self.similar_results),
            manager_review_report=_project_manager_review_digest(
                deepcopy(dict(self.manager_review_report or {}))
            ),
            allocation_review_report=_project_allocation_review_digest(
                deepcopy(dict(self.allocation_review_report or {}))
            ),
            ab_comparison=deepcopy(dict(self.ab_comparison or {})),
        )
        payload["stage_snapshots"] = stage_snapshots
        return payload

    def to_validation_review_payload(
        self,
        *,
        regime: str | None = None,
        research_feedback: dict[str, Any] | None = None,
        regime_summary: dict[str, Any] | None = None,
    ) -> ValidationReviewResultPayload:
        return cast(ValidationReviewResultPayload, {
            "cycle_id": int(self.simulation.cycle_id),
            "regime": str(regime or self.simulation.regime or "unknown"),
            "failure_signature": {
                "return_direction": "profit" if self.simulation.return_pct > 0 else "loss",
                "benchmark_passed": bool(self.simulation.benchmark_passed),
                "primary_driver": str(self.causal_diagnosis.get("primary_driver") or ""),
                "feedback_bias": str(
                    dict(research_feedback or {}).get("recommendation", {}).get("bias") or ""
                ),
            },
            "regime_summary": deepcopy(dict(regime_summary or {})),
            "research_feedback": _normalize_research_feedback_payload(
                deepcopy(dict(research_feedback or {}))
            ),
            "causal_diagnosis": deepcopy(dict(self.causal_diagnosis)),
            "manager_review_report": _project_manager_review_digest(
                deepcopy(dict(self.manager_review_report))
            ),
            "allocation_review_report": _project_allocation_review_digest(
                deepcopy(dict(self.allocation_review_report))
            ),
        })


@dataclass(frozen=True)
class OptimizationInputEnvelope:
    simulation: SimulationStageEnvelope
    research_feedback: dict[str, Any] = field(default_factory=dict)
    research_feedback_optimization: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_cycle_payload(
        cls,
        cycle_payload: dict[str, Any] | None = None,
        *,
        simulation_envelope: SimulationStageEnvelope | None = None,
    ) -> "OptimizationInputEnvelope":
        payload = _resolve_cycle_payload(cycle_payload=cycle_payload)
        simulation = simulation_envelope or SimulationStageEnvelope.from_cycle_payload(
            payload,
            execution_snapshot=dict(payload.get("execution_snapshot") or {}),
            stage_snapshots=_coerce_stage_snapshots_input(payload.get("stage_snapshots") or {}),
        )
        return cls(
            simulation=simulation,
            research_feedback=deepcopy(dict(payload.get("research_feedback") or {})),
            research_feedback_optimization=deepcopy(
                dict(payload.get("research_feedback_optimization") or {})
            ),
        )

    @property
    def cycle_id(self) -> int:
        return int(self.simulation.cycle_id)

    def to_cycle_payload(
        self,
        *,
        base_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self.simulation.to_cycle_payload(base_payload=base_payload)
        payload["research_feedback"] = deepcopy(dict(self.research_feedback or {}))
        payload["research_feedback_optimization"] = deepcopy(
            dict(self.research_feedback_optimization or {})
        )
        return payload


@dataclass(frozen=True)
class ValidationInputEnvelope:
    cycle_id: int
    manager_id: str = ""
    run_context: dict[str, Any] = field(default_factory=dict)
    review_result: ValidationReviewResultPayload | dict[str, Any] = field(default_factory=dict)
    cycle_result: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_cycle_result(
        cls,
        cycle_result: Any,
        *,
        review_envelope: ReviewStageEnvelope,
        regime: str | None = None,
        research_feedback: dict[str, Any] | None = None,
        regime_summary: dict[str, Any] | None = None,
    ) -> "ValidationInputEnvelope":
        run_context = deepcopy(dict(getattr(cycle_result, "run_context", {}) or {}))
        cycle_payload = {
            "governance_decision": deepcopy(
                dict(getattr(cycle_result, "governance_decision", {}) or {})
            ),
            "ab_comparison": deepcopy(dict(getattr(cycle_result, "ab_comparison", {}) or {})),
            "research_feedback": deepcopy(
                dict(getattr(cycle_result, "research_feedback", {}) or {})
            ),
            "return_pct": float(getattr(cycle_result, "return_pct", 0.0) or 0.0),
            "benchmark_passed": bool(getattr(cycle_result, "benchmark_passed", False)),
            "strategy_scores": deepcopy(dict(getattr(cycle_result, "strategy_scores", {}) or {})),
            "portfolio_plan": deepcopy(dict(getattr(cycle_result, "portfolio_plan", {}) or {})),
            "portfolio_attribution": deepcopy(
                dict(getattr(cycle_result, "portfolio_attribution", {}) or {})
            ),
            "manager_results": deepcopy(list(getattr(cycle_result, "manager_results", []) or [])),
            "manager_review_report": _project_manager_review_digest(
                deepcopy(dict(getattr(cycle_result, "manager_review_report", {}) or {}))
            ),
            "allocation_review_report": _project_allocation_review_digest(
                deepcopy(dict(getattr(cycle_result, "allocation_review_report", {}) or {}))
            ),
        }
        return cls(
            cycle_id=int(getattr(cycle_result, "cycle_id", 0) or 0),
            manager_id=str(
                getattr(cycle_result, "dominant_manager_id", "")
                or run_context.get("dominant_manager_id")
                or ""
            ),
            run_context=run_context,
            review_result=review_envelope.to_validation_review_payload(
                regime=regime,
                research_feedback=research_feedback,
                regime_summary=regime_summary,
            ),
            cycle_result=cycle_payload,
        )


def build_outcome_stage_snapshot(
    *,
    cycle_id: int,
    execution_snapshot: ExecutionSnapshotPayload | dict[str, Any] | None = None,
    run_context: RunContextPayload | dict[str, Any] | None = None,
    promotion_record: PromotionRecordPayload | dict[str, Any] | None = None,
    lineage_record: LineageRecordPayload | dict[str, Any] | None = None,
    realism_metrics: RealismMetricsPayload | None = None,
) -> OutcomeStageSnapshotPayload:
    return cast(OutcomeStageSnapshotPayload, {
        "contract_version": CYCLE_STAGE_SNAPSHOT_CONTRACT_VERSION,
        "stage": "outcome",
        "cycle_id": int(cycle_id),
        "execution_snapshot": deepcopy(dict(execution_snapshot or {})),
        "run_context": deepcopy(dict(run_context or {})),
        "promotion_record": deepcopy(dict(promotion_record or {})),
        "lineage_record": deepcopy(dict(lineage_record or {})),
        "realism_metrics": deepcopy(dict(realism_metrics or {})),
    })


def build_validation_stage_snapshot(
    *,
    cycle_id: int,
    validation_report: ValidationReportInputPayload | None = None,
    judge_report: dict[str, Any] | None = None,
) -> ValidationStageSnapshotPayload:
    payload = cast(dict[str, Any], dict(validation_report or {}))
    return cast(ValidationStageSnapshotPayload, {
        "contract_version": CYCLE_STAGE_SNAPSHOT_CONTRACT_VERSION,
        "stage": "validation",
        "cycle_id": int(cycle_id),
        "validation_task_id": str(payload.get("validation_task_id") or ""),
        "shadow_mode": bool(payload.get("shadow_mode", False)),
        "validation_summary": _project_validation_summary_payload(
            deepcopy(dict(payload.get("summary") or {}))
        ),
        "market_tagging": deepcopy(dict(payload.get("market_tagging") or {})),
        "failure_tagging": deepcopy(dict(payload.get("failure_tagging") or {})),
        "validation_tagging": deepcopy(dict(payload.get("validation_tagging") or {})),
        "judge_report": deepcopy(dict(judge_report or {})),
    })


def capture_cycle_stage_snapshot(
    stage: str,
    *,
    cycle_payload: dict[str, Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    run_context: dict[str, Any] | None = None,
    validation_report: ValidationReportInputPayload | None = None,
    optimization_events: list[dict[str, Any]] | None = None,
    manager_results: list[dict[str, Any]] | None = None,
    portfolio_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _resolve_cycle_payload(cycle_payload=cycle_payload)
    snapshot_payload = dict(execution_snapshot or payload.get("execution_snapshot") or {})
    run_payload = dict(run_context or {})
    validation_payload = _coerce_validation_report_input(validation_report)
    strategy_scores = dict(payload.get("strategy_scores") or {})
    validation_summary = cast(dict[str, Any], validation_payload.get("summary") or {})
    active_runtime_config_ref = str(
        run_payload.get("active_runtime_config_ref") or snapshot_payload.get("active_runtime_config_ref") or ""
    )
    candidate_runtime_config_ref = str(run_payload.get("candidate_runtime_config_ref") or "")
    manager_payload = list(manager_results or snapshot_payload.get("manager_results") or [])
    portfolio_payload = dict(portfolio_plan or snapshot_payload.get("portfolio_plan") or {})
    ab_comparison = dict(payload.get("ab_comparison") or run_payload.get("ab_comparison") or {})
    ab_summary = dict(ab_comparison.get("comparison") or {})

    return {
        "stage": str(stage or "").strip().lower(),
        "cycle_id": int(payload.get("cycle_id") or snapshot_payload.get("cycle_id") or 0),
        "regime": str(payload.get("regime") or "unknown"),
        "basis_stage": str(snapshot_payload.get("basis_stage") or ""),
        "selected_count": len(list(payload.get("selected_stocks") or [])),
        "benchmark_passed": bool(payload.get("benchmark_passed", False)),
        "review_applied": bool(payload.get("review_applied", False)),
        "strategy_score": _finite_float(strategy_scores.get("overall_score")),
        "active_runtime_config_ref": active_runtime_config_ref,
        "candidate_runtime_config_ref": candidate_runtime_config_ref,
        "optimization_event_stages": [
            str(item.get("stage") or "")
            for item in list(optimization_events or [])
            if str(item.get("stage") or "")
        ],
        "manager_count": len(manager_payload),
        "has_portfolio_plan": bool(portfolio_payload),
        "ab_winner": str(ab_summary.get("winner") or ""),
        "validation_status": str(validation_summary.get("status") or ""),
        "shadow_mode": bool(validation_payload.get("shadow_mode", run_payload.get("shadow_mode", False))),
    }


# Export canonical envelope types; legacy cycle_dict adapters remain methods on
# these classes only for explicit boundary compatibility.
__all__ = [
    "CYCLE_STAGE_SNAPSHOT_CONTRACT_VERSION",
    "AllocationReviewDigestPayload",
    "AllocationReviewDigestSummaryPayload",
    "ContractStageSnapshotSummaryPayload",
    "ContractStageSnapshotsSummaryPayload",
    "ExecutionDefaultsPayload",
    "GovernanceDecisionPayload",
    "GovernanceDecisionInputPayload",
    "JudgeReportSummaryPayload",
    "LineageRecordCompactPayload",
    "LineageRecordPersistedPayload",
    "LlmAnalysisOptimizationStagePayload",
    "ManagerReviewDigestPayload",
    "ManagerReviewDigestSummaryPayload",
    "OptimizationInputEnvelope",
    "OptimizationErrorStagePayload",
    "OptimizationEventPayload",
    "OptimizationEventLogPayload",
    "OutcomeContractStageSnapshotSummaryPayload",
    "OutcomeStageSnapshotPersistedPayload",
    "PeerComparisonCompactPayload",
    "PeerComparisonPeerSummaryPayload",
    "PersistedAllocationReviewDigestPayload",
    "PersistedManagerReviewDigestPayload",
    "PersistedOptimizationEventPayload",
    "PersistedTaggingDigestPayload",
    "PersistedPeerComparisonPayload",
    "PersistedReviewDecisionSummaryPayload",
    "PersistedValidationSummaryPayload",
    "PromotionRecordCompactPayload",
    "PromotionRecordPersistedPayload",
    "PromotionRecordPayload",
    "RealismMetricsPayload",
    "RealismMetricsSummaryPayload",
    "ReviewContractStageSnapshotSummaryPayload",
    "ReviewDecisionOptimizationStagePayload",
    "ReviewStageEnvelope",
    "ReviewDecisionPayload",
    "ReviewDecisionInputPayload",
    "ReviewDecisionSummaryCompactPayload",
    "ReviewAppliedEffectsPayload",
    "ReviewStageSnapshotPersistedPayload",
    "ResearchFeedbackOptimizationStagePayload",
    "ResearchFeedbackPayload",
    "RuntimeConfigMutationOptimizationStagePayload",
    "RuntimeConfigMutationSkippedOptimizationStagePayload",
    "RunContextPayload",
    "SimilarResultCompactPayload",
    "SimilaritySummaryCompactPayload",
    "SimilaritySummaryPayload",
    "SimilaritySummaryInputPayload",
    "SimilaritySummaryPersistedPayload",
    "SimulationStageEnvelope",
    "SimulationContractStageSnapshotSummaryPayload",
    "SimulationStageSnapshotPersistedPayload",
    "StageSnapshotPersistedPayload",
    "StageSnapshotRefsPayload",
    "StageSnapshotsInputPayload",
    "StageSnapshotsPersistedPayload",
    "StageSnapshotsPayload",
    "StrategyScoresInputPayload",
    "StrategyScoresPayload",
    "TaggingDigestPayload",
    "ValidationInputEnvelope",
    "ValidationCheckSummaryPayload",
    "ValidationContractStageSnapshotSummaryPayload",
    "ValidationRawEvidenceCycleResultPayload",
    "ValidationRawEvidenceSummaryPayload",
    "ValidationReportInputPayload",
    "ValidationReportPayload",
    "ValidationReportSummaryPayload",
    "ValidationStageSnapshotPersistedPayload",
    "ValidationSummaryCompactPayload",
    "ValidationSummaryPayload",
    "LineageRecordPayload",
    "build_cycle_contract_stage_snapshots",
    "build_cycle_run_context",
    "build_execution_snapshot",
    "build_outcome_stage_snapshot",
    "build_review_basis_window",
    "build_review_stage_snapshot",
    "build_review_stage_snapshot_from_fields",
    "build_simulation_stage_snapshot",
    "build_simulation_stage_snapshot_from_fields",
    "build_validation_stage_snapshot",
    "capture_cycle_stage_snapshot",
    "latest_runtime_config_mutation_event",
    "project_allocation_review_digest",
    "project_manager_compatibility",
    "project_manager_review_digest",
    "project_persisted_allocation_review_digest",
    "project_persisted_manager_review_digest",
    "project_persisted_optimization_event",
    "project_review_applied_effects_payload",
]
