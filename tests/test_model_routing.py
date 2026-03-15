import json

import invest.router.engine as routing_engine
from invest.contracts import AllocationPlan
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


def _write_leaderboard(tmp_path, payload):
    path = tmp_path / 'leaderboard.json'
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
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


def test_routing_coordinator_breaks_cooldown_for_strong_candidate_exception(tmp_path, monkeypatch):
    coordinator = ModelRoutingCoordinator(
        cooldown_cycles=2,
        hysteresis_margin=0.01,
        min_confidence=0.5,
    )
    monkeypatch.setattr(
        coordinator.observer,
        'observe',
        lambda *args, **kwargs: MarketObservation(
            as_of_date='20260310',
            stats={
                'avg_change_20d': 0.3,
                'above_ma20_ratio': 0.49,
                'market_breadth': 0.5,
                'avg_volatility': 0.018,
            },
        ),
    )
    monkeypatch.setattr(
        coordinator.classifier,
        'classify',
        lambda *args, **kwargs: {
            'regime': 'oscillation',
            'confidence': 0.84,
            'reasoning': '震荡切换增强',
            'suggested_exposure': 0.45,
            'source': 'rule',
            'rule_result': {},
            'agent_result': {},
        },
    )
    leaderboard_path = _write_leaderboard(
        tmp_path,
        {
            'generated_at': '2026-03-10T00:00:00',
            'entries': [
                {
                    'model_name': 'momentum',
                    'config_name': 'momentum_v1',
                    'score': 9.0,
                    'avg_return_pct': -0.4,
                    'avg_sharpe_ratio': 0.6,
                    'avg_max_drawdown': 7.0,
                    'benchmark_pass_rate': 0.2,
                    'avg_strategy_score': 0.38,
                    'rank': 2,
                    'eligible_for_routing': True,
                },
                {
                    'model_name': 'mean_reversion',
                    'config_name': 'mean_reversion_v1',
                    'score': 14.0,
                    'avg_return_pct': 1.9,
                    'avg_sharpe_ratio': 1.3,
                    'avg_max_drawdown': 3.8,
                    'benchmark_pass_rate': 0.82,
                    'avg_strategy_score': 0.76,
                    'rank': 1,
                    'eligible_for_routing': True,
                },
            ],
            'regime_leaderboards': {
                'oscillation': [
                    {
                        'model_name': 'mean_reversion',
                        'rank': 1,
                        'eligible_for_routing': True,
                    },
                    {
                        'model_name': 'momentum',
                        'rank': 2,
                        'eligible_for_routing': True,
                    },
                ],
            },
        },
    )

    decision = coordinator.route(
        stock_data={},
        cutoff_date='20260310',
        current_model='momentum',
        leaderboard_path=leaderboard_path,
        allocator_top_n=3,
        allowed_models=['momentum', 'mean_reversion'],
        routing_mode='rule',
        current_cycle_id=5,
        last_switch_cycle_id=4,
    )

    assert decision.hold_current is False
    assert decision.switch_applied is True
    assert decision.selected_model == 'mean_reversion'
    assert decision.metadata['cooldown_exception']['applied'] is True
    assert decision.metadata['cooldown_exception']['reason'] == 'candidate_outperforms_weak_current'


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


def test_routing_coordinator_holds_current_when_no_qualified_candidates(tmp_path, monkeypatch):
    coordinator = ModelRoutingCoordinator(cooldown_cycles=2, hysteresis_margin=0.01, min_confidence=0.5)
    monkeypatch.setattr(
        coordinator.observer,
        'observe',
        lambda *args, **kwargs: MarketObservation(
            as_of_date='20260310',
            stats={'avg_change_20d': 0.4, 'above_ma20_ratio': 0.5, 'market_breadth': 0.48, 'avg_volatility': 0.018},
        ),
    )
    monkeypatch.setattr(
        coordinator.classifier,
        'classify',
        lambda *args, **kwargs: {'regime': 'oscillation', 'confidence': 0.78, 'reasoning': '震荡市', 'suggested_exposure': 0.45, 'source': 'rule', 'rule_result': {}, 'agent_result': {}},
    )
    leaderboard_path = _write_leaderboard(
        tmp_path,
        {
            'generated_at': '2026-03-10T00:00:00',
            'entries': [
                {
                    'model_name': 'mean_reversion',
                    'config_name': 'mean_reversion_v1',
                    'score': -4.0,
                    'avg_return_pct': -1.0,
                    'avg_sharpe_ratio': 0.3,
                    'avg_max_drawdown': 18.0,
                    'benchmark_pass_rate': 0.0,
                    'avg_strategy_score': 0.2,
                    'rank': 0,
                    'eligible_for_routing': False,
                    'deployment_stage': 'candidate',
                    'ineligible_reason': 'quality_gate:block_negative_score',
                },
            ],
            'regime_leaderboards': {},
        },
    )

    decision = coordinator.route(
        stock_data={},
        cutoff_date='20260310',
        current_model='momentum',
        leaderboard_path=leaderboard_path,
        allocator_top_n=3,
        allowed_models=['momentum', 'mean_reversion'],
        routing_mode='rule',
    )

    assert decision.hold_current is True
    assert decision.hold_reason == 'no_qualified_routing_candidates'
    assert decision.selected_model == 'momentum'
    assert decision.evidence['allocator_quality']['qualified_candidate_count'] == 0
    assert '没有通过质量门的正式候选' in decision.reasoning


def test_routing_coordinator_blocks_switch_when_candidate_has_style_mismatch(tmp_path, monkeypatch):
    coordinator = ModelRoutingCoordinator(cooldown_cycles=0, hysteresis_margin=0.01, min_confidence=0.5)
    monkeypatch.setattr(
        coordinator.observer,
        'observe',
        lambda *args, **kwargs: MarketObservation(
            as_of_date='20260310',
            stats={'avg_change_20d': 4.2, 'above_ma20_ratio': 0.66, 'market_breadth': 0.63, 'avg_volatility': 0.016},
        ),
    )
    monkeypatch.setattr(
        coordinator.classifier,
        'classify',
        lambda *args, **kwargs: {'regime': 'bull', 'confidence': 0.85, 'reasoning': '趋势增强', 'suggested_exposure': 0.8, 'source': 'rule', 'rule_result': {}, 'agent_result': {}},
    )
    monkeypatch.setattr(
        routing_engine,
        'build_allocation_plan',
        lambda *args, **kwargs: AllocationPlan(
            as_of_date='20260310',
            regime='bull',
            active_models=['defensive_low_vol'],
            model_weights={'defensive_low_vol': 1.0},
            selected_configs={'defensive_low_vol': 'defensive_low_vol_v1'},
            cash_reserve=0.3,
            confidence=0.82,
            reasoning='错误地把防御模型排到了牛市第一。',
            metadata={
                'qualified_candidate_count': 1,
                'failed_quality_entries': [],
                'top_candidates': [
                    {
                        'model_name': 'defensive_low_vol',
                        'regime_compatibility': 0.35,
                        'regime_score': 18.0,
                    }
                ],
            },
        ),
    )

    decision = coordinator.route(
        stock_data={},
        cutoff_date='20260310',
        current_model='momentum',
        leaderboard_path=_leaderboard_path(tmp_path),
        allocator_top_n=3,
        allowed_models=['momentum', 'defensive_low_vol'],
        routing_mode='rule',
    )

    assert decision.hold_current is True
    assert decision.hold_reason == 'regime_style_mismatch'
    assert decision.selected_model == 'momentum'
    assert any(
        item['name'] == 'regime_compatibility' and item['passed'] is False
        for item in decision.guardrail_checks
    )
