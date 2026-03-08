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

