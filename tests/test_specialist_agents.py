from invest_evolution.investment.agents.specialists import DefensiveAgent, QualityAgent


class _FakeCaller:
    def __init__(self, json_result):
        self.json_result = dict(json_result)

    def call_json(self, system_prompt, user_message, **kwargs):
        return dict(self.json_result)


def test_quality_agent_falls_back_when_llm_returns_empty_picks():
    agent = QualityAgent(
        llm_caller=_FakeCaller(
            {
                "dry_run": True,
                "picks": [],
                "confidence": 0.5,
                "overall_view": "",
            }
        )
    )
    candidates = [
        {
            "code": "sh.600001",
            "close": 12.3,
            "change_5d": 1.5,
            "change_20d": 4.2,
            "ma_trend": "多头",
            "rsi": 56.0,
            "macd": "看多",
            "bb_pos": 0.62,
            "vol_ratio": 1.1,
            "algo_score": 0.65,
            "value_quality_score": 0.72,
            "pe_ttm": 12.0,
            "pb": 1.8,
            "roe": 15.0,
        },
        {
            "code": "sh.600002",
            "close": 9.8,
            "change_5d": -0.4,
            "change_20d": -1.2,
            "ma_trend": "交叉",
            "rsi": 49.0,
            "macd": "中性",
            "bb_pos": 0.48,
            "vol_ratio": 0.9,
            "algo_score": 0.40,
            "value_quality_score": 0.45,
            "pe_ttm": 38.0,
            "pb": 5.0,
            "roe": 4.0,
        },
    ]

    result = agent.analyze(candidates, regime="bull")

    assert result["picks"]
    assert result["picks"][0]["code"] == "sh.600001"
    assert result["overall_view"] == "价值质量优先，强调估值约束与盈利质量"


def test_defensive_agent_normalizes_candidate_codes_from_llm():
    agent = DefensiveAgent(
        llm_caller=_FakeCaller(
            {
                "picks": [
                    {
                        "code": "600001",
                        "score": 0.72,
                        "reasoning": "低波动、回撤可控",
                    }
                ],
                "confidence": 0.66,
                "overall_view": "优先低波标的",
            }
        )
    )
    candidates = [
        {
            "code": "sh.600001",
            "close": 10.2,
            "change_5d": 0.8,
            "algo_score": 0.55,
            "defensive_score": 0.63,
            "volatility": 0.02,
            "change_20d": 1.2,
            "ma_trend": "多头",
            "rsi": 52.0,
            "macd": "看多",
            "bb_pos": 0.41,
            "vol_ratio": 0.95,
        }
    ]

    result = agent.analyze(candidates, regime="bear")

    assert result["picks"] == [
        {
            "code": "sh.600001",
            "score": 0.72,
            "reasoning": "低波动、回撤可控",
        }
    ]
    assert result["confidence"] == 0.66
