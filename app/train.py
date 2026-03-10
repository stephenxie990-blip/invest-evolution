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
import inspect
import json
import logging
import os
import random
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from config import OUTPUT_DIR, PROJECT_ROOT, config, normalize_date
from config.services import EvolutionConfigService, RuntimePathConfigService
from invest.shared import AgentTracker, LLMCaller
from market_data import DataManager, DataSourceUnavailableError, MockDataProvider
from invest.evolution import LLMOptimizer, StrategyEvolutionOptimizer, EvolutionEngine, YamlConfigMutator
from invest.foundation import BenchmarkEvaluator, SimulatedTrader, StrategyEvaluator
from invest.agents import (
    MarketRegimeAgent, TrendHunterAgent, ContrarianAgent, QualityAgent, DefensiveAgent,
    ReviewDecisionAgent, StrategistAgent, EvoJudgeAgent
)
from invest.meetings import SelectionMeeting, ReviewMeeting, MeetingRecorder
from invest.contracts import EvalReport, ModelOutput
from invest.allocator import build_allocation_plan
from invest.foundation.compute import compute_market_stats
from invest.leaderboard import write_leaderboard
from invest.models import create_investment_model, resolve_model_config_path
from invest.models.defaults import COMMON_EXECUTION_DEFAULTS, COMMON_PARAM_DEFAULTS, COMMON_BENCHMARK_DEFAULTS
from app.training.optimization import trigger_loss_optimization
from app.training.reporting import (
    build_freeze_report,
    build_self_assessment_snapshot,
    generate_training_report,
    rolling_self_assessment,
    should_freeze as should_freeze_report,
)

logger = logging.getLogger(__name__)


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

# 事件发射回调
_event_callback: Optional[Callable] = None


def set_event_callback(callback: Callable):
    """设置事件回调，用于推送实时事件到前端"""
    global _event_callback
    _event_callback = callback


def emit_event(event_type: str, data: dict):
    """发射事件到前端"""
    if _event_callback:
        try:
            _event_callback(event_type, data)
        except Exception:
            pass


def _call_with_compatible_signature(func: Callable[..., Any], *, preferred_kwargs: dict[str, Any], positional_args: tuple[Any, ...] = ()) -> Any:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        signature = None

    if signature is None:
        try:
            return func(**preferred_kwargs)
        except TypeError:
            return func(*positional_args)

    params = list(signature.parameters.values())
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params):
        return func(**preferred_kwargs)

    accepted_kwargs = {
        name: preferred_kwargs[name]
        for name, param in signature.parameters.items()
        if name in preferred_kwargs
        and param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    if accepted_kwargs:
        return func(**accepted_kwargs)

    if any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in params):
        return func(*positional_args)

    positional_capacity = sum(
        1 for param in params
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    if positional_capacity:
        return func(*positional_args[:positional_capacity])

    return func()


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

        action   = max(self.q_table[state], key=self.q_table[state].get)
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


@dataclass
class SelfAssessmentSnapshot:
    """单周期自我评估快照（用于冻结门控与追踪）"""
    cycle_id: int
    cutoff_date: str
    regime: str
    plan_source: str
    return_pct: float
    is_profit: bool
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    excess_return: float = 0.0
    benchmark_passed: bool = False


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
        freeze_total_cycles:    int = 10,
        freeze_profit_required: int = 7,
        max_losses_before_optimize: int = 3,
        data_provider=None,
    ):
        # 组件
        self.llm_optimizer     = LLMOptimizer()
        self.evo_optimizer     = StrategyEvolutionOptimizer()
        self.evolution_engine  = EvolutionEngine(population_size=10)
        self.strategy_evaluator = StrategyEvaluator()
        self.benchmark_evaluator = BenchmarkEvaluator()
        self.execution_policy: Dict[str, Any] = {}
        self.train_policy: Dict[str, Any] = {}
        self.freeze_gate_policy: Dict[str, Any] = {}
        self.risk_policy: Dict[str, Any] = {}
        self.evaluation_policy: Dict[str, Any] = {}
        self.data_manager      = DataManager(data_provider=data_provider)
        self.requested_data_mode = getattr(self.data_manager, "requested_mode", "live")
        self.current_params: Dict[str, Any] = {}

        # Agent 团队 & 会议组件
        self.llm_caller = LLMCaller()
        self.llm_mode = "dry_run" if bool(getattr(self.llm_caller, "dry_run", False)) else "live"
        
        self.agents = {
            "market_regime": MarketRegimeAgent(),
            "trend_hunter": TrendHunterAgent(),
            "contrarian": ContrarianAgent(),
            "quality_agent": QualityAgent(),
            "defensive_agent": DefensiveAgent(),
            "strategist": StrategistAgent(),
            "review_decision": ReviewDecisionAgent(),
            "evo_judge": EvoJudgeAgent(),
        }
        
        self.selection_meeting = SelectionMeeting(
            llm_caller=self.llm_caller,
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
            llm_caller=self.llm_caller,
            agent_tracker=self.agent_tracker,
            strategist=self.agents["strategist"],
            evo_judge=self.agents["evo_judge"],
            commander=self.agents["review_decision"],
            enable_risk_debate=bool(getattr(config, "enable_debate", True)),
            max_risk_discuss_rounds=max(1, int(getattr(config, "max_risk_discuss_rounds", 1) or 1)),
            progress_callback=self._handle_review_progress,
        )
        self.meeting_recorder = MeetingRecorder(base_dir=meeting_log_dir)
        self.config_service = EvolutionConfigService(
            project_root=PROJECT_ROOT,
            live_config=config,
            audit_log_path=Path(config_audit_log_path) if config_audit_log_path else None,
            snapshot_dir=Path(config_snapshot_dir) if config_snapshot_dir else None,
        )

        # 条件
        self.freeze_total_cycles       = freeze_total_cycles
        self.freeze_profit_required    = freeze_profit_required
        self.max_losses_before_optimize = max_losses_before_optimize

        self.model_name = str(getattr(config, "investment_model", "momentum") or "momentum")
        self.model_config_path = str(getattr(config, "investment_model_config", "invest/models/configs/momentum_v1.yaml"))
        self.allocator_enabled = bool(getattr(config, "allocator_enabled", False))
        self.allocator_top_n = int(getattr(config, "allocator_top_n", 3) or 3)
        self.last_allocation_plan: Dict[str, Any] = {}
        self.stop_on_freeze = bool(getattr(config, "stop_on_freeze", True))
        self.model_mutator = YamlConfigMutator()
        self.investment_model = create_investment_model(
            self.model_name,
            config_path=self.model_config_path,
            runtime_overrides=self.current_params,
        )
        self._sync_runtime_policy_from_model()

        # 状态
        self.cycle_history:   List[TrainingResult] = []
        self.cycle_records:   List[Dict] = []
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

        # 回调
        self.on_cycle_complete: Optional[Callable] = None
        self.on_optimize:       Optional[Callable] = None

        # 输出目录
        self.output_dir = Path(output_dir) if output_dir else (
            OUTPUT_DIR / "training"
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("自我学习控制器初始化完成")

    def configure_experiment(self, spec: Dict[str, Any] | None = None) -> None:
        spec = dict(spec or {})
        self.experiment_spec = spec
        protocol = dict(spec.get("protocol") or {})
        dataset = dict(spec.get("dataset") or {})
        model_scope = dict(spec.get("model_scope") or {})
        llm = dict(spec.get("llm") or {})

        seed = protocol.get("seed")
        self.experiment_seed = int(seed) if seed is not None and str(seed).strip() else None
        min_date = protocol.get("min_date") or protocol.get("date_range", {}).get("min") if isinstance(protocol.get("date_range"), dict) else protocol.get("min_date")
        max_date = protocol.get("max_date") or protocol.get("date_range", {}).get("max") if isinstance(protocol.get("date_range"), dict) else protocol.get("max_date")
        self.experiment_min_date = normalize_date(str(min_date)) if min_date else None
        self.experiment_max_date = normalize_date(str(max_date)) if max_date else None
        self.experiment_min_history_days = int(dataset.get("min_history_days")) if dataset.get("min_history_days") is not None else None
        self.experiment_simulation_days = int(dataset.get("simulation_days")) if dataset.get("simulation_days") is not None else None
        allowed_models = model_scope.get("allowed_models") or []
        self.experiment_allowed_models = [str(name) for name in allowed_models if str(name).strip()]
        self.experiment_llm = llm
        self._apply_experiment_llm_overrides(llm)
        if model_scope.get("allocator_enabled") is not None:
            self.allocator_enabled = bool(model_scope.get("allocator_enabled"))
        if self.experiment_allowed_models and self.model_name not in self.experiment_allowed_models:
            self.model_name = self.experiment_allowed_models[0]
            self.model_config_path = str(resolve_model_config_path(self.model_name))
            self.current_params = {}
            self._reload_investment_model(self.model_config_path)


    def _apply_experiment_llm_overrides(self, llm_spec: Dict[str, Any] | None = None) -> None:
        llm_spec = dict(llm_spec or {})
        timeout = llm_spec.get("timeout")
        max_retries = llm_spec.get("max_retries")
        dry_run = llm_spec.get("dry_run")

        targets = [self.llm_caller]
        for agent in self.agents.values():
            llm = getattr(agent, "llm", None)
            if llm is not None:
                targets.append(llm)
        for component in (self.selection_meeting, self.review_meeting, self.llm_optimizer):
            llm = getattr(component, "llm", None)
            if llm is not None:
                targets.append(llm)

        seen = set()
        for llm in targets:
            if llm is None or id(llm) in seen:
                continue
            seen.add(id(llm))
            if hasattr(llm, "apply_runtime_limits"):
                llm.apply_runtime_limits(timeout=timeout, max_retries=max_retries)
            if dry_run is not None and hasattr(llm, "dry_run"):
                llm.dry_run = bool(dry_run)

    def set_llm_dry_run(self, enabled: bool = True) -> None:
        """统一切换 LLM 调用 dry-run 模式。"""
        dry_run = bool(enabled)
        self.llm_mode = "dry_run" if dry_run else "live"
        if hasattr(self.llm_caller, "dry_run"):
            self.llm_caller.dry_run = dry_run
        for agent in self.agents.values():
            llm = getattr(agent, "llm", None)
            if llm is not None and hasattr(llm, "dry_run"):
                llm.dry_run = dry_run
        for component in (self.selection_meeting, self.review_meeting, self.llm_optimizer):
            llm = getattr(component, "llm", None)
            if llm is not None and hasattr(llm, "dry_run"):
                llm.dry_run = dry_run

    def set_mock_mode(self, enabled: bool = True) -> None:
        """兼容别名：mock mode 仅表示 LLM dry-run，不再代表数据源选择。"""
        self.set_llm_dry_run(enabled)

    def _reload_investment_model(self, config_path: Optional[str] = None) -> None:
        if config_path:
            self.model_config_path = str(config_path)
        self.investment_model = create_investment_model(
            self.model_name,
            config_path=self.model_config_path,
            runtime_overrides=self.current_params,
        )
        self._sync_runtime_policy_from_model()

    def _sync_runtime_policy_from_model(self) -> None:
        if getattr(self, "investment_model", None) is None:
            return
        config_params = self.investment_model.config_section("params", {})
        merged_params = dict(self.DEFAULT_PARAMS)
        merged_params.update(config_params or {})
        explicit_overrides = {
            key: value
            for key, value in (self.current_params or {}).items()
            if key not in self.DEFAULT_PARAMS or value != self.DEFAULT_PARAMS.get(key)
        }
        merged_params.update(explicit_overrides)
        self.current_params = merged_params
        self.investment_model.update_runtime_overrides(self.current_params)

        self.execution_policy = self.investment_model.config_section("execution", {}) or {}
        self.risk_policy = self.investment_model.config_section("risk_policy", {}) or {}
        self.evaluation_policy = self.investment_model.config_section("evaluation_policy", {}) or {}
        self.review_policy = self.investment_model.config_section("review_policy", {}) or {}
        self.strategy_evaluator.set_policy(self.evaluation_policy)
        self.review_meeting.set_policy(self.review_policy)
        benchmark_policy = self.investment_model.config_section("benchmark", {}) or {}
        benchmark_criteria = dict(benchmark_policy.get("criteria") or COMMON_BENCHMARK_DEFAULTS.get("criteria") or {})
        self.benchmark_evaluator = BenchmarkEvaluator(
            risk_free_rate=float(benchmark_policy.get("risk_free_rate", COMMON_BENCHMARK_DEFAULTS["risk_free_rate"]) or COMMON_BENCHMARK_DEFAULTS["risk_free_rate"]),
            criteria=benchmark_criteria,
        )

        self.train_policy = self.investment_model.config_section("train", {}) or {}
        self.freeze_total_cycles = int(self.train_policy.get("freeze_total_cycles", self.freeze_total_cycles) or self.freeze_total_cycles)
        self.freeze_profit_required = int(self.train_policy.get("freeze_profit_required", self.freeze_profit_required) or self.freeze_profit_required)
        self.max_losses_before_optimize = int(self.train_policy.get("max_losses_before_optimize", self.max_losses_before_optimize) or self.max_losses_before_optimize)
        self.freeze_gate_policy = dict(self.train_policy.get("freeze_gate", {}) or {})
        self.auto_apply_mutation = bool(self.train_policy.get("auto_apply_mutation", False))

        agent_weights = self.investment_model.config_section("agent_weights", {}) or {}
        if agent_weights:
            self.selection_meeting.agent_weights = {
                "trend_hunter": float(agent_weights.get("trend_hunter", 1.0) or 1.0),
                "contrarian": float(agent_weights.get("contrarian", 1.0) or 1.0),
            }


    def _maybe_apply_allocator(self, stock_data: Dict[str, Any], cutoff_date: str, cycle_id: int) -> None:
        if not self.allocator_enabled:
            return
        leaderboard_root = self.output_dir.parent
        write_leaderboard(leaderboard_root)
        leaderboard_path = leaderboard_root / "leaderboard.json"
        market_stats = compute_market_stats(
            stock_data,
            cutoff_date,
            regime_policy=self.investment_model.config_section("market_regime", {}) or None,
        )
        regime = str(market_stats.get("regime_hint") or "unknown")
        allocation = build_allocation_plan(
            regime,
            leaderboard_path,
            as_of_date=cutoff_date,
            top_n=max(1, self.allocator_top_n),
        )
        self.last_allocation_plan = allocation.to_dict()
        active_models = list(allocation.active_models)
        selected_model = active_models[0] if active_models else self.model_name
        if selected_model != self.model_name:
            self.current_params = {}
            self.model_name = selected_model
            self.model_config_path = str(resolve_model_config_path(selected_model))
            self._reload_investment_model(self.model_config_path)
        self._emit_agent_status(
            "ModelAllocator",
            "completed",
            f"allocator 已为 {regime} 市场选择主模型 {self.model_name}",
            cycle_id=cycle_id,
            stage="model_allocation",
            progress_pct=24,
            step=2,
            total_steps=6,
            details=self.last_allocation_plan,
            thinking=self._thinking_excerpt(allocation.reasoning),
        )
        self._emit_module_log(
            "allocator",
            "模型分配完成",
            allocation.reasoning,
            cycle_id=cycle_id,
            kind="allocation_plan",
            details=self.last_allocation_plan,
            metrics={
                "active_model_count": len(active_models),
                "cash_reserve": allocation.cash_reserve,
                "confidence": allocation.confidence,
            },
        )

    def _thinking_excerpt(self, reasoning: Any, limit: int = 200) -> str:
        """将多种推理结果安全转换为前端展示文本。"""
        if not reasoning:
            return ""
        if isinstance(reasoning, dict):
            candidate = reasoning.get("reasoning") or reasoning.get("summary") or reasoning.get("regime") or ""
            return str(candidate)[:limit]
        if isinstance(reasoning, (list, tuple)):
            return "；".join(str(item) for item in reasoning[:5])[:limit]
        return str(reasoning)[:limit]

    def _event_context(self, cycle_id: int | None = None) -> Dict[str, Any]:
        meta = dict(self.last_cycle_meta or {})
        ctx: Dict[str, Any] = {"timestamp": datetime.now().isoformat()}
        if cycle_id is not None:
            ctx["cycle_id"] = cycle_id
        elif meta.get("cycle_id") is not None:
            ctx["cycle_id"] = meta.get("cycle_id")
        if meta.get("cutoff_date"):
            ctx["cutoff_date"] = meta.get("cutoff_date")
        return ctx

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
        payload = {
            **self._event_context(cycle_id),
            "agent": agent,
            "status": status,
            "message": message,
        }
        if stage:
            payload["stage"] = stage
        if progress_pct is not None:
            payload["progress_pct"] = int(progress_pct)
        if step is not None:
            payload["step"] = int(step)
        if total_steps is not None:
            payload["total_steps"] = int(total_steps)
        if thinking:
            payload["thinking"] = thinking
        if selected_stocks:
            payload["selected_stocks"] = list(selected_stocks)
        if details is not None:
            payload["details"] = details
        payload.update(extra)
        emit_event("agent_status", payload)
        emit_event("agent_progress", dict(payload))

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
        payload = {
            **self._event_context(cycle_id),
            "module": module,
            "title": title,
            "message": message,
            "kind": kind,
            "level": level,
        }
        if details is not None:
            payload["details"] = details
        if metrics:
            payload["metrics"] = metrics
        payload.update(extra)
        emit_event("module_log", payload)

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
        payload = {
            **self._event_context(cycle_id),
            "meeting": meeting,
            "speaker": speaker,
            "speech": str(speech or "").strip(),
        }
        if role:
            payload["role"] = role
        if picks:
            payload["picks"] = picks
        if suggestions:
            payload["suggestions"] = suggestions
        if decision:
            payload["decision"] = decision
        if confidence is not None:
            payload["confidence"] = confidence
        payload.update(extra)
        emit_event("meeting_speech", payload)

    def _handle_selection_progress(self, payload: dict[str, Any]) -> None:
        agent = str(payload.get("agent") or "SelectionMeeting")
        status = str(payload.get("status") or "running")
        stage = str(payload.get("stage") or "selection")
        progress_pct = payload.get("progress_pct")
        if progress_pct is None:
            progress_pct = {
                "TrendHunter": 38,
                "Contrarian": 46,
                "SelectionMeeting": 54,
            }.get(agent, 40)
            if status == "completed":
                progress_pct = min(100, int(progress_pct) + 8)
            elif status == "error":
                progress_pct = int(progress_pct)
        self._emit_agent_status(
            agent,
            status,
            str(payload.get("message") or ""),
            stage=stage,
            progress_pct=int(progress_pct),
            step=payload.get("step"),
            total_steps=payload.get("total_steps"),
            thinking=self._thinking_excerpt(payload.get("speech") or payload.get("reasoning") or payload.get("overall_view")),
            details=payload.get("details"),
            picks=payload.get("picks"),
        )
        speech = str(payload.get("speech") or payload.get("overall_view") or "").strip()
        if speech:
            self._emit_meeting_speech(
                "selection",
                agent,
                speech,
                role="selector",
                picks=payload.get("picks"),
                confidence=payload.get("confidence"),
            )
        picks = payload.get("picks") or []
        if picks:
            self._emit_module_log(
                "selection",
                f"{agent} 输出候选",
                f"推荐 {len(picks)} 只候选股票",
                kind="selection_candidates",
                details=picks[:10],
                metrics={"candidate_count": len(picks)},
            )

    def _handle_review_progress(self, payload: dict[str, Any]) -> None:
        agent = str(payload.get("agent") or "ReviewMeeting")
        status = str(payload.get("status") or "running")
        stage = str(payload.get("stage") or "review")
        progress_pct = payload.get("progress_pct")
        if progress_pct is None:
            progress_pct = {
                "Strategist": 82,
                "EvoJudge": 88,
                "ReviewDecision": 92,
                "ReviewMeeting": 95,
            }.get(agent, 85)
        self._emit_agent_status(
            agent,
            status,
            str(payload.get("message") or ""),
            stage=stage,
            progress_pct=int(progress_pct),
            thinking=self._thinking_excerpt(payload.get("speech") or payload.get("reasoning")),
            details=payload.get("details"),
        )
        speech = str(payload.get("speech") or payload.get("reasoning") or "").strip()
        if speech:
            self._emit_meeting_speech(
                "review",
                agent,
                speech,
                role="reviewer",
                suggestions=payload.get("suggestions"),
                decision=payload.get("decision"),
                confidence=payload.get("confidence"),
            )
        suggestions = payload.get("suggestions") or []
        if suggestions or payload.get("decision"):
            self._emit_module_log(
                "review",
                f"{agent} 复盘输出",
                str(payload.get("message") or ""),
                kind="review_update",
                details=suggestions or payload.get("decision"),
            )

    def _mark_cycle_skipped(self, cycle_id: int, cutoff_date: str, stage: str, reason: str, **extra: Any) -> None:
        meta = {
            "status": "no_data",
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "stage": stage,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
            **extra,
        }
        self.last_cycle_meta = meta
        self._emit_module_log(stage, f"周期 #{cycle_id} 已跳过", reason, cycle_id=cycle_id, kind="cycle_skipped", level="warn", details=extra or None)
        emit_event("cycle_skipped", meta)

    def run_training_cycle(self) -> Optional[TrainingResult]:
        """
        执行一个完整的训练周期

        Returns:
            TrainingResult 或 None（数据不足时）
        """
        cycle_id = self.current_cycle_id + 1
        logger.info(f"\n{'='*60}")
        logger.info(f"训练周期 #{cycle_id}")
        logger.info(f"{'='*60}")

        if self.experiment_seed is not None:
            seed_value = int(self.experiment_seed) + int(cycle_id)
            random.seed(seed_value)
            np.random.seed(seed_value % (2**32 - 1))
        cutoff_date = normalize_date(
            os.getenv("INVEST_FORCE_CUTOFF_DATE", "")
            or _call_with_compatible_signature(
                self.data_manager.random_cutoff_date,
                preferred_kwargs={
                    "min_date": self.experiment_min_date or "20180101",
                    "max_date": self.experiment_max_date,
                },
            )
        )
        logger.info(f"截断日期: {cutoff_date}")

        requested_data_mode = str(getattr(self, "requested_data_mode", getattr(self.data_manager, "requested_mode", "live")) or "live")
        llm_mode = str(getattr(self, "llm_mode", "live") or "live")

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
        review_applied = False
        benchmark_passed = False
        llm_used = bool(getattr(self.llm_caller.gateway, "available", False))
        self.last_cycle_meta = {
            "status": "running",
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "timestamp": datetime.now().isoformat(),
        }

        logger.info("加载数据...")
        self._emit_agent_status("DataLoader", "loading", f"正在加载 {cutoff_date} 的历史数据...", cycle_id=cycle_id, stage="data_loading", progress_pct=8, step=1, total_steps=6)
        self._emit_module_log("data_loading", "开始加载训练数据", f"截断日期 {cutoff_date}", cycle_id=cycle_id, kind="phase_start")
        min_history_days = max(30, int(self.experiment_min_history_days or getattr(config, "min_history_days", 200)))
        diagnostic_kwargs = {
            "cutoff_date": cutoff_date,
            "stock_count": config.max_stocks,
            "min_history_days": min_history_days,
        }
        diagnostic_order = ["check_training_readiness", "diagnose_training_data"]
        if callable(getattr(self.data_manager, "__dict__", {}).get("diagnose_training_data")):
            diagnostic_order = ["diagnose_training_data", "check_training_readiness"]

        diagnostics: dict[str, Any] | None = None
        for method_name in diagnostic_order:
            method = getattr(self.data_manager, method_name, None)
            if not callable(method):
                continue
            diagnostics = _call_with_compatible_signature(
                method,
                preferred_kwargs=diagnostic_kwargs,
                positional_args=(cutoff_date, config.max_stocks, min_history_days),
            )
            if isinstance(diagnostics, dict):
                break

        if not isinstance(diagnostics, dict):
            diagnostics = _default_training_diagnostics(cutoff_date, config.max_stocks, min_history_days)
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

        try:
            stock_data = self.data_manager.load_stock_data(
                cutoff_date,
                stock_count=config.max_stocks,
                min_history_days=min_history_days,
                include_future_days=max(30, int(self.experiment_simulation_days or getattr(config, "simulation_days", 30))),
            )
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
                error_payload["error"],
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

        resolution = dict(getattr(self.data_manager, "last_resolution", {}) or {})
        effective_data_mode = str(resolution.get("effective_data_mode") or getattr(self.data_manager, "last_source", "unknown") or "unknown")
        degrade_reason = str(resolution.get("degrade_reason") or "")
        degraded = bool(resolution.get("degraded", False))
        data_mode = effective_data_mode
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

        if self.experiment_allowed_models and self.model_name not in self.experiment_allowed_models:
            self.model_name = self.experiment_allowed_models[0]
            self.model_config_path = str(resolve_model_config_path(self.model_name))
            self.current_params = {}
            self._reload_investment_model(self.model_config_path)
        self._maybe_apply_allocator(stock_data, cutoff_date, cycle_id)
        if self.experiment_allowed_models and self.model_name not in self.experiment_allowed_models:
            self.model_name = self.experiment_allowed_models[0]
            self.model_config_path = str(resolve_model_config_path(self.model_name))
            self.current_params = {}
            self._reload_investment_model(self.model_config_path)

        logger.info("Agent 开会讨论选股...")
        self._emit_agent_status("SelectionMeeting", "running", "Agent 开会讨论选股...", cycle_id=cycle_id, stage="selection_meeting", progress_pct=26, step=2, total_steps=6)
        self._emit_module_log("selection", "进入选股会议", "系统开始汇总市场状态和候选标的", cycle_id=cycle_id, kind="phase_start")
        self.investment_model.update_runtime_overrides(self.current_params)
        model_output = self.investment_model.process(stock_data, cutoff_date)
        signal_packet = model_output.signal_packet
        agent_context = model_output.agent_context
        regime_result = {
            "regime": signal_packet.regime,
            "confidence": float(agent_context.metadata.get("confidence", 0.72) or 0.72),
            "reasoning": agent_context.summary,
            "suggested_exposure": max(0.0, min(1.0, 1.0 - float(signal_packet.cash_reserve))),
            "params": {
                **dict(signal_packet.params or {}),
                "top_n": max(len(signal_packet.selected_codes), len(signal_packet.signals)),
                "max_positions": signal_packet.max_positions,
                "stop_loss_pct": signal_packet.params.get("stop_loss_pct", self.current_params.get("stop_loss_pct", COMMON_PARAM_DEFAULTS["stop_loss_pct"])),
                "take_profit_pct": signal_packet.params.get("take_profit_pct", self.current_params.get("take_profit_pct", COMMON_PARAM_DEFAULTS["take_profit_pct"])),
                "position_size": signal_packet.params.get("position_size", self.current_params.get("position_size", COMMON_PARAM_DEFAULTS["position_size"])),
            },
        }
        logger.info(f"市场状态(v2): {regime_result.get('regime', 'unknown')}")
        self._emit_agent_status(
            "InvestmentModel",
            "completed",
            f"{self.model_name} 已输出结构化信号与叙事上下文",
            cycle_id=cycle_id,
            stage="model_extraction",
            progress_pct=30,
            step=2,
            total_steps=6,
            details=model_output.to_dict(),
        )
        self._emit_module_log(
            "model_extraction",
            "模型输出完成",
            agent_context.summary,
            cycle_id=cycle_id,
            kind="model_output",
            details={
                "model_name": model_output.model_name,
                "config_name": model_output.config_name,
                "selected_codes": signal_packet.selected_codes,
            },
            metrics={
                "signal_count": len(signal_packet.signals),
                "max_positions": signal_packet.max_positions,
            },
        )
        self._emit_agent_status(
            "MarketRegime",
            "thinking",
            f"分析当前市场状态: {regime_result.get('regime', 'unknown')}",
            cycle_id=cycle_id,
            stage="market_regime",
            progress_pct=32,
            step=2,
            total_steps=6,
            thinking=self._thinking_excerpt(agent_context.summary),
            details=regime_result,
        )
        self._emit_module_log(
            "market_regime",
            "市场状态识别",
            f"当前市场状态: {regime_result.get('regime', 'unknown')}",
            cycle_id=cycle_id,
            kind="market_regime",
            details=agent_context.summary,
            metrics={
                "confidence": regime_result.get("confidence"),
                "suggested_exposure": regime_result.get("suggested_exposure"),
            },
        )
        meeting_data = self.selection_meeting.run_with_model_output(model_output)

        trading_plan = meeting_data["trading_plan"]
        meeting_log = meeting_data.get("meeting_log", {})
        strategy_advice = meeting_data.get("strategy_advice", {})
        self.meeting_recorder.save_selection(meeting_log, cycle_id)

        for hunter in meeting_log.get("hunters", []):
            picks = hunter.get("result", {}).get("picks", [])
            if picks:
                self.agent_tracker.record_predictions(cycle_id, hunter.get("name", "unknown"), picks)
            self._emit_meeting_speech(
                "selection",
                hunter.get("name", "unknown"),
                hunter.get("result", {}).get("overall_view") or hunter.get("result", {}).get("reasoning") or "已完成候选输出",
                cycle_id=cycle_id,
                role="hunter",
                picks=picks[:10],
                confidence=hunter.get("result", {}).get("confidence"),
            )

        selected = [p.code for p in trading_plan.positions]
        agent_used = bool(meeting_log.get("hunters"))
        selection_mode = "meeting" if selected else "meeting_empty"
        if selected and trading_plan.source and trading_plan.source != "llm":
            selection_mode = f"{trading_plan.source}_selection"

        if not selected:
            logger.warning("模型与会议未产出可交易标的，跳过本周期")
            self._mark_cycle_skipped(cycle_id, cutoff_date, stage="selection", reason="模型与会议未产出可交易标的")
            return None

        logger.info(f"最终选中股票: {selected}")
        self._emit_agent_status(
            "SelectionMeeting",
            "completed",
            f"选股完成，共选中 {len(selected)} 只股票",
            cycle_id=cycle_id,
            stage="selection_meeting",
            progress_pct=58,
            step=2,
            total_steps=6,
            selected_stocks=selected[:10],
            details=meeting_log.get("selected", []),
        )
        self._emit_module_log(
            "selection",
            "选股会议完成",
            f"最终选中 {len(selected)} 只股票",
            cycle_id=cycle_id,
            kind="selection_result",
            details=meeting_log.get("selected", selected)[:10],
            metrics={"selected_count": len(selected), "selection_mode": selection_mode},
        )
        self.agent_tracker.mark_selected(cycle_id, selected)

        selected_data = {code: stock_data[code] for code in selected if code in stock_data}
        if not selected_data:
            logger.warning("选股结果在数据集中不可用，跳过本周期")
            self._mark_cycle_skipped(cycle_id, cutoff_date, stage="selection", reason="选股结果在数据集中不可用")
            return None

        trader = SimulatedTrader(
            initial_capital=float(self.execution_policy.get("initial_capital", getattr(config, "initial_capital", COMMON_EXECUTION_DEFAULTS["initial_capital"])) or getattr(config, "initial_capital", COMMON_EXECUTION_DEFAULTS["initial_capital"])),
            max_positions=trading_plan.max_positions or len(selected),
            position_size_pct=self.current_params.get("position_size", COMMON_PARAM_DEFAULTS["position_size"]),
            commission_rate=float(self.execution_policy.get("commission_rate", COMMON_EXECUTION_DEFAULTS["commission_rate"]) or COMMON_EXECUTION_DEFAULTS["commission_rate"]),
            stamp_tax_rate=float(self.execution_policy.get("stamp_tax_rate", COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"]) or COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"]),
            slippage_rate=float(self.execution_policy.get("slippage_rate", COMMON_EXECUTION_DEFAULTS["slippage_rate"]) or COMMON_EXECUTION_DEFAULTS["slippage_rate"]),
            risk_policy=self.risk_policy,
        )
        trader.set_stock_data(selected_data)
        trader.set_stock_info({
            code: {
                "name": str(frame["name"].iloc[-1]) if "name" in frame.columns and not frame.empty else code,
                "industry": str(frame["industry"].iloc[-1]) if "industry" in frame.columns and not frame.empty else "其他",
                "market_cap": float(frame["market_cap"].dropna().iloc[-1]) if "market_cap" in frame.columns and not frame["market_cap"].dropna().empty else 0.0,
                "roe": float(frame["roe"].dropna().iloc[-1]) if "roe" in frame.columns and not frame["roe"].dropna().empty else 0.0,
            }
            for code, frame in selected_data.items()
        })
        trader.set_trading_plan(trading_plan)

        all_dates = set()
        for df in selected_data.values():
            date_col = "trade_date" if "trade_date" in df.columns else "date"
            if date_col not in df.columns:
                continue
            all_dates.update(df[date_col].apply(normalize_date).tolist())

        dates_after = sorted(d for d in all_dates if d > cutoff_date)
        simulation_days = max(1, int(self.experiment_simulation_days or getattr(config, "simulation_days", 30)))
        if len(dates_after) < simulation_days:
            logger.warning(f"截断日期后交易日不足: {len(dates_after)} < {simulation_days}")
            self._mark_cycle_skipped(
                cycle_id,
                cutoff_date,
                stage="simulation",
                reason=f"截断日期后交易日不足: {len(dates_after)} < {simulation_days}",
            )
            return None

        self._emit_agent_status(
            "SimulatedTrader",
            "running",
            f"模拟交易中... 初始资金 {trader.initial_capital:.2f}",
            cycle_id=cycle_id,
            stage="simulation",
            progress_pct=68,
            step=3,
            total_steps=6,
            details={"simulation_days": simulation_days, "selected_count": len(selected)},
        )
        self._emit_module_log(
            "simulation",
            "开始模拟交易",
            f"模拟 {simulation_days} 个交易日，标的 {', '.join(selected[:5])}",
            cycle_id=cycle_id,
            kind="simulation_start",
            metrics={"simulation_days": simulation_days, "selected_count": len(selected)},
        )

        trading_dates = dates_after[:simulation_days]
        benchmark_daily_values = self.data_manager.get_benchmark_daily_values(trading_dates, index_code="sh.000300")
        market_index_start = (datetime.strptime(cutoff_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
        market_index_frame = self.data_manager.get_market_index_frame(
            index_code="sh.000300",
            start_date=market_index_start,
            end_date=trading_dates[-1] if trading_dates else cutoff_date,
        )
        if market_index_frame is not None and not market_index_frame.empty:
            trader.set_market_index_data(market_index_frame)
        sim_result = trader.run_simulation(trading_dates[0], trading_dates)
        is_profit = sim_result.return_pct > 0

        self.agent_tracker.record_outcomes(cycle_id, sim_result.per_stock_pnl)
        cycle_dict = {
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "return_pct": sim_result.return_pct,
            "profit_loss": sim_result.total_pnl,
            "total_trades": sim_result.total_trades,
            "winning_trades": sim_result.winning_trades,
            "losing_trades": sim_result.losing_trades,
            "win_rate": sim_result.win_rate,
            "selected_stocks": selected,
            "is_profit": is_profit,
            "regime": regime_result.get("regime", "unknown"),
            "plan_source": trading_plan.source,
            "data_mode": data_mode,
            "requested_data_mode": requested_data_mode,
            "effective_data_mode": effective_data_mode,
            "llm_mode": llm_mode,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "selection_mode": selection_mode,
            "agent_used": agent_used,
            "llm_used": llm_used,
            "initial_capital": sim_result.initial_capital,
            "final_value": sim_result.final_value,
        }
        trade_dicts = [
            {
                "date": t.date,
                "action": t.action.value if hasattr(t.action, "value") else str(t.action),
                "ts_code": t.ts_code,
                "price": t.price,
                "shares": t.shares,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "reason": t.reason,
                "source": getattr(t, "source", ""),
                "entry_reason": getattr(t, "entry_reason", ""),
                "exit_reason": getattr(t, "exit_reason", ""),
                "exit_trigger": getattr(t, "exit_trigger", ""),
                "entry_date": getattr(t, "entry_date", ""),
                "entry_price": getattr(t, "entry_price", 0.0),
                "holding_days": getattr(t, "holding_days", 0),
                "stop_loss_price": getattr(t, "stop_loss_price", 0.0),
                "take_profit_price": getattr(t, "take_profit_price", 0.0),
                "trailing_pct": getattr(t, "trailing_pct", None),
                "capital_before": getattr(t, "capital_before", 0.0),
                "capital_after": getattr(t, "capital_after", 0.0),
                "open_price": getattr(t, "open_price", 0.0),
                "high_price": getattr(t, "high_price", 0.0),
                "low_price": getattr(t, "low_price", 0.0),
                "volume": getattr(t, "volume", 0.0),
                "amount": getattr(t, "amount", 0.0),
                "pct_chg": getattr(t, "pct_chg", 0.0),
            }
            for t in sim_result.trade_history
        ]

        daily_values = [r.get("total_value") for r in sim_result.daily_records if r.get("total_value") is not None]
        benchmark_metrics = None
        if len(daily_values) >= 2:
            aligned_benchmark = benchmark_daily_values if len(benchmark_daily_values) == len(daily_values) else None
            benchmark_metrics = self.benchmark_evaluator.evaluate(
                daily_values=daily_values,
                benchmark_daily_values=aligned_benchmark,
                trade_history=trade_dicts,
            )
            benchmark_passed = bool(benchmark_metrics.passed)
            cycle_dict.update({
                "sharpe_ratio": benchmark_metrics.sharpe_ratio,
                "max_drawdown": benchmark_metrics.max_drawdown,
                "excess_return": benchmark_metrics.excess_return,
                "benchmark_return": benchmark_metrics.benchmark_return,
                "benchmark_source": "index_bar:sh.000300" if aligned_benchmark else "none",
                "benchmark_passed": benchmark_passed,
                "benchmark_strict_passed": benchmark_metrics.passed,
            })
        else:
            cycle_dict["benchmark_passed"] = False
            cycle_dict["benchmark_strict_passed"] = False

        strategy_eval = self.strategy_evaluator.evaluate(cycle_dict, trade_dicts, sim_result.daily_records)
        cycle_dict["strategy_scores"] = {
            "signal_accuracy": float(strategy_eval.signal_accuracy),
            "timing_score": float(strategy_eval.timing_score),
            "risk_control_score": float(strategy_eval.risk_control_score),
            "overall_score": float(strategy_eval.overall_score),
            "suggestions": list(strategy_eval.suggestions or []),
        }
        self._emit_agent_status(
            "SimulatedTrader",
            "completed",
            f"模拟完成，收益 {sim_result.return_pct:+.2f}% ，共 {sim_result.total_trades} 笔交易",
            cycle_id=cycle_id,
            stage="simulation",
            progress_pct=78,
            step=3,
            total_steps=6,
            details={"final_value": sim_result.final_value, "win_rate": sim_result.win_rate},
        )
        self._emit_module_log(
            "simulation",
            "模拟交易完成",
            f"期末资金 {sim_result.final_value:.2f}，收益 {sim_result.return_pct:+.2f}%",
            cycle_id=cycle_id,
            kind="simulation_result",
            details=trade_dicts[:12],
            metrics={
                "return_pct": sim_result.return_pct,
                "trade_count": sim_result.total_trades,
                "win_rate": sim_result.win_rate,
            },
        )

        if not is_profit:
            self.consecutive_losses += 1
            logger.warning(f"亏损！连续亏损: {self.consecutive_losses}")
            if self.consecutive_losses >= self.max_losses_before_optimize:
                optimization_events.extend(self._trigger_optimization(cycle_dict, trade_dicts))
        else:
            self.consecutive_losses = 0
            logger.info(f"盈利！收益率: {sim_result.return_pct:.2f}%")

        logger.info("周期结语：复盘会议自省...")
        self._emit_agent_status("ReviewMeeting", "running", "复盘会议自省中...", cycle_id=cycle_id, stage="review_meeting", progress_pct=84, step=4, total_steps=6)
        self._emit_module_log("review", "进入复盘会议", "开始汇总交易表现与策略偏差", cycle_id=cycle_id, kind="phase_start")
        eval_report = EvalReport(
            cycle_id=cycle_id,
            as_of_date=cutoff_date,
            return_pct=sim_result.return_pct,
            total_pnl=sim_result.total_pnl,
            total_trades=sim_result.total_trades,
            win_rate=sim_result.win_rate,
            regime=regime_result.get("regime", "unknown"),
            is_profit=bool(is_profit),
            selected_codes=list(selected),
            benchmark_passed=bool(cycle_dict.get("benchmark_passed", False)),
            benchmark_strict_passed=bool(cycle_dict.get("benchmark_strict_passed", False)),
            sharpe_ratio=float(cycle_dict.get("sharpe_ratio", 0.0) or 0.0),
            max_drawdown=float(cycle_dict.get("max_drawdown", 0.0) or 0.0),
            excess_return=float(cycle_dict.get("excess_return", 0.0) or 0.0),
            data_mode=data_mode,
            selection_mode=selection_mode,
            agent_used=bool(agent_used),
            llm_used=bool(llm_used),
            metadata={
                "model_name": getattr(model_output, "model_name", self.model_name) if 'model_output' in locals() else self.model_name,
                "config_name": getattr(model_output, "config_name", self.model_name) if 'model_output' in locals() and model_output is not None else self.model_config_path,
                "trade_count": len(trade_dicts),
                "requested_data_mode": requested_data_mode,
                "effective_data_mode": effective_data_mode,
                "llm_mode": llm_mode,
                "degraded": degraded,
                "degrade_reason": degrade_reason,
            },
        )
        self.cycle_records.append(cycle_dict)
        agent_accuracy = self.agent_tracker.compute_accuracy(last_n_cycles=20)
        review_decision = self.review_meeting.run_with_eval_report(eval_report, agent_accuracy, self.current_params)
        review_facts = getattr(self.review_meeting, "last_facts", None) or cycle_dict
        self.meeting_recorder.save_review(review_decision, review_facts, cycle_id)

        review_event = OptimizationEvent(
            trigger="review_meeting",
            stage="review_decision",
            decision={
                "strategy_suggestions": review_decision.get("strategy_suggestions", []),
                "param_adjustments": review_decision.get("param_adjustments", {}),
                "agent_weight_adjustments": review_decision.get("agent_weight_adjustments", {}),
            },
            applied_change={},
            notes=review_decision.get("reasoning", ""),
        )

        if review_decision.get("param_adjustments"):
            self.current_params.update(review_decision["param_adjustments"])
            if getattr(self, "investment_model", None) is not None:
                self.investment_model.update_runtime_overrides(review_decision["param_adjustments"])
            review_applied = True
            review_event.applied_change.update({"params": dict(review_decision["param_adjustments"])})
            logger.info(f"根据复盘调整参数: {review_decision['param_adjustments']}")
            self._emit_agent_status(
                "ReviewMeeting",
                "completed",
                f"参数已调整: {list(review_decision.get('param_adjustments', {}).keys())}",
                cycle_id=cycle_id,
                stage="review_meeting",
                progress_pct=96,
                step=4,
                total_steps=6,
                details=review_decision,
                adjustments=review_decision.get("param_adjustments", {}),
            )

        if review_decision.get("agent_weight_adjustments"):
            self.selection_meeting.update_weights(review_decision["agent_weight_adjustments"])
            review_applied = True
            review_event.applied_change.update({"agent_weights": dict(review_decision["agent_weight_adjustments"])})

        optimization_events.append(review_event.to_dict())
        cycle_dict["review_applied"] = review_applied
        self._emit_module_log(
            "review",
            "复盘会议结论",
            review_decision.get("reasoning", "复盘完成"),
            cycle_id=cycle_id,
            kind="review_decision",
            details={
                "strategy_suggestions": review_decision.get("strategy_suggestions", []),
                "param_adjustments": review_decision.get("param_adjustments", {}),
                "agent_weight_adjustments": review_decision.get("agent_weight_adjustments", {}),
            },
            metrics={
                "review_applied": review_applied,
                "suggestion_count": len(review_decision.get("strategy_suggestions", [])),
            },
        )

        config_snapshot_path = str(self.config_service.write_runtime_snapshot(cycle_id=cycle_id, output_dir=self.output_dir))
        audit_tags = {
            "data_mode": data_mode,
            "requested_data_mode": requested_data_mode,
            "effective_data_mode": effective_data_mode,
            "llm_mode": llm_mode,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "selection_mode": selection_mode,
            "meeting_fallback": False,
            "agent_used": agent_used,
            "llm_used": llm_used,
            "mock_data_used": data_mode == "mock",
            "benchmark_passed": benchmark_passed,
            "review_applied": review_applied,
        }

        cycle_result = TrainingResult(
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            selected_stocks=selected,
            initial_capital=sim_result.initial_capital,
            final_value=sim_result.final_value,
            return_pct=sim_result.return_pct,
            is_profit=is_profit,
            trade_history=trade_dicts,
            params=dict(self.current_params),
            analysis=review_decision.get("reasoning", ""),
            data_mode=data_mode,
            requested_data_mode=requested_data_mode,
            effective_data_mode=effective_data_mode,
            llm_mode=llm_mode,
            degraded=degraded,
            degrade_reason=degrade_reason,
            selection_mode=selection_mode,
            agent_used=agent_used,
            llm_used=llm_used,
            benchmark_passed=benchmark_passed,
            strategy_scores=dict(cycle_dict.get("strategy_scores") or {}),
            review_applied=review_applied,
            config_snapshot_path=config_snapshot_path,
            optimization_events=optimization_events,
            audit_tags=audit_tags,
            model_name=getattr(model_output, "model_name", self.model_name) if "model_output" in locals() and model_output is not None else self.model_name,
            config_name=getattr(model_output, "config_name", self.model_config_path) if "model_output" in locals() and model_output is not None else self.model_config_path,
        )
        self.cycle_history.append(cycle_result)
        self.current_cycle_id += 1
        self._record_self_assessment(cycle_result, cycle_dict)

        self.last_cycle_meta = {
            "status": "ok",
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "return_pct": sim_result.return_pct,
            "requested_data_mode": requested_data_mode,
            "effective_data_mode": effective_data_mode,
            "llm_mode": llm_mode,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "timestamp": datetime.now().isoformat(),
        }
        self._save_cycle_result(cycle_result)

        # 发射周期完成事件
        emit_event("cycle_complete", {
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "return_pct": sim_result.return_pct,
            "is_profit": bool(is_profit),
            "selected_count": len(selected),
            "selected_stocks": selected[:10],
            "trade_count": len(trade_dicts),
            "final_value": sim_result.final_value,
            "review_applied": review_applied,
            "selection_mode": selection_mode,
            "requested_data_mode": requested_data_mode,
            "effective_data_mode": effective_data_mode,
            "llm_mode": llm_mode,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "timestamp": datetime.now().isoformat()
        })
        self._emit_module_log(
            "cycle_complete",
            f"周期 #{cycle_id} 完成",
            f"收益 {sim_result.return_pct:+.2f}% ，共 {len(selected)} 只选股",
            cycle_id=cycle_id,
            kind="cycle_complete",
            details={
                "selected_stocks": selected[:10],
                "trade_count": len(trade_dicts),
                "review_applied": review_applied,
                "requested_data_mode": requested_data_mode,
                "effective_data_mode": effective_data_mode,
                "llm_mode": llm_mode,
                "degraded": degraded,
                "degrade_reason": degrade_reason,
            },
            metrics={
                "return_pct": sim_result.return_pct,
                "selected_count": len(selected),
                "trade_count": len(trade_dicts),
            },
        )

        if self.on_cycle_complete:
            self.on_cycle_complete(cycle_result)

        logger.info(
            f"\n周期 #{cycle_id} 完成: "
            f"收益率 {sim_result.return_pct:.2f}%, "
            f"{'盈利' if is_profit else '亏损'}"
        )
        return cycle_result

    def _trigger_optimization(self, cycle_dict: Dict, trade_dicts: List[Dict]) -> List[Dict[str, Any]]:
        return trigger_loss_optimization(self, cycle_dict, trade_dicts, event_factory=OptimizationEvent)

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
        logger.info(f"\n{'#'*60}")
        logger.info(f"开始持续训练 (最多 {max_cycles} 个周期)")
        logger.info(f"{'#'*60}")

        for i in range(max_cycles):
            # 检查固化条件
            if self.should_freeze():
                logger.info("🎉 达到固化条件！")
                if self.stop_on_freeze:
                    return self._freeze_model()
                logger.info("配置为继续训练，不因固化条件提前停止")

            self.total_cycle_attempts += 1
            result = self.run_training_cycle()
            if result is None:
                self.skipped_cycle_count += 1
                logger.warning(f"周期 {i+1} 执行失败，跳过")
                continue

            profits = sum(1 for r in self.cycle_history if r.is_profit)
            total   = len(self.cycle_history)
            logger.info(
                f"进度: {i+1}/{max_cycles} | 盈利: {profits}/{total} | "
                f"连续亏损: {self.consecutive_losses}"
            )

        return self._generate_report()

    def _record_self_assessment(self, cycle_result: TrainingResult, cycle_dict: Dict):
        """记录单周期自我评估快照"""
        snapshot = build_self_assessment_snapshot(SelfAssessmentSnapshot, cycle_result, cycle_dict)
        self.assessment_history.append(snapshot)

    def _rolling_self_assessment(self, window: Optional[int] = None) -> Dict:
        """滚动自我评估摘要（用于冻结门控）"""
        return rolling_self_assessment(self.assessment_history, self.freeze_total_cycles, window=window)

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
        rolling = self._rolling_self_assessment(self.freeze_total_cycles)
        return should_freeze_report(
            self.cycle_history,
            self.freeze_total_cycles,
            self.freeze_profit_required,
            self.freeze_gate_policy,
            rolling,
        )

    def _freeze_model(self) -> Dict:
        """固化模型并保存"""
        logger.info(f"\n{'='*50}\n🎉 模型固化！\n{'='*50}")

        rolling = self._rolling_self_assessment(self.freeze_total_cycles)
        report = build_freeze_report(
            self.cycle_history,
            self.current_params,
            self.freeze_total_cycles,
            self.freeze_profit_required,
            self.freeze_gate_policy,
            rolling,
        )

        path = self.output_dir / "model_frozen.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"固化报告: {path}")
        return report

    def _generate_report(self) -> Dict:
        return generate_training_report(
            self.total_cycle_attempts,
            self.skipped_cycle_count,
            self.cycle_history,
            self.current_params,
            self.should_freeze(),
            self._rolling_self_assessment(self.freeze_total_cycles),
        )

    def _save_cycle_result(self, result: TrainingResult):
        """将周期结果写入 JSON"""
        path = self.output_dir / f"cycle_{result.cycle_id}.json"

        def _bool(v):
            """将可能为 numpy.bool 的值转换为 Python bool"""
            return bool(v)

        def _jsonable(value):
            if isinstance(value, dict):
                return {k: _jsonable(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_jsonable(v) for v in value]
            if isinstance(value, tuple):
                return [_jsonable(v) for v in value]
            if isinstance(value, np.generic):
                return value.item()
            return value

        scoring_changed_keys = []
        scoring_mutation_count = 0
        for event in result.optimization_events:
            applied = dict(event.get("applied_change") or {})
            scoring = dict(applied.get("scoring") or {})
            if scoring:
                scoring_mutation_count += 1
                for section_name, section_values in scoring.items():
                    if isinstance(section_values, dict):
                        for key in section_values.keys():
                            scoring_changed_keys.append(f"{section_name}.{key}")

        data = {
            "cycle_id": result.cycle_id,
            "cutoff_date": result.cutoff_date,
            "selected_stocks": result.selected_stocks,
            "initial_capital": result.initial_capital,
            "final_value": result.final_value,
            "return_pct": result.return_pct,
            "is_profit": _bool(result.is_profit),
            "params": result.params,
            "trade_count": len(result.trade_history),
            "trades": _jsonable(result.trade_history),
            "analysis": result.analysis,
            "data_mode": result.data_mode,
            "requested_data_mode": result.requested_data_mode,
            "effective_data_mode": result.effective_data_mode,
            "llm_mode": result.llm_mode,
            "degraded": _bool(result.degraded),
            "degrade_reason": result.degrade_reason,
            "selection_mode": result.selection_mode,
            "agent_used": _bool(result.agent_used),
            "llm_used": _bool(result.llm_used),
            "benchmark_passed": _bool(result.benchmark_passed),
            "strategy_scores": _jsonable(dict(result.strategy_scores or {})),
            "review_applied": _bool(result.review_applied),
            "config_snapshot_path": result.config_snapshot_path,
            "optimization_events": _jsonable(result.optimization_events),
            "audit_tags": _jsonable({k: _bool(v) if isinstance(v, (bool, np.bool_)) else v for k, v in result.audit_tags.items()}),
            "model_name": result.model_name,
            "config_name": result.config_name,
            "allocation_plan": _jsonable(getattr(self, "last_allocation_plan", {}) or {}),
            "scoring_mutation_count": scoring_mutation_count,
            "scoring_changed_keys": sorted(set(scoring_changed_keys)),
        }
        snapshot = next((s for s in self.assessment_history if s.cycle_id == result.cycle_id), None)
        if snapshot:
            data["self_assessment"] = {
                "regime": snapshot.regime,
                "plan_source": snapshot.plan_source,
                "sharpe_ratio": snapshot.sharpe_ratio,
                "max_drawdown": snapshot.max_drawdown,
                "excess_return": snapshot.excess_return,
                "benchmark_passed": _bool(snapshot.benchmark_passed),
            }
        if result.strategy_scores:
            data.setdefault("self_assessment", {})
            data["self_assessment"].update({
                "signal_accuracy": float(result.strategy_scores.get("signal_accuracy", 0.0) or 0.0),
                "timing_score": float(result.strategy_scores.get("timing_score", 0.0) or 0.0),
                "risk_control_score": float(result.strategy_scores.get("risk_control_score", 0.0) or 0.0),
                "overall_score": float(result.strategy_scores.get("overall_score", 0.0) or 0.0),
            })
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        leaderboard_root = self.output_dir.parent
        try:
            write_leaderboard(leaderboard_root)
        except Exception:
            logger.debug("leaderboard update failed", exc_info=True)


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
