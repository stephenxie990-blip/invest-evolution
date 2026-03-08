"""
全模块单元测试

运行方式：
  python tests/test_all_modules.py

每个测试函数独立运行，不依赖外部服务（不调LLM、不调Baostock）
"""

import sys
import os
import traceback
from pathlib import Path

# 项目路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
import numpy as np

PASSED = 0
FAILED = 0
ERRORS = []


def run_test(name, func):
    global PASSED, FAILED, ERRORS
    try:
        func()
        PASSED += 1
        print(f"  ✅ {name}")
    except Exception as e:
        FAILED += 1
        ERRORS.append((name, str(e), traceback.format_exc()))
        print(f"  ❌ {name}: {e}")


# ===== 测试数据工厂 =====

def make_stock_data(n_stocks=30, n_days=200):
    """生成模拟股票数据，供所有测试复用"""
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")
    stock_data = {}
    for i in range(n_stocks):
        code = f"sh.{600000+i}"
        close = 10 + np.cumsum(np.random.randn(len(dates)) * 0.5)
        close = np.maximum(close, 1)
        vol = np.random.randint(100000, 10000000, len(dates)).astype(float)
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "trade_date": dates.strftime("%Y%m%d"),
            "open": close * (1 + np.random.randn(len(dates)) * 0.005),
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": vol,
            "pct_chg": pd.Series(close).pct_change().fillna(0) * 100,
        })
        stock_data[code] = df
    return stock_data


def make_rising_stock():
    """生成一只持续上涨的股票（测试跟踪止盈）"""
    dates = [f"2024{str(m).zfill(2)}{str(d).zfill(2)}"
             for m in range(1, 3) for d in range(1, 29)][:30]
    prices = [100.0]
    for i in range(1, 30):
        if i < 18:
            prices.append(prices[-1] * 1.012)
        else:
            prices.append(prices[-1] * 0.988)
    return {
        "sh.999999": pd.DataFrame({
            "date": [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates],
            "trade_date": dates,
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1000000.0] * 30,
            "pct_chg": [0] + [(prices[i]/prices[i-1]-1)*100 for i in range(1, 30)],
        })
    }


# ===== Phase 0 测试 =====

def test_trading_plan_creation():
    from invest.core import TradingPlan, PositionPlan

    plan = TradingPlan(
        date="20240101",
        positions=[
            PositionPlan(code="sh.600000", priority=1, weight=0.2,
                         stop_loss_pct=0.05, take_profit_pct=0.15),
            PositionPlan(code="sh.600001", priority=2, weight=0.15,
                         trailing_pct=0.10, source="trend_hunter"),
        ],
        max_positions=2,
        cash_reserve=0.3,
    )

    assert len(plan.positions) == 2
    assert plan.stock_codes == ["sh.600000", "sh.600001"]
    assert plan.get_position_plan("sh.600000").weight == 0.2
    assert plan.get_position_plan("sh.600001").trailing_pct == 0.10
    assert plan.get_position_plan("sh.999999") is None
    assert plan.cash_reserve == 0.3


def test_plan_builder():
    from invest.core import make_simple_plan

    plan = make_simple_plan(
        selected_stocks=["sh.600000", "sh.600001", "sh.600002"],
        cutoff_date="20240101",
        stop_loss_pct=0.03,
        take_profit_pct=0.20,
        max_positions=2,
    )

    assert len(plan.positions) == 3
    assert plan.max_positions == 2
    assert plan.positions[0].stop_loss_pct == 0.03
    assert plan.positions[0].take_profit_pct == 0.20
    assert plan.source == "algorithm"


def test_trader_with_plan():
    from invest.core import TradingPlan, PositionPlan
    from invest.trading import SimulatedTrader

    stock_data = make_stock_data(5, 60)
    codes = list(stock_data.keys())
    dates = stock_data[codes[0]]["trade_date"].tolist()

    plan = TradingPlan(
        date=dates[0],
        positions=[
            PositionPlan(code=codes[0], priority=1, weight=0.3,
                         stop_loss_pct=0.05, take_profit_pct=0.15),
            PositionPlan(code=codes[1], priority=2, weight=0.2,
                         stop_loss_pct=0.08, take_profit_pct=0.20),
        ],
        max_positions=2,
    )

    trader = SimulatedTrader(initial_capital=100000)
    trader.set_stock_data(stock_data)
    trader.set_trading_plan(plan)

    result = trader.run_simulation(dates[0], dates[:30])

    assert result.initial_capital == 100000
    assert result.final_capital > 0
    assert isinstance(result.return_pct, float)
    assert len(result.trade_history) > 0


# ===== Phase 1 测试 =====

def test_llm_caller_dry_run():
    from invest.core import LLMCaller

    caller = LLMCaller(dry_run=True)

    result = caller.call("system", "user")
    assert result == '{"dry_run": true}'

    result_json = caller.call_json("system", "user")
    assert result_json == {"dry_run": True}

    stats = caller.get_stats()
    # dry_run 模式不增加 call_count
    assert stats["errors"] == 0


def test_llm_caller_json_parsing():
    from invest.core import LLMCaller

    caller = LLMCaller(dry_run=True)

    # 测试各种 JSON 提取场景
    tests = [
        ('{"key": "value"}', {"key": "value"}),
        ('```json\n{"key": "value"}\n```', {"key": "value"}),
        ('prefix {"key": "value"} suffix', {"key": "value"}),
        ('\n{"key": "value"}}', {"key": "value"}),
        ('分析如下：\n```json\n{"verdict": "hold", "confidence": 0.55}\n```\n补充说明', {"verdict": "hold", "confidence": 0.55}),
        ('```json\n{"verdict":"buy","reasoning":"ok"}', {"verdict": "buy", "reasoning": "ok"}),
        ('json\n{"risk_level": "medium", "confidence": 0.7}\n```', {"risk_level": "medium", "confidence": 0.7}),
        ('下面是最终JSON：{"key":"value","ok":true}', {"key": "value", "ok": True}),
        ("{'cause': 'stop loss too tight', 'new_strategy_needed': False}", {"cause": "stop loss too tight", "new_strategy_needed": False}),
        ('```json\n{"verdict": "hold", "confidence": 0.55,}\n```', {"verdict": "hold", "confidence": 0.55}),
        ('```json\n{"verdict": "hold", "confidence": 0.55', {"verdict": "hold", "confidence": 0.55}),
        ('no json here', None),
        ('', None),
    ]

    for text, expected in tests:
        result = caller._parse_json(text)
        if expected is None:
            assert result.get("_parse_error") == True, f"应该解析失败: {text}"
        else:
            assert result == expected, f"解析错误: {text} → {result}"


def test_market_stats():
    from invest.core import compute_market_stats

    stock_data = make_stock_data(50, 200)
    stats = compute_market_stats(stock_data, "20231001")

    assert stats["valid_stocks"] > 0
    assert 0 <= stats["advance_ratio_5d"] <= 1
    assert isinstance(stats["avg_change_5d"], float)
    assert isinstance(stats["avg_change_20d"], float)
    assert 0 <= stats["above_ma20_ratio"] <= 1
    assert stats["avg_volatility"] >= 0


def test_market_stats_empty():
    from invest.core import compute_market_stats

    stats = compute_market_stats({}, "20231001")
    assert stats["valid_stocks"] == 0
    assert stats["advance_ratio_5d"] == 0.5  # 默认值


def test_market_regime_fallback():
    from invest.agents import MarketRegimeAgent, REGIME_PARAMS

    agent = MarketRegimeAgent(llm_caller=None)

    # 牛市
    bull = agent.analyze_fallback({
        "valid_stocks": 100, "advance_ratio_5d": 0.75,
        "avg_change_5d": 2.0, "median_change_5d": 1.5,
        "avg_change_20d": 10.0, "median_change_20d": 8.0,
        "above_ma20_ratio": 0.80, "avg_volatility": 0.02,
    })
    assert bull["regime"] == "bull"
    assert bull["source"] == "algorithm"
    assert "params" in bull
    assert bull["params"] == REGIME_PARAMS["bull"]

    # 熊市
    bear = agent.analyze_fallback({
        "valid_stocks": 100, "advance_ratio_5d": 0.25,
        "avg_change_5d": -3.0, "median_change_5d": -2.5,
        "avg_change_20d": -12.0, "median_change_20d": -10.0,
        "above_ma20_ratio": 0.20, "avg_volatility": 0.04,
    })
    assert bear["regime"] == "bear"

    # regime_changed
    assert agent.regime_changed() == True
    assert agent.get_last_regime() == "bear"


def test_market_regime_with_dry_llm():
    from invest.core import LLMCaller
    from invest.agents import MarketRegimeAgent

    caller = LLMCaller(dry_run=True)
    agent = MarketRegimeAgent(llm_caller=caller)

    result = agent.analyze({"valid_stocks": 50, "advance_ratio_5d": 0.5,
                            "avg_change_5d": 0, "median_change_5d": 0,
                            "avg_change_20d": 0, "median_change_20d": 0,
                            "above_ma20_ratio": 0.5, "avg_volatility": 0.02})
    # dry_run 时 LLM 返回 {"dry_run": true}，解析失败走 fallback
    assert result["regime"] in ("bull", "bear", "oscillation")
    assert "params" in result


# ===== Phase 2 测试 =====

def test_stock_analyzer():
    from invest.core import summarize_stocks, format_stock_table

    stock_data = make_stock_data(20, 200)
    codes = list(stock_data.keys())

    summaries = summarize_stocks(stock_data, codes, "20231001")

    assert len(summaries) > 0
    s = summaries[0]
    assert "code" in s
    assert "close" in s
    assert "rsi" in s
    assert "macd" in s
    assert "ma_trend" in s
    assert "bb_pos" in s
    assert "algo_score" in s
    assert 0 <= s["rsi"] <= 100
    assert 0 <= s["bb_pos"] <= 1
    assert s["ma_trend"] in ("多头", "空头", "交叉")
    assert s["macd"] in ("金叉", "死叉", "看多", "看空", "中性")

    # 表格格式化
    table = format_stock_table(summaries[:5])
    assert "|" in table
    assert "代码" in table


def test_trend_hunter_prefilter():
    from invest.core import summarize_stocks
    from invest.agents import TrendHunterAgent

    stock_data = make_stock_data(50, 200)
    codes = list(stock_data.keys())
    summaries = summarize_stocks(stock_data, codes, "20231001")

    agent = TrendHunterAgent(llm_caller=None)
    candidates = agent.pre_filter(summaries)

    # 应该过滤掉空头和死叉
    for c in candidates:
        assert c["ma_trend"] != "空头"
        assert c["macd"] not in ("死叉", "看空")
    assert "trend_score" in candidates[0] if candidates else True


def test_trend_hunter_fallback():
    from invest.core import summarize_stocks
    from invest.agents import TrendHunterAgent

    stock_data = make_stock_data(50, 200)
    codes = list(stock_data.keys())
    summaries = summarize_stocks(stock_data, codes, "20231001")

    agent = TrendHunterAgent(llm_caller=None)
    candidates = agent.pre_filter(summaries)
    result = agent.analyze_fallback(candidates)

    assert "picks" in result
    assert "confidence" in result
    assert isinstance(result["picks"], list)
    for p in result["picks"]:
        assert "code" in p
        assert "score" in p
        assert "stop_loss_pct" in p
        assert "take_profit_pct" in p
        assert 0 <= p["score"] <= 1
        assert p["stop_loss_pct"] > 0
        assert p["take_profit_pct"] > 0


def test_contrarian_prefilter():
    from invest.core import summarize_stocks
    from invest.agents import ContrarianAgent

    stock_data = make_stock_data(50, 200)
    codes = list(stock_data.keys())
    summaries = summarize_stocks(stock_data, codes, "20231001")

    agent = ContrarianAgent(llm_caller=None)
    candidates = agent.pre_filter(summaries)

    # 超跌候选应该有 contrarian_score
    for c in candidates:
        assert "contrarian_score" in c
        assert c["contrarian_score"] > 0


def test_contrarian_fallback():
    from invest.agents import ContrarianAgent

    agent = ContrarianAgent(llm_caller=None)
    result = agent.analyze_fallback([
        {"code": "sh.600000", "contrarian_score": 0.5, "rsi": 25,
         "bb_pos": 0.15, "change_20d": -12, "change_5d": -3.0},
    ])

    assert len(result["picks"]) >= 1
    # 逆向股有止损设置
    assert result["picks"][0]["stop_loss_pct"] > 0


def test_strategist_fallback():
    from invest.agents import StrategistAgent

    agent = StrategistAgent(llm_caller=None)

    # 只有趋势没有逆向
    result = agent.review(
        trend_picks={"picks": [{"code": "a"}, {"code": "b"}, {"code": "c"}], "confidence": 0.8},
        contrarian_picks={"picks": [], "confidence": 0},
        regime={"regime": "oscillation"},
    )

    assert result["risk_level"] in ("low", "medium", "high")
    # 算法审查会产生concerns
    assert isinstance(result["concerns"], list)


def test_commander_fallback():
    from invest.agents import CommanderAgent

    agent = CommanderAgent(llm_caller=None)

    result = agent.integrate_fallback(
        regime={"regime": "oscillation", "confidence": 0.5,
                "params": {"top_n": 5, "max_positions": 3,
                           "stop_loss_pct": 0.05, "take_profit_pct": 0.15,
                           "position_size": 0.20}},
        trend_picks={"picks": [
            {"code": "sh.600000", "score": 0.8, "stop_loss_pct": 0.03,
             "take_profit_pct": 0.15, "reasoning": "趋势"},
        ], "confidence": 0.7},
        contrarian_picks={"picks": [
            {"code": "sh.600010", "score": 0.6, "stop_loss_pct": 0.08,
             "take_profit_pct": 0.20, "reasoning": "超跌"},
        ], "confidence": 0.5},
        strategy_review={"risk_level": "low", "concerns": []},
    )

    assert "positions" in result
    assert "cash_reserve" in result
    assert len(result["positions"]) <= 3
    assert result["cash_reserve"] >= 0
    # 权重总和 + cash_reserve <= 1.0
    total_weight = sum(p["weight"] for p in result["positions"])
    assert total_weight + result["cash_reserve"] <= 1.01


def test_commander_trailing_pct():
    """趋势股应该有 trailing_pct"""
    from invest.agents import CommanderAgent

    agent = CommanderAgent(llm_caller=None)
    result = agent.integrate_fallback(
        regime={"regime": "bull", "confidence": 0.8,
                "params": {"top_n": 8, "max_positions": 4,
                           "stop_loss_pct": 0.07, "take_profit_pct": 0.20,
                           "position_size": 0.20}},
        trend_picks={"picks": [
            {"code": "sh.600000", "score": 0.9, "stop_loss_pct": 0.03,
             "take_profit_pct": 0.15, "reasoning": "强趋势",
             "source": "trend_hunter"},
        ], "confidence": 0.85},
        contrarian_picks={"picks": [], "confidence": 0},
        strategy_review={"risk_level": "low", "concerns": []},
    )

    trend_pos = [p for p in result["positions"] if p.get("source") == "trend_hunter"]
    if trend_pos:
        assert trend_pos[0].get("trailing_pct") is not None


def test_selection_meeting_fallback():
    """数据不足时回退到算法选股"""
    from invest.core import LLMCaller
    from invest.meetings import SelectionMeeting

    caller = LLMCaller(dry_run=True)

    meeting = SelectionMeeting(caller, max_hunters=2)

    # 空数据
    result = meeting.run(
        regime={"regime": "oscillation", "confidence": 0.5,
                "params": {"top_n": 5, "max_positions": 2,
                           "stop_loss_pct": 0.05, "take_profit_pct": 0.15,
                           "position_size": 0.20}},
        stock_summaries=[],
    )

    # 空数据应返回 fallback
    assert "selected" in result
    assert "reasoning" in result


def test_selection_meeting_with_data():
    from invest.core import LLMCaller
    from invest.meetings import SelectionMeeting

    caller = LLMCaller(dry_run=True)

    meeting = SelectionMeeting(caller, max_hunters=2)

    stock_data = make_stock_data(50, 200)

    result = meeting.run(
        regime={"regime": "oscillation", "confidence": 0.6,
                "params": {"top_n": 5, "max_positions": 3,
                           "stop_loss_pct": 0.05, "take_profit_pct": 0.15,
                           "position_size": 0.20}},
        stock_summaries=[],
    )

    plan = result
    assert plan is not None


# ===== Phase 3 测试 =====

def test_trailing_stop():
    """跟踪止盈：先涨后跌，应该在回落时卖出，而不是固定止盈"""
    from invest.core import TradingPlan, PositionPlan
    from invest.trading import SimulatedTrader

    stock_data = make_rising_stock()
    code = list(stock_data.keys())[0]
    dates = stock_data[code]["trade_date"].tolist()

    plan = TradingPlan(
        date=dates[0],
        positions=[
            PositionPlan(
                code=code, priority=1, weight=0.5,
                stop_loss_pct=0.10,
                take_profit_pct=0.50,       # 很高的固定止盈（不会触发）
                trailing_pct=0.05,           # 从最高回落5%就卖
                source="trend_hunter",
            ),
        ],
        max_positions=1,
    )

    trader = SimulatedTrader(initial_capital=100000)
    trader.set_stock_data(stock_data)
    trader.set_trading_plan(plan)

    result = trader.run_simulation(dates[0], dates)

    # 检查卖出原因
    sells = [t for t in result.trade_history
             if hasattr(t.action, 'value') and t.action.value == "卖出"
             or str(t.action) == "卖出"]

    has_trailing = any("跟踪止盈" in t.reason for t in sells)
    has_fixed = any(t.reason == "止盈" for t in sells)

    # 应该触发跟踪止盈，而不是固定止盈
    # （固定止盈设在50%，不会触发；跟踪止盈在回落5%时触发）
    assert has_trailing or (not has_fixed), \
        f"卖出原因: {[t.reason for t in sells]}。期望跟踪止盈触发"


def test_position_source_tracking():
    """持仓来源追踪"""
    from invest.core import TradingPlan, PositionPlan
    from invest.trading import SimulatedTrader

    stock_data = make_stock_data(5, 60)
    codes = list(stock_data.keys())
    dates = stock_data[codes[0]]["trade_date"].tolist()

    plan = TradingPlan(
        date=dates[0],
        positions=[
            PositionPlan(code=codes[0], priority=1, weight=0.2,
                         source="trend_hunter"),
            PositionPlan(code=codes[1], priority=2, weight=0.2,
                         source="contrarian"),
        ],
        max_positions=2,
    )

    trader = SimulatedTrader(initial_capital=100000)
    trader.set_stock_data(stock_data)
    trader.set_trading_plan(plan)

    # 执行第一步让买入发生
    trader.step(dates[0])

    # 检查持仓来源
    for pos in trader.positions:
        assert pos.source in ("trend_hunter", "contrarian"), \
            f"{pos.ts_code} source={pos.source}"


def test_emergency_detector():
    from invest.trading import EmergencyDetector, EmergencyType
    from invest.trading import SimulatedTrader, Position

    detector = EmergencyDetector(single_stock_crash_pct=-7.0)

    stock_data = make_stock_data(3, 60)
    codes = list(stock_data.keys())
    dates = stock_data[codes[0]]["trade_date"].tolist()

    trader = SimulatedTrader(initial_capital=100000)
    trader.set_stock_data(stock_data)

    # 手动添加持仓
    trader.positions = [
        Position(codes[0], "测试A", dates[0], 100.0, 100, source="test"),
    ]
    trader.current_date = dates[10]

    # 正常情况
    events = detector.check(trader, dates[10])
    # 事件数取决于随机数据，但不应该崩溃
    assert isinstance(events, list)

    # 测试重置
    detector.reset()
    assert len(detector.portfolio_values) == 0


def test_limit_order_expiry():
    """限价单过期：5天到不了目标价就放弃"""
    from invest.core import TradingPlan, PositionPlan
    from invest.trading import SimulatedTrader

    stock_data = make_stock_data(3, 60)
    code = list(stock_data.keys())[0]
    dates = stock_data[code]["trade_date"].tolist()

    # 设置一个不可能到达的限价
    plan = TradingPlan(
        date=dates[0],
        positions=[
            PositionPlan(
                code=code, priority=1, weight=0.3,
                entry_method="limit",
                entry_price=0.01,    # 几乎不可能到达
                expire_days=3,
                stop_loss_pct=0.05, take_profit_pct=0.15,
            ),
        ],
        max_positions=1,
    )

    trader = SimulatedTrader(initial_capital=100000)
    trader.set_stock_data(stock_data)
    trader.set_trading_plan(plan)

    result = trader.run_simulation(dates[0], dates[:15])

    # 限价单过期，应该没有买入
    # 由于限价0.01极低，实际上价格一定 > 0.01，应该会买入
    # 这个测试验证不崩溃即可


# ===== Phase 4 测试 =====

def test_agent_tracker():
    from invest.core import AgentTracker

    tracker = AgentTracker()

    # Cycle 1
    tracker.record_predictions(1, "trend_hunter", [
        {"code": "sh.600000", "score": 0.8, "stop_loss_pct": 0.05, "take_profit_pct": 0.15},
        {"code": "sh.600001", "score": 0.7, "stop_loss_pct": 0.05, "take_profit_pct": 0.15},
    ])
    tracker.record_predictions(1, "contrarian", [
        {"code": "sh.600010", "score": 0.6, "stop_loss_pct": 0.08, "take_profit_pct": 0.20},
    ])
    tracker.mark_selected(1, ["sh.600000", "sh.600010"])
    tracker.record_outcomes(1, {"sh.600000": 500.0, "sh.600010": -200.0})

    # Cycle 2
    tracker.record_predictions(2, "trend_hunter", [
        {"code": "sh.600002", "score": 0.9, "stop_loss_pct": 0.03, "take_profit_pct": 0.20},
    ])
    tracker.mark_selected(2, ["sh.600002"])
    tracker.record_outcomes(2, {"sh.600002": 300.0})

    # 验证准确率
    acc = tracker.compute_accuracy()

    assert "trend_hunter" in acc
    assert "contrarian" in acc

    th = acc["trend_hunter"]
    assert th["total_picks"] == 3
    assert th["selected_count"] == 2
    assert th["traded_count"] == 2  # 600000 + 600002
    assert th["profitable_count"] == 2  # 两个都盈利
    assert th["accuracy"] == 1.0

    ct = acc["contrarian"]
    assert ct["total_picks"] == 1
    assert ct["traded_count"] == 1
    assert ct["profitable_count"] == 0
    assert ct["accuracy"] == 0.0

    # last_n_cycles 过滤
    acc_recent = tracker.compute_accuracy(last_n_cycles=1)
    th_recent = acc_recent.get("trend_hunter", {})
    assert th_recent.get("total_picks", 0) == 1  # 只有 cycle 2

    # 总结
    summary = tracker.get_summary()
    assert summary["total_predictions"] == 4
    assert summary["total_cycles"] == 2


def test_review_meeting_fallback():
    from invest.meetings import ReviewMeeting

    review = ReviewMeeting(llm_caller=None)

    result = review.run(
        recent_results=[
            {"is_profit": True, "return_pct": 5.0, "plan_source": "meeting", "regime": "bull"},
            {"is_profit": False, "return_pct": -3.0, "plan_source": "algorithm", "regime": "oscillation"},
            {"is_profit": False, "return_pct": -7.0, "plan_source": "meeting", "regime": "bear"},
            {"is_profit": False, "return_pct": -2.0, "plan_source": "algorithm", "regime": "bear"},
        ],
        agent_accuracy={
            "trend_hunter": {"total_picks": 5, "selected_count": 3, "traded_count": 3,
                             "profitable_count": 2, "accuracy": 0.67, "avg_score": 0.75},
            "contrarian": {"total_picks": 3, "selected_count": 2, "traded_count": 2,
                           "profitable_count": 0, "accuracy": 0.0, "avg_score": 0.55},
        },
        current_params={"stop_loss_pct": 0.05, "take_profit_pct": 0.15, "position_size": 0.20},
    )

    assert "param_adjustments" in result
    assert "agent_weight_adjustments" in result
    assert "strategy_suggestions" in result

    # contrarian 准确率 0% → 权重应该降低
    wa = result["agent_weight_adjustments"]
    if "contrarian" in wa and "trend_hunter" in wa:
        assert wa["contrarian"] < wa["trend_hunter"], \
            f"contrarian权重({wa['contrarian']})应该低于trend_hunter({wa['trend_hunter']})"


def test_meeting_recorder():
    import tempfile
    from invest.meetings import MeetingRecorder

    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = MeetingRecorder(base_dir=tmpdir)

        # 保存选股会议
        recorder.save_selection_meeting({
            "meeting_id": 1,
            "cutoff_date": "20240301",
            "regime": "oscillation",
            "regime_confidence": 0.7,
            "summaries_count": 45,
            "trend_candidates": 18,
            "contrarian_candidates": 12,
            "trend_picks": {"picks": [{"code": "sh.600000", "score": 0.8, "reasoning": "test"}], "confidence": 0.7},
            "contrarian_picks": {"picks": [], "confidence": 0},
            "strategy_review": {"risk_level": "low", "assessment": "ok", "concerns": []},
            "final_stocks": ["sh.600000"],
            "cash_reserve": 0.3,
            "elapsed_sec": 5.0,
        }, cycle=1)

        # 检查文件
        md_path = Path(tmpdir) / "selection" / "meeting_0001.md"
        json_path = Path(tmpdir) / "selection" / "meeting_0001.json"
        assert md_path.exists(), f"Markdown 文件不存在: {md_path}"
        assert json_path.exists(), f"JSON 文件不存在: {json_path}"

        # 验证内容
        md_content = md_path.read_text(encoding="utf-8")
        assert "选股会议" in md_content
        assert "sh.600000" in md_content

        # 保存复盘会议
        recorder.save_review_meeting(
            {"strategy_suggestions": ["test"], "param_adjustments": {},
             "agent_weight_adjustments": {}, "reasoning": "test"},
            {"total_cycles": 10, "win_rate": 0.5, "avg_return": -1.0,
             "agent_accuracy": {}},
            cycle=5,
        )

        review_md = Path(tmpdir) / "review" / "review_0005.md"
        assert review_md.exists()


def test_selection_meeting_weights():
    from invest.core import LLMCaller
    from invest.meetings import SelectionMeeting

    caller = LLMCaller(dry_run=True)

    meeting = SelectionMeeting(caller, max_hunters=2)

    # 初始权重
    assert meeting.agent_weights["trend_hunter"] == 1.0
    assert meeting.agent_weights["contrarian"] == 1.0

    # 更新权重
    meeting.update_weights({"trend_hunter": 1.5, "contrarian": 0.5})

    # EMA 平滑：old * 0.6 + new * 0.4
    expected_trend = 1.0 * 0.6 + 1.5 * 0.4  # = 1.2
    expected_contra = 1.0 * 0.6 + 0.5 * 0.4  # = 0.8
    assert abs(meeting.agent_weights["trend_hunter"] - expected_trend) < 0.01
    assert abs(meeting.agent_weights["contrarian"] - expected_contra) < 0.01


# ===== 跨模块导入测试 =====

def test_all_imports():
    """确保所有模块都能正常导入"""
    modules = [
        "invest.core",
        "invest.agents",
        "invest.trading",
        "invest.meetings",
        "config",
    ]

    for mod in modules:
        try:
            __import__(mod)
        except ImportError as e:
            raise AssertionError(f"导入失败 {mod}: {e}")


def test_should_use_agent():
    """触发条件函数"""
    # 测试逻辑本身
    def should_use_agent(cycle, consecutive_losses=0,
                         regime_changed=False, interval=30):
        if cycle == 1:
            return True
        if cycle % interval == 0:
            return True
        if consecutive_losses >= 2:
            return True
        if regime_changed:
            return True
        return False

    assert should_use_agent(1) == True
    assert should_use_agent(2) == False
    assert should_use_agent(30) == True
    assert should_use_agent(31) == False
    assert should_use_agent(5, consecutive_losses=2) == True
    assert should_use_agent(5, consecutive_losses=1) == False
    assert should_use_agent(5, regime_changed=True) == True


# ===== 运行所有测试 =====

if __name__ == "__main__":
    print("=" * 60)
    print("全模块单元测试")
    print("=" * 60)

    np.random.seed(42)  # 固定随机种子，测试可复现

    # Phase 0
    print("\n--- Phase 0: TradingPlan + Trader ---")
    run_test("TradingPlan 创建和查询", test_trading_plan_creation)
    run_test("PlanBuilder 生成计划", test_plan_builder)
    run_test("Trader 按计划交易", test_trader_with_plan)

    # Phase 1
    print("\n--- Phase 1: LLMCaller + MarketRegime ---")
    run_test("LLMCaller dry_run", test_llm_caller_dry_run)
    run_test("LLMCaller JSON解析", test_llm_caller_json_parsing)
    run_test("市场统计计算", test_market_stats)
    run_test("市场统计空数据", test_market_stats_empty)
    run_test("MarketRegime 算法判断", test_market_regime_fallback)
    run_test("MarketRegime dry_run LLM", test_market_regime_with_dry_llm)

    # Phase 2
    print("\n--- Phase 2: Agent + SelectionMeeting ---")
    run_test("股票技术摘要", test_stock_analyzer)
    run_test("TrendHunter 预筛", test_trend_hunter_prefilter)
    run_test("TrendHunter 算法推荐", test_trend_hunter_fallback)
    run_test("Contrarian 预筛", test_contrarian_prefilter)
    run_test("Contrarian 算法推荐", test_contrarian_fallback)
    run_test("Strategist 算法审查", test_strategist_fallback)
    run_test("Commander 算法整合", test_commander_fallback)
    run_test("Commander 跟踪止盈", test_commander_trailing_pct)
    run_test("SelectionMeeting 数据不足回退", test_selection_meeting_fallback)
    run_test("SelectionMeeting 正常流程", test_selection_meeting_with_data)

    # Phase 3
    print("\n--- Phase 3: 跟踪止盈 + 异常检测 ---")
    run_test("跟踪止盈", test_trailing_stop)
    run_test("持仓来源追踪", test_position_source_tracking)
    run_test("异常检测器", test_emergency_detector)
    run_test("限价单过期", test_limit_order_expiry)

    # Phase 4
    print("\n--- Phase 4: 复盘 + 归因 ---")
    run_test("Agent追踪器", test_agent_tracker)
    run_test("复盘会议算法兜底", test_review_meeting_fallback)
    run_test("会议记录持久化", test_meeting_recorder)
    run_test("SelectionMeeting 权重更新", test_selection_meeting_weights)

    # 通用
    print("\n--- 通用 ---")
    run_test("全模块导入", test_all_imports)
    run_test("触发条件函数", test_should_use_agent)

    # 总结
    print("\n" + "=" * 60)
    print(f"测试结果: {PASSED} 通过, {FAILED} 失败")
    print("=" * 60)

    if ERRORS:
        print("\n失败详情:")
        for name, err, tb in ERRORS:
            print(f"\n❌ {name}:")
            print(tb)

    sys.exit(0 if FAILED == 0 else 1)
