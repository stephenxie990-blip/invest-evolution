from invest.meetings import SelectionMeeting, ReviewMeeting
from invest.meetings.recorder import MeetingRecorder
from invest.meetings.review import (
    _REVIEW_DECISION_SYSTEM,
    _REVIEW_EVO_JUDGE_SYSTEM,
    _REVIEW_STRATEGIST_SYSTEM,
)


def test_selection_meeting_trading_plan_preserves_pick_metadata():
    meeting = SelectionMeeting(llm_caller=None)
    meeting_result = {
        "selected": ["AAA", "BBB"],
        "selected_meta": [
            {
                "code": "AAA",
                "source": "trend_hunter",
                "stop_loss_pct": 0.04,
                "take_profit_pct": 0.18,
                "trailing_pct": 0.10,
                "reasoning": "趋势更强",
            },
            {
                "code": "BBB",
                "source": "contrarian",
                "stop_loss_pct": 0.09,
                "take_profit_pct": 0.20,
                "trailing_pct": None,
                "reasoning": "超跌修复",
            },
        ],
        "reasoning": "会议整合后的结果",
        "source": "llm",
    }
    regime = {
        "suggested_exposure": 0.45,
        "params": {"max_positions": 2, "stop_loss_pct": 0.05, "take_profit_pct": 0.15},
    }

    plan = meeting._to_trading_plan(meeting_result, regime, "20240101")

    assert plan.cash_reserve == 0.55
    assert plan.positions[0].source == "trend_hunter"
    assert plan.positions[0].stop_loss_pct == 0.04
    assert plan.positions[0].trailing_pct == 0.10
    assert plan.positions[1].source == "contrarian"
    assert plan.positions[1].stop_loss_pct == 0.09
    assert plan.positions[1].trailing_pct is None


def test_review_meeting_prompt_contracts_include_examples_and_negative_constraints():
    for prompt in (_REVIEW_STRATEGIST_SYSTEM, _REVIEW_EVO_JUDGE_SYSTEM, _REVIEW_DECISION_SYSTEM):
        assert "少样本示例" in prompt
        assert "负例约束" in prompt
        assert "只输出一个 JSON 对象" in prompt


def test_review_meeting_validation_clamps_outputs():
    review = ReviewMeeting(llm_caller=None)

    strategist = review._validate_strategy_analysis(
        {"problems": ["问题1", ""], "suggestions": [1, "建议2"], "confidence": "1.4"}
    )
    assert strategist["problems"] == ["问题1"]
    assert strategist["suggestions"] == ["1", "建议2"]
    assert strategist["confidence"] == 1.0

    evo = review._validate_evo_assessment(
        {
            "param_adjustments": {"stop_loss_pct": 0.5, "position_size": 0.01, "bad": 3},
            "evolution_direction": "wild",
            "suggestions": ["收紧仓位", ""],
            "confidence": -1,
            "reasoning": None,
        }
    )
    assert evo["param_adjustments"]["stop_loss_pct"] == 0.15
    assert evo["param_adjustments"]["position_size"] == 0.05
    assert evo["evolution_direction"] == "maintain"
    assert evo["suggestions"] == ["收紧仓位"]
    assert evo["confidence"] == 0.0
    assert evo["reasoning"] == ""

    decision = review._validate_decision(
        {
            "strategy_suggestions": ["建议", ""],
            "param_adjustments": {"position_size": 0.9},
            "agent_weight_adjustments": {"trend_hunter": 3, "ghost": 1.5},
            "reasoning": 123,
        },
        {"agent_accuracy": {"trend_hunter": {}}},
    )
    assert decision["strategy_suggestions"] == ["建议"]
    assert decision["param_adjustments"]["position_size"] == 0.3
    assert decision["agent_weight_adjustments"] == {"trend_hunter": 2.0}
    assert decision["reasoning"] == ""


def test_selection_meeting_aggregate_respects_top_n_and_keeps_meta():
    meeting = SelectionMeeting(llm_caller=None)
    hunter_outputs = [
        {
            "name": "trend_hunter",
            "result": {
                "confidence": 0.8,
                "overall_view": "趋势占优",
                "picks": [
                    {"code": "AAA", "score": 0.9, "stop_loss_pct": 0.04, "take_profit_pct": 0.18, "reasoning": "趋势强"},
                    {"code": "BBB", "score": 0.7, "stop_loss_pct": 0.05, "take_profit_pct": 0.16, "reasoning": "次优趋势"},
                ],
            },
        },
        {
            "name": "contrarian",
            "result": {
                "confidence": 0.6,
                "overall_view": "逆向补充",
                "picks": [
                    {"code": "CCC", "score": 0.8, "stop_loss_pct": 0.09, "take_profit_pct": 0.20, "reasoning": "超跌修复"},
                ],
            },
        },
    ]
    result = meeting._aggregate(hunter_outputs, {"regime": "oscillation", "params": {"max_positions": 3}}, top_n=2)

    assert len(result["selected"]) == 2
    assert len(result["selected_meta"]) == 2
    assert result["selected_meta"][0]["code"] in {"AAA", "BBB", "CCC"}



def test_review_meeting_validation_adds_applied_summary_for_clamped_values():
    review = ReviewMeeting(llm_caller=None)

    decision = review._validate_decision(
        {
            "param_adjustments": {"position_size": 0.9, "stop_loss_pct": 0.5},
            "agent_weight_adjustments": {"trend_hunter": 1.6},
            "reasoning": "建议把仓位降到10%以控制风险。",
        },
        {"agent_accuracy": {"trend_hunter": {}}},
    )

    assert decision["param_adjustments"]["position_size"] == 0.3
    assert decision["param_adjustments"]["stop_loss_pct"] == 0.15
    assert decision["applied_summary"] == "最终执行参数：position_size=30%，stop_loss_pct=15%；最终执行权重：trend_hunter=1.60"
    assert "10%" in decision["reasoning"]


def test_review_recorder_markdown_uses_aggregated_facts_and_applied_summary(tmp_path):
    recorder = MeetingRecorder(base_dir=str(tmp_path))

    markdown = recorder._review_to_md(
        {
            "strategy_suggestions": ["减少追高型交易"],
            "param_adjustments": {"position_size": 0.3},
            "agent_weight_adjustments": {"trend_hunter": 1.2},
            "applied_summary": "最终执行参数：position_size=30%",
            "reasoning": "近期波动加大，先控制风险。",
        },
        {"total_cycles": 5, "win_rate": 0.4, "avg_return": 3.25},
        7,
    )

    assert "- 总轮数: 5" in markdown
    assert "- 胜率: 40%" in markdown
    assert "- 平均收益: +3.25%" in markdown
    assert "**最终执行摘要**: 最终执行参数：position_size=30%" in markdown


def test_review_meeting_validation_normalizes_list_weight_adjustments():
    review = ReviewMeeting(llm_caller=None)

    decision = review._validate_decision(
        {
            "strategy_suggestions": ["建议"],
            "param_adjustments": {},
            "agent_weight_adjustments": [
                {"agent": "trend_hunter", "weight": 1.4},
                {"agent": "ghost", "weight": 1.9},
            ],
            "reasoning": "ok",
        },
        {"agent_accuracy": {"trend_hunter": {}}},
    )

    assert decision["agent_weight_adjustments"] == {"trend_hunter": 1.4}
