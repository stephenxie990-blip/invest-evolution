import json

import invest_evolution.investment.governance.engine as governance_engine
from invest_evolution.investment.agents.specialists import GovernanceSelectorAgent
from invest_evolution.investment.contracts import AllocationPlan
from invest_evolution.investment.governance import GovernanceCoordinator, RegimeClassifier
from invest_evolution.investment.governance.engine import MarketObservation


LEADERBOARD = {
    'generated_at': '2026-03-10T00:00:00',
    'entries': [
        {'manager_id': 'momentum', 'manager_config_ref': 'momentum_v1', 'score': 12.0, 'avg_return_pct': 2.1, 'avg_sharpe_ratio': 1.2, 'avg_max_drawdown': 5.0, 'benchmark_pass_rate': 0.7, 'avg_strategy_score': 0.75, 'rank': 1},
        {'manager_id': 'mean_reversion', 'manager_config_ref': 'mean_reversion_v1', 'score': 10.0, 'avg_return_pct': 1.2, 'avg_sharpe_ratio': 1.0, 'avg_max_drawdown': 4.0, 'benchmark_pass_rate': 0.6, 'avg_strategy_score': 0.7, 'rank': 2},
        {'manager_id': 'defensive_low_vol', 'manager_config_ref': 'defensive_low_vol_v1', 'score': 8.0, 'avg_return_pct': 0.8, 'avg_sharpe_ratio': 1.1, 'avg_max_drawdown': 2.0, 'benchmark_pass_rate': 0.8, 'avg_strategy_score': 0.68, 'rank': 3},
    ],
    'regime_leaderboards': {
        'bull': [{'manager_id': 'momentum', 'rank': 1}],
        'bear': [{'manager_id': 'defensive_low_vol', 'rank': 1}],
        'oscillation': [{'manager_id': 'mean_reversion', 'rank': 1}],
    },
}


def test_governance_selector_returns_dominant_manager_id_as_canonical_field():
    agent = GovernanceSelectorAgent()

    advice = agent.analyze(
        {
            "regime": "oscillation",
            "dominant_manager_id": "momentum",
            "allowed_manager_ids": ["momentum", "mean_reversion", "value_quality"],
            "candidate_manager_ids": ["mean_reversion", "value_quality", "momentum"],
            "candidate_weights": {"mean_reversion": 0.55, "value_quality": 0.3, "momentum": 0.15},
        }
    )

    assert advice["dominant_manager_id"] == "mean_reversion"
    assert advice["candidate_manager_ids"][0] == "mean_reversion"


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


def test_governance_coordinator_holds_current_manager_id_when_cooldown_is_active(tmp_path, monkeypatch):
    coordinator = GovernanceCoordinator(cooldown_cycles=2, hysteresis_margin=0.01, min_confidence=0.5)
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

    decision = coordinator.decide(
        stock_data={},
        cutoff_date='20260310',
        current_manager_id='momentum',
        leaderboard_path=_leaderboard_path(tmp_path),
        allocator_top_n=3,
        allowed_manager_ids=['momentum', 'defensive_low_vol'],
        governance_mode='rule',
        current_cycle_id=5,
        last_governance_change_cycle_id=4,
    )

    assert decision.active_manager_ids == ['momentum']
    assert decision.manager_budget_weights == {'momentum': 1.0}
    assert decision.dominant_manager_id == 'momentum'
    assert decision.metadata['historical']['guardrail_hold'] is True
    assert decision.hold_reason == 'governance_cooldown_active'


def test_governance_coordinator_breaks_cooldown_for_strong_candidate_exception(tmp_path, monkeypatch):
    coordinator = GovernanceCoordinator(
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
                    'manager_id': 'momentum',
                    'manager_config_ref': 'momentum_v1',
                    'score': 9.0,
                    'avg_return_pct': -0.4,
                    'avg_sharpe_ratio': 0.6,
                    'avg_max_drawdown': 7.0,
                    'benchmark_pass_rate': 0.2,
                    'avg_strategy_score': 0.38,
                    'rank': 2,
                    'eligible_for_governance': True,
                },
                {
                    'manager_id': 'mean_reversion',
                    'manager_config_ref': 'mean_reversion_v1',
                    'score': 14.0,
                    'avg_return_pct': 1.9,
                    'avg_sharpe_ratio': 1.3,
                    'avg_max_drawdown': 3.8,
                    'benchmark_pass_rate': 0.82,
                    'avg_strategy_score': 0.76,
                    'rank': 1,
                    'eligible_for_governance': True,
                },
            ],
            'regime_leaderboards': {
                'oscillation': [
                    {
                        'manager_id': 'mean_reversion',
                        'rank': 1,
                        'eligible_for_governance': True,
                    },
                    {
                        'manager_id': 'momentum',
                        'rank': 2,
                        'eligible_for_governance': True,
                    },
                ],
            },
        },
    )

    decision = coordinator.decide(
        stock_data={},
        cutoff_date='20260310',
        current_manager_id='momentum',
        leaderboard_path=leaderboard_path,
        allocator_top_n=3,
        allowed_manager_ids=['momentum', 'mean_reversion'],
        governance_mode='rule',
        current_cycle_id=5,
        last_governance_change_cycle_id=4,
    )

    assert decision.active_manager_ids == ['mean_reversion', 'momentum']
    assert decision.dominant_manager_id == 'mean_reversion'
    assert decision.manager_budget_weights['mean_reversion'] > decision.manager_budget_weights['momentum']
    assert decision.metadata['historical']['guardrail_hold'] is False
    assert decision.metadata['historical']['governance_applied'] is True
    assert decision.metadata['cooldown_exception']['applied'] is True
    assert decision.metadata['cooldown_exception']['reason'] == 'candidate_outperforms_weak_current'


def test_governance_coordinator_supports_off_mode(tmp_path):
    coordinator = GovernanceCoordinator()

    decision = coordinator.decide(
        stock_data={},
        cutoff_date='20260310',
        current_manager_id='momentum',
        leaderboard_path=_leaderboard_path(tmp_path),
        governance_mode='off',
    )

    assert decision.active_manager_ids == ['momentum']
    assert decision.manager_budget_weights == {'momentum': 1.0}
    assert decision.dominant_manager_id == 'momentum'
    assert 'selected_model' not in decision.to_dict()
    assert decision.metadata['historical']['guardrail_hold'] is True
    assert decision.decision_source == 'disabled'


def test_governance_coordinator_holds_current_when_no_qualified_candidates(tmp_path, monkeypatch):
    coordinator = GovernanceCoordinator(cooldown_cycles=2, hysteresis_margin=0.01, min_confidence=0.5)
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
                    'manager_id': 'mean_reversion',
                    'manager_config_ref': 'mean_reversion_v1',
                    'score': -4.0,
                    'avg_return_pct': -1.0,
                    'avg_sharpe_ratio': 0.3,
                    'avg_max_drawdown': 18.0,
                    'benchmark_pass_rate': 0.0,
                    'avg_strategy_score': 0.2,
                    'rank': 0,
                    'eligible_for_governance': False,
                    'deployment_stage': 'candidate',
                    'ineligible_reason': 'quality_gate:block_negative_score',
                },
            ],
            'regime_leaderboards': {},
        },
    )

    decision = coordinator.decide(
        stock_data={},
        cutoff_date='20260310',
        current_manager_id='momentum',
        leaderboard_path=leaderboard_path,
        allocator_top_n=3,
        allowed_manager_ids=['momentum', 'mean_reversion'],
        governance_mode='rule',
    )

    assert decision.active_manager_ids == ['momentum']
    assert decision.manager_budget_weights == {'momentum': 1.0}
    assert decision.dominant_manager_id == 'momentum'
    assert decision.metadata['historical']['guardrail_hold'] is True
    assert decision.hold_reason == 'no_qualified_governance_candidates'
    assert decision.evidence['allocator_quality']['qualified_candidate_count'] == 0
    assert '没有通过质量门的正式候选' in decision.reasoning


def test_governance_coordinator_uses_shadow_regime_prior_fallback_when_no_qualified_candidates(tmp_path, monkeypatch):
    coordinator = GovernanceCoordinator(cooldown_cycles=0, hysteresis_margin=0.01, min_confidence=0.5)
    monkeypatch.setattr(
        coordinator.observer,
        'observe',
        lambda *args, **kwargs: MarketObservation(
            as_of_date='20260310',
            stats={'avg_change_20d': -4.2, 'above_ma20_ratio': 0.32, 'market_breadth': 0.36, 'avg_volatility': 0.024},
        ),
    )
    monkeypatch.setattr(
        coordinator.classifier,
        'classify',
        lambda *args, **kwargs: {'regime': 'bear', 'confidence': 0.83, 'reasoning': '防御优先', 'suggested_exposure': 0.25, 'source': 'rule', 'rule_result': {}, 'agent_result': {}},
    )
    leaderboard_path = _write_leaderboard(
        tmp_path,
        {
            'generated_at': '2026-03-10T00:00:00',
            'entries': [
                {
                    'manager_id': 'defensive_low_vol',
                    'manager_config_ref': 'defensive_low_vol_v1',
                    'score': -2.0,
                    'avg_return_pct': -0.5,
                    'avg_sharpe_ratio': 0.1,
                    'avg_max_drawdown': 10.0,
                    'benchmark_pass_rate': 0.0,
                    'avg_strategy_score': 0.2,
                    'rank': 0,
                    'eligible_for_governance': False,
                    'deployment_stage': 'candidate',
                    'ineligible_reason': 'quality_gate:block_negative_score',
                },
            ],
            'regime_leaderboards': {},
        },
    )

    decision = coordinator.decide(
        stock_data={},
        cutoff_date='20260310',
        current_manager_id='momentum',
        leaderboard_path=leaderboard_path,
        allocator_top_n=3,
        allowed_manager_ids=['mean_reversion', 'value_quality', 'defensive_low_vol'],
        governance_mode='rule',
        shadow_mode=True,
    )

    assert decision.active_manager_ids[0] == 'defensive_low_vol'
    assert decision.dominant_manager_id == 'defensive_low_vol'
    assert decision.metadata['historical']['guardrail_hold'] is False
    assert decision.decision_source == 'shadow_regime_prior'
    assert decision.metadata['shadow_provisional_fallback']['applied'] is True
    assert decision.portfolio_constraints['allowed_manager_ids'] == [
        'momentum',
        'mean_reversion',
        'value_quality',
        'defensive_low_vol',
    ]
    assert 'shadow 专用 provisional fallback' in decision.reasoning
    assert decision.evidence['allocator_quality']['qualified_candidate_count'] == 0


def test_governance_coordinator_shadow_fallback_ignores_invalid_allowlist_entries(tmp_path, monkeypatch):
    coordinator = GovernanceCoordinator(cooldown_cycles=0, hysteresis_margin=0.01, min_confidence=0.5)
    monkeypatch.setattr(
        coordinator.observer,
        'observe',
        lambda *args, **kwargs: MarketObservation(
            as_of_date='20260310',
            stats={'avg_change_20d': -4.2, 'above_ma20_ratio': 0.32, 'market_breadth': 0.36, 'avg_volatility': 0.024},
        ),
    )
    monkeypatch.setattr(
        coordinator.classifier,
        'classify',
        lambda *args, **kwargs: {'regime': 'bear', 'confidence': 0.83, 'reasoning': '防御优先', 'suggested_exposure': 0.25, 'source': 'rule', 'rule_result': {}, 'agent_result': {}},
    )
    leaderboard_path = _write_leaderboard(
        tmp_path,
        {
            'generated_at': '2026-03-10T00:00:00',
            'entries': [],
            'regime_leaderboards': {},
        },
    )

    decision = coordinator.decide(
        stock_data={},
        cutoff_date='20260310',
        current_manager_id='momentum',
        leaderboard_path=leaderboard_path,
        allocator_top_n=3,
        allowed_manager_ids=['bogus_manager'],
        governance_mode='rule',
        shadow_mode=True,
    )

    assert decision.active_manager_ids == ['momentum']
    assert decision.dominant_manager_id == 'momentum'
    assert decision.metadata['historical']['guardrail_hold'] is True
    assert decision.hold_reason == 'no_qualified_governance_candidates'


def test_governance_coordinator_marks_shadow_fallback_hold_as_guardrail_hold(tmp_path, monkeypatch):
    coordinator = GovernanceCoordinator(cooldown_cycles=0, hysteresis_margin=0.01, min_confidence=0.95)
    monkeypatch.setattr(
        coordinator.observer,
        'observe',
        lambda *args, **kwargs: MarketObservation(
            as_of_date='20260310',
            stats={'avg_change_20d': -4.2, 'above_ma20_ratio': 0.32, 'market_breadth': 0.36, 'avg_volatility': 0.024},
        ),
    )
    monkeypatch.setattr(
        coordinator.classifier,
        'classify',
        lambda *args, **kwargs: {'regime': 'bear', 'confidence': 0.60, 'reasoning': '防御优先', 'suggested_exposure': 0.25, 'source': 'rule', 'rule_result': {}, 'agent_result': {}},
    )
    leaderboard_path = _write_leaderboard(
        tmp_path,
        {
            'generated_at': '2026-03-10T00:00:00',
            'entries': [
                {
                    'manager_id': 'defensive_low_vol',
                    'manager_config_ref': 'defensive_low_vol_v1',
                    'score': -2.0,
                    'avg_return_pct': -0.5,
                    'avg_sharpe_ratio': 0.1,
                    'avg_max_drawdown': 10.0,
                    'benchmark_pass_rate': 0.0,
                    'avg_strategy_score': 0.2,
                    'rank': 0,
                    'eligible_for_governance': False,
                    'deployment_stage': 'candidate',
                    'ineligible_reason': 'quality_gate:block_negative_score',
                },
            ],
            'regime_leaderboards': {},
        },
    )

    decision = coordinator.decide(
        stock_data={},
        cutoff_date='20260310',
        current_manager_id='momentum',
        leaderboard_path=leaderboard_path,
        allocator_top_n=3,
        allowed_manager_ids=['momentum', 'defensive_low_vol'],
        governance_mode='rule',
        shadow_mode=True,
    )

    assert decision.dominant_manager_id == 'momentum'
    assert decision.metadata['historical']['guardrail_hold'] is True
    assert decision.decision_source == 'guardrail_hold'


def test_governance_coordinator_blocks_switch_when_candidate_has_style_mismatch(tmp_path, monkeypatch):
    coordinator = GovernanceCoordinator(cooldown_cycles=0, hysteresis_margin=0.01, min_confidence=0.5)
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
        governance_engine,
        'build_allocation_plan',
        lambda *args, **kwargs: AllocationPlan(
            as_of_date='20260310',
            regime='bull',
            active_manager_ids=['defensive_low_vol'],
            manager_budget_weights={'defensive_low_vol': 1.0},
            selected_manager_config_refs={'defensive_low_vol': 'defensive_low_vol_v1'},
            cash_reserve=0.3,
            confidence=0.82,
            reasoning='错误地把防御模型排到了牛市第一。',
            metadata={
                'qualified_candidate_count': 1,
                'failed_quality_entries': [],
                'top_candidates': [
                    {
                        'manager_id': 'defensive_low_vol',
                        'regime_compatibility': 0.35,
                        'regime_score': 18.0,
                    }
                ],
            },
        ),
    )

    decision = coordinator.decide(
        stock_data={},
        cutoff_date='20260310',
        current_manager_id='momentum',
        leaderboard_path=_leaderboard_path(tmp_path),
        allocator_top_n=3,
        allowed_manager_ids=['momentum', 'defensive_low_vol'],
        governance_mode='rule',
    )

    assert decision.active_manager_ids == ['momentum']
    assert decision.dominant_manager_id == 'momentum'
    assert decision.metadata['historical']['guardrail_hold'] is True
    assert decision.hold_reason == 'regime_style_mismatch'
    assert any(
        item['name'] == 'regime_compatibility' and item['passed'] is False
        for item in decision.guardrail_checks
    )
