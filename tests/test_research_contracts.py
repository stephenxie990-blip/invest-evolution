from invest.models import create_investment_model
from invest.research import resolve_policy_snapshot


def test_policy_snapshot_version_hash_is_stable():
    model = create_investment_model("momentum")

    first = resolve_policy_snapshot(
        investment_model=model,
        routing_context={
            "routing_mode": "off",
            "selected_model": "momentum",
            "selected_config": "momentum_v1",
        },
        data_window={
            "as_of_date": "20240131",
            "lookback_days": 120,
            "simulation_days": 30,
            "universe_definition": "stock_count=50|min_history_days=60",
        },
    )
    second = resolve_policy_snapshot(
        investment_model=model,
        routing_context={
            "routing_mode": "off",
            "selected_model": "momentum",
            "selected_config": "momentum_v1",
        },
        data_window={
            "as_of_date": "20240131",
            "lookback_days": 120,
            "simulation_days": 30,
            "universe_definition": "stock_count=50|min_history_days=60",
        },
    )

    assert first.version_hash == second.version_hash
    assert first.policy_id == second.policy_id
    assert first.signature == second.signature



def test_policy_snapshot_version_hash_changes_with_signature_changes():
    model = create_investment_model("momentum")

    first = resolve_policy_snapshot(
        investment_model=model,
        routing_context={"selected_model": "momentum", "routing_mode": "off"},
        data_window={"as_of_date": "20240131"},
    )
    second = resolve_policy_snapshot(
        investment_model=model,
        routing_context={"selected_model": "mean_reversion", "routing_mode": "rule"},
        data_window={"as_of_date": "20240131"},
    )

    assert first.version_hash != second.version_hash
    assert first.policy_id != second.policy_id
