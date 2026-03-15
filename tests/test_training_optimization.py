from __future__ import annotations

from types import SimpleNamespace

from app.training.optimization import trigger_loss_optimization


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

    def to_dict(self):
        return {
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


class FakeLLMOptimizer:
    def analyze_loss(self, cycle_dict, trade_dicts):
        return SimpleNamespace(cause='回撤来自追高', suggestions=['减少仓位', '加强确认'])

    def generate_strategy_fix(self, analysis):
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


class FakeMutator:
    def mutate(self, config_path, **kwargs):
        return {
            'config_path': '/tmp/candidate.yaml',
            'meta': {'config_path': '/tmp/candidate.yaml', **kwargs},
        }


class FakeModel:
    def __init__(self):
        self.overrides = []

    def update_runtime_overrides(self, updates):
        self.overrides.append(dict(updates))


class FakeCycle:
    def __init__(self, return_pct, *, benchmark_passed=False, strategy_scores=None):
        self.return_pct = return_pct
        self.benchmark_passed = benchmark_passed
        self.strategy_scores = dict(strategy_scores or {})


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
        model_mutator=FakeMutator(),
        model_name='momentum',
        model_config_path='invest/models/configs/momentum_v1.yaml',
        auto_apply_mutation=False,
        current_params={'position_size': 0.2, 'take_profit_pct': 0.15},
        investment_model=FakeModel(),
        cycle_history=[FakeCycle(-2.0), FakeCycle(-1.0), FakeCycle(0.5)],
        on_optimize=lambda params: optimized.append(dict(params)),
        _emit_agent_status=lambda *args, **kwargs: agent_events.append((args, kwargs)),
        _emit_module_log=lambda *args, **kwargs: logs.append((args, kwargs)),
        _emit_meeting_speech=lambda *args, **kwargs: speeches.append((args, kwargs)),
        _append_optimization_event=lambda event: appended.append(event.to_dict()),
        _reload_investment_model=lambda path: (_ for _ in ()).throw(AssertionError('should not auto reload')),
    )

    events = trigger_loss_optimization(controller, {'cycle_id': 7}, [], event_factory=Event)

    assert controller.consecutive_losses == 0
    assert len(events) == 3
    assert [event['stage'] for event in events] == ['llm_analysis', 'evolution_engine', 'yaml_mutation']
    assert events[-1]['decision']['auto_applied'] is False
    assert controller.current_params['position_size'] == 0.12
    assert controller.current_params['take_profit_pct'] == 0.18
    assert optimized and optimized[-1]['take_profit_pct'] == 0.18
    assert appended[-1]['stage'] == 'yaml_mutation'
    assert any(kwargs.get('kind') == 'yaml_mutation' for _, kwargs in logs)


def test_trigger_loss_optimization_uses_benchmark_oriented_fitness_scores():
    controller = SimpleNamespace(
        consecutive_losses=3,
        llm_optimizer=FakeLLMOptimizer(),
        evolution_engine=FakeEvolutionEngine(),
        model_mutator=FakeMutator(),
        model_name='momentum',
        model_config_path='invest/models/configs/momentum_v1.yaml',
        auto_apply_mutation=False,
        current_params={'position_size': 0.2, 'take_profit_pct': 0.15},
        investment_model=FakeModel(),
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
        _reload_investment_model=lambda path: None,
    )

    events = trigger_loss_optimization(controller, {'cycle_id': 9}, [], event_factory=Event)

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
        model_mutator=FakeMutator(),
        model_name='momentum',
        model_config_path='invest/models/configs/momentum_v1.yaml',
        auto_apply_mutation=False,
        current_params={'position_size': 0.2, 'take_profit_pct': 0.15},
        investment_model=FakeModel(),
        cycle_history=[FakeCycle(-2.0), FakeCycle(-1.0), FakeCycle(0.5)],
        on_optimize=lambda params: None,
        _emit_agent_status=lambda *args, **kwargs: None,
        _emit_module_log=lambda *args, **kwargs: None,
        _emit_meeting_speech=lambda *args, **kwargs: None,
        _append_optimization_event=lambda event: None,
        _reload_investment_model=lambda path: None,
    )

    events = trigger_loss_optimization(controller, {'cycle_id': 11}, [], event_factory=Event)

    assert all(event['cycle_id'] == 11 for event in events)
    assert all('lineage' in event for event in events)
    assert all(event['lineage']['active_config_ref'] for event in events)
    mutation_event = next(item for item in events if item['stage'] == 'yaml_mutation')
    assert mutation_event['lineage']['deployment_stage'] == 'candidate'
    assert mutation_event['lineage']['candidate_config_ref'].endswith('candidate.yaml')
    assert mutation_event['evidence']['auto_applied'] is False


def test_trigger_loss_optimization_skips_new_candidate_when_pending_candidate_is_unresolved():
    appended = []
    controller = SimpleNamespace(
        consecutive_losses=3,
        llm_optimizer=FakeLLMOptimizer(),
        evolution_engine=FakeEvolutionEngine(),
        model_mutator=FakeMutator(),
        model_name='momentum',
        model_config_path='invest/models/configs/momentum_v1.yaml',
        auto_apply_mutation=False,
        current_params={'position_size': 0.2, 'take_profit_pct': 0.15},
        investment_model=FakeModel(),
        cycle_history=[
            SimpleNamespace(
                cycle_id=6,
                lineage_record={
                    'deployment_stage': 'candidate',
                    'lineage_status': 'candidate_pending',
                    'candidate_config_ref': '/tmp/pending_candidate.yaml',
                    'active_config_ref': 'invest/models/configs/momentum_v1.yaml',
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
        _reload_investment_model=lambda path: None,
    )

    events = trigger_loss_optimization(controller, {'cycle_id': 12}, [], event_factory=Event)

    assert [event['stage'] for event in events] == ['llm_analysis', 'evolution_engine', 'yaml_mutation_skipped']
    skipped_event = events[-1]
    assert skipped_event['decision']['pending_candidate_ref'].endswith('pending_candidate.yaml')
    assert skipped_event['lineage']['deployment_stage'] == 'candidate'
    assert appended[-1]['stage'] == 'yaml_mutation_skipped'
