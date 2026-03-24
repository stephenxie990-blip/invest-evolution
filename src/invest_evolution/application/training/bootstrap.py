"""Training bootstrap and diagnostics services."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from invest_evolution.application.training.controller import (
    TrainingCycleDataService,
    TrainingExecutionService,
    TrainingLLMRuntimeService,
    TrainingLifecycleService,
    TrainingSessionState,
)
from invest_evolution.application.training.execution import (
    ManagerExecutionService,
    TrainingABService,
    TrainingOutcomeService,
    TrainingSelectionService,
    TrainingSimulationService,
    build_manager_runtime,
    resolve_manager_config_ref,
)
from invest_evolution.application.training.observability import (
    FreezeGateService,
    TrainingObservabilityService,
)
from invest_evolution.application.training.persistence import TrainingPersistenceService
from invest_evolution.application.training.policy import (
    TrainingExperimentService,
    TrainingGovernanceService,
    TrainingPolicyService,
    runtime_config_projection_from_live_config,
)
from invest_evolution.application.training.research import (
    TrainingFeedbackService,
    TrainingResearchService,
)
from invest_evolution.application.training.review import (
    AllocationReviewStageService,
    ManagerReviewStageService,
    TrainingReviewService,
    TrainingReviewStageService,
)
from invest_evolution.config import (
    EFFECTIVE_RUNTIME_MODE,
    OUTPUT_DIR,
    PROJECT_ROOT,
    RUNTIME_CONTRACT_VERSION,
    RUNTIME_DIR,
    config,
)
from invest_evolution.config.control_plane import (
    EvolutionConfigService,
    build_component_llm_caller,
    resolve_default_llm,
)
from invest_evolution.investment.agents.base import MarketRegimeAgent
from invest_evolution.investment.agents.specialists import (
    ContrarianAgent,
    TrendHunterAgent,
    DefensiveAgent,
    EvoJudgeAgent,
    QualityAgent,
    ReviewDecisionAgent,
    StrategistAgent,
)
from invest_evolution.investment.evolution import (
    EvolutionEngine,
    EvolutionService,
    LLMOptimizer,
    RuntimeConfigMutator,
    RuntimeEvolutionOptimizer,
)
from invest_evolution.investment.foundation.metrics import BenchmarkEvaluator, StrategyEvaluator
from invest_evolution.investment.research import (
    ResearchAttributionEngine,
    ResearchCaseStore,
    ResearchScenarioEngine,
    TrainingArtifactRecorder,
)
from invest_evolution.investment.shared.policy import AgentTracker, resolve_governance_matrix
from invest_evolution.market_data import DataManager, MarketDataRepository, MockDataProvider


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


def build_default_training_diagnostics(
    cutoff_date: str,
    stock_count: int,
    min_history_days: int,
) -> dict[str, Any]:
    return {
        "cutoff_date": cutoff_date,
        "target_stock_count": int(stock_count),
        "min_history_days": int(min_history_days),
        "eligible_stock_count": 0,
        "ready": True,
        "issues": [],
        "suggestions": [],
        "status": {},
        "date_range": {},
    }


def initialize_core_runtime(controller: Any, *, data_provider: Any = None) -> None:
    controller.session_state = TrainingSessionState()
    controller.runtime_evolution_optimizer = None
    controller.evolution_engine = EvolutionEngine(population_size=10)
    controller.strategy_evaluator = StrategyEvaluator()
    controller.benchmark_evaluator = BenchmarkEvaluator()
    controller.training_experiment_service = TrainingExperimentService()
    controller.training_llm_runtime_service = TrainingLLMRuntimeService()
    controller.training_observability_service = TrainingObservabilityService()
    controller.execution_policy = {}
    controller.train_policy = {}
    controller.freeze_gate_policy = {}
    controller.promotion_gate_policy = {}
    controller.risk_policy = {}
    controller.evaluation_policy = {}
    controller.review_policy = {}
    controller.data_manager = DataManager(data_provider=data_provider)
    controller.requested_data_mode = getattr(controller.data_manager, "requested_mode", "live")
    controller.current_params = {}
    controller.auto_apply_mutation = False
    controller.aggregate_leaderboard_enabled = True
    controller.quality_gate_matrix = resolve_governance_matrix()
    controller.selection_agent_weights = {
        "trend_hunter": 1.0,
        "contrarian": 1.0,
    }
    controller.selection_debate_enabled = bool(getattr(config, "enable_debate", True))
    controller.review_risk_debate_enabled = bool(getattr(config, "enable_debate", True))
    controller.max_selection_debate_rounds = max(
        1,
        int(getattr(config, "max_debate_rounds", 1) or 1),
    )
    controller.max_review_risk_rounds = max(
        1,
        int(getattr(config, "max_risk_discuss_rounds", 1) or 1),
    )


def initialize_llm_runtime(controller: Any) -> Callable[..., Any]:
    controller._default_fast_llm = resolve_default_llm("fast")
    controller._default_deep_llm = resolve_default_llm("deep")

    def build_llm(component_key: str, fallback_model: str, *, fallback_kind: str) -> Any:
        resolved_default = (
            controller._default_fast_llm
            if fallback_kind == "fast"
            else controller._default_deep_llm
        )
        return build_component_llm_caller(
            component_key,
            fallback_model=fallback_model or resolved_default.model,
            fallback_api_key=resolved_default.api_key,
            fallback_api_base=resolved_default.api_base,
            timeout=config.llm_timeout,
            max_retries=config.llm_max_retries,
        )

    controller.llm_caller = build_llm(
        "controller.main",
        controller._default_fast_llm.model,
        fallback_kind="fast",
    )
    controller.llm_optimizer = LLMOptimizer(
        llm_caller=build_llm(
            "optimizer.loss_analysis",
            controller._default_deep_llm.model,
            fallback_kind="deep",
        )
    )
    controller.runtime_evolution_optimizer = RuntimeEvolutionOptimizer(
        llm_optimizer=controller.llm_optimizer,
    )
    controller.llm_mode = (
        "dry_run" if bool(getattr(controller.llm_caller, "dry_run", False)) else "live"
    )
    return build_llm


def initialize_agents_and_runtime_support(
    controller: Any,
    *,
    build_llm: Callable[..., Any],
    artifact_log_dir: str | None,
) -> None:
    controller.agents = {
        "market_regime": MarketRegimeAgent(
            llm_caller=build_llm(
                "agent.MarketRegime",
                controller._default_deep_llm.model,
                fallback_kind="deep",
            )
        ),
        "governance_selector": ReviewDecisionAgent(
            llm_caller=build_llm(
                "agent.GovernanceSelector",
                controller._default_fast_llm.model,
                fallback_kind="fast",
            )
        ),
        "trend_hunter": TrendHunterAgent(
            llm_caller=build_llm(
                "agent.TrendHunter",
                controller._default_fast_llm.model,
                fallback_kind="fast",
            )
        ),
        "contrarian": ContrarianAgent(
            llm_caller=build_llm(
                "agent.Contrarian",
                controller._default_fast_llm.model,
                fallback_kind="fast",
            )
        ),
        "quality_agent": QualityAgent(
            llm_caller=build_llm(
                "agent.QualityAgent",
                controller._default_fast_llm.model,
                fallback_kind="fast",
            )
        ),
        "defensive_agent": DefensiveAgent(
            llm_caller=build_llm(
                "agent.DefensiveAgent",
                controller._default_fast_llm.model,
                fallback_kind="fast",
            )
        ),
        "strategist": StrategistAgent(
            llm_caller=build_llm(
                "agent.Strategist",
                controller._default_deep_llm.model,
                fallback_kind="deep",
            )
        ),
        "review_decision": ReviewDecisionAgent(
            llm_caller=build_llm(
                "agent.ReviewDecision",
                controller._default_deep_llm.model,
                fallback_kind="deep",
            )
        ),
        "evo_judge": EvoJudgeAgent(
            llm_caller=build_llm(
                "agent.EvoJudge",
                controller._default_deep_llm.model,
                fallback_kind="deep",
            )
        ),
    }
    controller.agent_tracker = AgentTracker()
    controller.artifact_recorder = TrainingArtifactRecorder(
        base_dir=str(artifact_log_dir or (OUTPUT_DIR / "artifacts"))
    )
    controller.evolution_service = EvolutionService(engine=controller.evolution_engine)


def initialize_config_service(
    controller: Any,
    *,
    config_audit_log_path: str | None,
    config_snapshot_dir: str | None,
) -> None:
    controller.config_service = EvolutionConfigService(
        project_root=PROJECT_ROOT,
        live_config=config,
        audit_log_path=Path(config_audit_log_path) if config_audit_log_path else None,
        snapshot_dir=Path(config_snapshot_dir) if config_snapshot_dir else None,
    )


def initialize_model_runtime(
    controller: Any,
    *,
    freeze_total_cycles: int,
    freeze_profit_required: int,
    max_losses_before_optimize: int,
) -> None:
    controller.freeze_total_cycles = freeze_total_cycles
    controller.freeze_profit_required = freeze_profit_required
    controller.max_losses_before_optimize = max_losses_before_optimize

    runtime_projection = runtime_config_projection_from_live_config()
    controller.default_manager_id = str(runtime_projection["default_manager_id"] or "momentum")
    controller.default_manager_config_ref = resolve_manager_config_ref(
        controller.default_manager_id,
        str(
            runtime_projection["default_manager_config_ref"]
            or "src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml"
        ),
    )
    controller.allocator_enabled = bool(runtime_projection["allocator_enabled"])
    controller.allocator_top_n = int(runtime_projection["allocator_top_n"])
    controller.manager_arch_enabled = bool(runtime_projection["manager_arch_enabled"])
    controller.manager_shadow_mode = bool(runtime_projection["manager_shadow_mode"])
    controller.manager_allocator_enabled = bool(runtime_projection["manager_allocator_enabled"])
    controller.portfolio_assembly_enabled = bool(
        runtime_projection["portfolio_assembly_enabled"]
    )
    controller.dual_review_enabled = bool(runtime_projection["dual_review_enabled"])
    controller.manager_persistence_enabled = bool(
        runtime_projection["manager_persistence_enabled"]
    )
    controller.manager_active_ids = list(runtime_projection["manager_active_ids"])
    controller.manager_budget_weights = dict(runtime_projection["manager_budget_weights"])
    controller.governance_enabled = bool(runtime_projection["governance_enabled"])
    controller.governance_mode = str(
        runtime_projection["governance_mode"] or "rule"
    ).strip().lower()
    controller.governance_allowed_manager_ids = list(
        runtime_projection["governance_allowed_manager_ids"]
    )
    controller.governance_cooldown_cycles = int(
        runtime_projection["governance_cooldown_cycles"]
    )
    controller.governance_min_confidence = float(
        runtime_projection["governance_min_confidence"]
    )
    controller.governance_hysteresis_margin = float(
        runtime_projection["governance_hysteresis_margin"]
    )
    controller.governance_agent_override_enabled = bool(
        runtime_projection["governance_agent_override_enabled"]
    )
    controller.governance_agent_override_max_gap = float(
        runtime_projection["governance_agent_override_max_gap"]
    )
    controller.governance_policy = dict(runtime_projection["governance_policy"])
    controller.effective_runtime_mode = str(
        runtime_projection.get("effective_runtime_mode") or EFFECTIVE_RUNTIME_MODE
    )
    controller.runtime_contract_version = int(
        runtime_projection.get("runtime_contract_version") or RUNTIME_CONTRACT_VERSION
    )
    controller.last_allocation_plan = {}
    controller.last_governance_decision = {}
    controller.governance_history = []
    controller.last_governance_change_cycle_id = None
    controller.stop_on_freeze = bool(getattr(config, "stop_on_freeze", True))
    controller.runtime_config_mutator = RuntimeConfigMutator()
    controller.training_policy_service = TrainingPolicyService()
    controller.training_governance_service = TrainingGovernanceService()
    controller.manager_runtime = build_manager_runtime(
        manager_id=controller.default_manager_id,
        manager_config_ref=controller.default_manager_config_ref,
        runtime_overrides=controller.current_params,
    )
    controller._sync_runtime_policy_from_manager_runtime()
    controller._refresh_governance_coordinator()


def initialize_training_state(controller: Any) -> None:
    controller.cycle_history = []
    controller.cycle_records = []
    controller.current_cycle_id = 0
    controller.total_cycle_attempts = 0
    controller.skipped_cycle_count = 0
    controller.consecutive_losses = 0
    if getattr(controller, "manager_runtime", None) is not None:
        controller.manager_runtime.update_runtime_overrides(controller.current_params)

    controller.assessment_history = []
    controller.optimization_events_history = []
    controller.last_cycle_meta = {}
    controller.experiment_spec = {}
    controller.experiment_seed = None
    controller.experiment_min_date = None
    controller.experiment_max_date = None
    controller.experiment_allowed_manager_ids = []
    controller.experiment_min_history_days = None
    controller.experiment_simulation_days = None
    controller.experiment_llm = {}
    controller.experiment_protocol = {}
    controller.experiment_cutoff_policy = {
        "mode": "random",
        "date": "",
        "anchor_date": "",
        "step_days": 30,
        "dates": [],
    }
    controller.experiment_review_window = {"mode": "single_cycle", "size": 1}
    controller.experiment_promotion_policy = {}


def initialize_callbacks(controller: Any) -> None:
    controller.on_cycle_complete = None
    controller.on_optimize = None


def infer_runtime_state_dir(
    *,
    output_dir: Path,
    runtime_state_dir: str | None,
    config_audit_log_path: str | None,
) -> Path:
    if runtime_state_dir:
        return Path(runtime_state_dir).expanduser().resolve()
    if config_audit_log_path:
        return Path(config_audit_log_path).expanduser().resolve().parent
    output_root = output_dir.expanduser().resolve()
    if output_root.parent.name == "outputs":
        return output_root.parent.parent / "state"
    if output_root == (OUTPUT_DIR / "training").resolve():
        return RUNTIME_DIR / "state"
    return output_root.parent / "state"


def initialize_output_runtime(
    controller: Any,
    *,
    output_dir: str | None,
    runtime_state_dir: str | None,
    config_audit_log_path: str | None,
) -> None:
    controller.output_dir = Path(output_dir) if output_dir else (OUTPUT_DIR / "training")
    controller.output_dir.mkdir(parents=True, exist_ok=True)
    controller.runtime_generations_dir = controller.output_dir / "runtime_generations"
    controller.runtime_generations_dir.mkdir(parents=True, exist_ok=True)
    controller.runtime_config_mutator = RuntimeConfigMutator(
        generations_dir=controller.runtime_generations_dir
    )
    controller.runtime_state_dir = infer_runtime_state_dir(
        output_dir=controller.output_dir,
        runtime_state_dir=runtime_state_dir,
        config_audit_log_path=config_audit_log_path,
    )
    controller.runtime_state_dir.mkdir(parents=True, exist_ok=True)
    controller.research_case_store = ResearchCaseStore(controller.runtime_state_dir)
    controller.research_market_repository = MarketDataRepository()
    controller.research_scenario_engine = ResearchScenarioEngine(
        controller.research_case_store
    )
    controller.research_attribution_engine = ResearchAttributionEngine(
        controller.research_market_repository
    )
    controller.last_research_feedback = {}
    controller.last_freeze_gate_evaluation = {}
    controller.last_feedback_optimization = {}
    controller.last_cutoff_policy_context = {}
    controller.last_feedback_optimization_cycle_id = 0
    controller.research_feedback_policy = {}
    controller.research_feedback_optimization_policy = {}
    controller.research_feedback_freeze_policy = {}


def initialize_training_services(controller: Any) -> None:
    controller.training_feedback_service = TrainingFeedbackService()
    controller.freeze_gate_service = FreezeGateService()
    controller.training_persistence_service = TrainingPersistenceService()
    controller.training_cycle_data_service = TrainingCycleDataService()
    controller.training_execution_service = TrainingExecutionService()
    controller.training_lifecycle_service = TrainingLifecycleService()
    controller.training_outcome_service = TrainingOutcomeService()
    controller.training_research_service = TrainingResearchService()
    controller.training_ab_service = TrainingABService()
    controller.training_review_service = TrainingReviewService()
    controller.training_review_stage_service = TrainingReviewStageService()
    controller.training_manager_review_stage_service = ManagerReviewStageService()
    controller.training_allocation_review_stage_service = AllocationReviewStageService()
    controller.training_selection_service = TrainingSelectionService()
    controller.training_simulation_service = TrainingSimulationService()
    controller.training_manager_execution_service = ManagerExecutionService()
