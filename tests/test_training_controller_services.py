import json
from types import SimpleNamespace
from typing import cast

from config import config
from app.train import SelfLearningController, TrainingResult
from app.training.controller_services import (
    FreezeGateService,
    TrainingExperimentService,
    TrainingFeedbackService,
    TrainingLLMRuntimeService,
    TrainingPersistenceService,
)
from app.training.cycle_services import TrainingCycleContext, TrainingCycleDataService, TrainingDataLoadResult
from app.training.execution_services import TrainingExecutionService
from app.training.lifecycle_services import TrainingLifecycleService
from app.training.observability_services import TrainingObservabilityService
from app.training.outcome_services import TrainingOutcomeService
from app.training.ab_services import TrainingABService
from app.training.policy_services import TrainingPolicyService
from app.training.review_services import TrainingReviewService
from app.training.review_stage_services import TrainingReviewStageResult, TrainingReviewStageService
from app.training.selection_services import TrainingSelectionResult, TrainingSelectionService
from app.training.routing_services import TrainingRoutingService
from app.training.simulation_services import TrainingSimulationService
from invest.services import EvolutionService, ReviewMeetingService, SelectionMeetingService


def _make_controller(tmp_path):
    return SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        meeting_log_dir=str(tmp_path / 'meetings'),
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
    assert isinstance(controller.training_routing_service, TrainingRoutingService)
    assert isinstance(controller.training_simulation_service, TrainingSimulationService)
    assert isinstance(controller.selection_meeting_service, SelectionMeetingService)
    assert isinstance(controller.review_meeting_service, ReviewMeetingService)
    assert isinstance(controller.evolution_service, EvolutionService)


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
        experiment_allowed_models = []
        model_name = 'momentum'
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


def test_training_experiment_service_configures_protocol_dataset_and_model_scope(monkeypatch):
    import app.training.controller_services as controller_services_module

    service = TrainingExperimentService()
    llm_calls = {}
    refresh_calls = {}
    reload_calls = {}
    direct_service_calls = {"llm": 0, "refresh": 0, "reload": 0}

    monkeypatch.setattr(
        controller_services_module,
        'resolve_model_config_path',
        lambda model_name: f'configs/{model_name}.yaml',
    )

    class DummyController:
        experiment_spec = {}
        experiment_seed = None
        experiment_min_date = None
        experiment_max_date = None
        experiment_min_history_days = None
        experiment_simulation_days = None
        experiment_allowed_models = []
        experiment_llm = {}
        experiment_review_window = {}
        experiment_cutoff_policy = {}
        experiment_promotion_policy = {}
        allocator_enabled = False
        model_routing_enabled = False
        model_routing_mode = 'rule'
        model_routing_allowed_models = ['momentum']
        model_switch_cooldown_cycles = 2
        model_switch_min_confidence = 0.6
        model_switch_hysteresis_margin = 0.08
        model_routing_agent_override_enabled = False
        model_routing_agent_override_max_gap = 0.18
        model_name = 'mean_reversion'
        model_config_path = 'configs/mean_reversion.yaml'
        current_params = {'position_size': 0.2}
        training_llm_runtime_service = SimpleNamespace(
            apply_experiment_overrides=lambda owner, llm_spec=None: direct_service_calls.__setitem__('llm', direct_service_calls['llm'] + 1)
        )
        training_routing_service = SimpleNamespace(
            refresh_routing_coordinator=lambda owner: direct_service_calls.__setitem__('refresh', direct_service_calls['refresh'] + 1),
            reload_investment_model=lambda owner, config_path=None: direct_service_calls.__setitem__('reload', direct_service_calls['reload'] + 1),
        )

        def _apply_experiment_llm_overrides(self, llm_spec=None):
            llm_calls['spec'] = dict(llm_spec or {})

        def _refresh_model_routing_coordinator(self):
            refresh_calls['called'] = True

        def _reload_investment_model(self, config_path=None):
            reload_calls['config_path'] = config_path

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
            'model_scope': {
                'allowed_models': ['value_quality', 'momentum'],
                'allocator_enabled': True,
                'routing_mode': 'hybrid',
                'switch_cooldown_cycles': 5,
                'switch_min_confidence': 0.72,
                'switch_hysteresis_margin': 0.11,
                'agent_override_enabled': True,
                'agent_override_max_gap': 0.23,
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
    assert controller.experiment_allowed_models == ['value_quality', 'momentum']
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
    assert controller.model_routing_enabled is True
    assert controller.model_routing_mode == 'hybrid'
    assert controller.model_routing_allowed_models == ['value_quality', 'momentum']
    assert controller.model_switch_cooldown_cycles == 5
    assert controller.model_switch_min_confidence == 0.72
    assert controller.model_switch_hysteresis_margin == 0.11
    assert controller.model_routing_agent_override_enabled is True
    assert controller.model_routing_agent_override_max_gap == 0.23
    assert refresh_calls['called'] is True
    assert controller.model_name == 'value_quality'
    assert controller.model_config_path == 'configs/value_quality.yaml'
    assert controller.current_params == {}
    assert reload_calls['config_path'] == 'configs/value_quality.yaml'
    assert direct_service_calls == {'llm': 0, 'refresh': 0, 'reload': 0}


def test_training_experiment_service_respects_controller_compatibility_hooks(monkeypatch):
    import app.training.controller_services as controller_services_module

    service = TrainingExperimentService()
    observed = {"llm": 0, "refresh": 0, "reload": 0}

    monkeypatch.setattr(
        controller_services_module,
        'resolve_model_config_path',
        lambda model_name: f'configs/{model_name}.yaml',
    )

    class DummyController:
        experiment_spec = {}
        experiment_seed = None
        experiment_min_date = None
        experiment_max_date = None
        experiment_min_history_days = None
        experiment_simulation_days = None
        experiment_allowed_models = []
        experiment_llm = {}
        allocator_enabled = False
        model_routing_enabled = False
        model_routing_mode = 'rule'
        model_routing_allowed_models = []
        model_switch_cooldown_cycles = 0
        model_switch_min_confidence = 0.0
        model_switch_hysteresis_margin = 0.0
        model_routing_agent_override_enabled = False
        model_routing_agent_override_max_gap = 0.0
        model_name = 'mean_reversion'
        model_config_path = 'configs/mean_reversion.yaml'
        current_params = {'position_size': 0.2}
        training_llm_runtime_service = SimpleNamespace(
            apply_experiment_overrides=lambda owner, llm_spec=None: (_ for _ in ()).throw(AssertionError('service path should not be called directly'))
        )
        training_routing_service = SimpleNamespace(
            refresh_routing_coordinator=lambda owner: (_ for _ in ()).throw(AssertionError('service path should not be called directly')),
            reload_investment_model=lambda owner, config_path=None: (_ for _ in ()).throw(AssertionError('service path should not be called directly')),
        )

        def _apply_experiment_llm_overrides(self, llm_spec=None):
            observed['llm'] += 1
            self.experiment_llm_applied = dict(llm_spec or {})

        def _refresh_model_routing_coordinator(self):
            observed['refresh'] += 1

        def _reload_investment_model(self, config_path=None):
            observed['reload'] += 1
            self.reloaded_config_path = config_path

    controller = DummyController()

    service.configure_experiment(
        controller,
        {
            'model_scope': {'allowed_models': ['value_quality']},
            'llm': {'timeout': 9, 'dry_run': True},
        },
    )

    assert observed == {'llm': 1, 'refresh': 1, 'reload': 1}
    assert controller.experiment_llm_applied['timeout'] == 9
    assert controller.reloaded_config_path == 'configs/value_quality.yaml'


def test_refresh_runtime_from_config_delegates_to_routing_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_sync(owner):
        captured['owner'] = owner

    monkeypatch.setattr(controller.training_routing_service, 'sync_runtime_from_config', fake_sync)

    controller.refresh_runtime_from_config()

    assert captured['owner'] is controller


def test_reload_investment_model_delegates_to_routing_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_reload(owner, config_path=None):
        captured['owner'] = owner
        captured['config_path'] = config_path

    monkeypatch.setattr(controller.training_routing_service, 'reload_investment_model', fake_reload)

    controller._reload_investment_model('cfg.yaml')  # pylint: disable=protected-access

    assert captured['owner'] is controller
    assert captured['config_path'] == 'cfg.yaml'


def test_maybe_apply_allocator_delegates_to_routing_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_apply(owner, *, stock_data, cutoff_date, cycle_id, event_emitter):
        captured['owner'] = owner
        captured['stock_data'] = dict(stock_data)
        captured['cutoff_date'] = cutoff_date
        captured['cycle_id'] = cycle_id
        captured['event_emitter'] = event_emitter

    monkeypatch.setattr(controller.training_routing_service, 'apply_model_routing', fake_apply)

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

    controller._sync_runtime_policy_from_model()  # pylint: disable=protected-access

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
        model_name = 'momentum'
        model_config_path = 'cfg.yaml'
        model_routing_enabled = True
        model_routing_mode = 'rule'
        last_routing_decision = {'selected_model': 'mean_reversion', 'regime': 'oscillation'}
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
        cycle_dict={
            'strategy_scores': {'overall_score': 0.8},
            'analysis': 'review ok',
            'execution_snapshot': {
                'basis_stage': 'pre_optimization',
                'cycle_id': 8,
                'model_name': 'value_quality',
                'active_config_ref': 'executed.yaml',
                'runtime_overrides': {'position_size': 0.08},
                'routing_decision': {'selected_model': 'value_quality', 'regime': 'bull'},
                'selection_mode': 'meeting_selection',
                'benchmark_passed': True,
            },
        },
        review_applied=True,
        config_snapshot_path='snap.json',
        optimization_events=[
            {
                'stage': 'yaml_mutation',
                'decision': {
                    'config_path': 'candidate.yaml',
                    'auto_applied': False,
                },
            }
        ],
        audit_tags=audit_tags,
        model_output=SimpleNamespace(model_name='value_quality', config_name='value.yaml'),
        research_feedback={'recommendation': {'bias': 'maintain'}},
    )

    assert audit_tags['routing_model'] == 'mean_reversion'
    assert audit_tags['routing_regime'] == 'oscillation'
    assert cycle_result.analysis == 'review ok'
    assert cycle_result.model_name == 'value_quality'
    assert cycle_result.config_name == 'executed.yaml'
    assert cycle_result.params == {'position_size': 0.08}
    assert cycle_result.routing_decision == {'selected_model': 'value_quality', 'regime': 'bull'}
    assert cycle_result.execution_snapshot['basis_stage'] == 'pre_optimization'
    assert cycle_result.run_context['basis_stage'] == 'pre_optimization'
    assert cycle_result.run_context['runtime_overrides'] == {'position_size': 0.08}
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

    assert realism_metrics['avg_trade_amount'] == 300.0
    assert realism_metrics['avg_turnover_rate'] == 1.25
    assert realism_metrics['avg_holding_days'] == 2.0


def test_run_continuous_delegates_to_lifecycle_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_run(owner, *, max_cycles):
        captured['owner'] = owner
        captured['max_cycles'] = max_cycles
        return {'status': 'delegated'}

    monkeypatch.setattr(controller.training_lifecycle_service, 'run_continuous', fake_run)

    payload = controller.run_continuous(max_cycles=3)

    assert payload == {'status': 'delegated'}
    assert captured['owner'] is controller
    assert captured['max_cycles'] == 3


def test_training_lifecycle_service_finalize_cycle_updates_meta_and_callback(monkeypatch, tmp_path):
    from app.train import TrainingResult
    import app.train as train_module

    service = TrainingLifecycleService()
    emitted = []
    logs = []
    callback_result = {}

    monkeypatch.setattr(train_module, 'emit_event', lambda event_type, data: emitted.append((event_type, data)))

    class DummyController:
        def __init__(self):
            self.cycle_history = []
            self.current_cycle_id = 0
            self.last_routing_decision = {'selected_model': 'momentum', 'regime': 'bull'}
            self.last_feedback_optimization = {'triggered': False}
            self.last_cycle_meta = {}
            self.model_name = 'momentum'
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
    cycle_dict = {'strategy_scores': {'overall_score': 0.8}}
    controller = DummyController()
    service.finalize_cycle(
        controller,
        cycle_result=cycle_result,
        cycle_dict=cycle_dict,
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

    class DummyInvestmentModel:
        def update_runtime_overrides(self, updates):
            self.updates = dict(updates)

        def process(self, stock_data, cutoff_date):
            del stock_data, cutoff_date
            signal_packet = SimpleNamespace(
                regime='bull',
                cash_reserve=0.2,
                params={'position_size': 0.15},
                selected_codes=['sh.600519'],
                signals=[{'code': 'sh.600519'}],
                max_positions=2,
            )
            agent_context = SimpleNamespace(
                summary='bull summary',
                metadata={'confidence': 0.81},
            )
            return SimpleNamespace(
                signal_packet=signal_packet,
                agent_context=agent_context,
                model_name='momentum',
                config_name='cfg.yaml',
                to_dict=lambda: {'model_name': 'momentum'},
            )

    class DummyRecorder:
        def __init__(self):
            self.saved = []

        def save_selection(self, meeting_log, cycle_id):
            self.saved.append((cycle_id, dict(meeting_log)))

    class DummyTracker:
        def __init__(self):
            self.predictions = []
            self.selected = []

        def record_predictions(self, cycle_id, name, picks):
            self.predictions.append((cycle_id, name, list(picks)))

        def mark_selected(self, cycle_id, selected):
            self.selected.append((cycle_id, list(selected)))

    trading_plan = SimpleNamespace(
        positions=[SimpleNamespace(code='sh.600519')],
        source='meeting',
    )

    class DummySelectionMeetingService:
        @staticmethod
        def run_with_model_output(model_output):
            del model_output
            return {
                'trading_plan': trading_plan,
                'meeting_log': {
                    'hunters': [{
                        'name': 'trend_hunter',
                        'result': {
                            'picks': ['sh.600519'],
                            'overall_view': 'strong trend',
                            'confidence': 0.9,
                        },
                    }],
                    'selected': ['sh.600519'],
                },
                'strategy_advice': {'bias': 'trend'},
            }

    class DummyController:
        current_params = {'position_size': 0.12}
        model_name = 'momentum'
        investment_model = DummyInvestmentModel()
        selection_meeting_service = DummySelectionMeetingService()
        meeting_recorder = DummyRecorder()
        agent_tracker = DummyTracker()
        last_routing_decision = {'regime': 'bull', 'decision_source': 'router'}

        @staticmethod
        def _thinking_excerpt(text):
            return text

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
    assert result == TrainingSelectionResult(
        model_output=result.model_output,
        regime_result=result.regime_result,
        trading_plan=trading_plan,
        meeting_log={
            'hunters': [{
                'name': 'trend_hunter',
                'result': {
                    'picks': ['sh.600519'],
                    'overall_view': 'strong trend',
                    'confidence': 0.9,
                },
            }],
            'selected': ['sh.600519'],
        },
        strategy_advice={'bias': 'trend'},
        selected=['sh.600519'],
        selected_data={'sh.600519': {'rows': 1}},
        selection_mode='meeting_selection',
        agent_used=True,
    )
    assert result.regime_result['decision_source'] == 'router'
    assert any(item[0] == 'speech' for item in events)


def test_training_selection_service_skips_when_no_selected_codes():
    service = TrainingSelectionService()
    skipped = {}

    class DummyInvestmentModel:
        def update_runtime_overrides(self, updates):
            del updates

        def process(self, stock_data, cutoff_date):
            del stock_data, cutoff_date
            return SimpleNamespace(
                signal_packet=SimpleNamespace(
                    regime='oscillation',
                    cash_reserve=0.4,
                    params={},
                    selected_codes=[],
                    signals=[],
                    max_positions=0,
                ),
                agent_context=SimpleNamespace(summary='flat', metadata={}),
                model_name='momentum',
                config_name='cfg.yaml',
                to_dict=lambda: {},
            )

    class DummyController:
        current_params = {}
        model_name = 'momentum'
        investment_model = DummyInvestmentModel()
        last_routing_decision = {}
        selection_meeting_service = SimpleNamespace(
            run_with_model_output=lambda model_output: {
                'trading_plan': SimpleNamespace(positions=[], source='meeting'),
                'meeting_log': {},
                'strategy_advice': {},
            }
        )
        meeting_recorder = SimpleNamespace(save_selection=lambda *args, **kwargs: None)
        agent_tracker = SimpleNamespace(
            record_predictions=lambda *args, **kwargs: None,
            mark_selected=lambda *args, **kwargs: None,
        )

        @staticmethod
        def _thinking_excerpt(text):
            return text

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
    assert skipped['reason'] == '模型与会议未产出可交易标的'


def test_training_review_stage_service_runs_review_flow():
    service = TrainingReviewStageService()
    events = []

    class DummyReviewMeetingService:
        @staticmethod
        def run_with_eval_report(
            eval_report,
            *,
            agent_accuracy,
            current_params,
            recent_results=None,
            review_basis_window=None,
            similar_results=None,
            similarity_summary=None,
            causal_diagnosis=None,
        ):
            events.append(
                (
                    'review_meeting',
                    eval_report,
                    agent_accuracy,
                    current_params,
                    recent_results,
                    review_basis_window,
                    similar_results,
                    similarity_summary,
                    causal_diagnosis,
                )
            )
            return {
                'reasoning': 'tighten risk',
                'strategy_suggestions': ['reduce exposure'],
                'param_adjustments': {'position_size': 0.1},
                'agent_weight_adjustments': {'trend_hunter': 0.9},
            }

    class DummyTrainingReviewService:
        @staticmethod
        def build_eval_report(owner, **kwargs):
            events.append(('build_eval_report', owner, kwargs))
            return {'eval': 'report', 'cycle_id': kwargs['cycle_id']}

        @staticmethod
        def apply_review_decision(owner, **kwargs):
            events.append(('apply_review_decision', owner, kwargs))
            return True

    class DummyTracker:
        @staticmethod
        def compute_accuracy(last_n_cycles=20):
            return {'window': last_n_cycles, 'accuracy': 0.75}

    saved_reviews = []

    class DummyController:
        training_review_service = DummyTrainingReviewService()
        review_meeting_service = DummyReviewMeetingService()
        agent_tracker = DummyTracker()
        current_params = {'position_size': 0.12}
        experiment_review_window = {'mode': 'rolling', 'size': 3}
        review_meeting = SimpleNamespace(last_facts={'facts': 'ok'})
        meeting_recorder = SimpleNamespace(
            save_review=lambda decision, facts, cycle_id: saved_reviews.append(
                (decision, facts, cycle_id)
            )
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
                model_name='momentum',
                config_name='configs/active.yaml',
                routing_decision={'regime': 'bear'},
                audit_tags={'routing_regime': 'bear'},
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
                model_name='momentum',
                config_name='configs/active.yaml',
                routing_decision={'regime': 'oscillation'},
                audit_tags={'routing_regime': 'oscillation'},
                research_feedback={},
            ),
        ]

        @staticmethod
        def _emit_agent_status(*args, **kwargs):
            events.append(('status', args, kwargs))

        @staticmethod
        def _emit_module_log(*args, **kwargs):
            events.append(('module', args, kwargs))

    class DummyEvent:
        def __init__(self, **kwargs):
            self.kwargs = dict(kwargs)

    cycle_dict = {'benchmark_passed': True}
    result = service.run_review_stage(
        DummyController(),
        cycle_id=6,
        cutoff_date='20240201',
        sim_result=SimpleNamespace(return_pct=1.0, total_pnl=1000, total_trades=2, win_rate=0.5),
        regime_result={'regime': 'bull'},
        selected=['sh.600519'],
        cycle_dict=cycle_dict,
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
        model_output=None,
        research_feedback={'recommendation': {'bias': 'maintain'}},
        optimization_event_factory=DummyEvent,
    )

    assert result == TrainingReviewStageResult(
        eval_report={'eval': 'report', 'cycle_id': 6},
        review_decision={
            'reasoning': 'tighten risk',
            'strategy_suggestions': ['reduce exposure'],
            'param_adjustments': {'position_size': 0.1},
            'agent_weight_adjustments': {'trend_hunter': 0.9},
        },
        review_applied=True,
        review_event=result.review_event,
    )
    assert cycle_dict['review_applied'] is True
    assert saved_reviews[0][2] == 6
    assert any(item[0] == 'apply_review_decision' for item in events)
    review_call = next(item for item in events if item[0] == 'review_meeting')
    assert review_call[6] == []
    assert review_call[7]['matched_cycle_ids'] == []
    assert review_call[8]['primary_driver'] == 'insufficient_history'
    assert [entry['cycle_id'] for entry in review_call[4]] == [4, 5, 6]
    assert review_call[5] == {
        'mode': 'rolling',
        'size': 3,
        'cycle_ids': [4, 5, 6],
        'current_cycle_id': 6,
    }


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
            'active_config_ref': 'configs/active.yaml',
            'candidate_config_ref': 'data/evolution/generations/candidate.yaml',
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
            'active_config_ref': 'configs/active.yaml',
        },
    )

    controller._save_cycle_result(result)  # pylint: disable=protected-access

    payload = json.loads((tmp_path / 'training' / 'cycle_3.json').read_text(encoding='utf-8'))
    assert payload['cycle_id'] == 3
    assert payload['return_pct'] == 1.5
    assert payload['benchmark_passed'] is True
    assert payload['experiment_spec']['protocol']['seed'] == 7
    assert payload['run_context']['active_config_ref'] == 'configs/active.yaml'
    assert payload['run_context']['promotion_decision']['status'] == 'candidate_generated'
    assert payload['promotion_record']['gate_status'] == 'awaiting_gate'
    assert payload['lineage_record']['lineage_status'] == 'candidate_pending'


def test_generate_report_delegates_to_freeze_gate_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)

    monkeypatch.setattr(
        controller.freeze_gate_service,
        'generate_training_report',
        lambda owner: {'status': 'ok', 'owner_bound': owner is controller},
    )

    payload = controller._generate_report()  # pylint: disable=protected-access

    assert payload == {'status': 'ok', 'owner_bound': True}


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
        def preview_routing(owner, *, cutoff_date, stock_count, min_history_days, allowed_models=None):
            del owner, stock_count, min_history_days, allowed_models
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
        experiment_allowed_models = []
        cycle_history = [
            SimpleNamespace(routing_decision={'regime': 'bull'}),
            SimpleNamespace(routing_decision={'regime': 'bull'}),
            SimpleNamespace(routing_decision={'regime': 'bear'}),
        ]
        data_manager = DummyDataManager()
        training_routing_service = DummyRoutingService()
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
        model_name = 'momentum'
        model_config_path = 'invest/models/configs/momentum_v1.yaml'

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
        cycle_dict={'benchmark_passed': True, 'sharpe_ratio': 1.1},
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
        model_output=None,
        research_feedback={'recommendation': {'bias': 'maintain'}},
    )

    assert report.cycle_id == 9
    assert report.selected_codes == ['sh.600519']
    assert report.metadata['effective_data_mode'] == 'offline'
    assert report.metadata['research_feedback']['recommendation']['bias'] == 'maintain'


def test_training_simulation_service_builds_cycle_and_trade_payloads():
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

    cycle = service.build_cycle_dict(
        cycle_id=11,
        cutoff_date='20240201',
        sim_result=sim_result,
        selected=['sh.600519'],
        is_profit=True,
        regime_result={'regime': 'bull'},
        routing_decision={'selected_model': 'momentum'},
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
    assert cycle['routing_decision']['selected_model'] == 'momentum'
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
    cycle_dict: dict[str, object] = {'cycle_id': 12}
    trade_dicts = [{'action': 'SELL', 'pnl': 100.0}]

    benchmark_passed = service.evaluate_cycle(
        DummyController(),
        cycle_dict=cycle_dict,
        trade_dicts=trade_dicts,
        sim_result=sim_result,
        benchmark_daily_values=[3000.0, 3015.0],
    )

    assert benchmark_passed is True
    assert cycle_dict['benchmark_passed'] is True
    assert cycle_dict['benchmark_source'] == 'index_bar:sh.000300'
    strategy_scores = cast(dict[str, object], cycle_dict['strategy_scores'])
    assert strategy_scores['overall_score'] == 0.82
    assert benchmark_calls['daily_values'] == [100000.0, 101000.0]
    assert strategy_calls['trade_count'] == 1


def test_training_routing_service_refreshes_controller_coordinator():
    service = TrainingRoutingService()

    class DummyController:
        model_routing_policy = {'bull_avg_change_20d': 4.0}
        model_switch_min_confidence = 0.7
        model_switch_cooldown_cycles = 3
        model_switch_hysteresis_margin = 0.12
        model_routing_agent_override_max_gap = 0.2

    controller = DummyController()
    coordinator = service.refresh_routing_coordinator(controller)

    assert getattr(controller, 'routing_coordinator') is coordinator
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
        selection_meeting = SimpleNamespace(llm=DummyLLM())
        review_meeting = SimpleNamespace(llm=DummyLLM())
        llm_optimizer = SimpleNamespace(llm=optimizer_llm)

    controller = DummyController()
    service.apply_experiment_overrides(
        controller,
        {'timeout': 11, 'max_retries': 2, 'dry_run': True},
    )
    service.set_dry_run(controller, False)

    assert shared.calls == [{'timeout': 11, 'max_retries': 2}]
    assert controller.agents['strategist'].llm.calls == [{'timeout': 11, 'max_retries': 2}]
    assert controller.selection_meeting.llm.calls == [{'timeout': 11, 'max_retries': 2}]
    assert optimizer_llm.calls == [{'timeout': 11, 'max_retries': 2}]
    assert controller.llm_mode == 'live'
    assert shared.dry_run is False


def test_training_routing_service_sync_runtime_from_config_reloads_on_model_change(monkeypatch):
    service = TrainingRoutingService()
    refresh_calls = {}
    reload_calls = {}

    monkeypatch.setattr(config, 'investment_model', 'value_quality')
    monkeypatch.setattr(config, 'investment_model_config', 'configs/value.yaml')
    monkeypatch.setattr(config, 'allocator_enabled', True)
    monkeypatch.setattr(config, 'allocator_top_n', 4)
    monkeypatch.setattr(config, 'model_routing_enabled', True)
    monkeypatch.setattr(config, 'model_routing_mode', 'hybrid')
    monkeypatch.setattr(config, 'model_routing_allowed_models', ['value_quality', 'momentum'])
    monkeypatch.setattr(config, 'model_switch_cooldown_cycles', 6)
    monkeypatch.setattr(config, 'model_switch_min_confidence', 0.72)
    monkeypatch.setattr(config, 'model_switch_hysteresis_margin', 0.11)
    monkeypatch.setattr(config, 'model_routing_agent_override_enabled', True)
    monkeypatch.setattr(config, 'model_routing_agent_override_max_gap', 0.22)
    monkeypatch.setattr(config, 'model_routing_policy', {'bull_avg_change_20d': 4.5})

    def fake_refresh(owner):
        refresh_calls['owner'] = owner

    def fake_reload(owner, config_path=None):
        reload_calls['owner'] = owner
        reload_calls['config_path'] = config_path

    monkeypatch.setattr(service, 'refresh_routing_coordinator', fake_refresh)
    monkeypatch.setattr(service, 'reload_investment_model', fake_reload)

    class DummyController:
        model_name = 'momentum'
        model_config_path = 'configs/momentum.yaml'
        allocator_enabled = False
        allocator_top_n = 3
        model_routing_enabled = False
        model_routing_mode = 'rule'
        model_routing_allowed_models = ['momentum']
        model_switch_cooldown_cycles = 2
        model_switch_min_confidence = 0.6
        model_switch_hysteresis_margin = 0.08
        model_routing_agent_override_enabled = False
        model_routing_agent_override_max_gap = 0.18
        model_routing_policy = {}
        current_params = {'position_size': 0.2}

    controller = DummyController()
    service.sync_runtime_from_config(controller)

    assert refresh_calls['owner'] is controller
    assert reload_calls['owner'] is controller
    assert reload_calls['config_path'] == 'configs/value.yaml'
    assert controller.model_name == 'value_quality'
    assert controller.current_params == {}
    assert controller.model_routing_mode == 'hybrid'


def test_training_routing_service_apply_model_routing_updates_state_and_emits_events(monkeypatch):
    service = TrainingRoutingService()
    events = []

    decision = SimpleNamespace(
        regime='bull',
        regime_confidence=0.83,
        regime_source='rule',
        evidence={'rule_result': {'reasoning': 'trend improving'}},
        reasoning='trend improving',
        current_model='momentum',
        selected_model='momentum',
        selected_config='configs/momentum.yaml',
        candidate_models=['momentum', 'value_quality'],
        candidate_weights={'momentum': 0.7, 'value_quality': 0.3},
        decision_confidence=0.79,
        decision_source='router',
        switch_applied=False,
        hold_current=False,
        hold_reason='',
        guardrail_checks={'cooldown': True},
        allocation_plan={'top_n': 2},
        cash_reserve_hint=0.15,
        to_dict=lambda: {
            'selected_model': 'momentum',
            'selected_config': 'configs/momentum.yaml',
            'regime': 'bull',
        },
    )

    monkeypatch.setattr(service, 'route_model', lambda *args, **kwargs: decision)

    class DummyController:
        model_routing_enabled = True
        model_routing_mode = 'rule'
        model_name = 'momentum'
        data_manager = object()
        output_dir = 'outputs/training'
        experiment_allowed_models = []
        model_routing_allowed_models = ['momentum', 'value_quality']
        last_routing_decision = {}
        routing_history = []
        last_allocation_plan = {}
        current_params = {'position_size': 0.2}
        model_config_path = 'configs/momentum.yaml'
        last_model_switch_cycle_id = None

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
        def _sync_runtime_policy_from_model():
            raise AssertionError('should not reload when switch_applied is false')

    controller = DummyController()
    service.apply_model_routing(
        controller,
        stock_data={'sh.600519': {'rows': 5}},
        cutoff_date='20240201',
        cycle_id=4,
        event_emitter=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert controller.last_routing_decision['regime'] == 'bull'
    assert controller.last_allocation_plan['top_n'] == 2
    assert controller.routing_history[0]['selected_model'] == 'momentum'
    event_types = [item[0] for item in events]
    assert 'routing_started' in event_types
    assert 'regime_classified' in event_types
    assert 'routing_decided' in event_types


def test_training_review_service_applies_review_decision():
    agent_events = []

    class DummyModel:
        def __init__(self):
            self.updates = []

        def update_runtime_overrides(self, updates):
            self.updates.append(dict(updates))

    class DummySelectionMeetingService:
        def __init__(self):
            self.weight_updates = []

        def update_weights(self, updates):
            self.weight_updates.append(dict(updates))

    class DummyController:
        def __init__(self):
            self.current_params = {'position_size': 0.2}
            self.investment_model = DummyModel()
            self.selection_meeting_service = DummySelectionMeetingService()

        def _emit_agent_status(self, *args, **kwargs):
            agent_events.append((args, kwargs))

    class DummyEvent:
        def __init__(self):
            self.applied_change = {}

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
    assert controller.investment_model.updates[-1] == {'position_size': 0.12}
    assert controller.selection_meeting_service.weight_updates[-1] == {'trend_hunter': 0.9}
    assert review_event.applied_change == {
        'params': {'position_size': 0.12},
        'agent_weights': {'trend_hunter': 0.9},
    }
    assert agent_events


def test_training_policy_service_uses_selection_meeting_service_for_agent_weights():
    class DummyInvestmentModel:
        @staticmethod
        def config_section(name, default=None):
            mapping = {
                'params': {},
                'execution': {},
                'risk_policy': {},
                'evaluation_policy': {},
                'review_policy': {},
                'benchmark': {},
                'train': {},
                'agent_weights': {'trend_hunter': 0.8, 'contrarian': 1.2},
            }
            return mapping.get(name, default or {})

        @staticmethod
        def update_runtime_overrides(_overrides):
            return None

    class DummySelectionMeetingService:
        def __init__(self):
            self.agent_weights = None

        def set_policy(self, _policy=None):
            return None

        def set_agent_weights(self, weights=None):
            self.agent_weights = dict(weights or {})

    controller = SimpleNamespace(
        investment_model=DummyInvestmentModel(),
        DEFAULT_PARAMS={},
        current_params={},
        execution_policy={},
        risk_policy={},
        evaluation_policy={},
        review_policy={},
        strategy_evaluator=SimpleNamespace(set_policy=lambda _policy: None),
        review_meeting_service=SimpleNamespace(set_policy=lambda _policy: None),
        benchmark_evaluator=None,
        train_policy={},
        freeze_total_cycles=10,
        freeze_profit_required=7,
        max_losses_before_optimize=3,
        freeze_gate_policy={},
        auto_apply_mutation=False,
        research_feedback_policy={},
        research_feedback_optimization_policy={},
        research_feedback_freeze_policy={},
        selection_meeting_service=DummySelectionMeetingService(),
    )

    TrainingPolicyService().sync_runtime_policy(controller)

    assert controller.selection_meeting_service.agent_weights == {
        'trend_hunter': 0.8,
        'contrarian': 1.2,
    }
