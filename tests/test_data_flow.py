"""
数据流集成测试

验证跨模块的数据传递：
1. Selector → Meeting → TradingPlan → Trader → Result
2. Result → AgentTracker → ReviewMeeting → 权重调整 → 下一次Meeting
3. 各环节输出格式匹配下游输入格式

运行方式：
  python tests/test_data_flow.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd


def make_stock_data(n=50, days=200):
    dates = pd.date_range("2023-01-01", periods=days, freq="B")
    stock_data = {}
    for i in range(n):
        code = f"sh.{600000+i}"
        close = 10 + np.cumsum(np.random.randn(len(dates)) * 0.5)
        close = np.maximum(close, 1)
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "trade_date": dates.strftime("%Y%m%d"),
            "open": close * 0.998, "high": close * 1.02,
            "low": close * 0.98, "close": close,
            "volume": np.random.randint(100000, 10000000, len(dates)).astype(float),
            "pct_chg": pd.Series(close).pct_change().fillna(0) * 100,
        })
        stock_data[code] = df
    return stock_data


def test_full_pipeline():
    """
    完整数据流：
    DataLoader → MarketStats → MarketRegime → SelectionMeeting → TradingPlan → Trader → Result
    → AgentTracker → ReviewMeeting → 权重调整
    """
    from invest.core import LLMCaller
    from invest.core import compute_market_stats
    from invest.meetings import SelectionMeeting
    from invest.core import AgentTracker
    from invest.meetings import ReviewMeeting
    from invest.agents import MarketRegimeAgent
    from invest.optimization import AdaptiveSelector
    from invest.trading import SimulatedTrader

    np.random.seed(42)

    # 初始化 - 使用算法模式(传None)以避免LLM dry_run返回空结果
    caller = LLMCaller(dry_run=True)
    selector = AdaptiveSelector()
    regime_agent = MarketRegimeAgent(llm_caller=caller)
    meeting = SelectionMeeting(llm_caller=None, max_hunters=2)  # 使用算法模式
    tracker = AgentTracker()
    review = ReviewMeeting(llm_caller=None)

    stock_data = make_stock_data(50, 200)
    cutoff = "20230601"  # 6月1日，在数据范围内

    all_results = []

    # 模拟3个cycle
    for cycle in range(1, 4):
        print(f"\n--- Cycle {cycle} ---")

        # 1. 市场统计
        stats = compute_market_stats(stock_data, cutoff)
        assert stats["valid_stocks"] > 0, "市场统计无有效数据"

        # 2. 市场状态判断
        regime = regime_agent.analyze_fallback(stats)
        assert regime["regime"] in ("bull", "bear", "oscillation")
        assert "params" in regime

        # 3. 选股会议
        meeting_result = meeting.run_with_data(
            regime=regime,
            stock_data=stock_data,
            cutoff_date=cutoff,
        )
        plan = meeting_result["trading_plan"]
        log = meeting_result["meeting_log"]

        assert plan is not None, "TradingPlan 为空"

        # 如果没有选中股票，跳过这个cycle
        if len(plan.positions) == 0:
            print(f"  跳过：无持仓")
            continue

        # 4. 记录预测
        source = log.get("source", "algorithm")
        if source == "llm":
            # LLM 模式
            tp = log.get("trend_picks", {})
            cp = log.get("contrarian_picks", {})
            tracker.record_predictions(cycle, "trend_hunter", tp.get("picks", []))
            tracker.record_predictions(cycle, "contrarian", cp.get("picks", []))
        else:
            # 算法模式：从 selected 列表记录
            for code in plan.stock_codes:
                tracker.record_predictions(
                    cycle, "algorithm",
                    [{"code": code, "score": 0.6, "stop_loss_pct": 0.05, "take_profit_pct": 0.15}]
                )
        tracker.mark_selected(cycle, plan.stock_codes)

        # 5. 验证 TradingPlan → Trader 接口
        selected_data = {code: stock_data[code]
                         for code in plan.stock_codes
                         if code in stock_data}

        if not selected_data:
            print(f"  跳过：选中股票不在数据中")
            continue

        dates = list(selected_data.values())[0]["trade_date"].tolist()
        cutoff_idx = next(
            (i for i, d in enumerate(dates) if d > cutoff.replace("-", "")),
            len(dates) - 30
        )
        trade_dates = dates[cutoff_idx:cutoff_idx+30]

        if len(trade_dates) < 10:
            print(f"  跳过：交易日不足")
            continue

        trader = SimulatedTrader(initial_capital=100000)
        trader.set_stock_data(selected_data)
        trader.set_trading_plan(plan)

        result = trader.run_simulation(trade_dates[0], trade_dates)

        # 6. 验证 Result 格式
        assert hasattr(result, 'return_pct'), "Result 缺少 return_pct"
        assert hasattr(result, 'per_stock_pnl'), "Result 缺少 per_stock_pnl"
        assert hasattr(result, 'trade_history'), "Result 缺少 trade_history"
        assert isinstance(result.per_stock_pnl, dict), "per_stock_pnl 类型错误"

        print(f"  收益: {result.return_pct:+.2f}%, 交易{result.total_trades}次")

        # 7. 记录结果
        tracker.record_outcomes(cycle, result.per_stock_pnl)

        all_results.append({
            "cycle": cycle,
            "is_profit": result.return_pct > 0,
            "return_pct": result.return_pct,
            "plan_source": plan.source,
            "regime": regime["regime"],
        })

    # 8. 复盘会议
    print("\n--- 复盘会议 ---")
    accuracy = tracker.compute_accuracy()

    review_result = review.run(
        recent_results=all_results,
        agent_accuracy=accuracy,
        current_params={"stop_loss_pct": 0.05, "take_profit_pct": 0.15},
    )

    assert "param_adjustments" in review_result
    assert "agent_weight_adjustments" in review_result

    # 9. 权重调整
    if review_result["agent_weight_adjustments"]:
        meeting.update_weights(review_result["agent_weight_adjustments"])

    print(f"\n权重调整后: {meeting.agent_weights}")

    # 10. 验证追踪器总结
    summary = tracker.get_summary()
    assert summary["total_predictions"] > 0
    print(f"总预测: {summary['total_predictions']}")
    print(f"准确率: {summary['accuracy_by_agent']}")

    print("\n✅ 完整数据流测试通过")


if __name__ == "__main__":
    test_full_pipeline()
