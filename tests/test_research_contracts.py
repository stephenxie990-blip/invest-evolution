from invest_evolution.investment.runtimes import create_manager_runtime
from invest_evolution.investment.research import resolve_policy_snapshot


def test_policy_snapshot_version_hash_is_stable():
    model = create_manager_runtime("momentum")

    first = resolve_policy_snapshot(
        manager_runtime=model,
        manager_id="momentum",
        governance_context={
            "governance_mode": "off",
            "active_manager_ids": ["momentum"],
            "dominant_manager_id": "momentum",
        },
        data_window={
            "as_of_date": "20240131",
            "lookback_days": 120,
            "simulation_days": 30,
            "universe_definition": "stock_count=50|min_history_days=60",
        },
    )
    second = resolve_policy_snapshot(
        manager_runtime=model,
        manager_id="momentum",
        governance_context={
            "governance_mode": "off",
            "active_manager_ids": ["momentum"],
            "dominant_manager_id": "momentum",
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
    assert first.manager_id == "momentum"
    assert "governance_context" in first.to_dict()
    assert first.to_dict()["governance_context"]["dominant_manager_id"] == "momentum"
    assert first.to_dict()["manager_config_ref"]



def test_policy_snapshot_version_hash_changes_with_signature_changes():
    model = create_manager_runtime("momentum")

    first = resolve_policy_snapshot(
        manager_runtime=model,
        manager_id="momentum",
        governance_context={
            "dominant_manager_id": "momentum",
            "active_manager_ids": ["momentum"],
            "governance_mode": "off",
        },
        data_window={"as_of_date": "20240131"},
    )
    second = resolve_policy_snapshot(
        manager_runtime=model,
        manager_id="momentum",
        governance_context={
            "dominant_manager_id": "mean_reversion",
            "active_manager_ids": ["mean_reversion", "momentum"],
            "governance_mode": "rule",
        },
        data_window={"as_of_date": "20240131"},
    )

    assert first.version_hash != second.version_hash
    assert first.policy_id != second.policy_id
