from __future__ import annotations

from types import SimpleNamespace

from invest_evolution.application.training.review_contracts import (
    OptimizationInputEnvelope,
    SimulationStageEnvelope,
)
from invest_evolution.application.training.execution import trigger_loss_optimization
from invest_evolution.application.training.controller import TrainingSessionState


class Event:
    def __init__(self, **kwargs):
        self.cycle_id = kwargs.get('cycle_id')
        self.trigger = kwargs['trigger']
        self.stage = kwargs['stage']
        self.status = kwargs.get('status', 'ok')
        self.suggestions = list(kwargs.get('suggestions', []))
        self.decision = dict(kwargs.get('decision', {}))
        self.applied_change = dict(kwargs.get('applied_change', {}))
        self.lineage = dict(kwargs.get('lineage', {}))
        self.evidence = dict(kwargs.get('evidence', {}))
        self.notes = kwargs.get('notes', '')
        self.review_decision_payload = dict(kwargs.get('review_decision_payload', {}))
        self.research_feedback_payload = dict(kwargs.get('research_feedback_payload', {}))
        self.llm_analysis_payload = dict(kwargs.get('llm_analysis_payload', {}))
        self.evolution_engine_payload = dict(kwargs.get('evolution_engine_payload', {}))
        self.runtime_config_mutation_payload = dict(kwargs.get('runtime_config_mutation_payload', {}))
        self.runtime_config_mutation_skipped_payload = dict(
            kwargs.get('runtime_config_mutation_skipped_payload', {})
        )
        self.optimization_error_payload = dict(kwargs.get('optimization_error_payload', {}))

    def to_dict(self):
        payload = {
            'cycle_id': self.cycle_id,
            'trigger': self.trigger,
            'stage': self.stage,
            'status': self.status,
            'suggestions': list(self.suggestions),
            'decision': dict(self.decision),
            'applied_change': dict(self.applied_change),
            'lineage': dict(self.lineage),
            'evidence': dict(self.evidence),
            'notes': self.notes,
        }
        if self.review_decision_payload:
            payload['review_decision_payload'] = dict(self.review_decision_payload)
        if self.research_feedback_payload:
            payload['research_feedback_payload'] = dict(self.research_feedback_payload)
        if self.llm_analysis_payload:
            payload['llm_analysis_payload'] = dict(self.llm_analysis_payload)
        if self.evolution_engine_payload:
            payload['evolution_engine_payload'] = dict(self.evolution_engine_payload)
        if self.runtime_config_mutation_payload:
            payload['runtime_config_mutation_payload'] = dict(self.runtime_config_mutation_payload)
        if self.runtime_config_mutation_skipped_payload:
            payload['runtime_config_mutation_skipped_payload'] = dict(
                self.runtime_config_mutation_skipped_payload
            )
        if self.optimization_error_payload:
            payload['optimization_error_payload'] = dict(self.optimization_error_payload)
        return payload


class FakeLLMOptimizer:
    def __init__(self):
        self.calls = []

    def analyze_loss(self, cycle_dict, trade_dicts):
        self.calls.append(
            {
                'cycle_payload': dict(cycle_dict or {}),
                'trade_dicts': [dict(item) for item in list(trade_dicts or [])],
            }
        )
        return SimpleNamespace(cause='回撤来自追高', suggestions=['减少仓位', '加强确认'])

    def generate_runtime_fix(self, analysis):
        return {'position_size': 0.12}


class FakeEvolutionEngine:
    def __init__(self):
        self.population = []
        self.last_fitness = None

    def initialize_population(self, base_params=None):
        params = dict(base_params or {})
        self.population = [dict(params), dict(params), dict(params)]

    def evolve(self, fitness_scores):
        self.last_fitness = list(fitness_scores)

    def get_best_params(self):
        return {'take_profit_pct': 0.18}


class FakeRuntimeConfigMutator:
    def __init__(self):
        self.calls = []

    def mutate(self, runtime_config_ref, **kwargs):
        self.calls.append((runtime_config_ref, dict(kwargs)))
        return {
            'runtime_config_ref': '/tmp/candidate.yaml',
            'meta': {'output_runtime_config_ref': '/tmp/candidate.yaml', **kwargs},
        }


class FakeManagerRuntime:
    def __init__(self):
        self.overrides = []

    def update_runtime_overrides(self, updates):
        self.overrides.append(dict(updates))


class FakeCycle:
    def __init__(self, return_pct, *, benchmark_passed=False, strategy_scores=None):
        self.return_pct = return_pct
        self.benchmark_passed = benchmark_passed
        self.strategy_scores = dict(strategy_scores or {})


def _optimization_input_from_payload(payload: dict) -> OptimizationInputEnvelope:
    return OptimizationInputEnvelope.from_cycle_payload(payload)


def test_trigger_loss_optimization_generates_candidate_without_auto_apply():
    appended = []
    agent_events = []
    logs = []
    speeches = []
    optimized = []
    controller = SimpleNamespace(
        consecutive_losses=3,
        llm_optimizer=FakeLLMOptimizer(),
        evolution_engine=FakeEvolutionEngine(),
        runtime_config_mutator=FakeRuntimeConfigMutator(),
        default_manager_id='momentum',
        default_manager_config_ref='src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml',
        auto_apply_mutation=False,
        current_params={'position_size': 0.2, 'take_profit_pct': 0.15},
        manager_runtime=FakeManagerRuntime(),
        cycle_history=[FakeCycle(-2.0), FakeCycle(-1.0), FakeCycle(0.5)],
        on_optimize=lambda params: optimized.append(dict(params)),
        _emit_agent_status=lambda *args, **kwargs: agent_events.append((args, kwargs)),
        _emit_module_log=lambda *args, **kwargs: logs.append((args, kwargs)),
        _emit_meeting_speech=lambda *args, **kwargs: speeches.append((args, kwargs)),
        _append_optimization_event=lambda event: appended.append(event.to_dict()),
        _reload_manager_runtime=lambda path: (_ for _ in ()).throw(AssertionError('should not auto reload')),
    )

    events = trigger_loss_optimization(
        controller,
        _optimization_input_from_payload({'cycle_id': 7}),
        [],
        event_factory=Event,
    )

    assert controller.consecutive_losses == 0
    assert len(events) == 3
    assert [event['stage'] for event in events] == ['llm_analysis', 'evolution_engine', 'runtime_config_mutation']
    assert events[-1]['decision']['auto_applied'] is False
    assert events[-1]['runtime_config_mutation_payload']['auto_applied'] is False
    assert controller.current_params['position_size'] == 0.12
    assert controller.current_params['take_profit_pct'] == 0.18
    assert optimized and optimized[-1]['take_profit_pct'] == 0.18
    assert appended[-1]['stage'] == 'runtime_config_mutation'
    assert any(kwargs.get('kind') == 'runtime_config_mutation' for _, kwargs in logs)


def test_trigger_loss_optimization_uses_benchmark_oriented_fitness_scores():
    controller = SimpleNamespace(
        consecutive_losses=3,
        llm_optimizer=FakeLLMOptimizer(),
        evolution_engine=FakeEvolutionEngine(),
        runtime_config_mutator=FakeRuntimeConfigMutator(),
        default_manager_id='momentum',
        default_manager_config_ref='src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml',
        auto_apply_mutation=False,
        current_params={'position_size': 0.2, 'take_profit_pct': 0.15},
        manager_runtime=FakeManagerRuntime(),
        cycle_history=[
            FakeCycle(1.4, benchmark_passed=False, strategy_scores={'overall_score': 0.15}),
            FakeCycle(0.9, benchmark_passed=True, strategy_scores={'overall_score': 0.82}),
            FakeCycle(0.6, benchmark_passed=True, strategy_scores={'overall_score': 0.75}),
        ],
        on_optimize=lambda params: None,
        _emit_agent_status=lambda *args, **kwargs: None,
        _emit_module_log=lambda *args, **kwargs: None,
        _emit_meeting_speech=lambda *args, **kwargs: None,
        _append_optimization_event=lambda event: None,
        _reload_manager_runtime=lambda path: None,
    )

    events = trigger_loss_optimization(
        controller,
        _optimization_input_from_payload({'cycle_id': 9}),
        [],
        event_factory=Event,
    )

    evo_event = next(item for item in events if item['stage'] == 'evolution_engine')
    fitness_scores = controller.evolution_engine.last_fitness

    assert len(fitness_scores) == 3
    assert fitness_scores[1] > fitness_scores[0]
    assert evo_event['decision']['fitness_scores'] == fitness_scores[-5:]


def test_trigger_loss_optimization_emits_cycle_id_and_lineage_contract_fields():
    controller = SimpleNamespace(
        consecutive_losses=3,
        llm_optimizer=FakeLLMOptimizer(),
        evolution_engine=FakeEvolutionEngine(),
        runtime_config_mutator=FakeRuntimeConfigMutator(),
        default_manager_id='momentum',
        default_manager_config_ref='src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml',
        auto_apply_mutation=False,
        current_params={'position_size': 0.2, 'take_profit_pct': 0.15},
        manager_runtime=FakeManagerRuntime(),
        cycle_history=[FakeCycle(-2.0), FakeCycle(-1.0), FakeCycle(0.5)],
        on_optimize=lambda params: None,
        _emit_agent_status=lambda *args, **kwargs: None,
        _emit_module_log=lambda *args, **kwargs: None,
        _emit_meeting_speech=lambda *args, **kwargs: None,
        _append_optimization_event=lambda event: None,
        _reload_manager_runtime=lambda path: None,
    )

    events = trigger_loss_optimization(
        controller,
        _optimization_input_from_payload({'cycle_id': 11}),
        [],
        event_factory=Event,
    )

    assert all(event['cycle_id'] == 11 for event in events)
    assert all('lineage' in event for event in events)
    assert all(event['lineage']['active_runtime_config_ref'] for event in events)
    mutation_event = next(item for item in events if item['stage'] == 'runtime_config_mutation')
    assert mutation_event['lineage']['deployment_stage'] == 'candidate'
    assert mutation_event['lineage']['candidate_runtime_config_ref'].endswith('candidate.yaml')
    assert mutation_event['evidence']['auto_applied'] is False
    assert mutation_event['runtime_config_mutation_payload']['runtime_config_ref'].endswith(
        'candidate.yaml'
    )


def test_trigger_loss_optimization_skips_new_candidate_when_pending_candidate_is_unresolved():
    appended = []
    controller = SimpleNamespace(
        consecutive_losses=3,
        llm_optimizer=FakeLLMOptimizer(),
        evolution_engine=FakeEvolutionEngine(),
        runtime_config_mutator=FakeRuntimeConfigMutator(),
        default_manager_id='momentum',
        default_manager_config_ref='src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml',
        auto_apply_mutation=False,
        current_params={'position_size': 0.2, 'take_profit_pct': 0.15},
        manager_runtime=FakeManagerRuntime(),
        cycle_history=[
            SimpleNamespace(
                cycle_id=6,
                lineage_record={
                    'deployment_stage': 'candidate',
                    'lineage_status': 'candidate_pending',
                    'candidate_runtime_config_ref': '/tmp/pending_candidate.yaml',
                    'active_runtime_config_ref': 'src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml',
                },
                return_pct=-1.0,
                benchmark_passed=False,
                strategy_scores={},
            ),
            FakeCycle(-2.0),
            FakeCycle(-1.0),
        ],
        on_optimize=lambda params: None,
        _emit_agent_status=lambda *args, **kwargs: None,
        _emit_module_log=lambda *args, **kwargs: None,
        _emit_meeting_speech=lambda *args, **kwargs: None,
        _append_optimization_event=lambda event: appended.append(event.to_dict()),
        _reload_manager_runtime=lambda path: None,
    )

    events = trigger_loss_optimization(
        controller,
        _optimization_input_from_payload({'cycle_id': 12}),
        [],
        event_factory=Event,
    )

    assert [event['stage'] for event in events] == ['llm_analysis', 'evolution_engine', 'runtime_config_mutation_skipped']
    skipped_event = events[-1]
    assert skipped_event['decision']['pending_candidate_runtime_config_ref'].endswith('pending_candidate.yaml')
    assert skipped_event['runtime_config_mutation_skipped_payload'] == {
        'skipped': True,
        'pending_candidate_runtime_config_ref': '/tmp/pending_candidate.yaml',
        'auto_applied': False,
        'param_adjustments': {'position_size': 0.12, 'take_profit_pct': 0.18},
        'scoring_adjustments': {},
        'skip_reason': 'pending_candidate_unresolved',
    }
    assert skipped_event['lineage']['deployment_stage'] == 'candidate'
    assert appended[-1]['stage'] == 'runtime_config_mutation_skipped'


def test_trigger_loss_optimization_uses_structured_loss_payload_and_execution_snapshot():
    llm_optimizer = FakeLLMOptimizer()
    controller = SimpleNamespace(
        consecutive_losses=3,
        llm_optimizer=llm_optimizer,
        evolution_engine=FakeEvolutionEngine(),
        runtime_config_mutator=FakeRuntimeConfigMutator(),
        default_manager_id='fallback_manager',
        default_manager_config_ref='src/invest_evolution/investment/runtimes/configs/fallback.yaml',
        auto_apply_mutation=False,
        current_params={'position_size': 0.2, 'take_profit_pct': 0.15},
        manager_runtime=FakeManagerRuntime(),
        cycle_history=[FakeCycle(-2.0), FakeCycle(-1.0), FakeCycle(0.5)],
        on_optimize=lambda params: None,
        _emit_agent_status=lambda *args, **kwargs: None,
        _emit_module_log=lambda *args, **kwargs: None,
        _emit_meeting_speech=lambda *args, **kwargs: None,
        _append_optimization_event=lambda event: None,
        _reload_manager_runtime=lambda path: None,
    )
    optimization_input = OptimizationInputEnvelope(
        simulation=SimulationStageEnvelope.from_structured_inputs(
            cycle_id=21,
            cutoff_date='20240301',
            regime='bull',
            selected_stocks=['sh.600001', 'sz.000001'],
            return_pct=-1.7,
            benchmark_passed=False,
            benchmark_strict_passed=False,
            sharpe_ratio=-0.3,
            max_drawdown=4.2,
            excess_return=-2.1,
            strategy_scores={'overall_score': 0.42},
            governance_decision={'dominant_manager_id': 'manager_alpha'},
            execution_snapshot={
                'active_runtime_config_ref': 'src/invest_evolution/investment/runtimes/configs/alpha.yaml',
                'manager_config_ref': 'src/invest_evolution/investment/runtimes/configs/alpha.yaml',
                'dominant_manager_id': 'manager_alpha',
                'manager_results': [{'manager_id': 'manager_alpha'}],
                'portfolio_plan': {'positions': [{'code': 'sh.600001', 'target_weight': 0.5}]},
            },
        ),
        research_feedback={'recommendation': {'bias': 'tighten_risk'}},
    )
    trade_dicts = [
        {'ts_code': 'sh.600001', 'pnl': 100.0},
        {'ts_code': 'sz.000001', 'pnl': -50.0},
    ]

    events = trigger_loss_optimization(
        controller,
        optimization_input,
        trade_dicts,
        event_factory=Event,
    )

    analysis_payload = llm_optimizer.calls[-1]['cycle_payload']
    assert analysis_payload['cycle_id'] == 21
    assert analysis_payload['cutoff_date'] == '20240301'
    assert analysis_payload['total_trades'] == 2
    assert analysis_payload['win_rate'] == 0.5
    assert analysis_payload['selected_stocks'] == ['sh.600001', 'sz.000001']
    assert analysis_payload['execution_snapshot']['active_runtime_config_ref'].endswith('alpha.yaml')
    mutation_event = next(item for item in events if item['stage'] == 'runtime_config_mutation')
    assert mutation_event['lineage']['active_runtime_config_ref'].endswith('alpha.yaml')


def test_trigger_loss_optimization_uses_session_state_when_available():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            current_params={'position_size': 0.2, 'take_profit_pct': 0.15},
            consecutive_losses=3,
            cycle_history=[FakeCycle(-2.0), FakeCycle(-1.0), FakeCycle(0.5)],
        ),
        llm_optimizer=FakeLLMOptimizer(),
        evolution_engine=FakeEvolutionEngine(),
        runtime_config_mutator=FakeRuntimeConfigMutator(),
        default_manager_id='momentum',
        default_manager_config_ref='src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml',
        auto_apply_mutation=False,
        manager_runtime=FakeManagerRuntime(),
        on_optimize=lambda params: None,
        _emit_agent_status=lambda *args, **kwargs: None,
        _emit_module_log=lambda *args, **kwargs: None,
        _emit_meeting_speech=lambda *args, **kwargs: None,
        _append_optimization_event=lambda event: None,
        _reload_manager_runtime=lambda path: None,
    )

    events = trigger_loss_optimization(
        controller,
        _optimization_input_from_payload({'cycle_id': 21}),
        [],
        event_factory=Event,
    )

    assert len(events) == 3
    assert controller.session_state.current_params['position_size'] == 0.12
    assert controller.session_state.current_params['take_profit_pct'] == 0.18
    assert controller.session_state.consecutive_losses == 0


def test_trigger_loss_optimization_prefers_cycle_scope_over_controller_defaults():
    mutator = FakeRuntimeConfigMutator()
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            current_params={'position_size': 0.2, 'take_profit_pct': 0.15},
            consecutive_losses=3,
            cycle_history=[FakeCycle(-2.0), FakeCycle(-1.0), FakeCycle(0.5)],
            default_manager_id='defensive',
            default_manager_config_ref='configs/defensive.yaml',
        ),
        llm_optimizer=FakeLLMOptimizer(),
        evolution_engine=FakeEvolutionEngine(),
        runtime_config_mutator=mutator,
        default_manager_id='defensive',
        default_manager_config_ref='configs/defensive.yaml',
        auto_apply_mutation=False,
        manager_runtime=FakeManagerRuntime(),
        on_optimize=lambda params: None,
        _emit_agent_status=lambda *args, **kwargs: None,
        _emit_module_log=lambda *args, **kwargs: None,
        _emit_meeting_speech=lambda *args, **kwargs: None,
        _append_optimization_event=lambda event: None,
        _reload_manager_runtime=lambda path: None,
    )

    events = trigger_loss_optimization(
        controller,
        _optimization_input_from_payload({
            'cycle_id': 34,
            'dominant_manager_id': 'value_quality',
            'governance_decision': {
                'dominant_manager_id': 'value_quality',
                'active_manager_ids': ['value_quality'],
                'manager_budget_weights': {'value_quality': 1.0},
                'regime': 'bear',
            },
            'execution_snapshot': {
                'active_runtime_config_ref': 'configs/value_active.yaml',
                'manager_config_ref': 'configs/value_active.yaml',
                'execution_defaults': {
                    'default_manager_id': 'value_quality',
                    'default_manager_config_ref': 'configs/value_active.yaml',
                },
                'dominant_manager_id': 'value_quality',
            },
        }),
        [],
        event_factory=Event,
    )

    llm_event = next(item for item in events if item['stage'] == 'llm_analysis')
    mutation_event = next(item for item in events if item['stage'] == 'runtime_config_mutation')

    assert llm_event['lineage']['manager_id'] == 'value_quality'
    assert llm_event['lineage']['active_runtime_config_ref'].endswith('configs/value_active.yaml')
    assert mutation_event['lineage']['manager_id'] == 'value_quality'
    assert mutator.calls[0][0].endswith('configs/value_active.yaml')


def test_trigger_loss_optimization_accepts_structured_optimization_envelope():
    controller = SimpleNamespace(
        consecutive_losses=3,
        llm_optimizer=FakeLLMOptimizer(),
        evolution_engine=FakeEvolutionEngine(),
        runtime_config_mutator=FakeRuntimeConfigMutator(),
        default_manager_id='momentum',
        default_manager_config_ref='configs/active.yaml',
        auto_apply_mutation=False,
        current_params={'position_size': 0.2, 'take_profit_pct': 0.15},
        manager_runtime=FakeManagerRuntime(),
        cycle_history=[FakeCycle(-2.0), FakeCycle(-1.0), FakeCycle(0.5)],
        on_optimize=lambda params: None,
        _emit_agent_status=lambda *args, **kwargs: None,
        _emit_module_log=lambda *args, **kwargs: None,
        _emit_meeting_speech=lambda *args, **kwargs: None,
        _append_optimization_event=lambda event: None,
        _reload_manager_runtime=lambda path: None,
    )

    optimization_input = OptimizationInputEnvelope(
        simulation=SimulationStageEnvelope.from_structured_inputs(
            cycle_id=55,
            cutoff_date='20250318',
            regime='bear',
            selection_mode='manager_portfolio',
            selected_stocks=['600519.SH'],
            return_pct=-1.9,
            benchmark_passed=False,
            benchmark_strict_passed=False,
            sharpe_ratio=-0.3,
            max_drawdown=0.09,
            excess_return=-0.04,
            strategy_scores={'overall_score': 0.2},
            governance_decision={'regime': 'bear'},
            execution_snapshot={
                'active_runtime_config_ref': 'configs/active.yaml',
                'manager_config_ref': 'configs/active.yaml',
                'execution_defaults': {
                    'default_manager_id': 'momentum',
                    'default_manager_config_ref': 'configs/active.yaml',
                },
                'dominant_manager_id': 'momentum',
            },
        ),
        research_feedback={'sample_count': 3},
        research_feedback_optimization={'triggered': True},
    )

    events = trigger_loss_optimization(controller, optimization_input, [], event_factory=Event)

    assert len(events) == 3
    mutation_event = next(item for item in events if item['stage'] == 'runtime_config_mutation')
    assert mutation_event['lineage']['manager_id'] == 'momentum'
    assert mutation_event['lineage']['active_runtime_config_ref'].endswith('configs/active.yaml')
