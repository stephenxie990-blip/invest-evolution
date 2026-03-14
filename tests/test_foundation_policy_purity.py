from invest.foundation.compute.factors import calc_algo_score
from invest.foundation.compute.features import compute_market_stats
from invest.foundation.compute.market_stats import compute_market_stats as compute_market_snapshot_stats
from app.train import SelfLearningController


def test_calc_algo_score_is_neutral_without_profile():
    score = calc_algo_score(3.0, 8.0, '多头', 65.0, '金叉', 0.2, profile=None)
    assert score == 0.0


def test_compute_market_stats_requires_explicit_regime_policy_for_regime_classification():
    import pandas as pd

    up = pd.DataFrame({'trade_date': [f'202401{day:02d}' for day in range(1, 40)], 'close': list(range(1, 40))})
    stock_data = {'A': up, 'B': up, 'C': up}
    out = compute_market_stats(stock_data, '20240131')
    assert out['regime_hint'] == 'unknown'

    policy = {
        'bull_avg_change_20d': 3.0,
        'bull_above_ma20_ratio': 0.55,
        'bear_avg_change_20d': -3.0,
        'bear_above_ma20_ratio': 0.45,
        'default_regime': 'oscillation',
    }
    out2 = compute_market_stats(stock_data, '20240131', regime_policy=policy)
    assert out2['regime_hint'] == 'bull'


def test_features_compute_market_stats_is_stable_facade_for_market_snapshot_module():
    import pandas as pd

    up = pd.DataFrame({'trade_date': [f'202401{day:02d}' for day in range(1, 40)], 'close': list(range(1, 40))})
    stock_data = {'A': up, 'B': up, 'C': up}

    assert compute_market_stats(stock_data, '20240131') == compute_market_snapshot_stats(stock_data, '20240131')


def test_model_config_exposes_explicit_market_and_summary_logic(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / 'out'),
        meeting_log_dir=str(tmp_path / 'meetings'),
        config_audit_log_path=str(tmp_path / 'state' / 'audit.jsonl'),
        config_snapshot_dir=str(tmp_path / 'state' / 'snapshots'),
    )
    summary_scoring = controller.investment_model.config_section('summary_scoring', {})
    market_regime = controller.investment_model.config_section('market_regime', {})

    assert summary_scoring['logic']['ma_bull_ratio'] == 1.01
    assert summary_scoring['logic']['ma_bear_ratio'] == 0.99
    assert market_regime['bull_avg_change_20d'] == 3.0
    assert market_regime['default_regime'] == 'oscillation'
