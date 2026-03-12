from app.commander import CommanderConfig, CommanderRuntime
from invest.research.contracts import OutcomeAttribution, PolicySnapshot, ResearchHypothesis, ResearchSnapshot


def _build_runtime(tmp_path):
    cfg = CommanderConfig(
        workspace=tmp_path / 'workspace',
        strategy_dir=tmp_path / 'strategies',
        state_file=tmp_path / 'state' / 'state.json',
        cron_store=tmp_path / 'state' / 'cron.json',
        memory_store=tmp_path / 'memory' / 'memory.jsonl',
        plugin_dir=tmp_path / 'plugins',
        bridge_inbox=tmp_path / 'inbox',
        bridge_outbox=tmp_path / 'outbox',
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    return CommanderRuntime(cfg)


def _seed_case(runtime):
    snapshot = ResearchSnapshot(
        snapshot_id='snapshot_1',
        as_of_date='20240131',
        scope='single_security',
        security={'code': 'sh.600001', 'name': 'FooBank'},
        universe={'size': 1, 'available_codes': ['sh.600001'], 'summary_top5': []},
        market_context={'regime': 'bull', 'cash_reserve': 0.2, 'model_name': 'momentum', 'config_name': 'default', 'market_stats': {}, 'routing_context': {}},
        cross_section_context={'selected_by_policy': True, 'rank': 1, 'percentile': 1.0, 'threshold_score': 0.1, 'threshold_gap': 0.1, 'threshold_gap_is_approximate': False, 'selected_count': 1, 'universe_size': 1, 'top_selected_codes': ['sh.600001']},
        feature_snapshot={'summary': {}, 'signal': {}, 'legacy_signals': {}, 'evidence': [], 'factor_values': {}, 'metadata': {}},
        data_lineage={},
        readiness={'has_model_output': True},
        metadata={'query_code': 'sh.600001'},
    )
    policy = PolicySnapshot(
        policy_id='policy_demo',
        model_name='momentum',
        config_name='default',
        params={'position_size': 0.2},
        routing_context={'regime': 'bull'},
        version_hash='hash_demo',
        metadata={},
    )
    hypothesis = ResearchHypothesis(
        hypothesis_id='hyp_1',
        snapshot_id='snapshot_1',
        policy_id='policy_demo',
        stance='buy',
        score=0.6,
        rank=1,
        percentile=1.0,
        selected_by_policy=True,
        entry_rule={'summary': 'x'},
        invalidation_rule={'summary': 'y'},
        de_risk_rule={'summary': 'z'},
        supporting_factors=['trend'],
        contradicting_factors=[],
        scenario_distribution={'horizons': {}},
        expected_return_interval={'T+20': {'p25': 0.02, 'p75': 0.12}},
        confidence=0.6,
        evaluation_protocol={'horizons': ['T+20']},
        metadata={},
    )
    runtime.research_case_store.save_case(snapshot=snapshot, policy=policy, hypothesis=hypothesis)
    attribution = OutcomeAttribution(
        attribution_id='attr_1',
        hypothesis_id='hyp_1',
        thesis_result='hit',
        horizon_results={'T+20': {'label': 'hit', 'return_pct': 0.12}},
        calibration_metrics={'positive_return_brier': 0.12},
        metadata={'score_clock': {'evaluated_at': '2024-02-20T00:00:00'}},
    )
    runtime.research_case_store.save_attribution(attribution, metadata={'policy_id': 'policy_demo', 'code': 'sh.600001', 'as_of_date': '20240131'})


def test_list_research_cases_returns_bounded_workflow_payload(tmp_path):
    runtime = _build_runtime(tmp_path)
    _seed_case(runtime)

    payload = runtime.list_research_cases(limit=10, policy_id='policy_demo')

    assert payload['status'] == 'ok'
    assert payload['entrypoint']['runtime_tool'] == 'invest_research_cases'
    assert payload['protocol']['domain'] == 'research'
    assert payload['items'][0]['policy_id'] == 'policy_demo'
    assert payload['task_bus']['planner']['operation'] == 'list_research_cases'


def test_get_research_calibration_returns_report(tmp_path):
    runtime = _build_runtime(tmp_path)
    _seed_case(runtime)

    payload = runtime.get_research_calibration(policy_id='policy_demo')

    assert payload['status'] == 'ok'
    assert payload['report']['subject']['policy_id'] == 'policy_demo'
    assert payload['entrypoint']['runtime_tool'] == 'invest_research_calibration'
    assert payload['feedback']['summary']
