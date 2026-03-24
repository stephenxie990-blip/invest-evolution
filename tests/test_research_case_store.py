from pathlib import Path

from invest_evolution.config import PROJECT_ROOT
from invest_evolution.investment.research import ResearchCaseStore
from invest_evolution.investment.research.analysis import OutcomeAttribution, PolicySnapshot, ResearchHypothesis, ResearchSnapshot



def test_research_case_store_can_filter_cases_and_write_calibration_report(tmp_path: Path):
    store = ResearchCaseStore(tmp_path)
    snapshot = ResearchSnapshot(
        snapshot_id="snapshot_1",
        as_of_date="20240131",
        scope="single_security",
        security={"code": "sh.600001", "name": "FooBank"},
        market_context={"regime": "bull"},
        cross_section_context={"rank": 1, "percentile": 1.0, "selected_by_policy": True},
    )
    policy = PolicySnapshot(
        policy_id="policy_1",
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        version_hash="version_1",
        signature={"manager_id": "momentum"},
    )
    hypothesis = ResearchHypothesis(
        hypothesis_id="hypothesis_1",
        snapshot_id=snapshot.snapshot_id,
        policy_id=policy.policy_id,
        stance="候选买入",
        score=88.0,
        scenario_distribution={
            "horizons": {
                "T+20": {
                    "positive_return_probability": 0.7,
                    "interval": {"p25": 1.0, "p50": 4.0, "p75": 8.0},
                }
            }
        },
        evaluation_protocol={"clock": ["T+20"]},
    )

    case_record = store.save_case(snapshot=snapshot, policy=policy, hypothesis=hypothesis)
    attribution = OutcomeAttribution(
        attribution_id="attribution_1",
        hypothesis_id=hypothesis.hypothesis_id,
        thesis_result="hit",
        horizon_results={
            "T+20": {
                "label": "hit",
                "return_pct": 6.0,
                "entry_triggered": True,
                "invalidation_triggered": False,
                "de_risk_triggered": False,
            }
        },
        calibration_metrics={"positive_return_brier": 0.09},
    )
    store.save_attribution(attribution)

    matches = store.find_cases(
        policy_id=policy.policy_id,
        symbol="sh.600001",
        as_of_date="20240131",
        horizon="T+20",
    )

    assert len(matches) == 1
    assert matches[0]["research_case_id"] == case_record["research_case_id"]
    assert matches[0]["attribution"]["thesis_result"] == "hit"

    report = store.write_calibration_report(policy_id=policy.policy_id)
    assert report["sample_count"] == 1
    assert report["horizons"]["T+20"]["hit_rate"] == 1.0
    assert report["scenario_hit_distribution"]["base"] == 1
    assert Path(report["path"]).exists()


def test_research_case_store_build_training_feedback_applies_as_of_filter_and_bias(tmp_path: Path):
    store = ResearchCaseStore(tmp_path)

    def seed_case(index: int, as_of_date: str, label: str, return_pct: float, brier: float) -> None:
        snapshot = ResearchSnapshot(
            snapshot_id=f"snapshot_{index}",
            as_of_date=as_of_date,
            scope="single_security",
            security={"code": f"sh.600{index:03d}", "name": f"Stock{index}"},
            market_context={"regime": "bear"},
            cross_section_context={"rank": index, "percentile": 0.5, "selected_by_policy": True},
        )
        policy = PolicySnapshot(
            policy_id=f"policy_{index}",
            manager_id="momentum",
            manager_config_ref="momentum_v1",
            version_hash=f"version_{index}",
            signature={"manager_id": "momentum", "manager_config_ref": "momentum_v1"},
        )
        hypothesis = ResearchHypothesis(
            hypothesis_id=f"hypothesis_{index}",
            snapshot_id=snapshot.snapshot_id,
            policy_id=policy.policy_id,
            stance="候选买入",
            score=70 + index,
            scenario_distribution={
                "horizons": {
                    "T+20": {
                        "positive_return_probability": 0.55,
                        "interval": {"p25": -2.0, "p50": 1.0, "p75": 4.0},
                    }
                }
            },
            evaluation_protocol={"clock": ["T+20"]},
        )
        store.save_case(snapshot=snapshot, policy=policy, hypothesis=hypothesis)
        store.save_attribution(
            OutcomeAttribution(
                attribution_id=f"attribution_{index}",
                hypothesis_id=hypothesis.hypothesis_id,
                thesis_result=label,
                horizon_results={
                    "T+20": {
                        "label": label,
                        "return_pct": return_pct,
                        "entry_triggered": True,
                        "invalidation_triggered": label == "invalidated",
                        "de_risk_triggered": False,
                    }
                },
                calibration_metrics={"positive_return_brier": brier},
            )
        )

    seed_case(1, "20240105", "hit", 3.0, 0.08)
    seed_case(2, "20240112", "invalidated", -4.0, 0.18)
    seed_case(3, "20240119", "timeout", -1.0, 0.22)
    seed_case(4, "20240220", "hit", 5.0, 0.05)

    feedback = store.build_training_feedback(
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        as_of_date="20240131",
    )

    assert feedback["sample_count"] == 3
    assert feedback["matched_case_count"] == 3
    assert feedback["recommendation"]["bias"] == "tighten_risk"
    assert "t20_hit_rate_low" in feedback["recommendation"]["reason_codes"]
    assert feedback["subject"]["as_of_date"] == "20240131"


def test_research_case_store_build_training_feedback_prefers_requested_regime_when_covered(tmp_path: Path):
    store = ResearchCaseStore(tmp_path)

    def seed_case(index: int, *, regime: str, as_of_date: str, label: str, return_pct: float) -> None:
        snapshot = ResearchSnapshot(
            snapshot_id=f"snapshot_regime_{index}",
            as_of_date=as_of_date,
            scope="single_security",
            security={"code": f"sh.601{index:03d}", "name": f"Stock{index}"},
            market_context={"regime": regime},
            cross_section_context={"rank": index, "percentile": 0.6, "selected_by_policy": True},
        )
        policy = PolicySnapshot(
            policy_id=f"policy_regime_{index}",
            manager_id="momentum",
            manager_config_ref="momentum_v1",
            version_hash=f"version_regime_{index}",
            signature={"manager_id": "momentum", "manager_config_ref": "momentum_v1"},
        )
        hypothesis = ResearchHypothesis(
            hypothesis_id=f"hypothesis_regime_{index}",
            snapshot_id=snapshot.snapshot_id,
            policy_id=policy.policy_id,
            stance="候选买入",
            score=75 + index,
            scenario_distribution={
                "horizons": {
                    "T+20": {
                        "positive_return_probability": 0.62,
                        "interval": {"p25": -1.0, "p50": 2.0, "p75": 5.0},
                    }
                }
            },
            evaluation_protocol={"clock": ["T+20"]},
        )
        store.save_case(snapshot=snapshot, policy=policy, hypothesis=hypothesis)
        store.save_attribution(
            OutcomeAttribution(
                attribution_id=f"attribution_regime_{index}",
                hypothesis_id=hypothesis.hypothesis_id,
                thesis_result=label,
                horizon_results={
                    "T+20": {
                        "label": label,
                        "return_pct": return_pct,
                        "entry_triggered": True,
                        "invalidation_triggered": label == "invalidated",
                        "de_risk_triggered": False,
                    }
                },
                calibration_metrics={"positive_return_brier": 0.12},
            )
        )

    seed_case(1, regime="bull", as_of_date="20240105", label="hit", return_pct=4.0)
    seed_case(2, regime="bull", as_of_date="20240112", label="hit", return_pct=3.5)
    seed_case(3, regime="bull", as_of_date="20240119", label="miss", return_pct=-0.5)
    seed_case(4, regime="bear", as_of_date="20240122", label="invalidated", return_pct=-4.0)

    feedback = store.build_training_feedback(
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        as_of_date="20240131",
        regime="bull",
    )

    assert feedback["scope"]["effective_scope"] == "regime"
    assert feedback["subject"]["regime"] == "bull"
    assert feedback["sample_count"] == 3
    assert feedback["overall_feedback"]["sample_count"] == 4
    assert feedback["regime_breakdown"]["bull"]["sample_count"] == 3
    assert feedback["requested_regime_feedback"]["recommendation"]["bias"] == "maintain"


def test_research_case_store_build_training_feedback_does_not_fallback_to_overall_when_requested_regime_is_missing(
    tmp_path: Path,
):
    store = ResearchCaseStore(tmp_path)

    for index in range(1, 4):
        snapshot = ResearchSnapshot(
            snapshot_id=f"snapshot_missing_regime_{index}",
            as_of_date=f"202401{index:02d}",
            scope="single_security",
            security={"code": f"sh.603{index:03d}", "name": f"Stock{index}"},
            market_context={"regime": "bear"},
            cross_section_context={"rank": index, "percentile": 0.5, "selected_by_policy": True},
        )
        policy = PolicySnapshot(
            policy_id=f"policy_missing_regime_{index}",
            manager_id="momentum",
            manager_config_ref="momentum_v1",
            version_hash=f"version_missing_regime_{index}",
            signature={"manager_id": "momentum", "manager_config_ref": "momentum_v1"},
        )
        hypothesis = ResearchHypothesis(
            hypothesis_id=f"hypothesis_missing_regime_{index}",
            snapshot_id=snapshot.snapshot_id,
            policy_id=policy.policy_id,
            stance="候选买入",
            score=70 + index,
            scenario_distribution={
                "horizons": {
                    "T+20": {
                        "positive_return_probability": 0.55,
                        "interval": {"p25": -2.0, "p50": 1.0, "p75": 4.0},
                    }
                }
            },
            evaluation_protocol={"clock": ["T+20"]},
        )
        store.save_case(snapshot=snapshot, policy=policy, hypothesis=hypothesis)
        store.save_attribution(
            OutcomeAttribution(
                attribution_id=f"attribution_missing_regime_{index}",
                hypothesis_id=hypothesis.hypothesis_id,
                thesis_result="invalidated",
                horizon_results={
                    "T+20": {
                        "label": "invalidated",
                        "return_pct": -4.0,
                        "entry_triggered": True,
                        "invalidation_triggered": True,
                        "de_risk_triggered": False,
                    }
                },
                calibration_metrics={"positive_return_brier": 0.18},
            )
        )

    feedback = store.build_training_feedback(
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        as_of_date="20240131",
        regime="bull",
    )

    assert feedback["scope"]["effective_scope"] == "requested_regime_unavailable"
    assert feedback["scope"]["actionable"] is False
    assert feedback["scope"]["unavailable_reason"] == "requested_regime_unavailable"
    assert feedback["sample_count"] == 0
    assert feedback["overall_feedback"]["sample_count"] == 3
    assert feedback["recommendation"]["bias"] == "maintain"
    assert feedback["requested_regime_feedback"] == {}


def test_research_case_store_build_training_feedback_matches_short_name_against_absolute_runtime_config_path(
    tmp_path: Path,
):
    store = ResearchCaseStore(tmp_path)
    runtime_config_path = (
        PROJECT_ROOT
        / "src"
        / "invest_evolution"
        / "investment"
        / "runtimes"
        / "configs"
        / "momentum_v1.yaml"
    )

    snapshot = ResearchSnapshot(
        snapshot_id="snapshot_abs_cfg",
        as_of_date="20240131",
        scope="single_security",
        security={"code": "sh.600888", "name": "CfgPath"},
        market_context={"regime": "bull"},
        cross_section_context={"rank": 1, "percentile": 0.9, "selected_by_policy": True},
    )
    policy = PolicySnapshot(
        policy_id="policy_abs_cfg",
        manager_id="momentum",
        manager_config_ref=str(runtime_config_path),
        version_hash="version_abs_cfg",
        signature={"manager_id": "momentum", "manager_config_ref": str(runtime_config_path)},
    )
    hypothesis = ResearchHypothesis(
        hypothesis_id="hypothesis_abs_cfg",
        snapshot_id=snapshot.snapshot_id,
        policy_id=policy.policy_id,
        stance="候选买入",
        score=88.0,
        scenario_distribution={
            "horizons": {
                "T+20": {
                    "positive_return_probability": 0.72,
                    "interval": {"p25": 1.0, "p50": 3.0, "p75": 6.0},
                }
            }
        },
        evaluation_protocol={"clock": ["T+20"]},
    )
    store.save_case(snapshot=snapshot, policy=policy, hypothesis=hypothesis)
    store.save_attribution(
        OutcomeAttribution(
            attribution_id="attribution_abs_cfg",
            hypothesis_id=hypothesis.hypothesis_id,
            thesis_result="hit",
            horizon_results={
                "T+20": {
                    "label": "hit",
                    "return_pct": 4.2,
                    "entry_triggered": True,
                    "invalidation_triggered": False,
                    "de_risk_triggered": False,
                }
            },
            calibration_metrics={"positive_return_brier": 0.09},
        )
    )

    feedback = store.build_training_feedback(
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        as_of_date="20240131",
    )

    assert feedback["sample_count"] == 1
    assert feedback["matched_case_count"] == 1
