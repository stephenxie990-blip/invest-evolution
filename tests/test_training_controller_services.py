import json
from types import SimpleNamespace
from typing import cast

from invest_evolution.config import config
from invest_evolution.application.train import SelfLearningController, TrainingResult
from invest_evolution.application.training.controller import (
    TrainingLLMRuntimeService,
)
from invest_evolution.application.training.controller import TrainingCycleContext, TrainingCycleDataService, TrainingDataLoadResult
from invest_evolution.application.training.controller import TrainingExecutionService
from invest_evolution.application.training.controller import TrainingLifecycleService
from invest_evolution.application.training.execution import TrainingABService
from invest_evolution.application.training.observability import FreezeGateService
from invest_evolution.application.training.observability import TrainingObservabilityService
from invest_evolution.application.training.persistence import TrainingPersistenceService
from invest_evolution.application.training.policy import TrainingExperimentService
from invest_evolution.application.training.execution import TrainingOutcomeService
from invest_evolution.application.training.policy import TrainingPolicyService
from invest_evolution.application.training.research import TrainingFeedbackService, TrainingResearchService
from invest_evolution.application.training.review import TrainingReviewService
from invest_evolution.application.training.review import TrainingReviewStageResult, TrainingReviewStageService
from invest_evolution.application.training.execution import TrainingSelectionResult, TrainingSelectionService
from invest_evolution.application.training.policy import TrainingGovernanceService
from invest_evolution.application.training.policy import governance_from_controller
from invest_evolution.application.training.execution import (
    build_lineage_record,
    build_promotion_record,
    controller_default_manager_config_ref,
    controller_default_manager_id,
)
from invest_evolution.application.training.observability import build_selection_boundary_projection
from invest_evolution.application.training.execution import TrainingSimulationService
from invest_evolution.application.training.review_contracts import (
    ReviewStageEnvelope,
    SimulationStageEnvelope,
)
from invest_evolution.application.training.controller import TrainingSessionState
from invest_evolution.application.training.policy import runtime_config_projection_from_live_config
from invest_evolution.investment.evolution import EvolutionService


def _make_controller(tmp_path):
    return SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )


def _make_feedback(*, bias: str = 'tighten_risk'):
    return {
        'sample_count': 8,
        'recommendation': {
            'bias': bias,
            'summary': f'feedback:{bias}',
        },
        'horizons': {
            'T+20': {'hit_rate': 0.30, 'invalidation_rate': 0.40, 'interval_hit_rate': 0.30},
            'T+60': {'hit_rate': 0.42, 'invalidation_rate': 0.36, 'interval_hit_rate': 0.35},
        },
        'brier_like_direction_score': 0.31,
    }


def test_controller_exposes_training_services(tmp_path):
    controller = _make_controller(tmp_path)

    assert isinstance(controller.training_feedback_service, TrainingFeedbackService)
    assert isinstance(controller.training_experiment_service, TrainingExperimentService)
    assert isinstance(controller.training_llm_runtime_service, TrainingLLMRuntimeService)
    assert isinstance(controller.freeze_gate_service, FreezeGateService)
    assert isinstance(controller.training_persistence_service, TrainingPersistenceService)
    assert isinstance(controller.training_cycle_data_service, TrainingCycleDataService)
    assert isinstance(controller.training_execution_service, TrainingExecutionService)
    assert isinstance(controller.training_lifecycle_service, TrainingLifecycleService)
    assert isinstance(controller.training_observability_service, TrainingObservabilityService)
    assert isinstance(controller.training_outcome_service, TrainingOutcomeService)
    assert isinstance(controller.training_ab_service, TrainingABService)
    assert isinstance(controller.training_policy_service, TrainingPolicyService)
    assert isinstance(controller.training_review_service, TrainingReviewService)
    assert isinstance(controller.training_review_stage_service, TrainingReviewStageService)
    assert isinstance(controller.training_selection_service, TrainingSelectionService)
    assert isinstance(controller.training_governance_service, TrainingGovernanceService)
    assert isinstance(controller.training_simulation_service, TrainingSimulationService)
    assert isinstance(controller.evolution_service, EvolutionService)
    assert controller.last_governance_decision == {}


def test_controller_scopes_runtime_generations_to_output_dir(tmp_path):
    controller = _make_controller(tmp_path)

    assert controller.runtime_config_mutator.generations_dir == controller.output_dir / 'runtime_generations'
    assert str(controller.runtime_config_mutator.generations_dir).startswith(str(controller.output_dir))
    assert 'data/evolution/generations' not in str(controller.runtime_config_mutator.generations_dir)


def test_manager_runtime_helpers_prefer_session_state():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id='value_quality',
            default_manager_config_ref='configs/value_quality.yaml',
        ),
        default_manager_id='momentum',
        default_manager_config_ref='configs/momentum.yaml',
    )

    assert controller_default_manager_id(controller) == 'value_quality'
    assert controller_default_manager_config_ref(controller) == 'configs/value_quality.yaml'


def test_runtime_config_projection_preserves_config_precedence_and_owner_fallbacks(monkeypatch):
    monkeypatch.setattr(config, 'default_manager_id', '')
    monkeypatch.setattr(config, 'default_manager_config_ref', '')
    monkeypatch.setattr(config, 'allocator_enabled', False)
    monkeypatch.setattr(config, 'allocator_top_n', 0)
    monkeypatch.setattr(config, 'manager_arch_enabled', False)
    monkeypatch.setattr(config, 'manager_active_ids', [])
    monkeypatch.setattr(config, 'manager_budget_weights', {})
    monkeypatch.setattr(config, 'governance_enabled', False)
    monkeypatch.setattr(config, 'governance_mode', '')
    monkeypatch.setattr(config, 'governance_allowed_manager_ids', [])
    monkeypatch.setattr(config, 'governance_cooldown_cycles', 0)
    monkeypatch.setattr(config, 'governance_min_confidence', 0.0)
    monkeypatch.setattr(config, 'governance_hysteresis_margin', 0.0)
    monkeypatch.setattr(config, 'governance_agent_override_enabled', False)
    monkeypatch.setattr(config, 'governance_agent_override_max_gap', 0.0)
    monkeypatch.setattr(config, 'governance_policy', {})

    owner = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id='value_quality',
            default_manager_config_ref='configs/value_quality.yaml',
        ),
        allocator_enabled=True,
        allocator_top_n=6,
        manager_arch_enabled=True,
        manager_active_ids=['momentum'],
        manager_budget_weights={'momentum': 1.0},
        governance_enabled=True,
        governance_mode='hybrid',
        governance_allowed_manager_ids=['value_quality'],
        governance_cooldown_cycles=9,
        governance_min_confidence=0.77,
        governance_hysteresis_margin=0.13,
        governance_agent_override_enabled=True,
        governance_agent_override_max_gap=0.24,
        governance_policy={'bull_avg_change_20d': 4.2},
    )

    projection = runtime_config_projection_from_live_config(owner)

    assert projection['default_manager_id'] == 'value_quality'
    assert projection['default_manager_config_ref'] == 'configs/value_quality.yaml'
    assert projection['allocator_enabled'] is False
    assert projection['allocator_top_n'] == 6
    assert projection['manager_arch_enabled'] is False
    assert projection['manager_active_ids'] == []
    assert projection['manager_budget_weights'] == {}
    assert projection['governance_enabled'] is False
    assert projection['governance_mode'] == 'hybrid'
    assert projection['governance_allowed_manager_ids'] == []
    assert projection['governance_cooldown_cycles'] == 9
    assert projection['governance_min_confidence'] == 0.77
    assert projection['governance_hysteresis_margin'] == 0.13
    assert projection['governance_agent_override_enabled'] is False
    assert projection['governance_agent_override_max_gap'] == 0.24
    assert projection['governance_policy'] == {}


def test_governance_from_controller_prefers_session_state():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            last_governance_decision={'regime': 'bear', 'dominant_manager_id': 'defensive'},
        ),
        last_governance_decision={'regime': 'bull', 'dominant_manager_id': 'momentum'},
    )

    payload = governance_from_controller(controller)

    assert payload['regime'] == 'bear'
    assert payload['dominant_manager_id'] == 'defensive'


def test_training_execution_service_returns_none_when_selection_stage_short_circuits():
    service = TrainingExecutionService()
    captured = {}

    class DummySelectionService:
        @staticmethod
        def run_selection_stage(owner, **kwargs):
            captured['selection_owner'] = owner
            captured['selection_kwargs'] = dict(kwargs)
            return None

    class DummyController:
        experiment_allowed_manager_ids = []
        default_manager_id = 'momentum'
        training_selection_service = DummySelectionService()

        @staticmethod
        def _maybe_apply_allocator(stock_data, cutoff_date, cycle_id):
            captured['allocator'] = {
                'stock_data': dict(stock_data),
                'cutoff_date': cutoff_date,
                'cycle_id': cycle_id,
            }

        @staticmethod
        def _emit_agent_status(*args, **kwargs):
            captured['agent_status'] = (args, kwargs)

        @staticmethod
        def _emit_module_log(*args, **kwargs):
            captured['module_log'] = (args, kwargs)

    result = service.execute_loaded_cycle(
        DummyController(),
        result_factory=TrainingResult,
        optimization_event_factory=SimpleNamespace,
        cycle_id=9,
        cutoff_date='20240201',
        stock_data={'sh.600519': {'rows': 5}},
        diagnostics={'ready': True},
        requested_data_mode='offline',
        effective_data_mode='offline',
        llm_mode='dry_run',
        degraded=False,
        degrade_reason='',
        data_mode='offline',
        llm_used=False,
        optimization_events=[],
    )

    assert result is None
    assert captured['allocator']['cycle_id'] == 9
    assert captured['selection_kwargs']['cycle_id'] == 9
    assert captured['selection_kwargs']['cutoff_date'] == '20240201'


def test_training_execution_service_outcome_snapshot_accepts_contract_payloads():
    service = TrainingOutcomeService()
    simulation = SimulationStageEnvelope.from_structured_inputs(
        cycle_id=9,
        cutoff_date='20240201',
        strategy_scores={'overall_score': 0.72},
    )
    review = ReviewStageEnvelope.from_structured_inputs(
        simulation=simulation,
        analysis='review-ok',
    )
    run_context = {
        'basis_stage': 'post_cycle_result',
        'subject_type': 'manager_portfolio',
        'active_runtime_config_ref': 'configs/active.yaml',
        'candidate_runtime_config_ref': 'configs/candidate.yaml',
    }
    promotion_record = build_promotion_record(
        cycle_id=9,
        run_context=run_context,
        optimization_events=[],
    )
    lineage_record = build_lineage_record(
        SimpleNamespace(),
        cycle_id=9,
        manager_output=None,
        run_context=run_context,
        optimization_events=[],
    )

    snapshots = service._build_outcome_stage_snapshots(
        review_envelope=review,
        cycle_id=9,
        execution_snapshot={'basis_stage': 'post_cycle_result'},
        run_context=run_context,
        promotion_record=promotion_record,
        lineage_record=lineage_record,
        realism_metrics={'trade_record_count': 1},
    )
    outcome_snapshot = dict(snapshots.get('outcome') or {})
    promotion_record = outcome_snapshot.get('promotion_record')
    lineage_record = outcome_snapshot.get('lineage_record')

    assert outcome_snapshot['stage'] == 'outcome'
    assert isinstance(promotion_record, dict)
    assert isinstance(lineage_record, dict)
    assert promotion_record['cycle_id'] == 9
    assert lineage_record['cycle_id'] == 9


def test_training_execution_service_cycle_payload_accepts_contract_payloads():
    service = TrainingOutcomeService()
    simulation = SimulationStageEnvelope.from_structured_inputs(
        cycle_id=10,
        cutoff_date='20240202',
        strategy_scores={'overall_score': 0.81},
    )
    review = ReviewStageEnvelope.from_structured_inputs(
        simulation=simulation,
        analysis='review-pass',
        review_decision={'reasoning': 'keep'},
    )
    run_context = {
        'basis_stage': 'post_cycle_result',
        'subject_type': 'manager_portfolio',
        'active_runtime_config_ref': 'configs/active.yaml',
        'candidate_runtime_config_ref': 'configs/candidate.yaml',
    }
    promotion_record = build_promotion_record(
        cycle_id=10,
        run_context=run_context,
        optimization_events=[],
    )
    lineage_record = build_lineage_record(
        SimpleNamespace(),
        cycle_id=10,
        manager_output=None,
        run_context=run_context,
        optimization_events=[],
    )

    payload = service._build_cycle_result_payload(
        cycle_id=10,
        cutoff_date='20240202',
        selected=['sh.600519'],
        sim_result=SimpleNamespace(initial_capital=100000.0, final_value=102000.0, return_pct=2.0),
        is_profit=True,
        trade_dicts=[],
        execution_snapshot={'runtime_overrides': {'seed': 7}},
        review_envelope=review,
        data_mode='offline',
        requested_data_mode='offline',
        effective_data_mode='offline',
        llm_mode='dry_run',
        degraded=False,
        degrade_reason='',
        selection_mode='manager_portfolio',
        agent_used=False,
        llm_used=False,
        benchmark_passed=True,
        simulation_envelope=simulation,
        review_applied=True,
        config_snapshot_path='snapshots/cycle_10.json',
        optimization_events=[],
        audit_tags={},
        execution_defaults={},
        governance_decision={},
        research_feedback={},
        research_artifacts={},
        ab_comparison={},
        experiment_spec={},
        run_context=run_context,
        promotion_record=promotion_record,
        lineage_record=lineage_record,
        manager_results_payload=[],
        portfolio_payload={},
        portfolio_attribution_payload={},
        manager_review_report={},
        allocation_review_report={},
        dominant_manager_id='value_quality',
        compatibility_fields={},
        realism_metrics={'trade_record_count': 0},
        stage_snapshots={'outcome': {'stage': 'outcome'}},
        validation_report={},
        peer_comparison_report={},
        judge_report={},
    )

    assert payload['promotion_record']['cycle_id'] == 10
    assert payload['lineage_record']['cycle_id'] == 10
    assert payload['stage_snapshots']['outcome']['stage'] == 'outcome'


def test_thinking_excerpt_delegates_to_observability_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_excerpt(reasoning, limit=200):
        captured['reasoning'] = reasoning
        captured['limit'] = limit
        return 'delegated-thinking'

    monkeypatch.setattr(controller.training_observability_service, 'thinking_excerpt', fake_excerpt)

    payload = {'reasoning': 'risk control'}
    result = controller._thinking_excerpt(payload, limit=42)  # pylint: disable=protected-access

    assert result == 'delegated-thinking'
    assert captured['reasoning'] == payload
    assert captured['limit'] == 42


def test_training_observability_service_marks_cycle_skipped():
    service = TrainingObservabilityService()
    emitted = []
    logs = []

    class DummyController:
        last_cycle_meta = {}

        @staticmethod
        def _emit_module_log(*args, **kwargs):
            logs.append((args, kwargs))

    controller = DummyController()
    service.mark_cycle_skipped(
        controller,
        event_emitter=lambda event_type, payload: emitted.append((event_type, payload)),
        cycle_id=12,
        cutoff_date='20240201',
        stage='selection',
        reason='无可交易标的',
        suggestions=['扩大样本窗口'],
    )

    assert controller.last_cycle_meta['cycle_id'] == 12
    assert controller.last_cycle_meta['stage'] == 'selection'
    assert emitted[0][0] == 'cycle_skipped'
    assert emitted[0][1]['reason'] == '无可交易标的'
    assert logs[0][0][0] == 'selection'


def test_feedback_plan_delegates_to_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_build(owner, feedback, *, cycle_id):
        captured['owner'] = owner
        captured['cycle_id'] = cycle_id
        captured['feedback'] = dict(feedback)
        return {'trigger': 'research_feedback', 'summary': 'delegated'}

    monkeypatch.setattr(controller.training_feedback_service, 'build_feedback_optimization_plan', fake_build)

    payload = controller._build_feedback_optimization_plan(_make_feedback(), cycle_id=7)  # pylint: disable=protected-access

    assert payload['summary'] == 'delegated'
    assert captured['owner'] is controller
    assert captured['cycle_id'] == 7
    assert captured['feedback']['sample_count'] == 8


def test_research_feedback_brief_delegates_to_feedback_service(monkeypatch):
    captured = {}

    def fake_brief(feedback=None):
        captured['feedback'] = dict(feedback or {})
        return {'bias': 'delegated'}

    monkeypatch.setattr(TrainingFeedbackService, 'research_feedback_brief', staticmethod(fake_brief))

    result = SelfLearningController._research_feedback_brief({'sample_count': 3})  # pylint: disable=protected-access

    assert result == {'bias': 'delegated'}
    assert captured['feedback']['sample_count'] == 3


def test_apply_experiment_llm_overrides_delegates_to_llm_runtime_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_apply(owner, llm_spec=None):
        captured['owner'] = owner
        captured['llm_spec'] = dict(llm_spec or {})

    monkeypatch.setattr(
        controller.training_llm_runtime_service,
        'apply_experiment_overrides',
        fake_apply,
    )

    controller._apply_experiment_llm_overrides({'dry_run': True, 'timeout': 9})  # pylint: disable=protected-access

    assert captured['owner'] is controller
    assert captured['llm_spec']['dry_run'] is True
    assert captured['llm_spec']['timeout'] == 9


def test_configure_experiment_delegates_to_experiment_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_configure(owner, spec=None):
        captured['owner'] = owner
        captured['spec'] = dict(spec or {})

    monkeypatch.setattr(
        controller.training_experiment_service,
        'configure_experiment',
        fake_configure,
    )

    controller.configure_experiment({'protocol': {'seed': 7}})

    assert captured['owner'] is controller
    assert captured['spec']['protocol']['seed'] == 7


def test_set_llm_dry_run_delegates_to_llm_runtime_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_set(owner, enabled=True):
        captured['owner'] = owner
        captured['enabled'] = enabled

    monkeypatch.setattr(controller.training_llm_runtime_service, 'set_dry_run', fake_set)

    controller.set_llm_dry_run(True)

    assert captured['owner'] is controller
    assert captured['enabled'] is True


def test_set_mock_mode_delegates_to_llm_runtime_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_set(owner, enabled=True):
        captured['owner'] = owner
        captured['enabled'] = enabled

    monkeypatch.setattr(controller.training_llm_runtime_service, 'set_dry_run', fake_set)

    controller.set_mock_mode(False)

    assert captured['owner'] is controller
    assert captured['enabled'] is False


def test_training_experiment_service_configures_protocol_dataset_and_manager_scope(monkeypatch):
    import invest_evolution.application.training.policy as policy_module

    service = TrainingExperimentService()
    llm_calls = {}
    refresh_calls = {}
    reload_calls = {}
    direct_service_calls = {"llm": 0, "refresh": 0, "reload": 0}

    monkeypatch.setattr(
        policy_module,
        'resolve_manager_config_ref',
        lambda manager_id: f'configs/{manager_id}.yaml',
    )

    class DummyController:
        experiment_spec = {}
        experiment_seed = None
        experiment_min_date = None
        experiment_max_date = None
        experiment_min_history_days = None
        experiment_simulation_days = None
        experiment_allowed_manager_ids = []
        experiment_llm = {}
        experiment_review_window = {}
        experiment_cutoff_policy = {}
        experiment_promotion_policy = {}
        allocator_enabled = False
        governance_enabled = False
        governance_mode = 'rule'
        governance_allowed_manager_ids = ['momentum']
        governance_cooldown_cycles = 2
        governance_min_confidence = 0.6
        governance_hysteresis_margin = 0.08
        governance_agent_override_enabled = False
        governance_agent_override_max_gap = 0.18
        default_manager_id = 'mean_reversion'
        default_manager_config_ref = 'configs/mean_reversion.yaml'
        current_params = {'position_size': 0.2}
        training_llm_runtime_service = SimpleNamespace(
            apply_experiment_overrides=lambda owner, llm_spec=None: direct_service_calls.__setitem__('llm', direct_service_calls['llm'] + 1)
        )
        training_governance_service = SimpleNamespace(
            refresh_governance_coordinator=lambda owner: direct_service_calls.__setitem__('refresh', direct_service_calls['refresh'] + 1),
            reload_manager_runtime=lambda owner, runtime_config_ref=None: direct_service_calls.__setitem__('reload', direct_service_calls['reload'] + 1),
        )

        def _apply_experiment_llm_overrides(self, llm_spec=None):
            llm_calls['spec'] = dict(llm_spec or {})

        def _refresh_governance_coordinator(self):
            refresh_calls['called'] = True

        def _reload_manager_runtime(self, runtime_config_ref=None):
            reload_calls['runtime_config_ref'] = runtime_config_ref

    controller = DummyController()
    service.configure_experiment(
        controller,
        {
            'protocol': {
                'seed': '7',
                'date_range': {'min': '2025-01-02', 'max': '2025-03-04'},
                'review_window': {'mode': 'rolling', 'size': 5},
                'cutoff_policy': {'mode': 'rolling', 'anchor_date': '2025-01-02', 'step_days': 14},
            },
            'dataset': {
                'min_history_days': 240,
                'simulation_days': 45,
            },
            'manager_scope': {
                'allowed_manager_ids': ['value_quality', 'momentum'],
                'allocator_enabled': True,
                'governance_mode': 'hybrid',
                'governance_cooldown_cycles': 5,
                'governance_min_confidence': 0.72,
                'governance_hysteresis_margin': 0.11,
                'governance_agent_override_enabled': True,
                'governance_agent_override_max_gap': 0.23,
            },
            'optimization': {'promotion_gate': {'min_samples': 4}},
            'llm': {'timeout': 9, 'dry_run': True},
        },
    )

    assert controller.experiment_seed == 7
    assert controller.experiment_spec['protocol']['date_range'] == {'min': '20250102', 'max': '20250304'}
    assert controller.experiment_spec['protocol']['review_window'] == {'mode': 'rolling', 'size': 5}
    assert controller.experiment_spec['protocol']['cutoff_policy'] == {
        'mode': 'rolling',
        'date': '',
        'anchor_date': '20250102',
        'step_days': 14,
        'dates': [],
    }
    assert controller.experiment_spec['protocol']['promotion_policy'] == {'min_samples': 4}
    assert controller.experiment_min_date == '20250102'
    assert controller.experiment_max_date == '20250304'
    assert controller.experiment_min_history_days == 240
    assert controller.experiment_simulation_days == 45
    assert controller.experiment_allowed_manager_ids == ['value_quality', 'momentum']
    assert controller.experiment_llm['timeout'] == 9
    assert controller.experiment_llm['mode'] == 'dry_run'
    assert controller.experiment_review_window == {'mode': 'rolling', 'size': 5}
    assert controller.experiment_cutoff_policy == {
        'mode': 'rolling',
        'date': '',
        'anchor_date': '20250102',
        'step_days': 14,
        'dates': [],
    }
    assert controller.experiment_promotion_policy == {'min_samples': 4}
    assert llm_calls['spec']['dry_run'] is True
    assert controller.allocator_enabled is True
    assert controller.governance_enabled is True
    assert controller.governance_mode == 'hybrid'
    assert controller.governance_allowed_manager_ids == ['value_quality', 'momentum']
    assert controller.governance_cooldown_cycles == 5
    assert controller.governance_min_confidence == 0.72
    assert controller.governance_hysteresis_margin == 0.11
    assert controller.governance_agent_override_enabled is True
    assert controller.governance_agent_override_max_gap == 0.23
    assert refresh_calls['called'] is True
    assert controller.default_manager_id == 'value_quality'
    assert controller.default_manager_config_ref == 'configs/value_quality.yaml'
    assert controller.current_params == {}
    assert reload_calls['runtime_config_ref'] == 'configs/value_quality.yaml'
    assert direct_service_calls == {'llm': 0, 'refresh': 0, 'reload': 0}


def test_enforce_allowed_manager_scope_boundary_realigns_session_default_manager(monkeypatch):
    import invest_evolution.application.training.policy as policy_module

    captured = {}

    def fake_reload(owner, runtime_config_ref=None):
        captured['owner'] = owner
        captured['runtime_config_ref'] = runtime_config_ref

    monkeypatch.setattr(
        policy_module,
        'resolve_manager_config_ref',
        lambda manager_id: f'configs/{manager_id}.yaml',
    )

    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id='mean_reversion',
            default_manager_config_ref='configs/mean_reversion.yaml',
            current_params={'position_size': 0.2},
        ),
        experiment_allowed_manager_ids=['value_quality'],
        training_governance_service=SimpleNamespace(reload_manager_runtime=fake_reload),
    )

    policy_module.enforce_allowed_manager_scope_boundary(
        controller,
        allowed_manager_ids=controller.experiment_allowed_manager_ids,
    )

    assert controller.session_state.default_manager_id == 'value_quality'
    assert controller.session_state.default_manager_config_ref == 'configs/value_quality.yaml'
    assert controller.session_state.current_params == {}
    assert captured['owner'] is controller
    assert captured['runtime_config_ref'] == 'configs/value_quality.yaml'


def test_training_experiment_service_uses_controller_default_promotion_gate_when_unspecified():
    service = TrainingExperimentService()

    controller = SimpleNamespace(
        experiment_spec={},
        experiment_seed=None,
        experiment_min_date=None,
        experiment_max_date=None,
        experiment_min_history_days=None,
        experiment_simulation_days=None,
        experiment_cutoff_policy={},
        experiment_review_window={},
        experiment_promotion_policy={},
        promotion_gate_policy={'min_samples': 5, 'candidate_ab': {'min_return_lift_pct': 0.1}},
        experiment_allowed_manager_ids=[],
        experiment_llm={},
        allocator_enabled=False,
        governance_enabled=False,
        governance_mode='rule',
        governance_allowed_manager_ids=[],
        governance_cooldown_cycles=2,
        governance_min_confidence=0.6,
        governance_hysteresis_margin=0.08,
        governance_agent_override_enabled=False,
        governance_agent_override_max_gap=0.18,
        default_manager_id='momentum',
        default_manager_config_ref='configs/momentum.yaml',
        current_params={},
        _apply_experiment_llm_overrides=lambda llm_spec=None: None,
        _refresh_governance_coordinator=lambda: None,
        _reload_manager_runtime=lambda runtime_config_ref=None: None,
    )

    service.configure_experiment(controller, {'protocol': {}, 'dataset': {}, 'manager_scope': {}, 'llm': {}})

    assert controller.experiment_promotion_policy == {
        'min_samples': 5,
        'candidate_ab': {'min_return_lift_pct': 0.1},
    }


def test_refresh_runtime_from_config_delegates_to_routing_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_sync(owner):
        captured['owner'] = owner

    monkeypatch.setattr(controller.training_governance_service, 'sync_runtime_from_config', fake_sync)

    controller.refresh_runtime_from_config()

    assert captured['owner'] is controller


def test_reload_manager_runtime_delegates_to_governance_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_reload(owner, runtime_config_ref=None):
        captured['owner'] = owner
        captured['runtime_config_ref'] = runtime_config_ref

    monkeypatch.setattr(controller.training_governance_service, 'reload_manager_runtime', fake_reload)

    controller._reload_manager_runtime('cfg.yaml')  # pylint: disable=protected-access

    assert captured['owner'] is controller
    assert captured['runtime_config_ref'] == 'cfg.yaml'


def test_maybe_apply_allocator_delegates_to_routing_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_apply(owner, *, stock_data, cutoff_date, cycle_id, event_emitter):
        captured['owner'] = owner
        captured['stock_data'] = dict(stock_data)
        captured['cutoff_date'] = cutoff_date
        captured['cycle_id'] = cycle_id
        captured['event_emitter'] = event_emitter

    monkeypatch.setattr(controller.training_governance_service, 'apply_governance', fake_apply)

    controller._maybe_apply_allocator(  # pylint: disable=protected-access
        {'sh.600519': {'rows': 3}},
        '20240201',
        5,
    )

    assert captured['owner'] is controller
    assert captured['stock_data']['sh.600519']['rows'] == 3
    assert captured['cutoff_date'] == '20240201'
    assert captured['cycle_id'] == 5


def test_runtime_policy_sync_delegates_to_policy_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_sync(owner):
        captured['owner'] = owner

    monkeypatch.setattr(controller.training_policy_service, 'sync_runtime_policy', fake_sync)

    controller._sync_runtime_policy_from_manager_runtime()  # pylint: disable=protected-access

    assert captured['owner'] is controller


def test_policy_lookup_delegates_to_policy_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_lookup(policy, path, default):
        captured['policy'] = dict(policy or {})
        captured['path'] = path
        captured['default'] = default
        return {'resolved': True}

    monkeypatch.setattr(TrainingPolicyService, 'policy_lookup', staticmethod(fake_lookup))

    result = controller._policy_lookup({'review': {'x': 1}}, 'review.x', 'fallback')  # pylint: disable=protected-access

    assert result == {'resolved': True}
    assert captured['path'] == 'review.x'
    assert captured['default'] == 'fallback'


def test_sanitize_runtime_param_adjustments_delegates_to_policy_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_sanitize(owner, adjustments=None):
        captured['owner'] = owner
        captured['adjustments'] = dict(adjustments or {})
        return {'position_size': 0.11}

    monkeypatch.setattr(
        controller.training_policy_service,
        'sanitize_runtime_param_adjustments',
        fake_sanitize,
    )

    result = controller._sanitize_runtime_param_adjustments({'position_size': 0.9})  # pylint: disable=protected-access

    assert result == {'position_size': 0.11}
    assert captured['owner'] is controller
    assert captured['adjustments']['position_size'] == 0.9


def test_training_outcome_service_builds_audit_tags_and_cycle_result():
    service = TrainingOutcomeService()

    class DummyController:
        default_manager_id = 'momentum'
        default_manager_config_ref = 'cfg.yaml'
        governance_enabled = True
        governance_mode = 'rule'
        last_governance_decision = {
            'dominant_manager_id': 'mean_reversion',
            'active_manager_ids': ['mean_reversion'],
            'manager_budget_weights': {'mean_reversion': 1.0},
            'regime': 'oscillation',
        }
        current_params = {'position_size': 0.12}
        experiment_review_window = {'mode': 'rolling', 'size': 3}
        experiment_promotion_policy = {'min_samples': 2}
        cycle_history = [SimpleNamespace(cycle_id=6), SimpleNamespace(cycle_id=7)]

    audit_tags = service.build_audit_tags(
        DummyController(),
        data_mode='offline',
        requested_data_mode='live',
        effective_data_mode='offline',
        llm_mode='dry_run',
        degraded=False,
        degrade_reason='',
        selection_mode='meeting_selection',
        agent_used=True,
        llm_used=False,
        benchmark_passed=True,
        review_applied=True,
        regime_result={'regime': 'bull'},
    )
    cycle_payload = {
        'strategy_scores': {'overall_score': 0.8},
        'analysis': 'review ok',
        'execution_snapshot': {
            'basis_stage': 'pre_optimization',
            'cycle_id': 8,
            'dominant_manager_id': 'value_quality',
            'manager_config_ref': 'executed.yaml',
            'active_runtime_config_ref': 'executed.yaml',
            'runtime_overrides': {'position_size': 0.08},
            'governance_decision': {
                'dominant_manager_id': 'value_quality',
                'active_manager_ids': ['value_quality'],
                'manager_budget_weights': {'value_quality': 1.0},
                'regime': 'bull',
            },
            'selection_mode': 'meeting_selection',
            'benchmark_passed': True,
        },
    }
    simulation_envelope = SimulationStageEnvelope.from_cycle_payload(cycle_payload)
    review_envelope = ReviewStageEnvelope.from_structured_inputs(
        simulation=simulation_envelope,
        analysis='review ok',
        review_decision={},
        stage_snapshots=simulation_envelope.stage_snapshots,
    )

    cycle_result = service.build_cycle_result(
        DummyController(),
        result_factory=TrainingResult,
        cycle_id=8,
        cutoff_date='20240201',
        selected=['sh.600519'],
        sim_result=SimpleNamespace(
            initial_capital=100000.0,
            final_value=102000.0,
            return_pct=2.0,
        ),
        is_profit=True,
        trade_dicts=[{'action': 'SELL'}],
        data_mode='offline',
        requested_data_mode='live',
        effective_data_mode='offline',
        llm_mode='dry_run',
        degraded=False,
        degrade_reason='',
        selection_mode='meeting_selection',
        agent_used=True,
        llm_used=False,
        benchmark_passed=True,
        cycle_payload=cycle_payload,
        simulation_envelope=simulation_envelope,
        review_envelope=review_envelope,
        review_applied=True,
        config_snapshot_path='snap.json',
        optimization_events=[
            {
                'stage': 'runtime_config_mutation',
                'runtime_config_mutation_payload': {
                    'runtime_config_ref': 'candidate.yaml',
                    'auto_applied': False,
                },
                'decision': {
                    'runtime_config_ref': 'stale_candidate.yaml',
                    'auto_applied': True,
                },
            }
        ],
        audit_tags=audit_tags,
        manager_output=SimpleNamespace(manager_id='value_quality', manager_config_ref='value.yaml'),
        research_feedback={'recommendation': {'bias': 'maintain'}},
    )

    assert audit_tags['governance_dominant_manager'] == 'mean_reversion'
    assert audit_tags['governance_regime'] == 'oscillation'
    assert cycle_result.analysis == 'review ok'
    assert cycle_result.execution_defaults == {
        'default_manager_id': 'value_quality',
        'default_manager_config_ref': 'executed.yaml',
    }
    assert cycle_result.params == {'position_size': 0.08}
    assert cycle_result.governance_decision['dominant_manager_id'] == 'value_quality'
    assert cycle_result.execution_snapshot['basis_stage'] == 'pre_optimization'
    assert cycle_result.run_context['basis_stage'] == 'pre_optimization'
    assert cycle_result.run_context['runtime_overrides'] == {'position_size': 0.08}
    assert cycle_result.stage_snapshots['outcome']['run_context']['basis_stage'] == 'pre_optimization'
    assert cycle_result.stage_snapshots['outcome']['promotion_record']['status'] == cycle_result.promotion_record['status']
    assert cycle_result.realism_metrics == {
        'trade_record_count': 1,
        'selection_mode': 'meeting_selection',
        'optimization_event_count': 1,
        'avg_trade_amount': 0.0,
        'avg_turnover_rate': 0.0,
        'high_turnover_trade_count': 0,
        'avg_holding_days': 0.0,
        'source_mix': {'unknown': 1.0},
        'exit_trigger_mix': {},
    }


def test_training_outcome_service_ignores_non_finite_realism_amounts():
    service = TrainingOutcomeService()

    realism_metrics = service.build_realism_metrics(
        trade_dicts=[
            {'amount': float('nan'), 'source': 'signal'},
            {'amount': float('inf'), 'source': 'signal'},
            {'amount': 300.0, 'source': 'signal', 'holding_days': 2, 'turnover_rate': 1.25},
        ],
        selection_mode='meeting_selection',
        optimization_events=[],
    )

    assert realism_metrics.get('avg_trade_amount') == 300.0
    assert realism_metrics.get('avg_turnover_rate') == 1.25
    assert realism_metrics.get('avg_holding_days') == 2.0


def test_training_outcome_service_prefers_envelope_execution_snapshot_over_cycle_payload():
    service = TrainingOutcomeService()

    class DummyController:
        session_state = TrainingSessionState(
            default_manager_id='value_quality',
            default_manager_config_ref='configs/value_quality.yaml',
            last_governance_decision={
                'dominant_manager_id': 'value_quality',
                'active_manager_ids': ['value_quality'],
                'manager_budget_weights': {'value_quality': 1.0},
                'regime': 'bull',
            },
        )
        governance_enabled = True
        governance_mode = 'advisor'
        dual_review_enabled = True
        experiment_review_window = {'mode': 'rolling', 'size': 2}
        experiment_promotion_policy = {}
        cycle_history = []
        quality_gate_matrix = {}
        promotion_gate_policy = {}
        freeze_gate_policy = {}

    simulation_envelope = SimulationStageEnvelope(
        cycle_id=10,
        cutoff_date='20240202',
        regime='bull',
        benchmark_passed=True,
        strategy_scores={'overall_score': 0.91},
        governance_decision={'dominant_manager_id': 'value_quality', 'regime': 'bull'},
        execution_snapshot={
            'basis_stage': 'simulation_envelope',
            'cycle_id': 10,
            'active_runtime_config_ref': 'configs/envelope.yaml',
            'manager_config_ref': 'configs/envelope.yaml',
            'runtime_overrides': {'position_size': 0.07},
            'governance_decision': {
                'dominant_manager_id': 'value_quality',
                'active_manager_ids': ['value_quality'],
                'manager_budget_weights': {'value_quality': 1.0},
                'regime': 'bull',
            },
            'selection_mode': 'meeting_selection',
            'benchmark_passed': True,
        },
        stage_snapshots={'simulation': {'stage': 'simulation'}},
    )
    review_envelope = ReviewStageEnvelope(
        simulation=simulation_envelope,
        analysis='envelope analysis',
        review_decision={'reasoning': 'use envelope'},
        stage_snapshots={'simulation': {'stage': 'simulation'}, 'review': {'stage': 'review'}},
    )

    cycle_result = service.build_cycle_result(
        DummyController(),
        result_factory=TrainingResult,
        cycle_id=10,
        cutoff_date='20240202',
        selected=['sh.600519'],
        sim_result=SimpleNamespace(
            initial_capital=100000.0,
            final_value=101000.0,
            return_pct=1.0,
        ),
        is_profit=True,
        trade_dicts=[],
        data_mode='offline',
        requested_data_mode='offline',
        effective_data_mode='offline',
        llm_mode='dry_run',
        degraded=False,
        degrade_reason='',
        selection_mode='meeting_selection',
        agent_used=False,
        llm_used=False,
        benchmark_passed=True,
        cycle_payload={
            'analysis': 'legacy analysis',
            'execution_snapshot': {
                'basis_stage': 'legacy_cycle_dict',
                'active_runtime_config_ref': 'configs/legacy.yaml',
                'manager_config_ref': 'configs/legacy.yaml',
            },
        },
        simulation_envelope=simulation_envelope,
        review_envelope=review_envelope,
        review_applied=False,
        config_snapshot_path='snap.json',
        optimization_events=[],
        audit_tags={'subject_type': 'single_manager'},
        manager_output=SimpleNamespace(manager_id='value_quality', manager_config_ref='configs/value_quality.yaml'),
        research_feedback={},
    )

    assert cycle_result.execution_snapshot['basis_stage'] == 'simulation_envelope'
    assert cycle_result.execution_snapshot['active_runtime_config_ref'] == 'configs/envelope.yaml'
    assert cycle_result.analysis == 'envelope analysis'


def test_run_continuous_delegates_to_lifecycle_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_run(owner, *, max_cycles, successful_cycles_target=None):
        captured['owner'] = owner
        captured['max_cycles'] = max_cycles
        captured['successful_cycles_target'] = successful_cycles_target
        return {'status': 'delegated'}

    monkeypatch.setattr(controller.training_lifecycle_service, 'run_continuous', fake_run)

    payload = controller.run_continuous(max_cycles=3)

    assert payload == {'status': 'delegated'}
    assert captured['owner'] is controller
    assert captured['max_cycles'] == 3
    assert captured['successful_cycles_target'] is None


def test_training_lifecycle_service_finalize_cycle_updates_meta_and_callback(monkeypatch, tmp_path):
    from invest_evolution.application.train import TrainingResult
    import invest_evolution.application.train as train_module

    service = TrainingLifecycleService()
    emitted = []
    logs = []
    callback_result = {}

    monkeypatch.setattr(train_module, 'emit_event', lambda event_type, data: emitted.append((event_type, data)))

    class DummyController:
        def __init__(self):
            self.cycle_history = []
            self.current_cycle_id = 0
            self.last_governance_decision = {
                'dominant_manager_id': 'momentum',
                'active_manager_ids': ['momentum'],
                'manager_budget_weights': {'momentum': 1.0},
                'regime': 'bull',
            }
            self.last_feedback_optimization = {'triggered': False}
            self.last_cycle_meta = {}
            self.default_manager_id = 'momentum'
            self.on_cycle_complete = staticmethod(
                lambda result: callback_result.setdefault('cycle', result.cycle_id)
            )
            self.training_persistence_service = SimpleNamespace(
                record_self_assessment=lambda owner, snapshot_factory, cycle_result, cycle_dict: logs.append(
                    ('assessment', cycle_result.cycle_id, dict(cycle_dict), snapshot_factory.__name__)
                ),
                save_cycle_result=lambda owner, result: logs.append(('save', result.cycle_id)),
            )
            self.freeze_gate_service = SimpleNamespace(
                evaluate_freeze_gate=lambda owner: {'passed': False},
            )

        @staticmethod
        def _research_feedback_brief(feedback):
            return {'sample_count': int((feedback or {}).get('sample_count') or 0)}

        @staticmethod
        def _emit_module_log(*args, **kwargs):
            logs.append(('module', args, kwargs))

        @staticmethod
        def _emit_runtime_event(event_type, data):
            emitted.append((event_type, data))

    cycle_result = TrainingResult(
        cycle_id=2,
        cutoff_date='20240201',
        selected_stocks=['sh.600519'],
        initial_capital=100000,
        final_value=102000,
        return_pct=2.0,
        is_profit=True,
        trade_history=[],
        params={},
    )
    assessment_payload = {
        'regime': 'bull',
        'plan_source': 'meeting_selection',
        'benchmark_passed': True,
    }
    controller = DummyController()
    service.finalize_cycle(
        controller,
        cycle_result=cycle_result,
        assessment_payload=assessment_payload,
        cycle_id=2,
        cutoff_date='20240201',
        sim_result=SimpleNamespace(return_pct=2.0, final_value=102000),
        is_profit=True,
        selected=['sh.600519'],
        trade_dicts=[{'action': 'SELL'}],
        review_applied=True,
        selection_mode='meeting_selection',
        requested_data_mode='live',
        effective_data_mode='offline',
        llm_mode='dry_run',
        degraded=False,
        degrade_reason='',
        research_feedback={'sample_count': 6},
    )

    assert controller.current_cycle_id == 1
    assert controller.last_cycle_meta['cycle_id'] == 2
    assert emitted[0][0] == 'cycle_complete'
    assert emitted[0][1]['cycle_id'] == 2
    assert callback_result['cycle'] == 2
    assert any(item[0] == 'assessment' for item in logs)
    assert any(item[0] == 'save' for item in logs)


def test_training_selection_service_runs_selection_stage():
    service = TrainingSelectionService()
    events = []
    trading_plan = SimpleNamespace(
        positions=[SimpleNamespace(code='sh.600519')],
        source='portfolio_assembler',
        max_positions=2,
    )
    portfolio_plan = SimpleNamespace(
        active_manager_ids=['momentum', 'value_quality'],
        confidence=0.83,
        reasoning='portfolio assembled from manager sleeves',
        cash_reserve=0.2,
        to_dict=lambda: {
            'active_manager_ids': ['momentum', 'value_quality'],
            'manager_weights': {'momentum': 0.6, 'value_quality': 0.4},
            'positions': [{'code': 'sh.600519'}],
            'cash_reserve': 0.2,
            'confidence': 0.83,
            'reasoning': 'portfolio assembled from manager sleeves',
        },
        to_trading_plan=lambda: trading_plan,
    )
    manager_results = [
        SimpleNamespace(
            to_dict=lambda: {
                'manager_id': 'momentum',
                'plan': {'selected_codes': ['sh.600519']},
            }
        )
    ]
    bundle = SimpleNamespace(
        run_context=SimpleNamespace(regime='bull', budget_weights={'momentum': 0.6, 'value_quality': 0.4}),
        manager_results=manager_results,
        portfolio_plan=portfolio_plan,
        dominant_manager_id='momentum',
        manager_outputs={
            'momentum': SimpleNamespace(
                manager_id='momentum',
                manager_config_ref='cfg.yaml',
                to_dict=lambda: {'manager_id': 'momentum'},
            )
        },
    )

    class DummyTracker:
        def __init__(self):
            self.selected = []

        def mark_selected(self, cycle_id, selected):
            self.selected.append((cycle_id, list(selected)))

    class DummyManagerExecutionService:
        @staticmethod
        def execute_manager_selection(owner, *, cycle_id, cutoff_date, stock_data):
            del owner, cycle_id, cutoff_date, stock_data
            return bundle

    class DummyController:
        training_manager_execution_service = DummyManagerExecutionService()
        agent_tracker = DummyTracker()
        manager_arch_enabled = False
        manager_allocator_enabled = False
        portfolio_assembly_enabled = False
        dual_review_enabled = False
        manager_persistence_enabled = False
        effective_runtime_mode = 'manager_portfolio'

        def __getattr__(self, name):
            if name == 'selection_meeting_service':
                raise AssertionError('SelectionMeeting should not be the default owner')
            raise AttributeError(name)

        @staticmethod
        def _emit_agent_status(*args, **kwargs):
            events.append(('status', args, kwargs))

        @staticmethod
        def _emit_module_log(*args, **kwargs):
            events.append(('module', args, kwargs))

        @staticmethod
        def _emit_meeting_speech(*args, **kwargs):
            events.append(('speech', args, kwargs))

        @staticmethod
        def _mark_cycle_skipped(*args, **kwargs):
            raise AssertionError('selection should not skip')

    stock_data = {'sh.600519': {'rows': 1}}
    result = service.run_selection_stage(
        DummyController(),
        cycle_id=3,
        cutoff_date='20240201',
        stock_data=stock_data,
    )

    assert result is not None
    boundary = build_selection_boundary_projection(result)
    assert boundary.trading_plan is trading_plan
    assert boundary.manager_output is bundle.manager_outputs['momentum']
    assert result == TrainingSelectionResult(
        regime_result=result.regime_result,
        selected_codes=['sh.600519'],
        selected_data={'sh.600519': {'rows': 1}},
        selection_mode='manager_portfolio',
        agent_used=False,
        manager_bundle=bundle,
        manager_results=[item.to_dict() for item in manager_results],
        portfolio_plan=portfolio_plan.to_dict(),
        dominant_manager_id='momentum',
        selection_trace={
            'selected': ['sh.600519'],
            'active_managers': ['momentum', 'value_quality'],
            'dominant_manager_id': 'momentum',
            'portfolio_plan': portfolio_plan.to_dict(),
            'manager_results': [item.to_dict() for item in manager_results],
            'decision_source': 'manager_runtime',
        },
        compatibility_fields={
            'derived': True,
            'source': 'dominant_manager',
            'field_role': 'derived_compatibility',
            'manager_id': 'momentum',
            'manager_config_ref': 'cfg.yaml',
        },
    )
    assert result.regime_result['decision_source'] == 'manager_runtime'
    assert result.selection_trace['decision_source'] == 'manager_runtime'
    assert not any(item[0] == 'speech' for item in events)


def test_training_selection_service_skips_when_no_selected_codes():
    service = TrainingSelectionService()
    skipped = {}
    empty_portfolio_plan = SimpleNamespace(
        active_manager_ids=['defensive'],
        confidence=0.31,
        reasoning='no sleeve produced investable ideas',
        cash_reserve=1.0,
        to_dict=lambda: {
            'active_manager_ids': ['defensive'],
            'manager_weights': {'defensive': 1.0},
            'positions': [],
            'cash_reserve': 1.0,
            'confidence': 0.31,
            'reasoning': 'no sleeve produced investable ideas',
        },
        to_trading_plan=lambda: SimpleNamespace(positions=[], source='portfolio_assembler', max_positions=0),
    )
    empty_bundle = SimpleNamespace(
        run_context=SimpleNamespace(regime='oscillation', budget_weights={'defensive': 1.0}),
        manager_results=[],
        portfolio_plan=empty_portfolio_plan,
        dominant_manager_id='defensive',
        manager_outputs={},
    )

    class DummyManagerExecutionService:
        @staticmethod
        def execute_manager_selection(owner, *, cycle_id, cutoff_date, stock_data):
            del owner, cycle_id, cutoff_date, stock_data
            return empty_bundle

    class DummyController:
        training_manager_execution_service = DummyManagerExecutionService()
        agent_tracker = SimpleNamespace(mark_selected=lambda *args, **kwargs: None)

        @staticmethod
        def _emit_agent_status(*args, **kwargs):
            del args, kwargs

        @staticmethod
        def _emit_module_log(*args, **kwargs):
            del args, kwargs

        @staticmethod
        def _emit_meeting_speech(*args, **kwargs):
            del args, kwargs

        @staticmethod
        def _mark_cycle_skipped(cycle_id, cutoff_date, **kwargs):
            skipped['cycle_id'] = cycle_id
            skipped['cutoff_date'] = cutoff_date
            skipped.update(kwargs)

    result = service.run_selection_stage(
        DummyController(),
        cycle_id=5,
        cutoff_date='20240201',
        stock_data={'sh.600519': {'rows': 1}},
    )

    assert result is None
    assert skipped['stage'] == 'selection'
    assert skipped['reason'] == '多经理运行未产出可交易标的'


def test_training_outcome_service_uses_effective_runtime_mode_for_subject_type():
    service = TrainingOutcomeService()
    controller = SimpleNamespace(
        governance_enabled=True,
        governance_mode='rule',
        effective_runtime_mode='manager_portfolio',
        dual_review_enabled=False,
        session_state=TrainingSessionState(
            last_governance_decision={'regime': 'bull', 'dominant_manager_id': 'momentum'},
        ),
    )

    audit_tags = service.build_audit_tags(
        controller,
        data_mode='offline',
        requested_data_mode='offline',
        effective_data_mode='offline',
        llm_mode='dry_run',
        degraded=False,
        degrade_reason='',
        selection_mode='legacy_selection',
        agent_used=False,
        llm_used=False,
        benchmark_passed=True,
        review_applied=False,
        regime_result={'regime': 'bull'},
    )

    assert audit_tags['subject_type'] == 'manager_portfolio'


def test_training_review_stage_service_runs_review_flow():
    service = TrainingReviewStageService()
    events = []

    class DummyTrainingReviewService:
        @staticmethod
        def build_eval_report(owner, **kwargs):
            events.append(('build_eval_report', owner, kwargs))
            return SimpleNamespace(
                cycle_id=kwargs['cycle_id'],
                return_pct=1.0,
                benchmark_passed=True,
                metadata=kwargs,
                to_dict=lambda: {
                    'cycle_id': kwargs['cycle_id'],
                    'return_pct': 1.0,
                    'benchmark_passed': True,
                    'metadata': kwargs,
                },
            )

        @staticmethod
        def apply_review_decision(owner, **kwargs):
            events.append(('apply_review_decision', owner, kwargs))
            return True

    class DummyTracker:
        @staticmethod
        def compute_accuracy(last_n_cycles=20):
            return {'window': last_n_cycles, 'accuracy': 0.75}

    saved_reviews = []
    bundle = SimpleNamespace(
        run_context=SimpleNamespace(budget_weights={'momentum': 0.7, 'value_quality': 0.3}),
        dominant_manager_id='momentum',
        portfolio_plan=SimpleNamespace(
            to_dict=lambda: {
                'positions': [
                    {'code': 'sh.600519', 'target_weight': 0.41, 'source_managers': ['momentum']}
                ],
                'active_manager_ids': ['momentum', 'value_quality'],
                'manager_weights': {'momentum': 0.7, 'value_quality': 0.3},
                'cash_reserve': 0.45,
                'metadata': {'assembly_mode': 'portfolio_assembler'},
            }
        ),
        manager_results=[
            SimpleNamespace(
                manager_id='momentum',
                status='planned',
                plan=SimpleNamespace(
                    manager_id='momentum',
                    regime='bull',
                    positions=[SimpleNamespace(code='sh.600519')],
                    confidence=0.83,
                ),
                attribution=SimpleNamespace(active_exposure=0.41),
                to_dict=lambda: {
                    'manager_id': 'momentum',
                    'status': 'planned',
                    'plan': {
                        'manager_id': 'momentum',
                        'regime': 'bull',
                        'positions': [{'code': 'sh.600519'}],
                        'confidence': 0.83,
                    },
                    'attribution': {'active_exposure': 0.41},
                },
            ),
            SimpleNamespace(
                manager_id='value_quality',
                status='empty',
                plan=SimpleNamespace(
                    manager_id='value_quality',
                    regime='bear',
                    positions=[],
                    confidence=0.4,
                ),
                attribution=SimpleNamespace(active_exposure=0.0),
                to_dict=lambda: {
                    'manager_id': 'value_quality',
                    'status': 'empty',
                    'plan': {
                        'manager_id': 'value_quality',
                        'regime': 'bear',
                        'positions': [],
                        'confidence': 0.4,
                    },
                    'attribution': {'active_exposure': 0.0},
                },
            ),
        ],
    )

    class DummyController:
        manager_arch_enabled = True
        dual_review_enabled = True
        manager_allocator_enabled = True
        training_review_service = DummyTrainingReviewService()
        agent_tracker = DummyTracker()
        current_params = {'position_size': 0.12}
        experiment_review_window = {'mode': 'rolling', 'size': 3}
        artifact_recorder = SimpleNamespace(
            save_manager_review_artifact=lambda report, cycle_id: saved_reviews.append(
                ("manager_review", report, cycle_id)
            ),
            save_allocation_review_artifact=lambda report, cycle_id: saved_reviews.append(
                ("allocation_review", report, cycle_id)
            ),
        )
        cycle_records = []
        cycle_history = [
            SimpleNamespace(
                cycle_id=4,
                cutoff_date='20240130',
                return_pct=-1.2,
                is_profit=False,
                selection_mode='meeting',
                benchmark_passed=False,
                review_applied=False,
                manager_id='momentum',
                manager_config_ref='configs/active.yaml',
                governance_decision={'regime': 'bear'},
                audit_tags={'governance_regime': 'bear'},
                research_feedback={'recommendation': {'bias': 'tighten_risk'}},
            ),
            SimpleNamespace(
                cycle_id=5,
                cutoff_date='20240131',
                return_pct=0.6,
                is_profit=True,
                selection_mode='algorithm',
                benchmark_passed=True,
                review_applied=True,
                manager_id='momentum',
                manager_config_ref='configs/active.yaml',
                governance_decision={'regime': 'oscillation'},
                audit_tags={'governance_regime': 'oscillation'},
                research_feedback={},
            ),
        ]
        training_manager_review_stage_service: object | None = None
        training_allocation_review_stage_service: object | None = None

        def __getattr__(self, name):
            if name == 'review_meeting_service':
                raise AssertionError('ReviewMeeting should not own the default review flow')
            raise AttributeError(name)

        @staticmethod
        def _append_optimization_event(event):
            events.append(('append_event', event))

        @staticmethod
        def _emit_agent_status(*args, **kwargs):
            events.append(('status', args, kwargs))

        @staticmethod
        def _emit_module_log(*args, **kwargs):
            events.append(('module', args, kwargs))

    class DummyEvent:
        def __init__(self, **kwargs):
            self.kwargs = dict(kwargs)
            self.review_applied_effects_payload = {}
            self.lineage = {}

        def to_dict(self):
            payload = dict(self.kwargs)
            if self.review_applied_effects_payload:
                payload["review_applied_effects_payload"] = dict(
                    self.review_applied_effects_payload
                )
            return payload

    from invest_evolution.application.training.review import AllocationReviewStageService
    from invest_evolution.application.training.review import ManagerReviewStageService

    cycle_payload = {'benchmark_passed': True}
    simulation_envelope = SimulationStageEnvelope.from_cycle_payload(cycle_payload)
    controller = DummyController()
    controller.training_manager_review_stage_service = ManagerReviewStageService()
    controller.training_allocation_review_stage_service = AllocationReviewStageService()
    result = service.run_review_stage(
        controller,
        cycle_id=6,
        cutoff_date='20240201',
        sim_result=SimpleNamespace(return_pct=1.0, total_pnl=1000, total_trades=2, win_rate=0.5),
        regime_result={'regime': 'bull'},
        selected=['sh.600519'],
        cycle_payload=cycle_payload,
        trade_dicts=[{'action': 'SELL'}],
        requested_data_mode='live',
        effective_data_mode='offline',
        llm_mode='dry_run',
        degraded=False,
        degrade_reason='',
        data_mode='offline',
        selection_mode='meeting_selection',
        agent_used=True,
        llm_used=False,
        manager_output=None,
        research_feedback={'recommendation': {'bias': 'maintain'}},
        optimization_event_factory=DummyEvent,
        simulation_envelope=simulation_envelope,
        manager_bundle=bundle,
    )

    assert result == TrainingReviewStageResult(
        eval_report=result.eval_report,
        review_decision=result.review_decision,
        review_applied=True,
        review_event=result.review_event,
        manager_review_report=result.manager_review_report,
        allocation_review_report=result.allocation_review_report,
        review_trace=result.review_trace,
        cycle_payload=result.cycle_payload,
    )
    assert 'review_applied' not in cycle_payload
    assert result.cycle_payload['review_applied'] is True
    assert result.cycle_payload['review_applied'] is True
    assert result.cycle_payload['manager_review_report'] == result.manager_review_report
    assert saved_reviews[0][2] == 6
    assert saved_reviews[1][2] == 6
    assert any(item[0] == 'apply_review_decision' for item in events)
    assert not any(item[0] == 'review_meeting' for item in events)
    assert result.review_trace['decision_source'] == 'dual_review'
    assert result.review_trace['review_basis_window'] == {
        'mode': 'rolling',
        'size': 3,
        'cycle_ids': [4, 5, 6],
        'current_cycle_id': 6,
    }
    assert result.review_trace['similar_results']
    assert result.review_trace['similarity_summary']['matched_cycle_ids'] == [
        entry['cycle_id'] for entry in result.review_trace['similar_results']
    ]
    assert result.review_trace['causal_diagnosis']['primary_driver']
    assert result.manager_review_report['subject_type'] == 'manager_review'
    assert result.allocation_review_report['subject_type'] == 'allocation_review'
    assert result.review_decision.get('subject_type') == 'manager_portfolio'


def test_training_lifecycle_service_uses_effective_runtime_mode_when_cycle_result_omits_subject_type():
    service = TrainingLifecycleService()
    emitted = []
    logs = []
    callback_result = {}

    class DummyController:
        def __init__(self):
            self.current_cycle_id = 0
            self.effective_runtime_mode = 'manager_portfolio'
            self.last_governance_decision = {}
            self.last_feedback_optimization = {}
            self.last_cycle_meta = {}
            self.default_manager_id = 'momentum'
            self.on_cycle_complete = staticmethod(
                lambda result: callback_result.setdefault('cycle', result.cycle_id)
            )
            self.training_persistence_service = SimpleNamespace(
                record_self_assessment=lambda *args, **kwargs: None,
                save_cycle_result=lambda *args, **kwargs: None,
            )
            self.freeze_gate_service = SimpleNamespace(
                evaluate_freeze_gate=lambda owner: {'passed': True},
            )

        @staticmethod
        def _research_feedback_brief(feedback):
            return {'sample_count': int((feedback or {}).get('sample_count') or 0)}

        @staticmethod
        def _emit_module_log(*args, **kwargs):
            logs.append((args, kwargs))

        @staticmethod
        def _emit_runtime_event(event_type, data):
            emitted.append((event_type, data))

    controller = DummyController()
    cycle_result = TrainingResult(
        cycle_id=8,
        cutoff_date='20240201',
        selected_stocks=['sh.600519'],
        initial_capital=100000,
        final_value=101200,
        return_pct=1.2,
        is_profit=True,
        trade_history=[],
        params={},
    )
    service.finalize_cycle(
        controller,
        cycle_result=cycle_result,
        assessment_payload={'regime': 'bull'},
        cycle_id=8,
        cutoff_date='20240201',
        sim_result=SimpleNamespace(return_pct=1.2, final_value=101200),
        is_profit=True,
        selected=['sh.600519'],
        trade_dicts=[],
        review_applied=False,
        selection_mode='manager_portfolio',
        requested_data_mode='offline',
        effective_data_mode='offline',
        llm_mode='dry_run',
        degraded=False,
        degrade_reason='',
        research_feedback=None,
    )

    assert controller.last_cycle_meta['subject_type'] == 'manager_portfolio'
    assert emitted[0][1]['subject_type'] == 'manager_portfolio'
    assert callback_result['cycle'] == 8


def test_save_cycle_result_delegates_to_persistence_service(tmp_path):
    controller = _make_controller(tmp_path)
    result = TrainingResult(
        cycle_id=3,
        cutoff_date='20240103',
        selected_stocks=['sh.600000'],
        initial_capital=100000,
        final_value=101500,
        return_pct=1.5,
        is_profit=True,
        trade_history=[],
        params={},
        analysis='',
        data_mode='mock',
        selection_mode='meeting',
        agent_used=True,
        llm_used=False,
        benchmark_passed=True,
        review_applied=False,
        config_snapshot_path='',
        optimization_events=[],
        audit_tags={},
        experiment_spec={'protocol': {'seed': 7}},
        run_context={
            'active_runtime_config_ref': 'configs/active.yaml',
            'candidate_runtime_config_ref': 'data/evolution/generations/candidate.yaml',
            'runtime_overrides': {'position_size': 0.12},
            'review_basis_window': {'mode': 'single_cycle', 'size': 1, 'cycle_ids': [3], 'current_cycle_id': 3},
            'fitness_source_cycles': [1, 2],
            'promotion_decision': {'status': 'candidate_generated', 'applied_to_active': False},
        },
        promotion_record={
            'status': 'candidate_generated',
            'gate_status': 'awaiting_gate',
        },
        lineage_record={
            'lineage_status': 'candidate_pending',
            'active_runtime_config_ref': 'configs/active.yaml',
        },
        validation_report={
            'validation_task_id': 'val_123',
            'shadow_mode': True,
            'summary': {'status': 'hold'},
        },
        validation_summary={
            'validation_task_id': 'val_123',
            'status': 'hold',
            'shadow_mode': True,
        },
        peer_comparison_report={
            'compared_market_tag': 'bull',
            'comparable': True,
        },
        judge_report={
            'decision': 'hold',
            'shadow_mode': True,
        },
    )

    controller._save_cycle_result(result)  # pylint: disable=protected-access

    payload = json.loads((tmp_path / 'training' / 'cycle_3.json').read_text(encoding='utf-8'))
    assert payload['cycle_id'] == 3
    assert payload['return_pct'] == 1.5
    assert payload['benchmark_passed'] is True
    assert payload['experiment_spec']['protocol']['seed'] == 7
    assert payload['run_context']['active_runtime_config_ref'] == 'configs/active.yaml'
    assert payload['run_context']['promotion_decision']['status'] == 'candidate_generated'
    assert payload['promotion_record']['gate_status'] == 'awaiting_gate'
    assert payload['lineage_record']['lineage_status'] == 'candidate_pending'
    assert payload['validation_report']['validation_task_id'] == 'val_123'
    assert payload['validation_report']['summary']['status'] == 'hold'
    assert payload['validation_summary']['shadow_mode'] is True
    assert payload['peer_comparison_report']['compared_market_tag'] == 'bull'
    assert payload['judge_report']['decision'] == 'hold'
    assert payload['artifacts']['validation_report_path'].endswith('cycle_3_validation.json')
    validation_payload = json.loads(
        (tmp_path / 'training' / 'validation' / 'cycle_3_validation.json').read_text(
            encoding='utf-8'
        )
    )
    peer_payload = json.loads(
        (tmp_path / 'training' / 'validation' / 'cycle_3_peer_comparison.json').read_text(
            encoding='utf-8'
        )
    )
    judge_payload = json.loads(
        (tmp_path / 'training' / 'validation' / 'cycle_3_judge.json').read_text(
            encoding='utf-8'
        )
    )
    assert validation_payload['validation_task_id'] == 'val_123'
    assert peer_payload['compared_market_tag'] == 'bull'
    assert judge_payload['decision'] == 'hold'
    run_leaderboard = json.loads((tmp_path / 'training' / 'leaderboard.json').read_text(encoding='utf-8'))
    aggregate_leaderboard = json.loads((tmp_path / 'leaderboard.json').read_text(encoding='utf-8'))
    assert run_leaderboard['total_records'] == 1
    assert run_leaderboard['entries'][0]['latest_cycle_id'] == 3
    assert run_leaderboard['policy']['train']['freeze_gate']['avg_sharpe_gte'] == 0.8
    assert aggregate_leaderboard['entries'][0]['latest_cycle_id'] == 3


def test_generate_report_delegates_to_freeze_gate_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)

    monkeypatch.setattr(
        controller.freeze_gate_service,
        'generate_training_report',
        lambda owner: {'status': 'ok', 'owner_bound': owner is controller},
    )

    payload = controller._generate_report()  # pylint: disable=protected-access

    assert payload == {'status': 'ok', 'owner_bound': True}


def test_save_cycle_result_can_disable_aggregate_leaderboard(tmp_path):
    controller = _make_controller(tmp_path)
    controller.aggregate_leaderboard_enabled = False
    result = TrainingResult(
        cycle_id=4,
        cutoff_date='20240104',
        selected_stocks=['000001.SZ'],
        initial_capital=100000.0,
        final_value=101000.0,
        return_pct=1.0,
        is_profit=True,
        trade_history=[],
        params={},
        benchmark_passed=True,
        data_mode='mock',
        requested_data_mode='mock',
        effective_data_mode='mock',
        llm_mode='dry_run',
    )

    controller._save_cycle_result(result)  # pylint: disable=protected-access

    assert (tmp_path / 'training' / 'leaderboard.json').exists()
    assert not (tmp_path / 'leaderboard.json').exists()


def test_training_lifecycle_service_refreshes_leaderboards_before_final_report():
    service = TrainingLifecycleService()
    events = []

    class DummyController:
        stop_on_freeze = False
        total_cycle_attempts = 0
        skipped_cycle_count = 0
        cycle_history = []
        consecutive_losses = 0
        training_persistence_service = SimpleNamespace(
            refresh_leaderboards=lambda owner: events.append(('refresh', owner)),
        )
        freeze_gate_service = SimpleNamespace(
            should_freeze=lambda owner: False,
            generate_training_report=lambda owner: {'status': 'ok'},
        )

        @staticmethod
        def run_training_cycle():
            return None

    report = service.run_continuous(DummyController(), max_cycles=1)

    assert report == {'status': 'ok'}
    assert events and events[0][0] == 'refresh'


def test_training_cycle_data_service_prepares_context():
    class DummyDataManager:
        requested_mode = 'mock'

        def random_cutoff_date(self, *, min_date='20180101', max_date=None):
            return '20240201'

    class DummyController:
        current_cycle_id = 4
        experiment_seed = None
        experiment_min_date = None
        experiment_max_date = None
        data_manager = DummyDataManager()
        llm_mode = 'dry_run'

    service = TrainingCycleDataService()
    context = service.prepare_cycle_context(DummyController())

    assert context == TrainingCycleContext(
        cycle_id=5,
        cutoff_date='20240201',
        requested_data_mode='mock',
        llm_mode='dry_run',
        cutoff_policy_context={'mode': 'random'},
    )


def test_training_cycle_data_service_respects_fixed_cutoff_policy():
    class DummyDataManager:
        requested_mode = 'mock'

        def random_cutoff_date(self, *, min_date='20180101', max_date=None):
            raise AssertionError('fixed cutoff policy should not call random_cutoff_date')

    class DummyController:
        current_cycle_id = 4
        experiment_seed = None
        experiment_min_date = '20240101'
        experiment_max_date = '20241231'
        experiment_cutoff_policy = {'mode': 'fixed', 'date': '20240215'}
        data_manager = DummyDataManager()
        llm_mode = 'dry_run'

    service = TrainingCycleDataService()
    context = service.prepare_cycle_context(DummyController())

    assert context == TrainingCycleContext(
        cycle_id=5,
        cutoff_date='20240215',
        requested_data_mode='mock',
        llm_mode='dry_run',
        cutoff_policy_context={'mode': 'fixed'},
    )


def test_training_cycle_data_service_regime_balanced_selects_undercovered_regime():
    class DummyDataManager:
        requested_mode = 'mock'

        def __init__(self):
            self._dates = iter(['20240201', '20240220', '20240308'])

        def random_cutoff_date(self, *, min_date='20180101', max_date=None):
            del min_date, max_date
            return next(self._dates)

    class DummyRoutingService:
        @staticmethod
        def preview_governance(owner, *, cutoff_date, stock_count, min_history_days, allowed_manager_ids=None):
            del owner, stock_count, min_history_days, allowed_manager_ids
            mapping = {
                '20240201': {'regime': 'bull', 'regime_confidence': 0.81},
                '20240220': {'regime': 'oscillation', 'regime_confidence': 0.66},
                '20240308': {'regime': 'bear', 'regime_confidence': 0.73},
            }
            return mapping[cutoff_date]

    class DummyController:
        current_cycle_id = 4
        experiment_seed = None
        experiment_min_date = '20240101'
        experiment_max_date = '20241231'
        experiment_min_history_days = 180
        experiment_cutoff_policy = {'mode': 'regime_balanced', 'probe_count': 3}
        experiment_allowed_manager_ids = []
        cycle_history = [
            SimpleNamespace(
                governance_decision={
                    'dominant_manager_id': 'momentum',
                    'active_manager_ids': ['momentum'],
                    'manager_budget_weights': {'momentum': 1.0},
                    'regime': 'bull',
                },
            ),
            SimpleNamespace(
                governance_decision={
                    'dominant_manager_id': 'momentum',
                    'active_manager_ids': ['momentum'],
                    'manager_budget_weights': {'momentum': 1.0},
                    'regime': 'bull',
                },
            ),
            SimpleNamespace(
                governance_decision={
                    'dominant_manager_id': 'defensive_low_vol',
                    'active_manager_ids': ['defensive_low_vol'],
                    'manager_budget_weights': {'defensive_low_vol': 1.0},
                    'regime': 'bear',
                },
            ),
        ]
        data_manager = DummyDataManager()
        training_governance_service = DummyRoutingService()
        llm_mode = 'dry_run'
        last_cutoff_policy_context = {}

    service = TrainingCycleDataService()
    context = service.prepare_cycle_context(DummyController())

    assert context.cutoff_date == '20240220'
    assert context.cutoff_policy_context['mode'] == 'regime_balanced'
    assert context.cutoff_policy_context['target_regime'] == 'oscillation'
    assert context.cutoff_policy_context['selected_by'] == 'target_regime_match'


def test_training_cycle_data_service_loads_diagnostics_and_resolution():
    class DummyDataManager:
        last_resolution = {
            'effective_data_mode': 'offline',
            'degraded': False,
            'degrade_reason': '',
        }

        def diagnose_training_data(self, *, cutoff_date, stock_count, min_history_days):
            return {
                'ready': True,
                'cutoff_date': cutoff_date,
                'stock_count': stock_count,
                'min_history_days': min_history_days,
            }

        def load_stock_data(self, cutoff_date, *, stock_count, min_history_days, include_future_days):
            return {
                'sh.600519': {
                    'cutoff_date': cutoff_date,
                    'stock_count': stock_count,
                    'min_history_days': min_history_days,
                    'include_future_days': include_future_days,
                }
            }

    class DummyController:
        experiment_min_history_days = 160
        experiment_simulation_days = 40
        data_manager = DummyDataManager()

    service = TrainingCycleDataService()
    payload = service.load_training_data(
        DummyController(),
        cutoff_date='20240201',
        requested_data_mode='live',
    )

    assert payload == TrainingDataLoadResult(
        diagnostics={
            'ready': True,
            'cutoff_date': '20240201',
            'stock_count': config.max_stocks,
            'min_history_days': 160,
        },
        stock_data={
            'sh.600519': {
                'cutoff_date': '20240201',
                'stock_count': config.max_stocks,
                'min_history_days': 160,
                'include_future_days': 40,
            }
        },
        requested_data_mode='live',
        effective_data_mode='offline',
        data_mode='offline',
        degraded=False,
        degrade_reason='',
        min_history_days=160,
    )


def test_training_review_service_builds_eval_report():
    class DummyController:
        default_manager_id = 'momentum'
        default_manager_config_ref = 'src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml'

    class DummySimResult:
        return_pct = 1.25
        total_pnl = 1250.0
        total_trades = 4
        win_rate = 0.5

    service = TrainingReviewService()
    report = service.build_eval_report(
        DummyController(),
        cycle_id=9,
        cutoff_date='20240201',
        sim_result=DummySimResult(),
        regime_result={'regime': 'bull'},
        selected=['sh.600519'],
        cycle_payload={'benchmark_passed': True, 'sharpe_ratio': 1.1},
        trade_dicts=[{'symbol': 'sh.600519'}],
        requested_data_mode='live',
        effective_data_mode='offline',
        llm_mode='dry_run',
        degraded=False,
        degrade_reason='',
        data_mode='offline',
        selection_mode='meeting',
        agent_used=True,
        llm_used=False,
        manager_output=None,
        research_feedback={'recommendation': {'bias': 'maintain'}},
    )

    assert report.cycle_id == 9
    assert report.selected_codes == ['sh.600519']
    assert report.metadata['effective_data_mode'] == 'offline'
    assert report.metadata['research_feedback']['recommendation']['bias'] == 'maintain'
    compatibility = report.metadata['compatibility_fields']
    assert compatibility['derived'] is False
    assert compatibility['source'] == 'legacy_manager_output'
    assert compatibility['field_role'] == 'primary'
    assert compatibility['manager_id'] == 'momentum'
    assert str(compatibility['manager_config_ref']).endswith('src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml')


def test_training_simulation_service_builds_cycle_payloads_and_keeps_build_cycle_dict_as_compat_alias():
    service = TrainingSimulationService()
    trade = SimpleNamespace(
        date='20240202',
        action=SimpleNamespace(value='BUY'),
        ts_code='sh.600519',
        price=123.4,
        shares=100,
        pnl=0.0,
        pnl_pct=0.0,
        reason='entry',
        source='signal',
        entry_reason='breakout',
        exit_reason='',
        exit_trigger='',
        entry_date='20240202',
        entry_price=123.4,
        holding_days=0,
        stop_loss_price=120.0,
        take_profit_price=130.0,
        trailing_pct=None,
        capital_before=100000.0,
        capital_after=87660.0,
        open_price=122.0,
        high_price=124.0,
        low_price=121.5,
        volume=1000.0,
        amount=123400.0,
        pct_chg=1.2,
    )
    sim_result = SimpleNamespace(
        return_pct=2.5,
        total_pnl=2500.0,
        total_trades=1,
        winning_trades=1,
        losing_trades=0,
        win_rate=1.0,
        initial_capital=100000.0,
        final_value=102500.0,
        trade_history=[trade],
    )
    trading_plan = SimpleNamespace(source='meeting', max_positions=3)

    cycle = service.build_cycle_payload_projection(
        cycle_id=11,
        cutoff_date='20240201',
        sim_result=sim_result,
        selected=['sh.600519'],
        is_profit=True,
        regime_result={'regime': 'bull'},
        governance_decision={
            'dominant_manager_id': 'momentum',
            'active_manager_ids': ['momentum'],
            'manager_budget_weights': {'momentum': 1.0},
            'regime': 'bull',
        },
        trading_plan=trading_plan,
        data_mode='offline',
        requested_data_mode='live',
        effective_data_mode='offline',
        llm_mode='dry_run',
        degraded=False,
        degrade_reason='',
        selection_mode='meeting',
        agent_used=True,
        llm_used=False,
    )
    legacy_cycle = service.build_cycle_dict(
        cycle_id=11,
        cutoff_date='20240201',
        sim_result=sim_result,
        selected=['sh.600519'],
        is_profit=True,
        regime_result={'regime': 'bull'},
        governance_decision={
            'dominant_manager_id': 'momentum',
            'active_manager_ids': ['momentum'],
            'manager_budget_weights': {'momentum': 1.0},
            'regime': 'bull',
        },
        trading_plan=trading_plan,
        data_mode='offline',
        requested_data_mode='live',
        effective_data_mode='offline',
        llm_mode='dry_run',
        degraded=False,
        degrade_reason='',
        selection_mode='meeting',
        agent_used=True,
        llm_used=False,
    )
    trades = service.build_trade_dicts(sim_result)

    assert cycle['cycle_id'] == 11
    assert cycle['plan_source'] == 'meeting'
    assert cycle['governance_decision']['dominant_manager_id'] == 'momentum'
    assert legacy_cycle == cycle
    assert trades == [{
        'date': '20240202',
        'action': 'BUY',
        'ts_code': 'sh.600519',
        'price': 123.4,
        'shares': 100,
        'pnl': 0.0,
        'pnl_pct': 0.0,
        'reason': 'entry',
        'source': 'signal',
        'entry_reason': 'breakout',
        'exit_reason': '',
        'exit_trigger': '',
        'entry_date': '20240202',
        'entry_price': 123.4,
        'holding_days': 0,
        'stop_loss_price': 120.0,
        'take_profit_price': 130.0,
        'trailing_pct': None,
        'capital_before': 100000.0,
        'capital_after': 87660.0,
        'open_price': 122.0,
        'high_price': 124.0,
        'low_price': 121.5,
        'volume': 1000.0,
        'amount': 123400.0,
        'pct_chg': 1.2,
    }]


def test_training_simulation_service_sanitizes_non_finite_trade_payloads():
    service = TrainingSimulationService()
    trade = SimpleNamespace(
        date='20240202',
        action=SimpleNamespace(value='BUY'),
        ts_code='sh.600519',
        price=float('nan'),
        shares=100,
        pnl=float('inf'),
        pnl_pct=float('-inf'),
        reason='entry',
        source='signal',
        entry_reason='breakout',
        exit_reason='',
        exit_trigger='',
        entry_date='20240202',
        entry_price=float('nan'),
        holding_days=0,
        stop_loss_price=float('nan'),
        take_profit_price=float('nan'),
        trailing_pct=float('nan'),
        capital_before=float('nan'),
        capital_after=float('inf'),
        open_price=float('nan'),
        high_price=float('inf'),
        low_price=float('-inf'),
        volume=float('nan'),
        amount=float('nan'),
        pct_chg=float('nan'),
    )
    sim_result = SimpleNamespace(trade_history=[trade])

    trades = service.build_trade_dicts(sim_result)

    assert trades == [{
        'date': '20240202',
        'action': 'BUY',
        'ts_code': 'sh.600519',
        'price': 0.0,
        'shares': 100,
        'pnl': 0.0,
        'pnl_pct': 0.0,
        'reason': 'entry',
        'source': 'signal',
        'entry_reason': 'breakout',
        'exit_reason': '',
        'exit_trigger': '',
        'entry_date': '20240202',
        'entry_price': 0.0,
        'holding_days': 0,
        'stop_loss_price': 0.0,
        'take_profit_price': 0.0,
        'trailing_pct': None,
        'capital_before': 0.0,
        'capital_after': 0.0,
        'open_price': None,
        'high_price': None,
        'low_price': None,
        'volume': None,
        'amount': None,
        'pct_chg': None,
    }]


def test_training_simulation_service_evaluates_cycle():
    service = TrainingSimulationService()
    strategy_calls = {}
    benchmark_calls = {}

    class DummyController:
        class BenchmarkEvaluator:
            @staticmethod
            def evaluate(*, daily_values, benchmark_daily_values, trade_history):
                benchmark_calls['daily_values'] = list(daily_values)
                benchmark_calls['benchmark_daily_values'] = list(benchmark_daily_values or [])
                benchmark_calls['trade_history'] = list(trade_history)
                return SimpleNamespace(
                    passed=True,
                    sharpe_ratio=1.3,
                    max_drawdown=-0.05,
                    excess_return=0.08,
                    benchmark_return=0.03,
                )

        class StrategyEvaluator:
            @staticmethod
            def evaluate(cycle_dict, trade_dicts, daily_records):
                strategy_calls['cycle_id'] = cycle_dict['cycle_id']
                strategy_calls['trade_count'] = len(trade_dicts)
                strategy_calls['daily_records'] = list(daily_records)
                return SimpleNamespace(
                    signal_accuracy=0.8,
                    timing_score=0.7,
                    risk_control_score=0.9,
                    overall_score=0.82,
                    suggestions=['hold discipline'],
                )

        benchmark_evaluator = BenchmarkEvaluator()
        strategy_evaluator = StrategyEvaluator()

    sim_result = SimpleNamespace(
        daily_records=[
            {'total_value': 100000.0},
            {'total_value': 101000.0},
        ],
    )
    cycle_payload: dict[str, object] = {'cycle_id': 12}
    trade_dicts = [{'action': 'SELL', 'pnl': 100.0}]

    benchmark_passed = service.evaluate_cycle(
        DummyController(),
        cycle_payload=cycle_payload,
        trade_dicts=trade_dicts,
        sim_result=sim_result,
        benchmark_daily_values=[3000.0, 3015.0],
    )

    assert benchmark_passed is True
    assert cycle_payload['benchmark_passed'] is True
    assert cycle_payload['benchmark_source'] == 'index_bar:sh.000300'
    strategy_scores = cast(dict[str, object], cycle_payload['strategy_scores'])
    assert strategy_scores['overall_score'] == 0.82
    assert benchmark_calls['daily_values'] == [100000.0, 101000.0]
    assert strategy_calls['trade_count'] == 1


def test_training_simulation_service_evaluate_cycle_summary_does_not_mutate_payload():
    service = TrainingSimulationService()

    class DummyController:
        class BenchmarkEvaluator:
            @staticmethod
            def evaluate(*, daily_values, benchmark_daily_values, trade_history):
                return SimpleNamespace(
                    passed=True,
                    sharpe_ratio=1.1,
                    max_drawdown=-0.03,
                    excess_return=0.04,
                    benchmark_return=0.02,
                )

        class StrategyEvaluator:
            @staticmethod
            def evaluate(cycle_dict, trade_dicts, daily_records):
                assert cycle_dict['cycle_id'] == 42
                return SimpleNamespace(
                    signal_accuracy=0.7,
                    timing_score=0.8,
                    risk_control_score=0.9,
                    overall_score=0.81,
                    suggestions=['stay systematic'],
                )

        benchmark_evaluator = BenchmarkEvaluator()
        strategy_evaluator = StrategyEvaluator()

    sim_result = SimpleNamespace(
        daily_records=[
            {'total_value': 100000.0},
            {'total_value': 100500.0},
        ],
    )
    cycle_payload = {'cycle_id': 42}

    summary = service.evaluate_cycle_summary(
        DummyController(),
        cycle_payload=cycle_payload,
        trade_dicts=[{'action': 'BUY'}],
        sim_result=sim_result,
        benchmark_daily_values=[3000.0, 3010.0],
    )

    assert cycle_payload == {'cycle_id': 42}
    assert summary['benchmark_passed'] is True
    assert summary['strategy_scores']['overall_score'] == 0.81


def test_training_simulation_service_build_trader_prefers_session_state(monkeypatch):
    service = TrainingSimulationService()
    captured = {}

    class DummyTrader:
        def __init__(self, **kwargs):
            captured['kwargs'] = dict(kwargs)

        def set_stock_data(self, selected_data):
            captured['selected_data'] = dict(selected_data)

        def set_stock_info(self, stock_info):
            captured['stock_info'] = dict(stock_info)

        def set_trading_plan(self, trading_plan):
            captured['trading_plan'] = trading_plan

    monkeypatch.setitem(
        service.build_trader.__func__.__globals__,
        'SimulatedTrader',
        DummyTrader,
    )

    controller = SimpleNamespace(
        session_state=TrainingSessionState(current_params={'position_size': 0.33}),
        execution_policy={},
        risk_policy={'max_drawdown': 0.1},
    )
    trading_plan = SimpleNamespace(max_positions=2)

    service.build_trader(
        controller,
        selected_data={'sh.600519': SimpleNamespace(columns=['trade_date'])},
        trading_plan=trading_plan,
    )

    assert captured['kwargs']['position_size_pct'] == 0.33
    assert captured['kwargs']['risk_policy'] == {'max_drawdown': 0.1}
    assert captured['trading_plan'] is trading_plan


def test_training_ab_service_build_trader_prefers_session_state():
    service = TrainingABService()
    captured = {}

    class DummyManagerRuntime:
        @staticmethod
        def execution_param(_name, default=None):
            return default

        @staticmethod
        def param(name, default=None):
            if name == 'position_size':
                return 0.4
            return default

        @staticmethod
        def config_section(_name, default=None):
            return default or {}

        @staticmethod
        def risk_param(_name):
            return None

    controller = SimpleNamespace(
        session_state=TrainingSessionState(current_params={'position_size': 0.21, 'cash_reserve': 0.18}),
        current_params={'position_size': 0.99, 'cash_reserve': 0.01},
        execution_policy={},
        risk_policy={},
        training_simulation_service=SimpleNamespace(
            build_trader=lambda runtime_owner, *, selected_data, trading_plan: captured.update(
                {
                    'runtime_owner': runtime_owner,
                    'selected_data': dict(selected_data),
                    'trading_plan': trading_plan,
                }
            )
            or 'trader'
        ),
    )

    result = service._build_trader(
        controller,
        manager_runtime=DummyManagerRuntime(),
        selected_data={'sh.600519': {'rows': 5}},
        trading_plan=SimpleNamespace(max_positions=2),
    )

    assert result == 'trader'
    assert captured['runtime_owner'].current_params['cash_reserve'] == 0.18
    assert captured['runtime_owner'].current_params['position_size'] == 0.4


def test_training_ab_service_derive_trading_plan_caps_weight_hint_by_position_size_and_execution_limit():
    service = TrainingABService()
    manager_output = SimpleNamespace(
        manager_id='momentum',
        signal_packet=SimpleNamespace(
            selected_codes=['AAA', 'BBB', 'CCC'],
            max_positions=3,
            as_of_date='20240201',
            reasoning='bull picks',
            params={
                'position_size': 0.06,
                'max_hold_days': 24,
                'stop_loss_pct': 0.05,
                'take_profit_pct': 0.15,
                'trailing_pct': 0.08,
            },
            signals=[
                SimpleNamespace(code='AAA', weight_hint=0.25),
                SimpleNamespace(code='BBB', weight_hint=0.25),
                SimpleNamespace(code='CCC', weight_hint=0.25),
            ],
            cash_reserve=0.393,
        ),
        agent_context=SimpleNamespace(summary='momentum summary'),
    )

    trading_plan = service._derive_trading_plan(manager_output)

    assert trading_plan.max_positions == 3
    assert [position.code for position in trading_plan.positions] == ['AAA', 'BBB', 'CCC']
    assert all(position.weight <= 0.20 for position in trading_plan.positions)
    assert all(abs(position.weight - 0.06) < 1e-9 for position in trading_plan.positions)
    assert abs(trading_plan.cash_reserve - 0.82) < 1e-9


def test_training_ab_service_evaluate_arm_projects_runtime_scope_when_output_lacks_identity(
    monkeypatch,
):
    service = TrainingABService()

    class DummyRuntime:
        @staticmethod
        def process(_stock_data, _cutoff_date):
            return SimpleNamespace(
                signal_packet=SimpleNamespace(
                    selected_codes=[],
                    max_positions=1,
                    as_of_date='20240201',
                    regime='bull',
                    reasoning='no picks',
                    params={},
                    signals=[],
                    cash_reserve=1.0,
                ),
                agent_context=None,
            )

    monkeypatch.setattr(
        'invest_evolution.application.training.policy.build_manager_runtime',
        lambda *args, **kwargs: DummyRuntime(),
    )

    payload = service._evaluate_arm(
        SimpleNamespace(experiment_simulation_days=2),
        cycle_id=3,
        cutoff_date='20240201',
        stock_data={'sh.600519': {'rows': 1}},
        manager_id='momentum',
        runtime_config_ref='configs/candidate.yaml',
        arm_name='candidate',
    )

    assert payload['status'] == 'no_selection'
    assert payload['manager_id'] == 'momentum'
    assert payload['manager_config_ref'].endswith('configs/candidate.yaml')


def test_training_ab_service_project_arm_manager_prefers_runtime_scope_over_controller_defaults():
    service = TrainingABService()
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id='defensive',
            default_manager_config_ref='configs/defensive.yaml',
        ),
    )

    projection = service._project_arm_manager(
        controller,
        manager_output=SimpleNamespace(
            manager_id='value_quality',
            manager_config_ref='configs/output_value.yaml',
        ),
        manager_id='value_quality',
        runtime_config_ref='configs/runtime_value.yaml',
    )

    assert projection.manager_id == 'value_quality'
    assert projection.manager_config_ref == 'configs/runtime_value.yaml'


def test_training_research_service_prefers_session_state_governance(monkeypatch):
    service = TrainingResearchService()
    captured = {}

    monkeypatch.setattr(
        'invest_evolution.application.training.research.resolve_policy_snapshot',
        lambda **kwargs: captured.update({'governance_context': dict(kwargs['governance_context'])})
        or SimpleNamespace(policy_id='policy-1'),
    )
    monkeypatch.setattr(
        'invest_evolution.application.training.research.build_research_snapshot',
        lambda **kwargs: SimpleNamespace(cross_section_context={}),
    )
    monkeypatch.setattr(
        'invest_evolution.application.training.research.build_research_hypothesis',
        lambda **kwargs: {'summary': 'ok'},
    )

    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            last_governance_decision={'regime': 'bear', 'dominant_manager_id': 'defensive'},
            default_manager_id='defensive',
        ),
        last_governance_decision={'regime': 'bull', 'dominant_manager_id': 'momentum'},
        manager_runtime=None,
        experiment_min_history_days=120,
        experiment_simulation_days=20,
        research_case_store=SimpleNamespace(
            save_case=lambda **kwargs: {'research_case_id': 'case-1'},
            write_calibration_report=lambda policy_id: {'path': f'/tmp/{policy_id}.json'},
        ),
        research_scenario_engine=SimpleNamespace(estimate=lambda **kwargs: {'stance': 'watch'}),
        research_attribution_engine=SimpleNamespace(
            evaluate_case=lambda _case_record: SimpleNamespace(
                to_dict=lambda: {'horizon_results': {'T+20': {'label': 'timeout'}}}
            )
        ),
        research_market_repository=None,
    )

    payload = service.persist_cycle_research_artifacts(
        controller,
        cycle_id=5,
        cutoff_date='20240201',
        manager_output=SimpleNamespace(manager_id='defensive', manager_config_ref='configs/defensive.yaml'),
        stock_data={'sh.600519': SimpleNamespace(empty=True, columns=[])},
        selected=['sh.600519'],
        regime_result={'regime': 'oscillation'},
        selection_mode='meeting',
    )

    assert captured['governance_context']['regime'] == 'bear'
    assert payload['saved_case_count'] == 1
    assert payload['requested_regime'] == 'bear'


def test_training_research_service_prefers_manager_projection_over_controller_default(monkeypatch):
    service = TrainingResearchService()
    captured = {}

    monkeypatch.setattr(
        'invest_evolution.application.training.research.resolve_policy_snapshot',
        lambda **kwargs: captured.update({'manager_id': kwargs['manager_id']})
        or SimpleNamespace(policy_id='policy-2'),
    )
    monkeypatch.setattr(
        'invest_evolution.application.training.research.build_research_snapshot',
        lambda **kwargs: SimpleNamespace(cross_section_context={}),
    )
    monkeypatch.setattr(
        'invest_evolution.application.training.research.build_research_hypothesis',
        lambda **kwargs: {'summary': 'ok'},
    )

    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            last_governance_decision={'regime': 'bull', 'dominant_manager_id': 'value_quality'},
            default_manager_id='defensive',
            default_manager_config_ref='configs/defensive.yaml',
        ),
        manager_runtime=None,
        experiment_min_history_days=120,
        experiment_simulation_days=20,
        research_case_store=SimpleNamespace(
            save_case=lambda **kwargs: {'research_case_id': 'case-2'},
            write_calibration_report=lambda policy_id: {'path': f'/tmp/{policy_id}.json'},
        ),
        research_scenario_engine=SimpleNamespace(estimate=lambda **kwargs: {'stance': 'watch'}),
        research_attribution_engine=SimpleNamespace(
            evaluate_case=lambda _case_record: SimpleNamespace(
                to_dict=lambda: {'horizon_results': {'T+20': {'label': 'timeout'}}}
            )
        ),
        research_market_repository=None,
    )

    service.persist_cycle_research_artifacts(
        controller,
        cycle_id=8,
        cutoff_date='20240201',
        manager_output=SimpleNamespace(
            manager_id='value_quality',
            manager_config_ref='configs/value_quality.yaml',
        ),
        stock_data={'sh.600519': SimpleNamespace(empty=True, columns=[])},
        selected=['sh.600519'],
        regime_result={'regime': 'bull'},
        selection_mode='manager_portfolio',
    )

    assert captured['manager_id'] == 'value_quality'


def test_training_research_service_prefers_manager_output_identity_over_default_manager(
    monkeypatch,
):
    service = TrainingResearchService()
    captured = {}

    monkeypatch.setattr(
        'invest_evolution.application.training.research.resolve_policy_snapshot',
        lambda **kwargs: captured.update(
            {
                'manager_id': kwargs['manager_id'],
                'metadata': dict(kwargs['metadata']),
            }
        )
        or SimpleNamespace(policy_id='policy-1'),
    )
    monkeypatch.setattr(
        'invest_evolution.application.training.research.build_research_snapshot',
        lambda **kwargs: SimpleNamespace(cross_section_context={}),
    )
    monkeypatch.setattr(
        'invest_evolution.application.training.research.build_research_hypothesis',
        lambda **kwargs: {'summary': 'ok'},
    )

    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            last_governance_decision={'regime': 'bear', 'dominant_manager_id': 'defensive'},
            default_manager_id='defensive',
            default_manager_config_ref='configs/defensive.yaml',
        ),
        last_governance_decision={'regime': 'bull', 'dominant_manager_id': 'momentum'},
        manager_runtime=None,
        experiment_min_history_days=120,
        experiment_simulation_days=20,
        research_case_store=SimpleNamespace(
            save_case=lambda **kwargs: {'research_case_id': 'case-2'},
            write_calibration_report=lambda policy_id: {'path': f'/tmp/{policy_id}.json'},
        ),
        research_scenario_engine=SimpleNamespace(estimate=lambda **kwargs: {'stance': 'watch'}),
        research_attribution_engine=SimpleNamespace(
            evaluate_case=lambda _case_record: SimpleNamespace(
                to_dict=lambda: {'horizon_results': {'T+20': {'label': 'timeout'}}}
            )
        ),
        research_market_repository=None,
    )

    payload = service.persist_cycle_research_artifacts(
        controller,
        cycle_id=6,
        cutoff_date='20240201',
        manager_output=SimpleNamespace(
            manager_id='value_quality',
            manager_config_ref='configs/value_quality.yaml',
        ),
        stock_data={'sh.600519': SimpleNamespace(empty=True, columns=[])},
        selected=['sh.600519'],
        regime_result={'regime': 'oscillation'},
        selection_mode='manager_portfolio',
    )

    assert captured['manager_id'] == 'value_quality'
    assert captured['metadata']['manager_config_ref'] == 'configs/value_quality.yaml'
    assert payload['saved_case_count'] == 1


def test_training_research_service_uses_signal_packet_identity_when_manager_output_fields_are_blank(
    monkeypatch,
):
    service = TrainingResearchService()
    captured = {}

    monkeypatch.setattr(
        'invest_evolution.application.training.research.resolve_policy_snapshot',
        lambda **kwargs: captured.update(
            {
                'manager_id': kwargs['manager_id'],
                'metadata': dict(kwargs['metadata']),
            }
        )
        or SimpleNamespace(policy_id='policy-signal-packet'),
    )
    monkeypatch.setattr(
        'invest_evolution.application.training.research.build_research_snapshot',
        lambda **kwargs: SimpleNamespace(cross_section_context={}),
    )
    monkeypatch.setattr(
        'invest_evolution.application.training.research.build_research_hypothesis',
        lambda **kwargs: {'summary': 'ok'},
    )

    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            last_governance_decision={'regime': 'bear', 'dominant_manager_id': 'momentum'},
            default_manager_id='momentum',
            default_manager_config_ref='configs/momentum.yaml',
        ),
        last_governance_decision={'regime': 'bull', 'dominant_manager_id': 'momentum'},
        manager_runtime=None,
        experiment_min_history_days=120,
        experiment_simulation_days=20,
        research_case_store=SimpleNamespace(
            save_case=lambda **kwargs: {'research_case_id': 'case-signal-packet'},
            write_calibration_report=lambda policy_id: {'path': f'/tmp/{policy_id}.json'},
        ),
        research_scenario_engine=SimpleNamespace(estimate=lambda **kwargs: {'stance': 'watch'}),
        research_attribution_engine=SimpleNamespace(
            evaluate_case=lambda _case_record: SimpleNamespace(
                to_dict=lambda: {'horizon_results': {'T+20': {'label': 'timeout'}}}
            )
        ),
        research_market_repository=None,
    )

    payload = service.persist_cycle_research_artifacts(
        controller,
        cycle_id=7,
        cutoff_date='20240201',
        manager_output=SimpleNamespace(
            manager_id='',
            manager_config_ref='',
            signal_packet=SimpleNamespace(
                manager_id='defensive_low_vol',
                manager_config_ref='configs/defensive_low_vol.yaml',
            ),
        ),
        stock_data={'sh.600519': SimpleNamespace(empty=True, columns=[])},
        selected=['sh.600519'],
        regime_result={'regime': 'oscillation'},
        selection_mode='manager_portfolio',
    )

    assert captured['manager_id'] == 'defensive_low_vol'
    assert captured['metadata']['manager_config_ref'] == 'configs/defensive_low_vol.yaml'
    assert payload['saved_case_count'] == 1


def test_training_governance_service_refreshes_controller_coordinator():
    service = TrainingGovernanceService()

    class DummyController:
        governance_policy = {'bull_avg_change_20d': 4.0}
        governance_min_confidence = 0.7
        governance_cooldown_cycles = 3
        governance_hysteresis_margin = 0.12
        governance_agent_override_max_gap = 0.2

    controller = DummyController()
    coordinator = service.refresh_governance_coordinator(controller)

    assert getattr(controller, 'governance_coordinator') is coordinator
    assert coordinator.min_confidence == 0.7
    assert coordinator.cooldown_cycles == 3
    assert coordinator.hysteresis_margin == 0.12


def test_training_policy_service_sanitizes_runtime_param_adjustments():
    service = TrainingPolicyService()

    class DummyController:
        risk_policy = {
            'stop_loss_pct': {'min': 0.02, 'max': 0.15},
            'take_profit_pct': {'min': 0.04, 'max': 0.40},
            'position_size': {'min': 0.02, 'max': 0.30},
        }
        review_policy = {
            'param_clamps': {
                'cash_reserve': {'min': 0.0, 'max': 0.6},
                'trailing_pct': {'min': 0.04, 'max': 0.12},
            }
        }

    cleaned = service.sanitize_runtime_param_adjustments(
        DummyController(),
        {
            'stop_loss_pct': 0.5,
            'take_profit_pct': 0.01,
            'position_size': 0.9,
            'cash_reserve': 0.9,
            'trailing_pct': 0.2,
        },
    )

    assert cleaned['stop_loss_pct'] == 0.15
    assert cleaned['take_profit_pct'] == 0.05
    assert cleaned['position_size'] == 0.3
    assert cleaned['cash_reserve'] == 0.6
    assert cleaned['trailing_pct'] == 0.12


def test_training_feedback_service_builds_research_feedback_brief():
    summary = TrainingFeedbackService.research_feedback_brief(
        {
            'sample_count': 6,
            'recommendation': {'bias': 'tighten_risk'},
            'brier_like_direction_score': 0.31,
            'horizons': {'T+20': {'hit_rate': 0.42}},
        }
    )

    assert summary['sample_count'] == 6
    assert summary['bias'] == 'tighten_risk'
    assert summary['brier_like_direction_score'] == 0.31
    assert summary['t20_hit_rate'] == 0.42


def test_training_feedback_service_builds_research_feedback_summary():
    summary = TrainingFeedbackService.research_feedback_summary(
        {
            'sample_count': 6,
            'recommendation': {'bias': 'tighten_risk', 'summary': 'tighten now'},
            'brier_like_direction_score': 0.31,
            'horizons': {'T+20': {'hit_rate': 0.42, 'invalidation_rate': 0.37}},
        },
        source={'cycle_id': 9},
    )

    assert summary['available'] is True
    assert summary['source']['cycle_id'] == 9
    assert summary['summary'] == 'tighten now'
    assert summary['t20_invalidation_rate'] == 0.37
    assert summary['available_horizons'] == ['T+20']


def test_training_feedback_service_loads_and_caches_research_feedback():
    service = TrainingFeedbackService()
    calls = []
    controller = SimpleNamespace(
        research_case_store=SimpleNamespace(
            build_training_feedback=lambda **kwargs: calls.append(dict(kwargs)) or {
                'sample_count': 7,
                'recommendation': {'bias': 'tighten_risk'},
            }
        ),
        last_research_feedback={},
        research_feedback_policy={},
    )

    payload = service.load_research_feedback(
        controller,
        cutoff_date='20240201',
        manager_id='value_quality',
        manager_config_ref='configs/value_quality.yaml',
        regime='bear',
    )

    assert payload['sample_count'] == 7
    assert controller.last_research_feedback['recommendation']['bias'] == 'tighten_risk'
    assert calls[0]['manager_id'] == 'value_quality'
    assert calls[0]['manager_config_ref'] == 'configs/value_quality.yaml'
    assert calls[0]['limit'] == 200


def test_training_feedback_service_loads_research_feedback_with_history_limit_from_policy():
    service = TrainingFeedbackService()
    calls = []
    controller = SimpleNamespace(
        research_case_store=SimpleNamespace(
            build_training_feedback=lambda **kwargs: calls.append(dict(kwargs)) or {
                'sample_count': 11,
                'recommendation': {'bias': 'maintain'},
            }
        ),
        last_research_feedback={},
        research_feedback_policy={'history_limit': 28},
    )

    payload = service.load_research_feedback(
        controller,
        cutoff_date='20240201',
        manager_id='momentum',
        manager_config_ref='src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml',
        regime='bull',
    )

    assert payload['sample_count'] == 11
    assert calls[0]['limit'] == 28


def test_training_feedback_service_builds_plan_via_boundary_sanitize(monkeypatch):
    service = TrainingFeedbackService()
    captured = {}

    def fake_sanitize(owner, adjustments=None):
        captured['owner'] = owner
        captured['adjustments'] = dict(adjustments or {})
        return {'position_size': 0.08}

    controller = SimpleNamespace(
        training_policy_service=SimpleNamespace(
            sanitize_runtime_param_adjustments=fake_sanitize
        ),
        research_feedback_optimization_policy={},
        freeze_gate_policy={},
        freeze_total_cycles=10,
        current_params={
            'position_size': 0.2,
            'cash_reserve': 0.1,
            'max_hold_days': 20,
            'stop_loss_pct': 0.08,
            'take_profit_pct': 0.18,
            'trailing_pct': 0.06,
            'signal_threshold': 0.5,
        },
        freeze_gate_service=SimpleNamespace(
            rolling_self_assessment=lambda owner, window=None: {
                'benchmark_pass_rate': 0.4,
            }
        ),
    )

    payload = service.build_feedback_optimization_plan(
        controller,
        {
            'sample_count': 8,
            'recommendation': {'bias': 'tighten_risk', 'summary': 'tighten'},
            'horizons': {
                'T+20': {'hit_rate': 0.3, 'invalidation_rate': 0.4, 'interval_hit_rate': 0.3},
            },
            'brier_like_direction_score': 0.31,
        },
        cycle_id=9,
    )

    assert payload['param_adjustments'] == {'position_size': 0.08}
    assert captured['owner'] is controller
    assert 'cash_reserve' in captured['adjustments']
    assert 'position_size' in captured['adjustments']


def test_freeze_gate_service_resolves_controller_hook_and_syncs_evaluation(tmp_path):
    service = FreezeGateService()

    controller = SimpleNamespace(
        freeze_total_cycles=10,
        freeze_profit_required=7,
        freeze_gate_policy={},
        last_research_feedback={},
        last_freeze_gate_evaluation={},
        session_state=TrainingSessionState(
            cycle_history=[
                SimpleNamespace(
                    cycle_id=1,
                    return_pct=1.0,
                    is_profit=True,
                    benchmark_passed=True,
                    strategy_scores={},
                    promotion_record={},
                    lineage_record={},
                )
            ],
            current_params={'position_size': 0.2},
        ),
        assessment_history=[],
        output_dir=str(tmp_path / 'training'),
        _rolling_self_assessment=lambda window=None: {
            'window': window,
            'profit_count': 8,
            'win_rate': 0.8,
            'avg_return': 1.2,
            'avg_sharpe': 1.0,
            'avg_max_drawdown': 8.0,
            'avg_excess_return': 0.7,
            'benchmark_pass_rate': 0.8,
        },
    )

    evaluation = service.evaluate_freeze_gate(controller)
    report = service.freeze_runtime_state(controller)

    assert evaluation == controller.last_freeze_gate_evaluation
    assert controller.last_freeze_gate_evaluation == report['freeze_gate_evaluation']


def test_training_llm_runtime_service_applies_runtime_overrides_and_dry_run():
    service = TrainingLLMRuntimeService()

    class DummyLLM:
        def __init__(self):
            self.dry_run = False
            self.calls = []

        def apply_runtime_limits(self, *, timeout=None, max_retries=None):
            self.calls.append({'timeout': timeout, 'max_retries': max_retries})

    shared = DummyLLM()
    optimizer_llm = DummyLLM()

    class DummyController:
        llm_caller = shared
        llm_mode = 'live'
        agents = {
            'trend_hunter': SimpleNamespace(llm=shared),
            'strategist': SimpleNamespace(llm=DummyLLM()),
        }
        llm_optimizer = SimpleNamespace(llm=optimizer_llm)

    controller = DummyController()
    service.apply_experiment_overrides(
        controller,
        {'timeout': 11, 'max_retries': 2, 'dry_run': True},
    )
    service.set_dry_run(controller, False)

    assert shared.calls == [{'timeout': 11, 'max_retries': 2}]
    assert controller.agents['strategist'].llm.calls == [{'timeout': 11, 'max_retries': 2}]
    assert optimizer_llm.calls == [{'timeout': 11, 'max_retries': 2}]
    assert controller.llm_mode == 'live'
    assert shared.dry_run is False


def test_training_governance_service_sync_runtime_from_config_reloads_on_manager_runtime_change(monkeypatch):
    service = TrainingGovernanceService()
    refresh_calls = {}
    reload_calls = {}

    monkeypatch.setattr(config, 'default_manager_id', 'value_quality')
    monkeypatch.setattr(config, 'default_manager_config_ref', 'configs/value.yaml')
    monkeypatch.setattr(config, 'allocator_enabled', True)
    monkeypatch.setattr(config, 'allocator_top_n', 4)
    monkeypatch.setattr(config, 'governance_enabled', True)
    monkeypatch.setattr(config, 'governance_mode', 'hybrid')
    monkeypatch.setattr(config, 'governance_allowed_manager_ids', ['value_quality', 'momentum'])
    monkeypatch.setattr(config, 'governance_cooldown_cycles', 6)
    monkeypatch.setattr(config, 'governance_min_confidence', 0.72)
    monkeypatch.setattr(config, 'governance_hysteresis_margin', 0.11)
    monkeypatch.setattr(config, 'governance_agent_override_enabled', True)
    monkeypatch.setattr(config, 'governance_agent_override_max_gap', 0.22)
    monkeypatch.setattr(config, 'governance_policy', {'bull_avg_change_20d': 4.5})

    def fake_refresh(owner):
        refresh_calls['owner'] = owner

    def fake_reload(owner, runtime_config_ref=None):
        reload_calls['owner'] = owner
        reload_calls['runtime_config_ref'] = runtime_config_ref

    monkeypatch.setattr(service, 'refresh_governance_coordinator', fake_refresh)
    monkeypatch.setattr(service, 'reload_manager_runtime', fake_reload)

    class DummyController:
        default_manager_id = 'momentum'
        default_manager_config_ref = 'configs/momentum.yaml'
        allocator_enabled = False
        allocator_top_n = 3
        governance_enabled = False
        governance_mode = 'rule'
        governance_allowed_manager_ids = ['momentum']
        governance_cooldown_cycles = 2
        governance_min_confidence = 0.6
        governance_hysteresis_margin = 0.08
        governance_agent_override_enabled = False
        governance_agent_override_max_gap = 0.18
        governance_policy = {}
        current_params = {'position_size': 0.2}

    controller = DummyController()
    service.sync_runtime_from_config(controller)

    assert refresh_calls['owner'] is controller
    assert reload_calls['owner'] is controller
    assert reload_calls['runtime_config_ref'] == 'configs/value.yaml'
    assert controller.default_manager_id == 'value_quality'
    assert controller.current_params == {}
    assert controller.governance_mode == 'hybrid'


def test_runtime_config_projection_from_live_config_preserves_config_and_owner_fallbacks(
    monkeypatch,
):
    monkeypatch.setattr(config, 'default_manager_id', '')
    monkeypatch.setattr(config, 'default_manager_config_ref', '')
    monkeypatch.setattr(config, 'allocator_enabled', False)
    monkeypatch.setattr(config, 'allocator_top_n', 0)
    monkeypatch.setattr(config, 'manager_active_ids', [' momentum ', '', 'value_quality'])
    monkeypatch.setattr(config, 'manager_budget_weights', {'momentum': '0.7', 'value_quality': 0.3})
    monkeypatch.setattr(config, 'governance_enabled', False)
    monkeypatch.setattr(config, 'governance_mode', ' HYBRID ')
    monkeypatch.setattr(config, 'governance_allowed_manager_ids', [' momentum ', 'value_quality'])
    monkeypatch.setattr(config, 'governance_cooldown_cycles', 0)
    monkeypatch.setattr(config, 'governance_min_confidence', 0)
    monkeypatch.setattr(config, 'governance_hysteresis_margin', 0)
    monkeypatch.setattr(config, 'governance_agent_override_enabled', True)
    monkeypatch.setattr(config, 'governance_agent_override_max_gap', 0)
    monkeypatch.setattr(config, 'governance_policy', {'bull_avg_change_20d': 4.5})

    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id='value_quality',
            default_manager_config_ref='configs/value.yaml',
        ),
        allocator_enabled=True,
        allocator_top_n=4,
        manager_active_ids=['growth'],
        manager_budget_weights={'growth': 1.0},
        governance_enabled=True,
        governance_mode='rule',
        governance_allowed_manager_ids=['growth'],
        governance_cooldown_cycles=5,
        governance_min_confidence=0.61,
        governance_hysteresis_margin=0.09,
        governance_agent_override_enabled=False,
        governance_agent_override_max_gap=0.2,
        governance_policy={'owner': True},
    )

    projection = runtime_config_projection_from_live_config(controller)

    assert projection['default_manager_id'] == 'value_quality'
    assert projection['default_manager_config_ref'] == 'configs/value.yaml'
    assert projection['allocator_enabled'] is False
    assert projection['allocator_top_n'] == 4
    assert projection['manager_active_ids'] == ['momentum', 'value_quality']
    assert projection['manager_budget_weights'] == {'momentum': 0.7, 'value_quality': 0.3}
    assert projection['governance_enabled'] is False
    assert projection['governance_mode'] == 'hybrid'
    assert projection['governance_allowed_manager_ids'] == ['momentum', 'value_quality']
    assert projection['governance_cooldown_cycles'] == 5
    assert projection['governance_min_confidence'] == 0.61
    assert projection['governance_hysteresis_margin'] == 0.09
    assert projection['governance_agent_override_enabled'] is True
    assert projection['governance_agent_override_max_gap'] == 0.2
    assert projection['governance_policy'] == {'bull_avg_change_20d': 4.5}


def test_training_governance_service_sync_runtime_from_config_refreshes_manager_runtime_contract(monkeypatch):
    service = TrainingGovernanceService()

    monkeypatch.setattr(config, 'default_manager_id', 'momentum')
    monkeypatch.setattr(config, 'default_manager_config_ref', 'configs/momentum.yaml')
    monkeypatch.setattr(config, 'manager_arch_enabled', True)
    monkeypatch.setattr(config, 'manager_shadow_mode', True)
    monkeypatch.setattr(config, 'manager_allocator_enabled', True)
    monkeypatch.setattr(config, 'portfolio_assembly_enabled', True)
    monkeypatch.setattr(config, 'dual_review_enabled', True)
    monkeypatch.setattr(config, 'manager_persistence_enabled', True)
    monkeypatch.setattr(config, 'manager_active_ids', [' momentum ', '', 'value_quality'])
    monkeypatch.setattr(config, 'manager_budget_weights', {'momentum': '0.7', 'value_quality': 0.3})
    monkeypatch.setattr(service, 'refresh_governance_coordinator', lambda owner: owner)
    monkeypatch.setattr(service, 'reload_manager_runtime', lambda owner, runtime_config_ref=None: None)

    class DummyController:
        default_manager_id = 'momentum'
        default_manager_config_ref = 'configs/momentum.yaml'
        allocator_enabled = False
        allocator_top_n = 3
        manager_arch_enabled = False
        manager_shadow_mode = False
        manager_allocator_enabled = False
        portfolio_assembly_enabled = False
        dual_review_enabled = False
        manager_persistence_enabled = False
        manager_active_ids = []
        manager_budget_weights = {}
        governance_enabled = True
        governance_mode = 'rule'
        governance_allowed_manager_ids = ['momentum']
        governance_cooldown_cycles = 2
        governance_min_confidence = 0.6
        governance_hysteresis_margin = 0.08
        governance_agent_override_enabled = False
        governance_agent_override_max_gap = 0.18
        governance_policy = {}
        effective_runtime_mode = ''
        runtime_contract_version = 0

    controller = DummyController()
    service.sync_runtime_from_config(controller)

    assert controller.manager_arch_enabled is True
    assert controller.manager_shadow_mode is True
    assert controller.manager_allocator_enabled is True
    assert controller.portfolio_assembly_enabled is True
    assert controller.dual_review_enabled is True
    assert controller.manager_persistence_enabled is True
    assert controller.manager_active_ids == ['momentum', 'value_quality']
    assert controller.manager_budget_weights == {'momentum': 0.7, 'value_quality': 0.3}
    assert controller.effective_runtime_mode == 'manager_portfolio'
    assert controller.runtime_contract_version == 1


def test_training_governance_service_apply_governance_updates_state_and_emits_events(monkeypatch):
    service = TrainingGovernanceService()
    events = []

    decision = SimpleNamespace(
        regime='bull',
        regime_confidence=0.83,
        regime_source='rule',
        evidence={'rule_result': {'reasoning': 'trend improving'}},
        reasoning='trend improving',
        active_manager_ids=['momentum', 'value_quality'],
        manager_budget_weights={'momentum': 0.7, 'value_quality': 0.3},
        dominant_manager_id='momentum',
        decision_confidence=0.79,
        decision_source='router',
        guardrail_checks=[{'name': 'cooldown', 'passed': True}],
        allocation_plan={'top_n': 2},
        cash_reserve_hint=0.15,
        portfolio_constraints={'cash_reserve': 0.15, 'top_n': 2},
        metadata={'historical': {'guardrail_hold': False}},
        to_dict=lambda: {
            'regime': 'bull',
            'active_manager_ids': ['momentum', 'value_quality'],
            'manager_budget_weights': {'momentum': 0.7, 'value_quality': 0.3},
            'dominant_manager_id': 'momentum',
        },
    )

    monkeypatch.setattr(service, 'decide_governance', lambda *args, **kwargs: decision)

    class DummyController:
        governance_enabled = True
        governance_mode = 'rule'
        default_manager_id = 'momentum'
        data_manager = object()
        output_dir = 'outputs/training'
        experiment_allowed_manager_ids = []
        governance_allowed_manager_ids = ['momentum', 'value_quality']
        last_governance_decision = {}
        governance_history = []
        last_allocation_plan = {}
        manager_active_ids = []
        manager_budget_weights = {}
        portfolio_assembly_enabled = False
        current_params = {'position_size': 0.2}
        default_manager_config_ref = 'configs/momentum.yaml'
        last_governance_change_cycle_id = None

        @staticmethod
        def _event_context(cycle_id=None):
            return {'cycle_id': cycle_id, 'timestamp': '2026-03-14T00:00:00'}

        @staticmethod
        def _thinking_excerpt(text):
            return str(text)

        @staticmethod
        def _emit_agent_status(*args, **kwargs):
            events.append(('agent_status', args, kwargs))

        @staticmethod
        def _emit_module_log(*args, **kwargs):
            events.append(('module_log', args, kwargs))

        @staticmethod
        def _sync_runtime_policy_from_manager_runtime():
            raise AssertionError('governance cutover should not reload a single-model owner')

    controller = DummyController()
    service.apply_governance(
        controller,
        stock_data={'sh.600519': {'rows': 5}},
        cutoff_date='20240201',
        cycle_id=4,
        event_emitter=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert controller.last_governance_decision['regime'] == 'bull'
    assert controller.last_allocation_plan['top_n'] == 2
    assert controller.governance_history[0]['dominant_manager_id'] == 'momentum'
    assert controller.manager_active_ids == ['momentum', 'value_quality']
    assert controller.manager_budget_weights == {'momentum': 0.7, 'value_quality': 0.3}
    assert controller.portfolio_assembly_enabled is True
    event_types = [item[0] for item in events]
    assert 'governance_started' in event_types
    assert 'regime_classified' in event_types
    assert 'manager_activation_decided' in event_types
    assert 'governance_applied' in event_types


def test_training_governance_service_apply_governance_updates_session_state(monkeypatch):
    service = TrainingGovernanceService()

    decision = SimpleNamespace(
        regime='bear',
        regime_confidence=0.91,
        regime_source='rule',
        evidence={'rule_result': {'reasoning': 'volatility rising'}},
        reasoning='volatility rising',
        active_manager_ids=['defensive'],
        manager_budget_weights={'defensive': 1.0},
        dominant_manager_id='defensive',
        decision_confidence=0.87,
        decision_source='router',
        guardrail_checks=[],
        allocation_plan={'top_n': 1},
        cash_reserve_hint=0.25,
        portfolio_constraints={'cash_reserve': 0.25, 'top_n': 1},
        metadata={'historical': {'guardrail_hold': False}},
        to_dict=lambda: {
            'regime': 'bear',
            'active_manager_ids': ['defensive'],
            'manager_budget_weights': {'defensive': 1.0},
            'dominant_manager_id': 'defensive',
        },
    )

    monkeypatch.setattr(service, 'decide_governance', lambda *args, **kwargs: decision)

    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            last_governance_decision={},
            manager_budget_weights={},
            current_params={'position_size': 0.2},
            default_manager_id='momentum',
            default_manager_config_ref='configs/momentum.yaml',
        ),
        governance_enabled=True,
        governance_mode='rule',
        data_manager=object(),
        output_dir='outputs/training',
        experiment_allowed_manager_ids=[],
        governance_allowed_manager_ids=['defensive'],
        governance_history=[],
        last_allocation_plan={},
        manager_active_ids=[],
        portfolio_assembly_enabled=False,
        last_governance_change_cycle_id=None,
        _event_context=lambda cycle_id=None: {'cycle_id': cycle_id, 'timestamp': '2026-03-14T00:00:00'},
        _thinking_excerpt=lambda text: str(text),
        _emit_agent_status=lambda *args, **kwargs: None,
        _emit_module_log=lambda *args, **kwargs: None,
    )

    service.apply_governance(
        controller,
        stock_data={'sh.600519': {'rows': 5}},
        cutoff_date='20240201',
        cycle_id=7,
        event_emitter=lambda *_args, **_kwargs: None,
    )

    assert controller.session_state.last_governance_decision['regime'] == 'bear'
    assert controller.session_state.manager_budget_weights == {'defensive': 1.0}
    assert controller.manager_active_ids == ['defensive']


def test_training_governance_service_decide_governance_propagates_shadow_mode(monkeypatch):
    service = TrainingGovernanceService()
    captured = {}

    class DummyCoordinator:
        def decide(self, **kwargs):
            captured.update(kwargs)
            return {'status': 'ok'}

    owner = SimpleNamespace(
        governance_enabled=True,
        governance_mode='rule',
        allocator_top_n=3,
        governance_coordinator=DummyCoordinator(),
        agents={},
        governance_agent_override_enabled=False,
        experiment_protocol={'protocol': {'shadow_mode': True}},
        experiment_allowed_manager_ids=['defensive_low_vol'],
        governance_allowed_manager_ids=['momentum', 'defensive_low_vol'],
        last_governance_decision={},
        last_governance_change_cycle_id=None,
    )

    monkeypatch.setattr(service, 'prepare_leaderboard', lambda **kwargs: 'leaderboard.json')

    result = service.decide_governance(
        owner,
        stock_data={},
        cutoff_date='20240201',
        current_manager_id='momentum',
        data_manager=object(),
        output_dir='outputs/training',
        current_cycle_id=3,
    )

    assert result == {'status': 'ok'}
    assert captured['shadow_mode'] is True
    assert captured['allowed_manager_ids'] == ['defensive_low_vol']


def test_training_review_service_applies_review_decision():
    agent_events = []

    class DummyModel:
        def __init__(self):
            self.updates = []

        def update_runtime_overrides(self, updates):
            self.updates.append(dict(updates))

    class DummyController:
        def __init__(self):
            self.current_params = {'position_size': 0.2}
            self.manager_runtime = DummyModel()
            self.selection_agent_weights = {'trend_hunter': 1.0, 'contrarian': 1.0}

        def _emit_agent_status(self, *args, **kwargs):
            agent_events.append((args, kwargs))

    class DummyEvent:
        def __init__(self):
            self.review_applied_effects_payload = {}

    controller = DummyController()
    review_event = DummyEvent()
    service = TrainingReviewService()

    applied = service.apply_review_decision(
        controller,
        cycle_id=12,
        review_decision={
            'param_adjustments': {'position_size': 0.12},
            'agent_weight_adjustments': {'trend_hunter': 0.9},
        },
        review_event=review_event,
    )

    assert applied is True
    assert controller.current_params['position_size'] == 0.12
    assert controller.manager_runtime.updates[-1] == {'position_size': 0.12}
    assert controller.selection_agent_weights == {'trend_hunter': 0.9, 'contrarian': 1.0}
    assert review_event.review_applied_effects_payload == {
        'param_adjustments': {'position_size': 0.12},
        'agent_weight_adjustments': {'trend_hunter': 0.9},
    }
    assert agent_events


def test_training_review_service_applies_review_decision_via_session_state():
    class DummyModel:
        def __init__(self):
            self.updates = []

        def update_runtime_overrides(self, updates):
            self.updates.append(dict(updates))

    class DummyController:
        def __init__(self):
            self.session_state = TrainingSessionState(
                current_params={'position_size': 0.2},
                manager_budget_weights={'momentum': 1.0},
            )
            self.manager_runtime = DummyModel()
            self.selection_agent_weights = {'trend_hunter': 1.0, 'contrarian': 1.0}
            self.manager_allocator_enabled = True
            self.manager_shadow_mode = False

        def _emit_agent_status(self, *args, **kwargs):
            agent_events.append((args, kwargs))

    class DummyEvent:
        def __init__(self):
            self.review_applied_effects_payload = {}

    agent_events = []
    controller = DummyController()
    review_event = DummyEvent()
    service = TrainingReviewService()

    applied = service.apply_review_decision(
        controller,
        cycle_id=18,
        review_decision={
            'param_adjustments': {'position_size': 0.15},
            'manager_budget_adjustments': {'momentum': 0.8, 'value_quality': 0.2},
        },
        review_event=review_event,
    )

    assert applied is True
    assert controller.session_state.current_params['position_size'] == 0.15
    assert controller.manager_runtime.updates[-1] == {'position_size': 0.15}
    assert controller.session_state.manager_budget_weights == {'momentum': 0.8, 'value_quality': 0.2}
    assert review_event.review_applied_effects_payload['manager_budget_adjustments'] == {
        'momentum': 0.8,
        'value_quality': 0.2,
    }


def test_training_policy_service_loads_promotion_and_quality_gate_defaults():
    class DummyManagerRuntime:
        @staticmethod
        def config_section(name, default=None):
            mapping = {
                'params': {},
                'execution': {},
                'risk_policy': {},
                'evaluation_policy': {},
                'review_policy': {},
                'benchmark': {},
                'train': {
                    'promotion_gate': {'min_samples': 4},
                    'quality_gate_matrix': {'promotion': {'max_pending_cycles': 2}},
                    'freeze_gate': {'governance': {'max_candidate_pending_count': 0}},
                },
                'agent_weights': {},
            }
            return mapping.get(name, default or {})

        @staticmethod
        def update_runtime_overrides(_overrides):
            return None

    controller = SimpleNamespace(
        manager_runtime=DummyManagerRuntime(),
        DEFAULT_PARAMS={},
        current_params={},
        execution_policy={},
        risk_policy={},
        evaluation_policy={},
        review_policy={},
        strategy_evaluator=SimpleNamespace(set_policy=lambda _policy: None),
        benchmark_evaluator=None,
        train_policy={},
        freeze_total_cycles=10,
        freeze_profit_required=7,
        max_losses_before_optimize=3,
        freeze_gate_policy={},
        promotion_gate_policy={},
        experiment_promotion_policy={},
        quality_gate_matrix={},
        auto_apply_mutation=False,
        research_feedback_policy={},
        research_feedback_optimization_policy={},
        research_feedback_freeze_policy={},
        selection_agent_weights={},
    )

    TrainingPolicyService().sync_runtime_policy(controller)

    assert controller.freeze_gate_policy['avg_sharpe_gte'] == 0.8
    assert controller.freeze_gate_policy['benchmark_pass_rate_gte'] == 0.60
    assert controller.freeze_gate_policy['research_feedback']['min_sample_count'] == 8
    assert controller.promotion_gate_policy['min_samples'] == 4
    assert controller.experiment_promotion_policy['min_samples'] == 4
    assert controller.quality_gate_matrix['promotion']['max_pending_cycles'] == 2
    assert controller.freeze_gate_policy['governance']['max_candidate_pending_count'] == 0
    assert controller.freeze_gate_policy['governance']['max_override_pending_count'] == 0


def test_training_policy_service_sync_runtime_policy_loads_research_feedback_sections():
    class DummyManagerRuntime:
        @staticmethod
        def config_section(name, default=None):
            mapping = {
                'params': {},
                'execution': {},
                'risk_policy': {},
                'evaluation_policy': {},
                'review_policy': {},
                'benchmark': {},
                'train': {
                    'freeze_gate': {
                        'avg_sharpe_gte': 0.8,
                    },
                    'research_feedback': {
                        'history_limit': 28,
                        'optimization': {
                            'apply_default_horizon_policy': False,
                            'min_sample_count': 8,
                            'horizons': {
                                'T+5': {
                                    'min_hit_rate': 0.34,
                                    'max_invalidation_rate': 0.36,
                                    'min_interval_hit_rate': 0.55,
                                }
                            },
                        },
                        'freeze_gate': {
                            'apply_default_horizon_policy': False,
                            'min_sample_count': 8,
                            'horizons': {
                                'T+5': {
                                    'min_hit_rate': 0.34,
                                    'max_invalidation_rate': 0.36,
                                    'min_interval_hit_rate': 0.55,
                                }
                            },
                        },
                    },
                },
                'agent_weights': {},
            }
            return mapping.get(name, default or {})

        @staticmethod
        def update_runtime_overrides(_overrides):
            return None

    controller = SimpleNamespace(
        manager_runtime=DummyManagerRuntime(),
        DEFAULT_PARAMS={},
        current_params={},
        execution_policy={},
        risk_policy={},
        evaluation_policy={},
        review_policy={},
        strategy_evaluator=SimpleNamespace(set_policy=lambda _policy: None),
        benchmark_evaluator=None,
        train_policy={},
        freeze_total_cycles=10,
        freeze_profit_required=7,
        max_losses_before_optimize=3,
        freeze_gate_policy={},
        promotion_gate_policy={},
        experiment_promotion_policy={},
        quality_gate_matrix={},
        auto_apply_mutation=False,
        research_feedback_policy={},
        research_feedback_optimization_policy={},
        research_feedback_freeze_policy={},
        selection_agent_weights={},
    )

    TrainingPolicyService().sync_runtime_policy(controller)

    assert controller.research_feedback_policy['history_limit'] == 28
    assert controller.research_feedback_optimization_policy['apply_default_horizon_policy'] is False
    assert controller.research_feedback_optimization_policy['horizons']['T+5']['min_hit_rate'] == 0.34
    assert controller.research_feedback_freeze_policy['horizons']['T+5']['max_invalidation_rate'] == 0.36
    assert controller.freeze_gate_policy['research_feedback']['apply_default_horizon_policy'] is False


def test_training_policy_service_sync_runtime_policy_prefers_session_state():
    captured = {}

    class DummyManagerRuntime:
        @staticmethod
        def config_section(name, default=None):
            mapping = {
                'params': {'position_size': 0.2, 'cash_reserve': 0.12},
                'execution': {},
                'risk_policy': {},
                'evaluation_policy': {},
                'review_policy': {},
                'benchmark': {},
                'train': {},
                'agent_weights': {},
            }
            return mapping.get(name, default or {})

        @staticmethod
        def update_runtime_overrides(overrides):
            captured['overrides'] = dict(overrides)

    controller = SimpleNamespace(
        session_state=TrainingSessionState(current_params={'position_size': 0.25}),
        manager_runtime=DummyManagerRuntime(),
        DEFAULT_PARAMS={'position_size': 0.1, 'cash_reserve': 0.05},
        execution_policy={},
        risk_policy={},
        evaluation_policy={},
        review_policy={},
        strategy_evaluator=SimpleNamespace(set_policy=lambda _policy: None),
        benchmark_evaluator=None,
        train_policy={},
        freeze_total_cycles=10,
        freeze_profit_required=7,
        max_losses_before_optimize=3,
        freeze_gate_policy={},
        promotion_gate_policy={},
        experiment_promotion_policy={},
        quality_gate_matrix={},
        auto_apply_mutation=False,
        research_feedback_policy={},
        research_feedback_optimization_policy={},
        research_feedback_freeze_policy={},
        selection_agent_weights={},
    )

    TrainingPolicyService().sync_runtime_policy(controller)

    assert controller.session_state.current_params['position_size'] == 0.25
    assert controller.session_state.current_params['cash_reserve'] == 0.12
    assert captured['overrides'] == controller.session_state.current_params


def test_training_policy_service_sync_runtime_policy_normalizes_benchmark_policy_types():
    class DummyManagerRuntime:
        @staticmethod
        def config_section(name, default=None):
            mapping = {
                'params': {},
                'execution': {},
                'risk_policy': {},
                'evaluation_policy': {},
                'review_policy': {},
                'benchmark': {
                    'risk_free_rate': 'invalid',
                    'criteria': {
                        'calmar_ratio': '1.8',
                        'max_drawdown': None,
                        'monthly_turnover': 'oops',
                    },
                },
                'train': {},
                'agent_weights': {},
            }
            return mapping.get(name, default or {})

        @staticmethod
        def update_runtime_overrides(_overrides):
            return None

    controller = SimpleNamespace(
        manager_runtime=DummyManagerRuntime(),
        DEFAULT_PARAMS={},
        current_params={},
        execution_policy={},
        risk_policy={},
        evaluation_policy={},
        review_policy={},
        strategy_evaluator=SimpleNamespace(set_policy=lambda _policy: None),
        benchmark_evaluator=None,
        train_policy={},
        freeze_total_cycles=10,
        freeze_profit_required=7,
        max_losses_before_optimize=3,
        freeze_gate_policy={},
        promotion_gate_policy={},
        experiment_promotion_policy={},
        quality_gate_matrix={},
        auto_apply_mutation=False,
        research_feedback_policy={},
        research_feedback_optimization_policy={},
        research_feedback_freeze_policy={},
        selection_agent_weights={},
    )

    TrainingPolicyService().sync_runtime_policy(controller)

    assert controller.benchmark_evaluator.risk_free_rate == 0.03
    assert controller.benchmark_evaluator.criteria['calmar_ratio'] == 1.8
    assert controller.benchmark_evaluator.criteria['max_drawdown'] == 15.0
    assert controller.benchmark_evaluator.criteria['monthly_turnover'] == 3.0


def test_training_policy_service_sync_runtime_policy_accepts_non_dict_benchmark_policy():
    class DummyManagerRuntime:
        @staticmethod
        def config_section(name, default=None):
            mapping = {
                'params': {},
                'execution': {},
                'risk_policy': {},
                'evaluation_policy': {},
                'review_policy': {},
                'benchmark': ['unexpected'],
                'train': {},
                'agent_weights': {},
            }
            return mapping.get(name, default or {})

        @staticmethod
        def update_runtime_overrides(_overrides):
            return None

    controller = SimpleNamespace(
        manager_runtime=DummyManagerRuntime(),
        DEFAULT_PARAMS={},
        current_params={},
        execution_policy={},
        risk_policy={},
        evaluation_policy={},
        review_policy={},
        strategy_evaluator=SimpleNamespace(set_policy=lambda _policy: None),
        benchmark_evaluator=None,
        train_policy={},
        freeze_total_cycles=10,
        freeze_profit_required=7,
        max_losses_before_optimize=3,
        freeze_gate_policy={},
        promotion_gate_policy={},
        experiment_promotion_policy={},
        quality_gate_matrix={},
        auto_apply_mutation=False,
        research_feedback_policy={},
        research_feedback_optimization_policy={},
        research_feedback_freeze_policy={},
        selection_agent_weights={},
    )

    TrainingPolicyService().sync_runtime_policy(controller)

    assert controller.benchmark_evaluator.risk_free_rate == 0.03
    assert controller.benchmark_evaluator.criteria['calmar_ratio'] == 1.5
