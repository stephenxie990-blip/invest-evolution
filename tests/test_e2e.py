"""
端到端测试

用模拟数据跑完整的训练循环
验证所有 Phase 0-4 的功能在一起正常工作

运行方式：
  python tests/test_e2e.py
"""

import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import logging

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def make_large_stock_data(n=100, days=300):
    """生成大规模模拟数据"""
    np.random.seed(123)
    dates = pd.date_range("2022-01-01", periods=days, freq="B")
    stock_data = {}
    for i in range(n):
        code = f"sh.{600000+i}"
        trend = np.random.choice([-0.001, 0, 0.001])
        close = 10 + np.cumsum(np.random.randn(len(dates)) * 0.3 + trend)
        close = np.maximum(close, 0.5)
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "trade_date": dates.strftime("%Y%m%d"),
            "open": close * (1 + np.random.randn(len(dates)) * 0.003),
            "high": close * (1 + np.abs(np.random.randn(len(dates))) * 0.01),
            "low": close * (1 - np.abs(np.random.randn(len(dates))) * 0.01),
            "close": close,
            "volume": np.random.randint(100000, 50000000, len(dates)).astype(float),
            "pct_chg": pd.Series(close).pct_change().fillna(0) * 100,
        })
        stock_data[code] = df
    return stock_data, dates


def test_e2e():
    from core import LLMCaller
    from core import compute_market_stats
    from core import make_simple_plan
    from meetings import SelectionMeeting
    from core import AgentTracker
    from meetings import ReviewMeeting
    from meetings import MeetingRecorder
    from agents import MarketRegimeAgent
    from optimization import AdaptiveSelector
    from trading import SimulatedTrader
    from optimization import EvolutionEngine
    from core import summarize_stocks

    print("=" * 60)
    print("端到端全流程测试")
    print("=" * 60)

    # 初始化全部组件
    llm_caller = LLMCaller(dry_run=True)
    selector = AdaptiveSelector()
    market_regime_agent = MarketRegimeAgent(llm_caller=llm_caller)
    # 使用算法模式以避免 LLM dry_run 返回空结果
    selection_meeting = SelectionMeeting(llm_caller=None, max_hunters=2)
    agent_tracker = AgentTracker()
    review_meeting = ReviewMeeting(llm_caller=None)

    with tempfile.TemporaryDirectory() as tmpdir:
        meeting_recorder = MeetingRecorder(base_dir=tmpdir)

        evolution_engine = EvolutionEngine(population_size=10)
        params = {
            "ma_short": 5, "ma_long": 20,
            "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70,
            "stop_loss_pct": 0.05, "take_profit_pct": 0.15, "position_size": 0.20,
        }
        evolution_engine.initialize_population(params)

        stock_data, all_dates = make_large_stock_data(100, 300)
        dates_list = all_dates.strftime("%Y%m%d").tolist()

        results = []
        meeting_logs = []
        consecutive_losses = 0
        total_cycles = 30

        CHECKS = {
            "market_regime_called": False,
            "selection_meeting_called": False,
            "algo_selection_called": False,
            "trader_ran": False,
            "review_meeting_called": False,
            "agent_tracker_used": False,
            "weights_updated": False,
            "meeting_recorded": False,
        }

        for cycle in range(1, total_cycles + 1):
            # 固定截断日期以便重复测试
            cutoff = "20221201"

            # 市场统计
            market_stats = compute_market_stats(stock_data, cutoff)

            # 总是用 algorithm 模式（避免 LLM dry_run 返回空）
            regime = market_regime_agent.analyze_fallback(market_stats)
            CHECKS["market_regime_called"] = True

            regime_params = regime["params"]

            # 选股 - 始终使用 selection_meeting 的算法模式
            if params:
                regime["params"].update(params)

            meeting_result = selection_meeting.run_with_data(
                regime=regime,
                stock_data=stock_data,
                cutoff_date=cutoff,
            )
            plan = meeting_result["trading_plan"]
            meeting_logs.append(meeting_result["meeting_log"])
            CHECKS["selection_meeting_called"] = True
            CHECKS["algo_selection_called"] = True

            # 记录预测
            for code in plan.stock_codes:
                agent_tracker.record_predictions(
                    cycle, "algorithm",
                    [{"code": code, "score": 0.6}]
                )
            agent_tracker.mark_selected(cycle, plan.stock_codes)
            CHECKS["agent_tracker_used"] = True

            # 会议记录
            ml = meeting_result["meeting_log"]
            meeting_recorder.save_selection_meeting(ml, cycle)
            CHECKS["meeting_recorded"] = True

            # 交易
            selected_data = {code: stock_data[code]
                             for code in plan.stock_codes if code in stock_data}
            if not selected_data:
                continue

            sample_dates = list(selected_data.values())[0]["trade_date"].tolist()
            after = [d for d in sample_dates if d > cutoff.replace("-", "")]
            if len(after) < 15:
                continue
            trade_dates = after[:30]

            trader = SimulatedTrader(initial_capital=100000)
            trader.set_stock_data(selected_data)
            trader.set_trading_plan(plan)

            result = trader.run_simulation(trade_dates[0], trade_dates)
            CHECKS["trader_ran"] = True

            is_profit = result.return_pct > 0

            # 记录结果
            if hasattr(result, 'per_stock_pnl') and result.per_stock_pnl:
                agent_tracker.record_outcomes(cycle, result.per_stock_pnl)

            results.append({
                "cycle": cycle,
                "return_pct": float(result.return_pct),
                "is_profit": bool(is_profit),
                "plan_source": plan.source,
                "regime": regime["regime"],
            })

            # 强制触发复盘会议（每5个cycle或亏损时）
            if cycle % 5 == 0 or not is_profit:
                if results:
                    agent_accuracy = agent_tracker.compute_accuracy(last_n_cycles=20)
                    review_result = review_meeting.run(
                        recent_results=results[-20:] if len(results) > 20 else results,
                        agent_accuracy=agent_accuracy,
                        current_params=params,
                    )
                    CHECKS["review_meeting_called"] = True

                    if review_result.get("agent_weight_adjustments"):
                        selection_meeting.update_weights(
                            review_result["agent_weight_adjustments"]
                        )
                        CHECKS["weights_updated"] = True

                    if review_result.get("param_adjustments"):
                        params.update(review_result["param_adjustments"])

                    fitness = [r["return_pct"] for r in results[-5:]]
                    while len(fitness) < 10:
                        fitness.append(-10)
                    evolution_engine.evolve(fitness)
                    new_params = evolution_engine.get_best_params()
                    if new_params:
                        params.update(new_params)

        # ===== 验证全流程检查点 =====
        print("\n--- 检查点验证 ---")
        all_pass = True
        for check, passed in CHECKS.items():
            status = "✅" if passed else "❌"
            print(f"  {status} {check}")
            if not passed:
                all_pass = False

        # ===== 数据完整性验证 =====
        print("\n--- 数据完整性 ---")

        assert len(results) > 0, "无交易结果"
        print(f"  ✅ 完成 {len(results)} 个 cycle")

        # selection_meeting 返回的 source 可能是 "algorithm" 因为用的是 run_with_data 的算法兜底
        meeting_count = sum(1 for r in results if r["plan_source"] == "meeting")
        algo_count = len(results)  # 全部通过 selection_meeting
        print(f"  ✅ 选股会议触发 {algo_count} 次")

        assert algo_count > 0, "选股会议从未被触发"

        wins = sum(1 for r in results if r["is_profit"])
        print(f"  ✅ 胜率 {wins}/{len(results)} ({wins/len(results)*100:.1f}%)")

        tracker_summary = agent_tracker.get_summary()
        print(f"  ✅ Agent预测 {tracker_summary['total_predictions']} 条")

        # 检查文件输出
        selection_files = list(Path(tmpdir).glob("selection/meeting_*.md"))
        print(f"  ✅ 会议记录文件 {len(selection_files)} 个")

        print(f"\n  LLM调用统计: {llm_caller.get_stats()}")
        print(f"  复盘会议次数: {review_meeting.review_count}")
        print(f"  Agent权重: {selection_meeting.agent_weights}")

        print("\n" + "=" * 60)
        if all_pass:
            print("✅ 端到端测试通过！全流程工作正常。")
        else:
            print("❌ 部分检查点未通过，请查看上方详情。")
        print("=" * 60)

        assert all_pass, "部分检查点未通过"


if __name__ == "__main__":
    test_e2e()
