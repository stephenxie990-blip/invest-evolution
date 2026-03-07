"""
真实 LLM 端到端测试

使用真实 LLM + 模拟数据（等离线数据下载完成后可换真实数据）
验证系统在实际运行中的表现

运行：
    python tests/test_llm_e2e.py
"""

import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def generate_test_stock_data(n=50, days=300, seed=42):
    """生成测试股票数据"""
    np.random.seed(seed)
    dates = pd.date_range("2023-01-01", periods=days, freq="B")

    stock_data = {}
    for i in range(n):
        code = f"sh.{600000+i}"
        trend = np.random.choice([-0.001, 0.0, 0.001])
        close = 10 + np.cumsum(np.random.randn(len(dates)) * 0.3 + trend)
        close = np.maximum(close, 1)

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

    return stock_data


def test_real_llm():
    """使用真实 LLM 测试端到端流程"""
    from invest.core import LLMCaller
    from invest.core import compute_market_stats
    from invest.meetings import SelectionMeeting
    from invest.core import AgentTracker
    from invest.meetings import ReviewMeeting
    from invest.meetings import MeetingRecorder
    from invest.agents import MarketRegimeAgent
    from invest.trading import SimulatedTrader

    print("=" * 60)
    print("真实 LLM 端到端测试")
    print("=" * 60)

    # 使用真实 LLM（非 dry_run）
    llm_caller = LLMCaller(dry_run=False)
    print(f"\nLLM 配置: {llm_caller.model}")

    # 初始化组件
    market_regime_agent = MarketRegimeAgent(llm_caller=llm_caller)
    selection_meeting = SelectionMeeting(llm_caller=llm_caller, max_hunters=2)
    agent_tracker = AgentTracker()
    review_meeting = ReviewMeeting(llm_caller=llm_caller)

    with tempfile.TemporaryDirectory() as tmpdir:
        meeting_recorder = MeetingRecorder(base_dir=tmpdir)

        # 生成数据
        cutoff = "20231201"
        stock_data = generate_test_stock_data(n=30, days=200)

        print(f"\n生成测试数据: {len(stock_data)} 只股票")

        # 1. 市场统计
        market_stats = compute_market_stats(stock_data, cutoff)
        print(f"市场统计: {market_stats['valid_stocks']} 只有效股票")

        # 2. 市场状态判断（使用真实 LLM）
        print("\n--- 市场状态分析 (LLM) ---")
        regime = market_regime_agent.analyze(market_stats)
        print(f"市场状态: {regime['regime']}, 置信度: {regime.get('confidence', 0):.0%}")
        print(f"来源: {regime.get('source', 'unknown')}")

        # 3. 选股会议（使用真实 LLM）
        print("\n--- 选股会议 (LLM) ---")
        meeting_result = selection_meeting.run_with_data(
            regime=regime,
            stock_data=stock_data,
            cutoff_date=cutoff,
        )

        plan = meeting_result["trading_plan"]
        meeting_log = meeting_result["meeting_log"]

        print(f"选中股票: {plan.stock_codes}")
        print(f"来源: {plan.source}")

        # 4. 记录预测
        if plan.stock_codes:
            for code in plan.stock_codes:
                agent_tracker.record_predictions(
                    1, "llm_selection",
                    [{"code": code, "score": 0.7}]
                )
            agent_tracker.mark_selected(1, plan.stock_codes)

        # 5. 模拟交易
        return_pct = 0.0
        if plan.stock_codes:
            selected_data = {code: stock_data[code]
                          for code in plan.stock_codes
                          if code in stock_data}

            sample_dates = list(selected_data.values())[0]["trade_date"].tolist()
            after = [d for d in sample_dates if d > cutoff.replace("-", "")]

            if len(after) >= 10:
                trade_dates = after[:20]

                trader = SimulatedTrader(initial_capital=100000)
                trader.set_stock_data(selected_data)
                trader.set_trading_plan(plan)

                result = trader.run_simulation(trade_dates[0], trade_dates)

                print(f"\n--- 交易结果 ---")
                print(f"收益率: {result.return_pct:+.2f}%")
                print(f"交易次数: {result.total_trades}")

                return_pct = result.return_pct

                # 记录结果
                if result.per_stock_pnl:
                    agent_tracker.record_outcomes(1, result.per_stock_pnl)

        # 6. 复盘会议
        print("\n--- 复盘会议 (LLM) ---")
        accuracy = agent_tracker.compute_accuracy()

        review_result = review_meeting.run(
            recent_results=[{"return_pct": return_pct}],
            agent_accuracy=accuracy,
            current_params={"stop_loss_pct": 0.05, "take_profit_pct": 0.15},
        )

        print(f"策略建议: {len(review_result.get('strategy_suggestions', []))} 条")
        print(f"参数调整: {review_result.get('param_adjustments', {})}")
        print(f"权重调整: {review_result.get('agent_weight_adjustments', {})}")

        # 7. LLM 统计
        stats = llm_caller.get_stats()
        print(f"\n--- LLM 统计 ---")
        print(f"调用次数: {stats['call_count']}")
        print(f"总 Token: {stats['total_tokens']}")
        print(f"总耗时: {stats['total_time_sec']:.2f}秒")

        # 检查是否有解析错误
        if stats.get('errors', 0) > 0:
            print(f"⚠️ 有 {stats['errors']} 次调用出错")

        print("\n" + "=" * 60)
        print("✅ 真实 LLM 端到端测试完成")
        print("=" * 60)




if __name__ == "__main__":
    try:
        test_real_llm()
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
