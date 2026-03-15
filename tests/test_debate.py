"""
Tests for DebateOrchestrator and RiskDebateOrchestrator (Phase 3)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_dry_callers():
    from invest.shared.llm import LLMCaller
    fast = LLMCaller(dry_run=True)
    deep = LLMCaller(dry_run=True)
    return fast, deep


class _FakeCaller:
    def __init__(self, *, text="", json_result=None, model="fake-model"):
        self.text = text
        self.json_result = dict(json_result or {})
        self.model = model
        self.calls = []

    def call(self, system_prompt, user_message, temperature=0.7, max_tokens=2048):
        self.calls.append(
            {
                "kind": "text",
                "system_prompt": system_prompt,
                "user_message": user_message,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return self.text

    def call_json(self, system_prompt, user_message, **kwargs):
        self.calls.append(
            {
                "kind": "json",
                "system_prompt": system_prompt,
                "user_message": user_message,
                **dict(kwargs),
            }
        )
        return dict(self.json_result)


def _make_stock_info(code="sh.600001"):
    return {
        "code": code,
        "close": 15.5,
        "change_5d": 2.3,
        "change_20d": 5.1,
        "ma_trend": "多头",
        "rsi": 55.0,
        "macd": "看多",
        "bb_pos": 0.62,
        "vol_ratio": 1.3,
        "algo_score": 0.7,
    }


def _make_regime():
    return {
        "regime": "bull",
        "confidence": 0.75,
        "suggested_exposure": 0.8,
        "reasoning": "量价齐升，多数股票上涨",
        "params": {},
    }


def _make_trading_plan():
    from invest.shared.contracts import PositionPlan, TradingPlan
    positions = [
        PositionPlan(
            code="sh.600001",
            priority=1,
            weight=0.20,
            stop_loss_pct=0.05,
            take_profit_pct=0.15,
        )
    ]
    return TradingPlan(
        date="20241101",
        positions=positions,
        cash_reserve=0.30,
        max_positions=2,
        source="meeting",
    )


# ─────────────────────────── DebateOrchestrator ───────────────────────────

def test_debate_dry_run_returns_valid_structure():
    """DebateOrchestrator 在 dry_run 下能完成流程并返回正确结构"""
    from invest.debate import DebateOrchestrator
    fast, deep = _make_dry_callers()
    debate = DebateOrchestrator(fast, deep, max_rounds=1)

    result = debate.debate(_make_stock_info(), _make_regime())

    # 必须有 verdict 字段
    assert "verdict" in result
    assert result["verdict"] in ("buy", "hold", "avoid")
    assert "confidence" in result
    assert 0.0 <= result["confidence"] <= 1.0
    assert "source" in result


def test_debate_fallback():
    """debate_fallback 能正确执行算法兜底"""
    from invest.debate import DebateOrchestrator
    fast, deep = _make_dry_callers()
    debate = DebateOrchestrator(fast, deep)

    result = debate.debate_fallback(_make_stock_info())

    assert result["verdict"] in ("buy", "hold", "avoid")
    assert result["source"] == "debate_fallback"
    assert "bull_summary" in result
    assert "bear_summary" in result


def test_debate_bear_trend():
    """空头趋势股票的算法兜底应倾向 hold 或 avoid"""
    from invest.debate import DebateOrchestrator
    fast, deep = _make_dry_callers()
    debate = DebateOrchestrator(fast, deep)

    bear_stock = {
        "code": "sh.600002",
        "close": 10.0,
        "change_5d": -5.0,
        "change_20d": -12.0,
        "ma_trend": "空头",
        "rsi": 28.0,
        "macd": "死叉",
        "bb_pos": 0.1,
        "vol_ratio": 0.8,
    }
    result = debate.debate_fallback(bear_stock)
    assert result["verdict"] in ("hold", "avoid")


def test_debate_max_rounds():
    """max_rounds=2 时流程不崩溃"""
    from invest.debate import DebateOrchestrator
    fast, deep = _make_dry_callers()
    debate = DebateOrchestrator(fast, deep, max_rounds=2)

    result = debate.debate(_make_stock_info(), _make_regime())
    assert "verdict" in result


def test_debate_supports_role_specific_callers():
    from invest.debate import DebateOrchestrator

    fast = _FakeCaller(text="unused-fast", model="fast-fallback")
    deep = _FakeCaller(json_result={"verdict": "hold", "confidence": 0.4}, model="deep-fallback")
    bull = _FakeCaller(text="bull thesis", model="bull-model")
    bear = _FakeCaller(text="bear thesis", model="bear-model")
    judge = _FakeCaller(
        json_result={
            "verdict": "hold",
            "confidence": 0.61,
            "bull_summary": "bull thesis",
            "bear_summary": "bear thesis",
            "reasoning": "balanced",
        },
        model="judge-model",
    )
    debate = DebateOrchestrator(
        fast,
        deep,
        max_rounds=1,
        bull_llm=bull,
        bear_llm=bear,
        judge_llm=judge,
    )

    result = debate.debate(_make_stock_info(), _make_regime())

    assert result["verdict"] == "hold"
    assert bull.calls and bear.calls and judge.calls
    assert not fast.calls
    assert not deep.calls


# ─────────────────────────── RiskDebateOrchestrator ───────────────────────────

def test_risk_debate_dry_run_returns_valid_structure():
    """RiskDebateOrchestrator 在 dry_run 下能完成流程并返回正确结构"""
    from invest.debate import RiskDebateOrchestrator
    fast, deep = _make_dry_callers()
    risk_debate = RiskDebateOrchestrator(fast, deep, max_rounds=1)

    result = risk_debate.assess_risk(_make_trading_plan(), _make_regime())

    assert "risk_level" in result
    assert result["risk_level"] in ("low", "medium", "high")
    assert "position_size_suggestion" in result
    assert 0.0 <= result["position_size_suggestion"] <= 0.5
    assert "stop_loss_suggestion" in result
    assert "source" in result


def test_risk_debate_fallback():
    """assess_risk_fallback 在纯算法模式下返回正确格式"""
    from invest.debate import RiskDebateOrchestrator
    fast, deep = _make_dry_callers()
    risk_debate = RiskDebateOrchestrator(fast, deep)

    result = risk_debate.assess_risk_fallback(_make_trading_plan(), _make_regime())

    assert result["risk_level"] in ("low", "medium", "high")
    assert result["source"] == "risk_debate_fallback"
    assert isinstance(result["key_concerns"], list)


def test_risk_debate_bear_market():
    """熊市下风险评级应更高"""
    from invest.debate import RiskDebateOrchestrator
    fast, deep = _make_dry_callers()
    risk_debate = RiskDebateOrchestrator(fast, deep)

    bear_regime = {
        "regime": "bear",
        "confidence": 0.8,
        "suggested_exposure": 0.2,
    }
    result = risk_debate.assess_risk_fallback(_make_trading_plan(), bear_regime)
    assert result["risk_level"] == "high"


def test_risk_debate_with_portfolio_state():
    """传入 portfolio_state 不崩溃"""
    from invest.debate import RiskDebateOrchestrator
    fast, deep = _make_dry_callers()
    risk_debate = RiskDebateOrchestrator(fast, deep)

    portfolio_state = {
        "position_count": 3,
        "portfolio_value": 150000.0,
        "portfolio_return": 0.08,
    }
    result = risk_debate.assess_risk(_make_trading_plan(), _make_regime(), portfolio_state)
    assert "risk_level" in result


def test_risk_debate_supports_role_specific_callers():
    from invest.debate import RiskDebateOrchestrator

    fast = _FakeCaller(text="unused-fast", model="fast-fallback")
    deep = _FakeCaller(json_result={"risk_level": "medium"}, model="deep-fallback")
    aggressive = _FakeCaller(text="take more risk", model="aggressive-model")
    conservative = _FakeCaller(text="tighten risk", model="conservative-model")
    neutral = _FakeCaller(text="balanced plan", model="neutral-model")
    judge = _FakeCaller(
        json_result={
            "risk_level": "medium",
            "position_size_suggestion": 0.18,
            "stop_loss_suggestion": 0.05,
            "take_profit_suggestion": 0.14,
            "key_concerns": ["dispersion"],
            "reasoning": "balanced",
        },
        model="judge-model",
    )
    risk_debate = RiskDebateOrchestrator(
        fast,
        deep,
        max_rounds=1,
        aggressive_llm=aggressive,
        conservative_llm=conservative,
        neutral_llm=neutral,
        judge_llm=judge,
    )

    result = risk_debate.assess_risk(_make_trading_plan(), _make_regime())

    assert result["risk_level"] == "medium"
    assert aggressive.calls and conservative.calls and neutral.calls and judge.calls
    assert not fast.calls
    assert not deep.calls

def test_debate_recovers_truncated_judge_json():
    from invest.debate import DebateOrchestrator
    fast, deep = _make_dry_callers()
    debate = DebateOrchestrator(fast, deep)

    recovered = debate._recover_judge_result(  # pylint: disable=protected-access
        '{"verdict": "hold", "confidence": 0.65, "bull_summary": "MACD金叉，均线走强", "bear_summary": "估值略高", "reasoning": "等待更清晰信号'
    )

    assert recovered is not None
    assert recovered["verdict"] == "hold"
    assert recovered["confidence"] == 0.65
    assert "MACD" in recovered["bull_summary"]


def test_risk_debate_recovers_truncated_judge_json():
    from invest.debate import RiskDebateOrchestrator
    fast, deep = _make_dry_callers()
    risk_debate = RiskDebateOrchestrator(fast, deep)

    recovered = risk_debate._recover_risk_judge_result(  # pylint: disable=protected-access
        '{"risk_level": "medium", "position_size_suggestion": 0.18, "stop_loss_suggestion": 0.05, "take_profit_suggestion": 0.16, "key_concerns": ["波动放大"], "reasoning": "适中仓位更稳健'
    )

    assert recovered is not None
    assert recovered["risk_level"] == "medium"
    assert recovered["position_size_suggestion"] == 0.18
    assert recovered["key_concerns"] == ["波动放大"]
