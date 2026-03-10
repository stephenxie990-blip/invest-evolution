import json

from invest.router import ModelRoutingCoordinator, RegimeClassifier
from invest.router.engine import MarketObservation


LEADERBOARD = {
    'generated_at': '2026-03-10T00:00:00',
    'entries': [
        {'model_name': 'momentum', 'config_name': 'momentum_v1', 'score': 12.0, 'avg_return_pct': 2.1, 'avg_sharpe_ratio': 1.2, 'avg_max_drawdown': 5.0, 'benchmark_pass_rate': 0.7, 'avg_strategy_score': 0.75, 'rank': 1},
        {'model_name': 'mean_reversion', 'config_name': 'mean_reversion_v1', 'score': 10.0, 'avg_return_pct': 1.2, 'avg_sharpe_ratio': 1.0, 'avg_max_drawdown': 4.0, 'benchmark_pass_rate': 0.6, 'avg_strategy_score': 0.7, 'rank': 2},
        {'model_name': 'defensive_low_vol', 'config_name': 'defensive_low_vol_v1', 'score': 8.0, 'avg_return_pct': 0.8, 'avg_sharpe_ratio': 1.1, 'avg_max_drawdown': 2.0, 'benchmark_pass_rate': 0.8, 'avg_strategy_score': 0.68, 'rank': 3},
    ],
    'regime_leaderboards': {
        'bull': [{'model_name': 'momentum', 'rank': 1}],
        'bear': [{'model_name': 'defensive_low_vol', 'rank': 1}],
        'oscillation': [{'model_name': 'mean_reversion', 'rank': 1}],
    },
}


def _leaderboard_path(tmp_path):
    path = tmp_path / 'leaderboard.json'
    path.write_text(json.dumps(LEADERBOARD, ensure_ascii=False), encoding='utf-8')
    return path


def test_regime_classifier_marks_bull_when_breadth_and_trend_are_strong():
    classifier = RegimeClassifier()
    observation = MarketObservation(
        as_of_date='20260310',
        stats={
            'avg_change_20d': 4.5,
            'above_ma20_ratio': 0.68,
            'avg_volatility': 0.015,
            'market_breadth': 0.66,
            'index_change_20d': 3.2,
        },
    )

    result = classifier.classify(observation)

    assert result['regime'] == 'bull'
    assert result['confidence'] >= 0.72


def test_routing_coordinator_holds_current_model_when_cooldown_is_active(tmp_path, monkeypatch):
    coordinator = ModelRoutingCoordinator(cooldown_cycles=2, hysteresis_margin=0.01, min_confidence=0.5)
    monkeypatch.setattr(
        coordinator.observer,
        'observe',
        lambda *args, **kwargs: MarketObservation(as_of_date='20260310', stats={'avg_change_20d': -4.0, 'above_ma20_ratio': 0.3, 'market_breadth': 0.35, 'avg_volatility': 0.03}),
    )
    monkeypatch.setattr(
        coordinator.classifier,
        'classify',
        lambda *args, **kwargs: {'regime': 'bear', 'confidence': 0.82, 'reasoning': '市场偏弱', 'suggested_exposure': 0.2, 'source': 'rule', 'rule_result': {}, 'agent_result': {}},
    )

    decision = coordinator.route(
        stock_data={},
        cutoff_date='20260310',
        current_model='momentum',
        leaderboard_path=_leaderboard_path(tmp_path),
        allocator_top_n=3,
        allowed_models=['momentum', 'defensive_low_vol'],
        routing_mode='rule',
        current_cycle_id=5,
        last_switch_cycle_id=4,
    )

    assert decision.hold_current is True
    assert decision.hold_reason == 'routing_cooldown_active'
    assert decision.selected_model == 'momentum'


def test_routing_coordinator_supports_off_mode(tmp_path):
    coordinator = ModelRoutingCoordinator()

    decision = coordinator.route(
        stock_data={},
        cutoff_date='20260310',
        current_model='momentum',
        leaderboard_path=_leaderboard_path(tmp_path),
        routing_mode='off',
    )

    assert decision.hold_current is True
    assert decision.selected_model == 'momentum'
    assert decision.decision_source == 'disabled'
