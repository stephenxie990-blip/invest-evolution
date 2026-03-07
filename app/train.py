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
        2. AdaptiveSelector.select()      多因子选股
        3. MeetingRunner.run_selection_meeting()  Agent 开会讨论（若启用）
        4. SimulatedTrader.run_simulation()  30天模拟交易
        5. StrategyEvaluator.evaluate()   评估结果
        6. 连续亏损 ≥ 3 次 → 触发优化：
           LLMOptimizer.analyze_loss()   + EvolutionEngine.evolve()
        7. should_freeze() → 固化模型
"""

import argparse
import json
import logging
import random
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

from config import OUTPUT_DIR, PROJECT_ROOT, config, normalize_date
from config.services import EvolutionConfigService, RuntimePathConfigService
from invest.shared import AgentTracker, LLMCaller, compute_market_stats, make_simple_plan
from market_data import DataManager, MockDataProvider
from invest.selection import AdaptiveSelector
from invest.evolution import LLMOptimizer, StrategyEvolutionOptimizer, EvolutionEngine
from invest.trading import SimulatedTrader
from invest.evaluation import StrategyEvaluator, BenchmarkEvaluator
from invest.agents import (
    MarketRegimeAgent, TrendHunterAgent, ContrarianAgent,
    CommanderAgent, StrategistAgent, EvoJudgeAgent
)
from invest.meetings import SelectionMeeting, ReviewMeeting, MeetingRecorder

logger = logging.getLogger(__name__)


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
            new_params["position_size"]   = min(new_params.get("position_size", 0.2) * 1.1, 0.5)
            new_params["take_profit_pct"] = min(new_params.get("take_profit_pct", 0.15) * 1.1, 0.5)
        elif action == "decrease":
            new_params["position_size"]   = max(new_params.get("position_size", 0.2) * 0.9, 0.05)
            new_params["take_profit_pct"] = max(new_params.get("take_profit_pct", 0.15) * 0.9, 0.05)

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
    selection_mode: str = "unknown"
    agent_used: bool = False
    llm_used: bool = False
    benchmark_passed: bool = False
    review_applied: bool = False
    config_snapshot_path: str = ""
    optimization_events: List[Dict[str, Any]] = field(default_factory=list)
    audit_tags: Dict[str, Any] = field(default_factory=dict)


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

    DEFAULT_PARAMS = {
        "ma_short":        5,
        "ma_long":         20,
        "rsi_period":      14,
        "rsi_oversold":    30,
        "rsi_overbought":  70,
        "stop_loss_pct":   0.05,
        "take_profit_pct": 0.10,
        "position_size":   0.20,
    }

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
        self.selector          = AdaptiveSelector()
        self.llm_optimizer     = LLMOptimizer()
        self.evo_optimizer     = StrategyEvolutionOptimizer()
        self.evolution_engine  = EvolutionEngine(population_size=10)
        self.strategy_evaluator = StrategyEvaluator()
        self.benchmark_evaluator = BenchmarkEvaluator()
        self.data_manager      = DataManager(data_provider=data_provider)

        # Agent 团队 & 会议组件
        self.llm_caller = LLMCaller()
        
        self.agents = {
            "regime":     MarketRegimeAgent(),
            "trend":      TrendHunterAgent(),
            "contrarian": ContrarianAgent(),
            "strategist": StrategistAgent(),
            "commander":  CommanderAgent(),
            "evo_judge":  EvoJudgeAgent(),
        }
        
        self.selection_meeting = SelectionMeeting(
            llm_caller=self.llm_caller,
            trend_hunter=self.agents["trend"],
            contrarian=self.agents["contrarian"],
        )
        self.agent_tracker = AgentTracker()
        self.review_meeting = ReviewMeeting(
            llm_caller=self.llm_caller,
            agent_tracker=self.agent_tracker,
            strategist=self.agents["strategist"],
            evo_judge=self.agents["evo_judge"],
            commander=self.agents["commander"],
        )
        self.meeting_recorder = MeetingRecorder(base_dir=meeting_log_dir)
        self.config_service = EvolutionConfigService(
            project_root=PROJECT_ROOT,
            live_config=config,
            audit_log_path=Path(config_audit_log_path) if config_audit_log_path else None,
            snapshot_dir=Path(config_snapshot_dir) if config_snapshot_dir else None,
        )

        # 状态
        self.cycle_history:   List[TrainingResult] = []
        self.cycle_records:   List[Dict] = []
        self.current_cycle_id = 0
        self.consecutive_losses = 0
        self.current_params   = dict(self.DEFAULT_PARAMS)
        self.assessment_history: List[SelfAssessmentSnapshot] = []
        self.optimization_events_history: List[OptimizationEvent] = []

        # 条件
        self.freeze_total_cycles       = freeze_total_cycles
        self.freeze_profit_required    = freeze_profit_required
        self.max_losses_before_optimize = max_losses_before_optimize

        # 回调
        self.on_cycle_complete: Optional[Callable] = None
        self.on_optimize:       Optional[Callable] = None

        # 输出目录
        self.output_dir = Path(output_dir) if output_dir else (
            OUTPUT_DIR / "training"
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("自我学习控制器初始化完成")

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

        optimization_events: list[dict[str, Any]] = []
        review_applied = False
        benchmark_passed = False
        llm_used = bool(getattr(self.llm_caller.gateway, "available", False))

        cutoff_date = self.data_manager.random_cutoff_date()
        logger.info(f"截断日期: {cutoff_date}")

        logger.info("加载数据...")
        min_history_days = max(30, int(getattr(config, "min_history_days", 200)))
        diagnostics = self.data_manager.diagnose_training_data(
            cutoff_date=cutoff_date,
            stock_count=config.max_stocks,
            min_history_days=min_history_days,
        )
        if not diagnostics.get("ready", False):
            logger.warning(
                "训练前数据诊断: eligible=%s target=%s range=%s~%s issues=%s",
                diagnostics.get("eligible_stock_count", 0),
                diagnostics.get("target_stock_count", 0),
                diagnostics.get("date_range", {}).get("min"),
                diagnostics.get("date_range", {}).get("max"),
                "；".join(diagnostics.get("issues", [])) or "none",
            )

        stock_data = self.data_manager.load_stock_data(
            cutoff_date,
            stock_count=config.max_stocks,
            min_history_days=min_history_days,
            include_future_days=max(30, getattr(config, "simulation_days", 30)),
        )
        data_mode = getattr(self.data_manager, "last_source", "unknown")

        if not stock_data:
            latest = getattr(self.data_manager, "last_diagnostics", diagnostics)
            logger.error("没有加载到数据: %s", "；".join(latest.get("issues", [])) or "未知原因")
            for suggestion in latest.get("suggestions", []):
                logger.error("建议: %s", suggestion)
            return None

        logger.info("Agent 开会讨论选股...")
        market_stats = compute_market_stats(stock_data, cutoff_date)
        regime_perception = self.agents["regime"].perceive(market_stats)
        regime_reasoning = self.agents["regime"].reason(regime_perception)
        regime_result = self.agents["regime"].act(regime_reasoning)
        logger.info(f"市场状态: {regime_result.get('regime', 'unknown')}")

        meeting_data = self.selection_meeting.run_with_data(regime_result, stock_data, cutoff_date)
        trading_plan = meeting_data["trading_plan"]
        meeting_log = meeting_data.get("meeting_log", {})
        self.meeting_recorder.save_selection(meeting_log, cycle_id)

        for hunter in meeting_log.get("hunters", []):
            picks = hunter.get("result", {}).get("picks", [])
            if picks:
                self.agent_tracker.record_predictions(cycle_id, hunter.get("name", "unknown"), picks)

        selected = [p.code for p in trading_plan.positions]
        agent_used = bool(meeting_log.get("hunters"))
        selection_mode = "meeting" if selected else "meeting_empty"
        if selected and trading_plan.source and trading_plan.source != "llm":
            selection_mode = f"{trading_plan.source}_selection"

        if not selected:
            logger.warning("Agent 会议未选中股票，使用算法降级")
            selection_mode = "algorithm_fallback"
            self.selector = AdaptiveSelector(self.current_params)
            selected = self.selector.select(stock_data, cutoff_date, top_n=config.max_positions)
            if selected:
                regime_params = regime_result.get("params", {})
                trading_plan = make_simple_plan(
                    selected_stocks=selected,
                    cutoff_date=cutoff_date,
                    stop_loss_pct=regime_params.get("stop_loss_pct", self.current_params.get("stop_loss_pct", 0.05)),
                    take_profit_pct=regime_params.get("take_profit_pct", self.current_params.get("take_profit_pct", 0.15)),
                    trailing_pct=0.10,
                    position_size=regime_params.get("position_size", self.current_params.get("position_size", 0.20)),
                    max_positions=regime_params.get("max_positions", config.max_positions),
                    max_hold_days=max(30, getattr(config, "simulation_days", 30)),
                )
            else:
                logger.warning("算法降级后仍无可交易标的，跳过本周期")
                return None

        logger.info(f"最终选中股票: {selected}")
        self.agent_tracker.mark_selected(cycle_id, selected)

        selected_data = {code: stock_data[code] for code in selected if code in stock_data}
        if not selected_data:
            logger.warning("选股结果在数据集中不可用，跳过本周期")
            return None

        trader = SimulatedTrader(
            initial_capital=config.initial_capital,
            max_positions=trading_plan.max_positions or len(selected),
            position_size_pct=self.current_params.get("position_size", 0.20),
        )
        trader.set_stock_data(selected_data)
        trader.set_trading_plan(trading_plan)

        all_dates = set()
        for df in selected_data.values():
            date_col = "trade_date" if "trade_date" in df.columns else "date"
            if date_col not in df.columns:
                continue
            all_dates.update(df[date_col].apply(normalize_date).tolist())

        dates_after = sorted(d for d in all_dates if d > cutoff_date)
        simulation_days = max(1, getattr(config, "simulation_days", 30))
        if len(dates_after) < simulation_days:
            logger.warning(f"截断日期后交易日不足: {len(dates_after)} < {simulation_days}")
            return None

        trading_dates = dates_after[:simulation_days]
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
                "pnl": t.pnl,
                "reason": t.reason,
            }
            for t in sim_result.trade_history
        ]

        daily_values = [r.get("total_value") for r in sim_result.daily_records if r.get("total_value") is not None]
        benchmark_metrics = None
        if len(daily_values) >= 2:
            benchmark_metrics = self.benchmark_evaluator.evaluate(
                daily_values=daily_values,
                trade_history=trade_dicts,
            )
            benchmark_passed = (
                benchmark_metrics.total_return > 0
                and benchmark_metrics.sharpe_ratio >= 0.8
                and benchmark_metrics.max_drawdown < 15.0
                and benchmark_metrics.profit_loss_ratio >= 1.0
            )
            cycle_dict.update({
                "sharpe_ratio": benchmark_metrics.sharpe_ratio,
                "max_drawdown": benchmark_metrics.max_drawdown,
                "excess_return": benchmark_metrics.excess_return,
                "benchmark_passed": benchmark_passed,
                "benchmark_strict_passed": benchmark_metrics.passed,
            })
        else:
            cycle_dict["benchmark_passed"] = False
            cycle_dict["benchmark_strict_passed"] = False

        self.strategy_evaluator.evaluate(cycle_dict, trade_dicts, sim_result.daily_records)

        if not is_profit:
            self.consecutive_losses += 1
            logger.warning(f"亏损！连续亏损: {self.consecutive_losses}")
            if self.consecutive_losses >= self.max_losses_before_optimize:
                optimization_events.extend(self._trigger_optimization(cycle_dict, trade_dicts))
        else:
            self.consecutive_losses = 0
            logger.info(f"盈利！收益率: {sim_result.return_pct:.2f}%")

        logger.info("周期结语：复盘会议自省...")
        self.cycle_records.append(cycle_dict)
        recent_cycle_dicts = self.cycle_records[-max(1, self.freeze_total_cycles):]
        agent_accuracy = self.agent_tracker.compute_accuracy(last_n_cycles=20)
        review_decision = self.review_meeting.run(recent_cycle_dicts, agent_accuracy, self.current_params)
        self.meeting_recorder.save_review(review_decision, cycle_dict, cycle_id)

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
            review_applied = True
            review_event.applied_change.update({"params": dict(review_decision["param_adjustments"])})
            logger.info(f"根据复盘调整参数: {review_decision['param_adjustments']}")

        if review_decision.get("agent_weight_adjustments"):
            self.selection_meeting.update_weights(review_decision["agent_weight_adjustments"])
            review_applied = True
            review_event.applied_change.update({"agent_weights": dict(review_decision["agent_weight_adjustments"])})

        optimization_events.append(review_event.to_dict())
        cycle_dict["review_applied"] = review_applied

        config_snapshot_path = str(self.config_service.write_runtime_snapshot(cycle_id=cycle_id, output_dir=self.output_dir))
        audit_tags = {
            "data_mode": data_mode,
            "selection_mode": selection_mode,
            "meeting_fallback": selection_mode == "algorithm_fallback",
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
            selection_mode=selection_mode,
            agent_used=agent_used,
            llm_used=llm_used,
            benchmark_passed=benchmark_passed,
            review_applied=review_applied,
            config_snapshot_path=config_snapshot_path,
            optimization_events=optimization_events,
            audit_tags=audit_tags,
        )
        self.cycle_history.append(cycle_result)
        self.current_cycle_id += 1
        self._record_self_assessment(cycle_result, cycle_dict)

        self._save_cycle_result(cycle_result)

        if self.on_cycle_complete:
            self.on_cycle_complete(cycle_result)

        logger.info(
            f"\n周期 #{cycle_id} 完成: "
            f"收益率 {sim_result.return_pct:.2f}%, "
            f"{'盈利' if is_profit else '亏损'}"
        )
        return cycle_result

    def _trigger_optimization(self, cycle_dict: Dict, trade_dicts: List[Dict]) -> List[Dict[str, Any]]:
        """
        触发优化流程

        1. LLM 亏损分析 + 参数调整
        2. 遗传算法进化（用历史 return_pct 作适应度）
        """
        logger.info(f"⚠️ 连续 {self.consecutive_losses} 次亏损，触发自我优化...")
        events: List[Dict[str, Any]] = []

        try:
            analysis = self.llm_optimizer.analyze_loss(cycle_dict, trade_dicts)
            logger.info(f"LLM 分析: {analysis.cause}")
            logger.info(f"建议: {analysis.suggestions}")
            llm_event = OptimizationEvent(
                trigger="consecutive_losses",
                stage="llm_analysis",
                decision={"cause": analysis.cause},
                suggestions=list(getattr(analysis, "suggestions", []) or []),
            )

            adjustments = self.llm_optimizer.generate_strategy_fix(analysis)
            if adjustments:
                self.current_params.update(adjustments)
                llm_event.applied_change = dict(adjustments)
                logger.info(f"参数已更新: {self.current_params}")
            events.append(llm_event.to_dict())
            self._append_optimization_event(llm_event)

            if len(self.cycle_history) >= 3:
                fitness_scores = [max(r.return_pct, -50) for r in self.cycle_history[-10:]]
                if len(self.evolution_engine.population) == 0:
                    self.evolution_engine.initialize_population(self.current_params)

                pop_size = len(self.evolution_engine.population)
                if len(fitness_scores) > pop_size:
                    fitness_scores = fitness_scores[-pop_size:]
                elif len(fitness_scores) < pop_size:
                    fitness_scores = fitness_scores + [0.0] * (pop_size - len(fitness_scores))

                self.evolution_engine.evolve(fitness_scores)
                best_params = self.evolution_engine.get_best_params()
                evo_event = OptimizationEvent(
                    trigger="consecutive_losses",
                    stage="evolution_engine",
                    decision={"fitness_scores": fitness_scores[-5:]},
                    applied_change=dict(best_params or {}),
                    notes="population evolved",
                )
                if best_params:
                    self.current_params.update(best_params)
                    logger.info(f"遗传算法优化参数: {best_params}")
                events.append(evo_event.to_dict())
                self._append_optimization_event(evo_event)

        except Exception as e:
            err_event = OptimizationEvent(
                trigger="consecutive_losses",
                stage="optimization_error",
                status="error",
                notes=str(e),
            )
            events.append(err_event.to_dict())
            self._append_optimization_event(err_event)
            logger.error(f"优化过程出错: {e}")

        self.consecutive_losses = 0
        logger.info("✅ 优化完成，继续训练...")

        if self.on_optimize:
            self.on_optimize(self.current_params)
        return events

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
                return self._freeze_model()

            result = self.run_training_cycle()
            if result is None:
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
        snapshot = SelfAssessmentSnapshot(
            cycle_id=cycle_result.cycle_id,
            cutoff_date=cycle_result.cutoff_date,
            regime=cycle_dict.get("regime", "unknown"),
            plan_source=cycle_dict.get("plan_source", "unknown"),
            return_pct=cycle_result.return_pct,
            is_profit=cycle_result.is_profit,
            sharpe_ratio=float(cycle_dict.get("sharpe_ratio", 0.0) or 0.0),
            max_drawdown=float(cycle_dict.get("max_drawdown", 0.0) or 0.0),
            excess_return=float(cycle_dict.get("excess_return", 0.0) or 0.0),
            benchmark_passed=bool(cycle_dict.get("benchmark_passed", False)),
        )
        self.assessment_history.append(snapshot)

    def _rolling_self_assessment(self, window: Optional[int] = None) -> Dict:
        """滚动自我评估摘要（用于冻结门控）"""
        if not self.assessment_history:
            return {}

        w = max(1, window or self.freeze_total_cycles)
        recent = self.assessment_history[-w:]
        n = len(recent)
        profit_count = sum(1 for s in recent if s.is_profit)

        return {
            "window": n,
            "profit_count": profit_count,
            "win_rate": profit_count / n if n > 0 else 0.0,
            "avg_return": float(np.mean([s.return_pct for s in recent])) if recent else 0.0,
            "avg_sharpe": float(np.mean([s.sharpe_ratio for s in recent])) if recent else 0.0,
            "avg_max_drawdown": float(np.mean([s.max_drawdown for s in recent])) if recent else 0.0,
            "avg_excess_return": float(np.mean([s.excess_return for s in recent])) if recent else 0.0,
            "benchmark_pass_rate": (
                sum(1 for s in recent if s.benchmark_passed) / n if n > 0 else 0.0
            ),
        }

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
        if len(self.cycle_history) < self.freeze_total_cycles:
            return False

        rolling = self._rolling_self_assessment(self.freeze_total_cycles)
        if not rolling:
            return False

        required_win_rate = self.freeze_profit_required / max(self.freeze_total_cycles, 1)
        return (
            rolling["win_rate"] >= required_win_rate
            and rolling["avg_return"] > 0
            and rolling["avg_sharpe"] >= 0.8
            and rolling["avg_max_drawdown"] < 15.0
            and rolling["benchmark_pass_rate"] >= 0.60
        )

    def _freeze_model(self) -> Dict:
        """固化模型并保存"""
        logger.info(f"\n{'='*50}\n🎉 模型固化！\n{'='*50}")

        total  = len(self.cycle_history)
        profits = sum(1 for r in self.cycle_history if r.is_profit)
        rolling = self._rolling_self_assessment(self.freeze_total_cycles)

        report = {
            "frozen":               True,
            "total_cycles":         total,
            "total_profit_count":   profits,
            "profit_rate":          profits / total if total > 0 else 0,
            "recent_10_profit_count": sum(1 for r in self.cycle_history[-10:] if r.is_profit),
            "final_params":         self.current_params,
            "frozen_time":          datetime.now().isoformat(),
            "self_assessment":      rolling,
            "freeze_gate": {
                "window": self.freeze_total_cycles,
                "required_win_rate": self.freeze_profit_required / max(self.freeze_total_cycles, 1),
                "required_avg_return": 0.0,
                "required_avg_sharpe": 0.8,
                "required_avg_max_drawdown": 15.0,
                "required_benchmark_pass_rate": 0.60,
            },
        }

        path = self.output_dir / "model_frozen.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"固化报告: {path}")
        return report

    def _generate_report(self) -> Dict:
        if not self.cycle_history:
            return {"status": "no_data"}
        total   = len(self.cycle_history)
        profits = sum(1 for r in self.cycle_history if r.is_profit)
        return {
            "status":          "completed",
            "total_cycles":    total,
            "profit_cycles":   profits,
            "loss_cycles":     total - profits,
            "profit_rate":     profits / total if total > 0 else 0,
            "current_params":  self.current_params,
            "is_frozen":       self.should_freeze(),
            "self_assessment": self._rolling_self_assessment(self.freeze_total_cycles),
        }

    def _save_cycle_result(self, result: TrainingResult):
        """将周期结果写入 JSON"""
        path = self.output_dir / f"cycle_{result.cycle_id}.json"
        data = {
            "cycle_id": result.cycle_id,
            "cutoff_date": result.cutoff_date,
            "selected_stocks": result.selected_stocks,
            "initial_capital": result.initial_capital,
            "final_value": result.final_value,
            "return_pct": result.return_pct,
            "is_profit": result.is_profit,
            "params": result.params,
            "trade_count": len(result.trade_history),
            "analysis": result.analysis,
            "data_mode": result.data_mode,
            "selection_mode": result.selection_mode,
            "agent_used": result.agent_used,
            "llm_used": result.llm_used,
            "benchmark_passed": result.benchmark_passed,
            "review_applied": result.review_applied,
            "config_snapshot_path": result.config_snapshot_path,
            "optimization_events": result.optimization_events,
            "audit_tags": result.audit_tags,
        }
        snapshot = next((s for s in self.assessment_history if s.cycle_id == result.cycle_id), None)
        if snapshot:
            data["self_assessment"] = {
                "regime": snapshot.regime,
                "plan_source": snapshot.plan_source,
                "sharpe_ratio": snapshot.sharpe_ratio,
                "max_drawdown": snapshot.max_drawdown,
                "excess_return": snapshot.excess_return,
                "benchmark_passed": snapshot.benchmark_passed,
            }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


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

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    logger.info(f"训练参数: cycles={args.cycles}, mock={args.mock}")

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
        mock_provider = MockDataProvider(stock_count=30, days=1500, start_date="20200101")
        controller = SelfLearningController(
            output_dir=output_dir,
            meeting_log_dir=meeting_log_dir,
            config_audit_log_path=config_audit_log_path,
            config_snapshot_dir=config_snapshot_dir,
            freeze_total_cycles=args.freeze_n,
            freeze_profit_required=args.freeze_m,
            data_provider=mock_provider,
        )
        controller.llm_caller.dry_run = True

    report = controller.run_continuous(max_cycles=args.cycles)
    logger.info(f"\n训练完成: {report}")


if __name__ == "__main__":
    train_main()
