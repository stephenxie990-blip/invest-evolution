from pathlib import Path

from invest.research import ResearchCaseStore
from invest.research.contracts import OutcomeAttribution, PolicySnapshot, ResearchHypothesis, ResearchSnapshot



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
        model_name="momentum",
        config_name="momentum_v1",
        version_hash="version_1",
        signature={"model_name": "momentum"},
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
            model_name="momentum",
            config_name="momentum_v1",
            version_hash=f"version_{index}",
            signature={"model_name": "momentum", "config_name": "momentum_v1"},
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
        model_name="momentum",
        config_name="momentum_v1",
        as_of_date="20240131",
    )

    assert feedback["sample_count"] == 3
    assert feedback["matched_case_count"] == 3
    assert feedback["recommendation"]["bias"] == "tighten_risk"
    assert "t20_hit_rate_low" in feedback["recommendation"]["reason_codes"]
    assert feedback["subject"]["as_of_date"] == "20240131"
