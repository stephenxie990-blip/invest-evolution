"""
A3 对比实验：Agent选股 vs 纯算法选股

目的：验证 Agent 参与是否比纯算法更好

两组实验：
  组A：纯算法（selector + make_simple_plan，不调 LLM）
  组B：Agent会议（SelectionMeeting，调 LLM 或算法兜底）

使用相同的数据和相同的随机种子，确保可比性

运行方式：
  # 模拟数据（开发测试）
  python tests/test_comparison.py --mode mock --cycles 100

  # 真实数据（需要先下载离线数据）
  python tests/test_comparison.py --mode real --cycles 200

  # 真实 LLM（消耗 API，建议 cycles 不要太多）
  python tests/test_comparison.py --mode mock --cycles 50 --use-llm
"""

import sys
import os
import json
import argparse
import logging
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict

import numpy as np
import pandas as pd

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ===== 数据源 =====

def make_mock_data(n_stocks=80, n_days=400, seed=42):
    """
    生成模拟数据

    不是纯随机游走——加入了趋势和均值回归特征
    让 Agent 有机会发现模式
    """
    np.random.seed(seed)
    dates = pd.date_range("2022-01-01", periods=n_days, freq="B")
    stock_data = {}

    for i in range(n_stocks):
        code = f"sh.{600000 + i}"

        # 每只股票有不同的特征
        stock_type = i % 4
        if stock_type == 0:
            # 趋势上涨型
            trend = 0.001
            vol = 0.02
        elif stock_type == 1:
            # 趋势下跌型
            trend = -0.001
            vol = 0.025
        elif stock_type == 2:
            # 高波动震荡型
            trend = 0.0
            vol = 0.035
        else:
            # 低波动稳健型
            trend = 0.0003
            vol = 0.012

        # 生成价格序列（带趋势 + 随机）
        returns = np.random.normal(trend, vol, len(dates))
        # 加入一些动量效应（自相关）
        for j in range(3, len(returns)):
            returns[j] += returns[j - 1] * 0.05  # 微弱的动量

        prices = 10 * np.exp(np.cumsum(returns))
        prices = np.maximum(prices, 0.5)

        volume = np.random.randint(500000, 20000000, len(dates)).astype(float)
        # 下跌时放量
        for j in range(1, len(returns)):
            if returns[j] < -0.02:
                volume[j] *= 1.5

        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "trade_date": dates.strftime("%Y%m%d"),
            "open": prices * (1 + np.random.randn(len(dates)) * 0.003),
            "high": prices * (1 + np.abs(np.random.randn(len(dates))) * 0.012),
            "low": prices * (1 - np.abs(np.random.randn(len(dates))) * 0.012),
            "close": prices,
            "volume": volume,
            "pct_chg": np.concatenate([[0], np.diff(prices) / prices[:-1] * 100]),
        })
        stock_data[code] = df

    return stock_data


def load_real_data():
    """加载离线真实数据"""
    try:
        from data_datasets import TrainingDatasetBuilder
        loader = TrainingDatasetBuilder()
        stock_data = loader.get_stocks(cutoff_date="20231231", stock_count=50)
        if stock_data and len(stock_data) > 20:
            return stock_data
        else:
            print("⚠️ 离线数据不足，回退到模拟数据")
            return None
    except Exception as e:
        print(f"⚠️ 加载离线数据失败: {e}，回退到模拟数据")
        return None


# ===== 单轮交易执行 =====

def run_single_cycle(
    stock_data: dict,
    cutoff_date: str,
    mode: str,
    selector,
    regime_agent,
    selection_meeting,
    params: dict,
    use_llm: bool = False,
):
    """
    执行一个交易周期

    Args:
        mode: "algo" = 纯算法, "agent" = Agent会议

    Returns:
        dict: 本轮结果
    """
    from core import compute_market_stats
    from core import make_simple_plan
    from trading import SimulatedTrader

    # 1. 市场状态
    market_stats = compute_market_stats(stock_data, cutoff_date)

    if use_llm and mode == "agent":
        regime = regime_agent.analyze(market_stats)
    else:
        regime = regime_agent.analyze_fallback(market_stats)

    regime_params = regime["params"]

    # 2. 选股
    if mode == "agent":
        merged_regime = dict(regime)
        merged_params = dict(regime.get("params", {}))
        merged_params.update(params)
        merged_regime["params"] = merged_params
        meeting_result = selection_meeting.run_with_data(
            regime=merged_regime,
            stock_data=stock_data,
            cutoff_date=cutoff_date,
        )
        plan = meeting_result["trading_plan"]
    else:
        selected = selector.select(
            stock_data, cutoff_date,
            top_n=regime_params["top_n"],
        )
        if not selected:
            selected = list(stock_data.keys())[:3]

        plan = make_simple_plan(
            selected_stocks=selected,
            cutoff_date=cutoff_date,
            stop_loss_pct=params.get("stop_loss_pct", regime_params.get("stop_loss_pct", 0.05)),
            take_profit_pct=params.get("take_profit_pct", regime_params.get("take_profit_pct", 0.15)),
            position_size=params.get("position_size", regime_params.get("position_size", 0.20)),
            max_positions=regime_params.get("max_positions", 3),
        )

    # 3. 交易
    selected_data = {
        code: stock_data[code]
        for code in plan.stock_codes
        if code in stock_data
    }
    if not selected_data:
        return None

    # 找交易日
    sample_df = list(selected_data.values())[0]
    all_dates = sample_df["trade_date"].tolist()
    cutoff_norm = cutoff_date.replace("-", "")
    dates_after = [d for d in all_dates if d > cutoff_norm]

    if len(dates_after) < 15:
        return None

    trade_dates = dates_after[:30]

    trader = SimulatedTrader(initial_capital=100000)
    trader.set_stock_data(selected_data)
    trader.set_trading_plan(plan)

    result = trader.run_simulation(trade_dates[0], trade_dates)

    return {
        "return_pct": float(result.return_pct),
        "is_profit": result.return_pct > 0,
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "regime": regime["regime"],
        "n_stocks": len(plan.stock_codes),
        "plan_source": plan.source,
    }


# ===== 实验运行器 =====

@dataclass
class ExperimentResult:
    """实验结果"""
    name: str
    cycles_run: int = 0
    cycles_skipped: int = 0
    returns: List[float] = field(default_factory=list)
    regimes: List[str] = field(default_factory=list)

    @property
    def win_count(self):
        return sum(1 for r in self.returns if r > 0)

    @property
    def loss_count(self):
        return sum(1 for r in self.returns if r <= 0)

    @property
    def win_rate(self):
        if not self.returns:
            return 0
        return self.win_count / len(self.returns)

    @property
    def avg_return(self):
        if not self.returns:
            return 0
        return sum(self.returns) / len(self.returns)

    @property
    def median_return(self):
        if not self.returns:
            return 0
        return float(np.median(self.returns))

    @property
    def max_drawdown(self):
        if not self.returns:
            return 0
        return float(min(self.returns))

    @property
    def best_return(self):
        if not self.returns:
            return 0
        return float(max(self.returns))

    @property
    def sharpe(self):
        if not self.returns or len(self.returns) < 5:
            return 0
        arr = np.array(self.returns)
        if arr.std() == 0:
            return 0
        return float(arr.mean() / arr.std())

    def regime_breakdown(self) -> dict:
        breakdown = {}
        for ret, reg in zip(self.returns, self.regimes):
            if reg not in breakdown:
                breakdown[reg] = {"returns": [], "wins": 0, "total": 0}
            breakdown[reg]["returns"].append(ret)
            breakdown[reg]["total"] += 1
            if ret > 0:
                breakdown[reg]["wins"] += 1
        result = {}
        for reg, data in breakdown.items():
            result[reg] = {
                "total": data["total"],
                "win_rate": data["wins"] / data["total"] if data["total"] > 0 else 0,
                "avg_return": sum(data["returns"]) / len(data["returns"]),
            }
        return result


def run_experiment(
    stock_data: dict,
    mode: str,
    total_cycles: int,
    seed: int,
    use_llm: bool = False,
) -> ExperimentResult:
    """
    运行一组实验

    Args:
        mode: "algo" 或 "agent"
        seed: 随机种子（两组用相同种子确保选择相同的截断日期）
    """
    from core import LLMCaller
    from meetings import SelectionMeeting
    from agents import MarketRegimeAgent
    from optimization import AdaptiveSelector

    np.random.seed(seed)

    # 初始化
    if use_llm:
        llm_caller = LLMCaller()
    else:
        llm_caller = LLMCaller(dry_run=True)

    selector = AdaptiveSelector()

    if mode == "agent":
        regime_agent = MarketRegimeAgent(llm_caller=llm_caller if use_llm else None)
        selection_meeting = SelectionMeeting(
            llm_caller if use_llm else None, max_hunters=2
        )
    else:
        regime_agent = MarketRegimeAgent(llm_caller=None)
        selection_meeting = None

    params = {
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.15,
        "position_size": 0.20,
    }

    # 获取可用的截断日期范围
    sample_df = list(stock_data.values())[0]
    all_dates = sample_df["trade_date"].tolist()
    # 前 150 天用于历史，后 30 天用于交易，中间可选
    min_idx = 150
    max_idx = len(all_dates) - 35

    if max_idx <= min_idx:
        print(f"⚠️ 数据量不足")
        return ExperimentResult(name=mode)

    result = ExperimentResult(name=mode)

    for cycle in range(1, total_cycles + 1):
        # 随机截断日期
        idx = np.random.randint(min_idx, max_idx)
        cutoff = all_dates[idx]

        cycle_result = run_single_cycle(
            stock_data=stock_data,
            cutoff_date=cutoff,
            mode=mode,
            selector=selector,
            regime_agent=regime_agent,
            selection_meeting=selection_meeting,
            params=params,
            use_llm=use_llm,
        )

        if cycle_result is None:
            result.cycles_skipped += 1
            continue

        result.cycles_run += 1
        result.returns.append(cycle_result["return_pct"])
        result.regimes.append(cycle_result["regime"])

        if cycle % 50 == 0 or cycle == total_cycles:
            print(
                f"  [{mode}] {cycle}/{total_cycles}: "
                f"胜率{result.win_rate:.0%}, "
                f"均收益{result.avg_return:+.2f}%"
            )

    if use_llm and mode == "agent":
        stats = llm_caller.get_stats()
        print(f"  [{mode}] LLM调用: {stats['call_count']}次, "
              f"耗时{stats['total_time_sec']}秒")

    return result


# ===== 结果对比 =====

def compare_results(algo: ExperimentResult, agent: ExperimentResult):
    """对比两组实验结果"""
    print("\n" + "=" * 70)
    print("对比实验结果")
    print("=" * 70)

    headers = ["指标", "纯算法", "Agent会议", "差异"]
    rows = [
        ["有效轮数", algo.cycles_run, agent.cycles_run, ""],
        ["胜率",
         f"{algo.win_rate:.1%}",
         f"{agent.win_rate:.1%}",
         f"{(agent.win_rate - algo.win_rate):+.1%}"],
        ["平均收益",
         f"{algo.avg_return:+.2f}%",
         f"{agent.avg_return:+.2f}%",
         f"{(agent.avg_return - algo.avg_return):+.2f}%"],
        ["中位收益",
         f"{algo.median_return:+.2f}%",
         f"{agent.median_return:+.2f}%",
         f"{(agent.median_return - algo.median_return):+.2f}%"],
        ["最大单轮亏损",
         f"{algo.max_drawdown:+.2f}%",
         f"{agent.max_drawdown:+.2f}%",
         ""],
        ["最佳单轮收益",
         f"{algo.best_return:+.2f}%",
         f"{agent.best_return:+.2f}%",
         ""],
        ["夏普比率",
         f"{algo.sharpe:.3f}",
         f"{agent.sharpe:.3f}",
         f"{(agent.sharpe - algo.sharpe):+.3f}"],
    ]

    # 打印表格
    col_widths = [14, 12, 12, 12]
    header_line = " | ".join(h.center(w) for h, w in zip(headers, col_widths))
    separator = "-+-".join("-" * w for w in col_widths)
    print(f"\n{header_line}")
    print(separator)

    for row in rows:
        line = " | ".join(str(v).center(w) for v, w in zip(row, col_widths))
        print(line)

    # 按市场状态对比
    print(f"\n{'按市场状态对比':=^70}")

    algo_bd = algo.regime_breakdown()
    agent_bd = agent.regime_breakdown()
    all_regimes = sorted(set(list(algo_bd.keys()) + list(agent_bd.keys())))

    print(f"\n{'状态':^12} | {'算法胜率':^10} | {'Agent胜率':^10} | "
          f"{'算法均收益':^12} | {'Agent均收益':^12}")
    print("-" * 62)

    for reg in all_regimes:
        a = algo_bd.get(reg, {"total": 0, "win_rate": 0, "avg_return": 0})
        b = agent_bd.get(reg, {"total": 0, "win_rate": 0, "avg_return": 0})
        print(
            f"{reg:^12} | "
            f"{a['win_rate']:^10.0%} | "
            f"{b['win_rate']:^10.0%} | "
            f"{a['avg_return']:^+12.2f}% | "
            f"{b['avg_return']:^+12.2f}%"
        )

    # 结论
    print(f"\n{'结论':=^70}")

    if agent.avg_return > algo.avg_return + 0.5:
        print("✅ Agent 显著优于算法（平均收益高 >0.5%）")
    elif agent.avg_return > algo.avg_return:
        print("🟡 Agent 略优于算法")
    elif agent.avg_return > algo.avg_return - 0.5:
        print("🟡 Agent 与算法表现接近（差距 <0.5%）")
    else:
        print("❌ Agent 不如算法（平均收益低 >0.5%）")

    if agent.sharpe > algo.sharpe + 0.05:
        print("✅ Agent 风险调整后收益更好（夏普更高）")
    elif agent.sharpe < algo.sharpe - 0.05:
        print("❌ Agent 风险调整后收益更差")
    else:
        print("🟡 两者风险调整后收益相当")

    print("=" * 70)


# ===== 主函数 =====

def main():
    parser = argparse.ArgumentParser(description="A3 对比实验")
    parser.add_argument("--mode", choices=["mock", "real"], default="mock",
                        help="数据源: mock=模拟数据, real=离线真实数据")
    parser.add_argument("--cycles", type=int, default=100,
                        help="每组实验的轮数")
    parser.add_argument("--seed", type=int, default=42,
                        help="随机种子（两组相同）")
    parser.add_argument("--use-llm", action="store_true",
                        help="Agent组使用真实LLM（消耗API）")
    parser.add_argument("--export", type=str, default=None,
                        help="导出结果到指定目录")
    args = parser.parse_args()

    # 加载数据
    print(f"\n数据源: {args.mode}")
    if args.mode == "real":
        stock_data = load_real_data()
        if stock_data is None:
            print("回退到模拟数据")
            stock_data = make_mock_data()
    else:
        stock_data = make_mock_data()

    print(f"股票数量: {len(stock_data)}")
    print(f"实验轮数: {args.cycles}")
    print(f"随机种子: {args.seed}")
    print(f"使用LLM: {args.use_llm}")

    # 运行算法组
    print(f"\n{'=' * 50}")
    print(f"组A: 纯算法选股")
    print(f"{'=' * 50}")
    start = time.time()
    algo_result = run_experiment(
        stock_data, mode="algo",
        total_cycles=args.cycles, seed=args.seed,
        use_llm=False,
    )
    algo_time = time.time() - start
    print(f"耗时: {algo_time:.1f}秒")

    # 运行Agent组（使用相同随机种子）
    print(f"\n{'=' * 50}")
    print(f"组B: Agent会议选股")
    print(f"{'=' * 50}")
    start = time.time()
    agent_result = run_experiment(
        stock_data, mode="agent",
        total_cycles=args.cycles, seed=args.seed,
        use_llm=args.use_llm,
    )
    agent_time = time.time() - start
    print(f"耗时: {agent_time:.1f}秒")

    # 对比
    compare_results(algo_result, agent_result)

    print(f"\n⏱️ 总耗时: 算法{algo_time:.0f}秒 + Agent{agent_time:.0f}秒 "
          f"= {algo_time + agent_time:.0f}秒")

    # 导出
    if args.export:
        export_dir = Path(args.export)
        export_dir.mkdir(parents=True, exist_ok=True)

        export_data = {
            "config": {
                "mode": args.mode,
                "cycles": args.cycles,
                "seed": args.seed,
                "use_llm": args.use_llm,
            },
            "algo": {
                "cycles_run": algo_result.cycles_run,
                "win_rate": algo_result.win_rate,
                "avg_return": algo_result.avg_return,
                "median_return": algo_result.median_return,
                "sharpe": algo_result.sharpe,
                "returns": algo_result.returns,
                "regime_breakdown": algo_result.regime_breakdown(),
                "time_sec": algo_time,
            },
            "agent": {
                "cycles_run": agent_result.cycles_run,
                "win_rate": agent_result.win_rate,
                "avg_return": agent_result.avg_return,
                "median_return": agent_result.median_return,
                "sharpe": agent_result.sharpe,
                "returns": agent_result.returns,
                "regime_breakdown": agent_result.regime_breakdown(),
                "time_sec": agent_time,
            },
        }

        path = export_dir / "comparison_result.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        print(f"\n📁 结果已导出: {path}")


if __name__ == "__main__":
    main()
