from invest.meetings.selection import SelectionMeeting
from invest.meetings.review import ReviewMeeting
from invest.contracts import AgentContext, SignalPacket, StockSignal


def test_selection_meeting_uses_model_position_size_for_default_weight():
    meeting = SelectionMeeting(llm_caller=None)
    signal_packet = SignalPacket(
        as_of_date='20240101',
        model_name='momentum',
        config_name='momentum_v1',
        regime='bull',
        signals=[StockSignal(code='AAA', score=0.8, rank=1), StockSignal(code='BBB', score=0.7, rank=2)],
        selected_codes=['AAA', 'BBB'],
        max_positions=2,
        cash_reserve=0.4,
        params={'position_size': 0.12, 'max_hold_days': 10},
    )
    ctx = AgentContext(
        as_of_date='20240101',
        model_name='momentum',
        config_name='momentum_v1',
        summary='x',
        narrative='x',
        regime='bull',
    )
    plan = meeting._to_trading_plan_v2({'selected_meta': [{'code': 'AAA'}, {'code': 'BBB'}], 'reasoning': 'x'}, signal_packet, ctx)
    assert len(plan.positions) == 2
    assert plan.positions[0].weight == 0.12
    assert plan.positions[1].weight == 0.12


def test_review_meeting_policy_sanitizes_adjustments_and_weights():
    meeting = ReviewMeeting(llm_caller=None)
    meeting.set_policy({
        'confidence': {'default': 0.45},
        'param_clamps': {
            'cash_reserve': {'min': 0.0, 'max': 0.6},
            'trailing_pct': {'min': 0.04, 'max': 0.12},
        },
        'agent_weight': {'min': 0.8, 'max': 1.2, 'default': 1.0},
    })
    facts = {'agent_accuracy': {'trend_hunter': {'accuracy': 0.5, 'traded_count': 5}}}
    out = meeting._validate_decision({
        'strategy_suggestions': ['x'],
        'param_adjustments': {'stop_loss_pct': 0.5, 'position_size': 0.9, 'cash_reserve': 0.9, 'trailing_pct': 0.2},
        'agent_weight_adjustments': {'trend_hunter': 2.5},
        'confidence': 'bad',
        'reasoning': 'ok',
    }, facts)
    assert out['param_adjustments']['stop_loss_pct'] == 0.15
    assert out['param_adjustments']['position_size'] == 0.3
    assert out['param_adjustments']['cash_reserve'] == 0.6
    assert out['param_adjustments']['trailing_pct'] == 0.12
    assert out['agent_weight_adjustments']['trend_hunter'] == 1.2

