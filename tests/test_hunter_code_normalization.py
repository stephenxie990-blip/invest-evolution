from types import SimpleNamespace

import pytest

from invest.agents.hunters import (
    ContrarianAgent,
    TrendHunterAgent,
    _normalize_candidate_code,
    _recover_hunter_result,
)


def test_normalize_candidate_code_accepts_equivalent_formats():
    valid = ["sh.600058", "sz.000001"]
    assert _normalize_candidate_code("600058", valid) == "sh.600058"
    assert _normalize_candidate_code("SH600058", valid) == "sh.600058"
    assert _normalize_candidate_code("sz000001", valid) == "sz.000001"

def test_recover_hunter_result_from_truncated_json():
    raw = '{"picks":[{"code":"600058","score":0.82,"reasoning":"趋势延续","stop_loss_pct":0.05,"take_profit_pct":0.18},{"code":"sz000001","score":0.74,"reasoning":"量价改善","stop_loss_pct":0.04,"take_profit_pct":0.15}],"overall_view":"候选质量较好","confidence":0.68'
    recovered = _recover_hunter_result(raw, ["sh.600058", "sz.000001"], 0.05, 0.15)

    assert [item["code"] for item in recovered["picks"]] == ["sh.600058", "sz.000001"]
    assert recovered["confidence"] == 0.68
    assert recovered["overall_view"] == "候选质量较好"


@pytest.mark.parametrize(
    ("agent_factory", "candidates"),
    [
        (
            TrendHunterAgent,
            [{
                "code": "sh.600058",
                "close": 12.3,
                "change_5d": 3.1,
                "change_20d": 8.6,
                "ma_trend": "UP",
                "rsi": 61,
                "macd": "golden",
                "bb_pos": 0.72,
                "vol_ratio": 1.4,
                "trend_score": 0.82,
            }],
        ),
        (
            ContrarianAgent,
            [{
                "code": "sz.000001",
                "close": 9.8,
                "change_5d": -4.5,
                "change_20d": -12.0,
                "ma_trend": "DOWN",
                "rsi": 28,
                "macd": "weak",
                "bb_pos": 0.18,
                "vol_ratio": 1.1,
                "contrarian_score": 0.74,
            }],
        ),
    ],
)
def test_hunters_fallback_to_algorithmic_picks_on_parse_error(agent_factory, candidates):
    agent = agent_factory()
    agent.llm = SimpleNamespace(call_json=lambda *args, **kwargs: {"_parse_error": True})

    result = agent.analyze(candidates, {"regime": "oscillation", "reasoning": "test"})

    assert result["contract_status"] == "fallback_algorithm"
    assert [item["code"] for item in result["picks"]] == [candidates[0]["code"]]
    assert result["confidence"] == 0.5
