from invest.contracts import EvalReport
from invest.meetings.review import ReviewMeeting


def test_review_meeting_counts_v2_meeting_results_correctly():
    meeting = ReviewMeeting(llm_caller=None)
    facts = meeting._compile_facts(  # pylint: disable=protected-access
        recent_results=[
            {
                "is_profit": False,
                "return_pct": -2.5,
                "selection_mode": "meeting",
                "plan_source": "llm",
                "regime": "bull",
                "strategy_advice": {"source": "review_meeting"},
            }
        ],
        agent_accuracy={},
    )
    assert facts["meeting_stats"]["count"] == 1
    assert facts["algo_stats"]["count"] == 0


def test_review_meeting_eval_report_preserves_profit_flag():
    meeting = ReviewMeeting(llm_caller=None)
    report = EvalReport(
        cycle_id=1,
        as_of_date="20240131",
        return_pct=1.25,
        total_pnl=1250.0,
        total_trades=3,
        win_rate=1 / 3,
        regime="bear",
        is_profit=True,
        selected_codes=["sh.600000"],
        selection_mode="meeting",
    )

    facts = meeting._compile_facts([report.to_dict()], agent_accuracy={})  # pylint: disable=protected-access

    assert facts["wins"] == 1
    assert facts["losses"] == 0
    assert facts["meeting_stats"]["wins"] == 1


def test_review_meeting_compile_facts_reads_research_feedback_from_eval_report_metadata():
    meeting = ReviewMeeting(llm_caller=None)
    report = EvalReport(
        cycle_id=2,
        as_of_date="20240229",
        return_pct=-1.8,
        total_pnl=-1800.0,
        total_trades=4,
        win_rate=0.25,
        regime="bear",
        is_profit=False,
        selected_codes=["sh.600519"],
        selection_mode="meeting",
        metadata={
            "research_feedback": {
                "sample_count": 5,
                "recommendation": {
                    "bias": "tighten_risk",
                    "reason_codes": ["t20_hit_rate_low"],
                    "summary": "基于 ask 侧归因样本给训练侧的建议：tighten_risk",
                },
                "horizons": {"T+20": {"hit_rate": 0.2, "invalidation_rate": 0.4}},
                "brier_like_direction_score": 0.31,
            }
        },
    )

    facts = meeting._compile_facts([report.to_dict()], agent_accuracy={})  # pylint: disable=protected-access

    assert facts["research_feedback"]["recommendation"]["bias"] == "tighten_risk"
    assert facts["research_feedback"]["horizons"]["T+20"]["hit_rate"] == 0.2


def test_review_meeting_evo_fallback_turns_conservative_on_research_feedback_bias():
    meeting = ReviewMeeting(llm_caller=None)
    facts = {
        "empty": False,
        "win_rate": 0.72,
        "avg_return": 2.4,
        "research_feedback": {
            "sample_count": 5,
            "recommendation": {
                "bias": "tighten_risk",
                "reason_codes": ["t20_hit_rate_low"],
                "summary": "基于 ask 侧归因样本给训练侧的建议：tighten_risk",
            },
            "horizons": {"T+20": {"hit_rate": 0.2, "invalidation_rate": 0.4}},
            "brier_like_direction_score": 0.31,
        },
    }

    result = meeting._evo_judge_fallback(facts)  # pylint: disable=protected-access

    assert result["evolution_direction"] == "conservative"
    assert result["param_adjustments"]
    assert any("问股校准" in item for item in result["suggestions"])
    assert "偏保守" in result["reasoning"]
