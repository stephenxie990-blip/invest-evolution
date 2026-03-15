"""
投资进化系统 - 训练主循环

包含：
1. Individual / EvolutionEngine        — 遗传算法策略参数优化
2. ReinforcementLearningOptimizer      — 简单 Q-Learning 参数调整
3. TrainingResult                      — 训练周期数据类
4. SelfLearningController              — 自我学习主控制器（协调各模块）
5. train_main()                        — 命令行启动入口

系统运行流程：
    SelfLearningController.run_continuous()
    ↳ run_training_cycle()                每轮：
        1. DataManager.load_stock_data()  加载 T0 历史数据
        2. InvestmentModel.process()     输出 SignalPacket + AgentContext
        3. SelectionMeeting.run_with_model_output()  Agent 会议协作
        4. SimulatedTrader.run_simulation()  30天模拟交易
        5. StrategyEvaluator / BenchmarkEvaluator 评估结果
        6. 连续亏损 ≥ 3 次 → 触发优化：
           LLMOptimizer.analyze_loss()   + EvolutionEngine.evolve()
        7. should_freeze() → 固化模型
"""

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import OUTPUT_DIR, PROJECT_ROOT, RUNTIME_DIR, config
from config.services import EvolutionConfigService, RuntimePathConfigService
from config.control_plane import build_component_llm_caller, resolve_default_llm
from invest.shared.tracking import AgentTracker
from market_data import DataManager, DataSourceUnavailableError, MarketDataRepository, MockDataProvider
from invest.evolution import LLMOptimizer, StrategyEvolutionOptimizer, EvolutionEngine, YamlConfigMutator
from invest.foundation.metrics.benchmark import BenchmarkEvaluator
from invest.foundation.metrics.cycle import StrategyEvaluator
from invest.agents.hunters import ContrarianAgent, TrendHunterAgent
from invest.agents.model_selector import ModelSelectorAgent
from invest.agents.regime import MarketRegimeAgent
from invest.agents.reviewers import EvoJudgeAgent, ReviewDecisionAgent, StrategistAgent
from invest.agents.specialists import DefensiveAgent, QualityAgent
from invest.meetings import SelectionMeeting, ReviewMeeting, MeetingRecorder
from invest.models import create_investment_model
from invest.models.defaults import COMMON_PARAM_DEFAULTS
from invest.research import ResearchAttributionEngine, ResearchCaseStore, ResearchScenarioEngine
from invest.services import EvolutionService, ReviewMeetingService, SelectionMeetingService
from app.training.optimization import trigger_loss_optimization
from app.training.controller_services import (
    TrainingExperimentService,
    FreezeGateService,
    TrainingFeedbackService,
    TrainingLLMRuntimeService,
    TrainingPersistenceService,
)
from app.training.cycle_services import TrainingCycleDataService
from app.training.execution_services import TrainingExecutionService
from app.training.lifecycle_services import TrainingLifecycleService
from app.training.observability_services import TrainingObservabilityService
from app.training.outcome_services import TrainingOutcomeService
from app.training.policy_services import TrainingPolicyService
from app.training import runtime_hooks as training_runtime_hooks
from app.training.review_services import TrainingReviewService
from app.training.review_stage_services import TrainingReviewStageService
from app.training.ab_services import TrainingABService
from app.training.research_services import TrainingResearchService
from app.training.selection_services import TrainingSelectionService
from app.training.routing_services import TrainingRoutingService
from app.training.simulation_services import TrainingSimulationService

logger = logging.getLogger(__name__)

SelfAssessmentSnapshot = training_runtime_hooks.SelfAssessmentSnapshot
_event_callback_state = training_runtime_hooks._event_callback_state
emit_event = training_runtime_hooks.emit_event
set_event_callback = training_runtime_hooks.set_event_callback


def _build_mock_provider() -> MockDataProvider:
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

def _default_training_diagnostics(cutoff_date: str, stock_count: int, min_history_days: int) -> dict[str, Any]:
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
    strategy_scores: Dict[str, Any] = field(default_factory=dict)
    review_applied: bool = False
    config_snapshot_path: str = ""
    optimization_events: List[Dict[str, Any]] = field(default_factory=list)
    audit_tags: Dict[str, Any] = field(default_factory=dict)
    model_name: str = "momentum"
    config_name: str = ""
    routing_decision: Dict[str, Any] = field(default_factory=dict)
    research_feedback: Dict[str, Any] = field(default_factory=dict)
    research_artifacts: Dict[str, Any] = field(default_factory=dict)
    ab_comparison: Dict[str, Any] = field(default_factory=dict)
    experiment_spec: Dict[str, Any] = field(default_factory=dict)
    execution_snapshot: Dict[str, Any] = field(default_factory=dict)
    run_context: Dict[str, Any] = field(default_factory=dict)
    promotion_record: Dict[str, Any] = field(default_factory=dict)
    lineage_record: Dict[str, Any] = field(default_factory=dict)
    review_decision: Dict[str, Any] = field(default_factory=dict)
    causal_diagnosis: Dict[str, Any] = field(default_factory=dict)
    similarity_summary: Dict[str, Any] = field(default_factory=dict)
    similar_results: List[Dict[str, Any]] = field(default_factory=list)
    realism_metrics: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (not self.effective_data_mode or self.effective_data_mode == "unknown") and self.data_mode:
            self.effective_data_mode = self.data_mode
        if (not self.data_mode or self.data_mode == "unknown") and self.effective_data_mode:
            self.data_mode = self.effective_data_mode
        if self.requested_data_mode in {"", "unknown", "live"} and self.effective_data_mode == "mock":
            self.requested_data_mode = "mock"
        if not self.llm_mode:
            self.llm_mode = "live"


@dataclass
class OptimizationEvent:
    trigger: str
    stage: str
    status: str = "ok"
    suggestions: List[str] = field(default_factory=list)
    decision: Dict[str, Any] = field(default_factory=dict)
    applied_change: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    ts: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trigger": self.trigger,
            "stage": self.stage,
            "status": self.status,
            "suggestions": list(self.suggestions),
            "decision": dict(self.decision),
            "applied_change": dict(self.applied_change),
            "notes": self.notes,
            "ts": self.ts,
        }


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

    def __init__(
        self,
        output_dir: Optional[str] = None,
        meeting_log_dir: Optional[str] = None,
        config_audit_log_path: Optional[str] = None,
        config_snapshot_dir: Optional[str] = None,
        runtime_state_dir: Optional[str] = None,
        freeze_total_cycles:    int = 10,
        freeze_profit_required: int = 7,
        max_losses_before_optimize: int = 3,
        data_provider=None,
    ):
        self._initialize_core_runtime(data_provider=data_provider)
        build_llm = self._initialize_llm_runtime()
        self._initialize_agents_and_meetings(build_llm=build_llm, meeting_log_dir=meeting_log_dir)
        self._initialize_config_service(
            config_audit_log_path=config_audit_log_path,
            config_snapshot_dir=config_snapshot_dir,
        )
        self._initialize_model_runtime(
            freeze_total_cycles=freeze_total_cycles,
            freeze_profit_required=freeze_profit_required,
            max_losses_before_optimize=max_losses_before_optimize,
        )
        self._initialize_training_state()
        self._initialize_callbacks()
        self._initialize_output_runtime(
            output_dir=output_dir,
            runtime_state_dir=runtime_state_dir,
            config_audit_log_path=config_audit_log_path,
        )
        self._initialize_training_services()

        logger.info("自我学习控制器初始化完成")

    def _initialize_core_runtime(self, *, data_provider: Any = None) -> None:
        self.evo_optimizer = StrategyEvolutionOptimizer()
        self.evolution_engine = EvolutionEngine(population_size=10)
        self.strategy_evaluator = StrategyEvaluator()
        self.benchmark_evaluator = BenchmarkEvaluator()
        self.training_experiment_service = TrainingExperimentService()
        self.training_llm_runtime_service = TrainingLLMRuntimeService()
        self.training_observability_service = TrainingObservabilityService()
        self.execution_policy: Dict[str, Any] = {}
        self.train_policy: Dict[str, Any] = {}
        self.freeze_gate_policy: Dict[str, Any] = {}
        self.risk_policy: Dict[str, Any] = {}
        self.evaluation_policy: Dict[str, Any] = {}
        self.review_policy: Dict[str, Any] = {}
        self.data_manager = DataManager(data_provider=data_provider)
        self.requested_data_mode = getattr(self.data_manager, "requested_mode", "live")
        self.current_params: Dict[str, Any] = {}
        self.auto_apply_mutation = False

    def _initialize_llm_runtime(self) -> Callable[..., Any]:
        self._default_fast_llm = resolve_default_llm("fast")
        self._default_deep_llm = resolve_default_llm("deep")

        def _build_llm(component_key: str, fallback_model: str, *, fallback_kind: str):
            resolved_default = (
                self._default_fast_llm if fallback_kind == "fast" else self._default_deep_llm
            )
            return build_component_llm_caller(
                component_key,
                fallback_model=fallback_model or resolved_default.model,
                fallback_api_key=resolved_default.api_key,
                fallback_api_base=resolved_default.api_base,
                timeout=config.llm_timeout,
                max_retries=config.llm_max_retries,
            )

        self.llm_caller = _build_llm(
            "controller.main",
            self._default_fast_llm.model,
            fallback_kind="fast",
        )
        self.llm_optimizer = LLMOptimizer(
            llm_caller=_build_llm(
                "optimizer.loss_analysis",
                self._default_deep_llm.model,
                fallback_kind="deep",
            )
        )
        self.llm_mode = "dry_run" if bool(getattr(self.llm_caller, "dry_run", False)) else "live"
        return _build_llm

    def _initialize_agents_and_meetings(
        self,
        *,
        build_llm: Callable[..., Any],
        meeting_log_dir: Optional[str],
    ) -> None:
        self.agents = {
            "market_regime": MarketRegimeAgent(
                llm_caller=build_llm(
                    "agent.MarketRegime",
                    self._default_deep_llm.model,
                    fallback_kind="deep",
                )
            ),
            "model_selector": ModelSelectorAgent(
                llm_caller=build_llm(
                    "agent.ModelSelector",
                    self._default_fast_llm.model,
                    fallback_kind="fast",
                )
            ),
            "trend_hunter": TrendHunterAgent(
                llm_caller=build_llm(
                    "agent.TrendHunter",
                    self._default_fast_llm.model,
                    fallback_kind="fast",
                )
            ),
            "contrarian": ContrarianAgent(
                llm_caller=build_llm(
                    "agent.Contrarian",
                    self._default_fast_llm.model,
                    fallback_kind="fast",
                )
            ),
            "quality_agent": QualityAgent(
                llm_caller=build_llm(
                    "agent.QualityAgent",
                    self._default_fast_llm.model,
                    fallback_kind="fast",
                )
            ),
            "defensive_agent": DefensiveAgent(
                llm_caller=build_llm(
                    "agent.DefensiveAgent",
                    self._default_fast_llm.model,
                    fallback_kind="fast",
                )
            ),
            "strategist": StrategistAgent(
                llm_caller=build_llm(
                    "agent.Strategist",
                    self._default_deep_llm.model,
                    fallback_kind="deep",
                )
            ),
            "review_decision": ReviewDecisionAgent(
                llm_caller=build_llm(
                    "agent.ReviewDecision",
                    self._default_deep_llm.model,
                    fallback_kind="deep",
                )
            ),
            "evo_judge": EvoJudgeAgent(
                llm_caller=build_llm(
                    "agent.EvoJudge",
                    self._default_deep_llm.model,
                    fallback_kind="deep",
                )
            ),
        }

        self.selection_meeting = SelectionMeeting(
            llm_caller=build_llm(
                "meeting.selection.fast",
                self._default_fast_llm.model,
                fallback_kind="fast",
            ),
            deep_llm_caller=build_llm(
                "meeting.selection.deep",
                self._default_deep_llm.model,
                fallback_kind="deep",
            ),
            bull_llm_caller=build_llm(
                "meeting.selection.debate.bull",
                self._default_fast_llm.model,
                fallback_kind="fast",
            ),
            bear_llm_caller=build_llm(
                "meeting.selection.debate.bear",
                self._default_fast_llm.model,
                fallback_kind="fast",
            ),
            judge_llm_caller=build_llm(
                "meeting.selection.debate.judge",
                self._default_deep_llm.model,
                fallback_kind="deep",
            ),
            trend_hunter=self.agents["trend_hunter"],
            contrarian=self.agents["contrarian"],
            quality_agent=self.agents["quality_agent"],
            defensive_agent=self.agents["defensive_agent"],
            enable_debate=bool(getattr(config, "enable_debate", True)),
            max_debate_rounds=max(1, int(getattr(config, "max_debate_rounds", 1) or 1)),
            progress_callback=self._handle_selection_progress,
        )
        self.agent_tracker = AgentTracker()
        self.review_meeting = ReviewMeeting(
            llm_caller=build_llm(
                "meeting.review.fast",
                self._default_fast_llm.model,
                fallback_kind="fast",
            ),
            deep_llm_caller=build_llm(
                "meeting.review.deep",
                self._default_deep_llm.model,
                fallback_kind="deep",
            ),
            aggressive_llm_caller=build_llm(
                "meeting.review.risk.aggressive",
                self._default_fast_llm.model,
                fallback_kind="fast",
            ),
            conservative_llm_caller=build_llm(
                "meeting.review.risk.conservative",
                self._default_fast_llm.model,
                fallback_kind="fast",
            ),
            neutral_llm_caller=build_llm(
                "meeting.review.risk.neutral",
                self._default_fast_llm.model,
                fallback_kind="fast",
            ),
            risk_judge_llm_caller=build_llm(
                "meeting.review.risk.judge",
                self._default_deep_llm.model,
                fallback_kind="deep",
            ),
            agent_tracker=self.agent_tracker,
            strategist=self.agents["strategist"],
            evo_judge=self.agents["evo_judge"],
            commander=self.agents["review_decision"],
            enable_risk_debate=bool(getattr(config, "enable_debate", True)),
            max_risk_discuss_rounds=max(1, int(getattr(config, "max_risk_discuss_rounds", 1) or 1)),
            progress_callback=self._handle_review_progress,
        )
        self.meeting_recorder = MeetingRecorder(
            base_dir=str(meeting_log_dir or (OUTPUT_DIR / "meetings"))
        )
        self.selection_meeting_service = SelectionMeetingService(meeting=self.selection_meeting)
        self.review_meeting_service = ReviewMeetingService(meeting=self.review_meeting)
        self.evolution_service = EvolutionService(engine=self.evolution_engine)

    def _initialize_config_service(
        self,
        *,
        config_audit_log_path: Optional[str],
        config_snapshot_dir: Optional[str],
    ) -> None:
        self.config_service = EvolutionConfigService(
            project_root=PROJECT_ROOT,
            live_config=config,
            audit_log_path=Path(config_audit_log_path) if config_audit_log_path else None,
            snapshot_dir=Path(config_snapshot_dir) if config_snapshot_dir else None,
        )

    def _initialize_model_runtime(
        self,
        *,
        freeze_total_cycles: int,
        freeze_profit_required: int,
        max_losses_before_optimize: int,
    ) -> None:
        self.freeze_total_cycles = freeze_total_cycles
        self.freeze_profit_required = freeze_profit_required
        self.max_losses_before_optimize = max_losses_before_optimize

        self.model_name = str(getattr(config, "investment_model", "momentum") or "momentum")
        self.model_config_path = str(
            getattr(config, "investment_model_config", "invest/models/configs/momentum_v1.yaml")
        )
        self.allocator_enabled = bool(getattr(config, "allocator_enabled", False))
        self.allocator_top_n = int(getattr(config, "allocator_top_n", 3) or 3)
        self.model_routing_enabled = bool(
            getattr(config, "model_routing_enabled", True) or self.allocator_enabled
        )
        self.model_routing_mode = str(getattr(config, "model_routing_mode", "rule") or "rule").strip().lower()
        self.model_routing_allowed_models = [
            str(item).strip()
            for item in (getattr(config, "model_routing_allowed_models", []) or [])
            if str(item).strip()
        ]
        self.model_switch_cooldown_cycles = int(
            getattr(config, "model_switch_cooldown_cycles", 2) or 2
        )
        self.model_switch_min_confidence = float(
            getattr(config, "model_switch_min_confidence", 0.60) or 0.60
        )
        self.model_switch_hysteresis_margin = float(
            getattr(config, "model_switch_hysteresis_margin", 0.08) or 0.08
        )
        self.model_routing_agent_override_enabled = bool(
            getattr(config, "model_routing_agent_override_enabled", False)
        )
        self.model_routing_agent_override_max_gap = float(
            getattr(config, "model_routing_agent_override_max_gap", 0.18) or 0.18
        )
        self.model_routing_policy = dict(getattr(config, "model_routing_policy", {}) or {})
        self.last_allocation_plan: Dict[str, Any] = {}
        self.last_routing_decision: Dict[str, Any] = {}
        self.routing_history: List[Dict[str, Any]] = []
        self.last_model_switch_cycle_id: int | None = None
        self.stop_on_freeze = bool(getattr(config, "stop_on_freeze", True))
        self.model_mutator = YamlConfigMutator()
        self.training_policy_service = TrainingPolicyService()
        self.training_routing_service = TrainingRoutingService()
        self.investment_model = create_investment_model(
            self.model_name,
            config_path=self.model_config_path,
            runtime_overrides=self.current_params,
        )
        self._sync_runtime_policy_from_model()
        self._refresh_model_routing_coordinator()

    def _initialize_training_state(self) -> None:
        self.cycle_history: List[TrainingResult] = []
        self.cycle_records: List[Dict] = []
        self.current_cycle_id = 0
        self.total_cycle_attempts = 0
        self.skipped_cycle_count = 0
        self.consecutive_losses = 0
        if getattr(self, "investment_model", None) is not None:
            self.investment_model.update_runtime_overrides(self.current_params)

        self.assessment_history: List[SelfAssessmentSnapshot] = []
        self.optimization_events_history: List[OptimizationEvent] = []
        self.last_cycle_meta: Dict[str, Any] = {}
        self.experiment_spec: Dict[str, Any] = {}
        self.experiment_seed: int | None = None
        self.experiment_min_date: str | None = None
        self.experiment_max_date: str | None = None
        self.experiment_allowed_models: list[str] = []
        self.experiment_min_history_days: int | None = None
        self.experiment_simulation_days: int | None = None
        self.experiment_llm: Dict[str, Any] = {}
        self.experiment_protocol: Dict[str, Any] = {}
        self.experiment_cutoff_policy: Dict[str, Any] = {
            "mode": "random",
            "date": "",
            "anchor_date": "",
            "step_days": 30,
            "dates": [],
        }
        self.experiment_review_window: Dict[str, Any] = {"mode": "single_cycle", "size": 1}
        self.experiment_promotion_policy: Dict[str, Any] = {}

    def _initialize_callbacks(self) -> None:
        self.on_cycle_complete: Optional[Callable] = None
        self.on_optimize: Optional[Callable] = None

    def _initialize_output_runtime(
        self,
        *,
        output_dir: Optional[str],
        runtime_state_dir: Optional[str],
        config_audit_log_path: Optional[str],
    ) -> None:
        self.output_dir = Path(output_dir) if output_dir else (OUTPUT_DIR / "training")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_state_dir = self._infer_runtime_state_dir(
            runtime_state_dir,
            config_audit_log_path,
        )
        self.runtime_state_dir.mkdir(parents=True, exist_ok=True)
        self.research_case_store = ResearchCaseStore(self.runtime_state_dir)
        self.research_market_repository = MarketDataRepository()
        self.research_scenario_engine = ResearchScenarioEngine(self.research_case_store)
        self.research_attribution_engine = ResearchAttributionEngine(self.research_market_repository)
        self.last_research_feedback: Dict[str, Any] = {}
        self.last_freeze_gate_evaluation: Dict[str, Any] = {}
        self.last_feedback_optimization: Dict[str, Any] = {}
        self.last_cutoff_policy_context: Dict[str, Any] = {}
        self.last_feedback_optimization_cycle_id: int = 0
        self.research_feedback_policy: Dict[str, Any] = {}
        self.research_feedback_optimization_policy: Dict[str, Any] = {}
        self.research_feedback_freeze_policy: Dict[str, Any] = {}

    def _initialize_training_services(self) -> None:
        self.training_feedback_service = TrainingFeedbackService()
        self.freeze_gate_service = FreezeGateService()
        self.training_persistence_service = TrainingPersistenceService()
        self.training_cycle_data_service = TrainingCycleDataService()
        self.training_execution_service = TrainingExecutionService()
        self.training_lifecycle_service = TrainingLifecycleService()
        self.training_outcome_service = TrainingOutcomeService()
        self.training_research_service = TrainingResearchService()
        self.training_ab_service = TrainingABService()
        self.training_review_service = TrainingReviewService()
        self.training_review_stage_service = TrainingReviewStageService()
        self.training_selection_service = TrainingSelectionService()
        self.training_simulation_service = TrainingSimulationService()

    def _infer_runtime_state_dir(self, runtime_state_dir: Optional[str], config_audit_log_path: Optional[str]) -> Path:
        if runtime_state_dir:
            return Path(runtime_state_dir).expanduser().resolve()
        if config_audit_log_path:
            return Path(config_audit_log_path).expanduser().resolve().parent
        output_root = self.output_dir.expanduser().resolve()
        if output_root.parent.name == "outputs":
            return output_root.parent.parent / "state"
        if output_root == (OUTPUT_DIR / "training").resolve():
            return RUNTIME_DIR / "state"
        return output_root.parent / "state"

    @staticmethod
    def _research_feedback_brief(feedback: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return TrainingFeedbackService.research_feedback_brief(feedback)

    def _load_research_feedback(self, *, cutoff_date: str, model_name: str, config_name: str, regime: str = "") -> Dict[str, Any]:
        return self.training_feedback_service.load_research_feedback(
            self,
            cutoff_date=cutoff_date,
            model_name=model_name,
            config_name=config_name,
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

    def _refresh_model_routing_coordinator(self) -> None:
        self.training_routing_service.refresh_routing_coordinator(self)

    def refresh_runtime_from_config(self) -> None:
        self.training_routing_service.sync_runtime_from_config(self)

    def preview_model_routing(
        self,
        *,
        cutoff_date: Optional[str] = None,
        stock_count: Optional[int] = None,
        min_history_days: Optional[int] = None,
        allowed_models: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return self.training_routing_service.preview_routing(
            self,
            cutoff_date=cutoff_date,
            stock_count=stock_count,
            min_history_days=min_history_days,
            allowed_models=allowed_models or None,
        )

    def _reload_investment_model(self, config_path: Optional[str] = None) -> None:
        self.training_routing_service.reload_investment_model(self, config_path)

    def _sync_runtime_policy_from_model(self) -> None:
        self.training_policy_service.sync_runtime_policy(self)


    def _maybe_apply_allocator(self, stock_data: Dict[str, Any], cutoff_date: str, cycle_id: int) -> None:
        self.training_routing_service.apply_model_routing(
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

    def _trigger_optimization(self, cycle_dict: Dict, trade_dicts: List[Dict], *, trigger_reason: str = "consecutive_losses", feedback_plan: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        return trigger_loss_optimization(
            self,
            cycle_dict,
            trade_dicts,
            event_factory=OptimizationEvent,
            trigger_reason=trigger_reason,
            feedback_plan=feedback_plan,
        )

    def _append_optimization_event(self, event: OptimizationEvent) -> None:
        self.optimization_events_history.append(event)
        path = self.output_dir / "optimization_events.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def run_continuous(self, max_cycles: int = 100) -> Dict:
        """
        持续训练主循环

        Args:
            max_cycles: 最大训练周期数

        Returns:
            训练报告字典
        """
        return self.training_lifecycle_service.run_continuous(
            self,
            max_cycles=max_cycles,
        )

    def _record_self_assessment(self, cycle_result: TrainingResult, cycle_dict: Dict):
        """记录单周期自我评估快照"""
        self.training_persistence_service.record_self_assessment(self, SelfAssessmentSnapshot, cycle_result, cycle_dict)

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

    def _freeze_model(self) -> Dict:
        """固化模型并保存"""
        return self.freeze_gate_service.freeze_model(self)

    def _generate_report(self) -> Dict:
        return self.freeze_gate_service.generate_training_report(self)

    def _save_cycle_result(self, result: TrainingResult):
        """将周期结果写入 JSON"""
        self.training_persistence_service.save_cycle_result(self, result)


# ============================================================
# Part 5: 命令行入口
# ============================================================

def train_main():
    """
    训练系统命令行入口

    用法：
        python train.py --cycles 50 --mock
        python train.py --cycles 100
    """
    parser = argparse.ArgumentParser(description="投资进化系统 - 训练主程序")
    parser.add_argument("--cycles",    type=int,  default=20,    help="最大训练周期数")
    parser.add_argument("--mock",      action="store_true",       help="使用模拟数据（无需数据库）")
    parser.add_argument("--output",    type=str,  default=None,  help="训练输出目录")
    parser.add_argument("--meeting-log-dir", type=str, default=None, help="会议记录输出目录")
    parser.add_argument("--config-audit-log-path", type=str, default=None, help="配置变更审计日志路径")
    parser.add_argument("--config-snapshot-dir", type=str, default=None, help="配置快照输出目录")
    parser.add_argument("--freeze-n",  type=int,  default=10,    help="固化评估窗口大小")
    parser.add_argument("--freeze-m",  type=int,  default=7,     help="固化要求最低盈利次数")
    parser.add_argument("--log-level", type=str,  default="INFO", help="日志级别")
    parser.add_argument("--use-allocator", action="store_true", help="启用 market regime allocator")
    parser.add_argument("--allocator-top-n", type=int, default=None, help="allocator 参与分配的前 N 个模型")
    parser.add_argument("--force-full-cycles", action="store_true", help="即使达到冻结条件也继续跑满 cycles")

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    logger.info(f"训练参数: cycles={args.cycles}, mock={args.mock}")
    if args.use_allocator:
        config.allocator_enabled = True
        config.model_routing_enabled = True
        config.model_routing_mode = "rule"
    if args.allocator_top_n is not None:
        config.allocator_top_n = max(1, int(args.allocator_top_n))
    if args.force_full_cycles:
        config.stop_on_freeze = False

    runtime_paths = RuntimePathConfigService(project_root=PROJECT_ROOT).get_payload()
    output_dir = args.output or runtime_paths["training_output_dir"]
    meeting_log_dir = args.meeting_log_dir or runtime_paths["meeting_log_dir"]
    config_audit_log_path = args.config_audit_log_path or runtime_paths["config_audit_log_path"]
    config_snapshot_dir = args.config_snapshot_dir or runtime_paths["config_snapshot_dir"]

    controller = SelfLearningController(
        output_dir=output_dir,
        meeting_log_dir=meeting_log_dir,
        config_audit_log_path=config_audit_log_path,
        config_snapshot_dir=config_snapshot_dir,
        freeze_total_cycles=args.freeze_n,
        freeze_profit_required=args.freeze_m,
    )

    if args.mock:
        logger.info("使用模拟数据模式")
        mock_provider = _build_mock_provider()
        controller = SelfLearningController(
            output_dir=output_dir,
            meeting_log_dir=meeting_log_dir,
            config_audit_log_path=config_audit_log_path,
            config_snapshot_dir=config_snapshot_dir,
            freeze_total_cycles=args.freeze_n,
            freeze_profit_required=args.freeze_m,
            data_provider=mock_provider,
        )
        controller.set_llm_dry_run(True)

    report = controller.run_continuous(max_cycles=args.cycles)
    logger.info(f"\n训练完成: {report}")


if __name__ == "__main__":
    train_main()
