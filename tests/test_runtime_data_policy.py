import pytest

from invest_evolution.config.control_plane import clear_control_plane_cache
from invest_evolution.market_data.manager import DataManager, DataSourceUnavailableError


class _ForbiddenOnlineLoader:
    def __init__(self, *args, **kwargs):
        raise AssertionError('online loader should not be constructed when control plane disables fallback')


def test_runtime_data_policy_disables_online_fallback(monkeypatch, tmp_path):
    control_plane = tmp_path / 'control_plane.yaml'
    control_plane.write_text(
        '\n'.join([
            'data:',
            '  runtime_policy:',
            '    allow_online_fallback: false',
            '    allow_capital_flow_sync: false',
        ]),
        encoding='utf-8',
    )
    monkeypatch.setenv('INVEST_CONTROL_PLANE_PATH', str(control_plane))
    monkeypatch.setattr('invest_evolution.market_data.manager.EvolutionDataLoader', _ForbiddenOnlineLoader)
    clear_control_plane_cache()

    manager = DataManager(db_path=str(tmp_path / 'missing.sqlite'), prefer_offline=False)
    monkeypatch.setattr(manager, 'check_training_readiness', lambda *args, **kwargs: {'issues': [], 'suggestions': []})

    with pytest.raises(DataSourceUnavailableError) as exc_info:
        manager.load_stock_data(cutoff_date='20240131', stock_count=5, min_history_days=60)

    assert exc_info.value.payload['online_error'] == 'disabled_by_control_plane'
