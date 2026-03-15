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


def test_review_meeting_run_with_eval_report_accepts_explicit_recent_results_window():
    meeting = ReviewMeeting(llm_caller=None)
    report = EvalReport(
        cycle_id=3,
        as_of_date="20240301",
        return_pct=1.2,
        total_pnl=1200.0,
        total_trades=3,
        win_rate=2 / 3,
        regime="bull",
        is_profit=True,
        selected_codes=["sh.600519"],
        selection_mode="meeting",
    )

    result = meeting.run_with_eval_report(
        report,
        agent_accuracy={},
        current_params={},
        recent_results=[
            {"cycle_id": 1, "is_profit": False, "return_pct": -1.0, "selection_mode": "meeting", "regime": "bear"},
            {"cycle_id": 2, "is_profit": True, "return_pct": 0.8, "selection_mode": "algorithm", "regime": "oscillation"},
            report.to_dict(),
        ],
        review_basis_window={"mode": "rolling", "size": 3, "cycle_ids": [1, 2, 3], "current_cycle_id": 3},
    )

    assert result["review_basis_window"]["cycle_ids"] == [1, 2, 3]
    assert result["strategy_advice"]["metadata"]["review_basis_window"]["size"] == 3


def test_review_meeting_compiles_similar_cases_and_causal_diagnosis_into_facts():
    meeting = ReviewMeeting(llm_caller=None)

    facts = meeting._compile_facts(  # pylint: disable=protected-access
        recent_results=[
            {"cycle_id": 8, "is_profit": False, "return_pct": -1.2, "selection_mode": "meeting", "regime": "bear"},
            {"cycle_id": 9, "is_profit": True, "return_pct": 0.6, "selection_mode": "algorithm", "regime": "bull"},
        ],
        agent_accuracy={},
        similar_results=[
            {"cycle_id": 5, "regime": "bear", "return_pct": -1.5, "selection_mode": "meeting"},
            {"cycle_id": 3, "regime": "bear", "return_pct": -0.9, "selection_mode": "meeting"},
        ],
        similarity_summary={
            "matched_cycle_ids": [5, 3],
            "dominant_regime": "bear",
            "match_features": ["regime", "selection_mode", "benchmark_passed"],
        },
        causal_diagnosis={
            "primary_driver": "regime_repeat_loss",
            "summary": "同一市场状态下重复亏损，且复盘改动尚未形成修复。",
            "drivers": [
                {"code": "regime_repeat_loss", "score": 0.55, "evidence_cycle_ids": [5, 3]},
                {"code": "review_not_applied", "score": 0.25, "evidence_cycle_ids": [8]},
            ],
        },
    )

    assert [item["cycle_id"] for item in facts["similar_cases"]] == [5, 3]
    assert facts["similarity_summary"]["dominant_regime"] == "bear"
    assert facts["causal_diagnosis"]["primary_driver"] == "regime_repeat_loss"
    assert facts["causal_diagnosis"]["drivers"][0]["score"] == 0.55
    assert facts["evidence_gate"]["passed"] is True
    assert facts["evidence_gate"]["support_cycle_ids"] == [5, 3]


def test_review_meeting_run_with_eval_report_preserves_similarity_and_causal_metadata():
    meeting = ReviewMeeting(llm_caller=None)
    report = EvalReport(
        cycle_id=4,
        as_of_date="20240302",
        return_pct=-1.1,
        total_pnl=-1100.0,
        total_trades=4,
        win_rate=0.25,
        regime="bear",
        is_profit=False,
        selected_codes=["sh.600519"],
        selection_mode="meeting",
    )

    result = meeting.run_with_eval_report(
        report,
        agent_accuracy={},
        current_params={},
        recent_results=[report.to_dict()],
        review_basis_window={"mode": "rolling", "size": 1, "cycle_ids": [4], "current_cycle_id": 4},
        similar_results=[{"cycle_id": 2, "regime": "bear", "return_pct": -0.8, "selection_mode": "meeting"}],
        similarity_summary={"matched_cycle_ids": [2], "dominant_regime": "bear"},
        causal_diagnosis={
            "primary_driver": "regime_repeat_loss",
            "summary": "同一市场状态下重复亏损。",
            "drivers": [{"code": "regime_repeat_loss", "score": 0.6, "evidence_cycle_ids": [2]}],
        },
    )

    assert result["similarity_summary"]["matched_cycle_ids"] == [2]
    assert result["causal_diagnosis"]["primary_driver"] == "regime_repeat_loss"
    assert result["strategy_advice"]["metadata"]["similarity_summary"]["dominant_regime"] == "bear"
    assert result["strategy_advice"]["metadata"]["causal_diagnosis"]["primary_driver"] == "regime_repeat_loss"
    assert result["strategy_advice"]["metadata"]["evidence_gate"]["passed"] is True


def test_review_meeting_validate_decision_blocks_adjustments_when_evidence_gate_fails():
    meeting = ReviewMeeting(llm_caller=None)

    result = meeting._validate_decision(  # pylint: disable=protected-access
        {
            "strategy_suggestions": ["继续调大仓位"],
            "param_adjustments": {"position_size": 0.25},
            "agent_weight_adjustments": {"trend_hunter": 1.4},
            "reasoning": "建议更积极一点。",
        },
        facts={
            "agent_accuracy": {"trend_hunter": {"accuracy": 0.4, "traded_count": 5, "total_picks": 5, "profitable_count": 2}},
            "evidence_gate": {
                "passed": False,
                "summary": "相似失败样本和结构化证据不足，暂不支持本轮直接调参。",
                "support_cycle_ids": [],
            },
            "causal_diagnosis": {"primary_driver": "insufficient_history"},
        },
    )

    assert result["param_adjustments"] == {}
    assert result["agent_weight_adjustments"] == {}
    assert result["evidence_gate"]["passed"] is False
    assert "证据不足" in result["reasoning"]
