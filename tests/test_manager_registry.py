from invest_evolution.investment.managers import ManagerRegistry, build_default_manager_registry


def test_default_manager_registry_contains_four_seed_managers():
    registry = build_default_manager_registry()

    assert registry.list_manager_ids() == [
        "momentum",
        "mean_reversion",
        "value_quality",
        "defensive_low_vol",
    ]


def test_registry_builds_runtime_backed_manager():
    registry = ManagerRegistry()
    manager = registry.build_manager("momentum", runtime_overrides={"position_size": 0.12})

    assert manager.spec.manager_id == "momentum"
    assert manager.spec.runtime_id == "momentum"
    assert getattr(manager.runtime, "manager_id", "") == "momentum"
    assert manager.spec.runtime_config_ref.endswith("momentum_v1.yaml")
