from __future__ import annotations

import logging
from typing import Any, Callable

from app.training.runtime_discipline import record_learning_proposal
from invest.evolution import derive_scoring_adjustments
from invest.services import EvolutionService
from invest.shared.model_governance import build_optimization_event_lineage, normalize_config_ref

logger = logging.getLogger(__name__)


def _queue_runtime_adjustments(
    controller: Any,
    adjustments: dict[str, Any],
    *,
    source: str,
    cycle_id: int | None,
    rationale: str = '',
    evidence: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    if not adjustments:
        return {}, []
    sanitize = getattr(controller, '_sanitize_runtime_param_adjustments', None)
    clean_adjustments = (
        sanitize(adjustments) if callable(sanitize) else dict(adjustments)
    )
    if not clean_adjustments:
        return {}, []
    proposal = record_learning_proposal(
        controller,
        source=source,
        patch=clean_adjustments,
        target_scope='candidate',
        rationale=rationale,
        evidence=evidence,
        metadata={'proposal_kind': 'runtime_param_adjustment'},
        cycle_id=cycle_id,
    )
    return clean_adjustments, [str(proposal.get('proposal_id') or '')]


def _queue_scoring_adjustments(
    controller: Any,
    adjustments: dict[str, Any],
    *,
    source: str,
    cycle_id: int | None,
    rationale: str = '',
    evidence: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    if not adjustments:
        return {}, []
    clean_adjustments = dict(adjustments)
    proposal = record_learning_proposal(
        controller,
        source=source,
        patch=clean_adjustments,
        target_scope='candidate',
        rationale=rationale,
        evidence=evidence,
        metadata={'proposal_kind': 'scoring_adjustment'},
        cycle_id=cycle_id,
    )
    return clean_adjustments, [str(proposal.get('proposal_id') or '')]


def _population_size(controller: Any) -> int:
    service = _resolve_evolution_service(controller)
    if service is None:
        return 0
    try:
        return int(getattr(service, "population_size"))
    except Exception:
        return 0


def _resolve_evolution_service(controller: Any) -> EvolutionService | Any | None:
    service = getattr(controller, "evolution_service", None)
    if service is not None:
        return service
    engine = getattr(controller, "evolution_engine", None)
    if engine is None:
        return None
    return EvolutionService(engine=engine)


def _benchmark_oriented_fitness(result: Any) -> float:
    base_return = max(min(float(getattr(result, "return_pct", 0.0) or 0.0), 50.0), -50.0)
    strategy_scores = dict(getattr(result, "strategy_scores", {}) or {})
    overall_score = max(0.0, min(1.0, float(strategy_scores.get("overall_score", 0.0) or 0.0)))
    benchmark_bonus = 2.5 if bool(getattr(result, "benchmark_passed", False)) else -2.5
    return round(base_return + overall_score * 3.0 + benchmark_bonus, 4)


def trigger_loss_optimization(
    controller: Any,
    cycle_dict: dict[str, Any],
    trade_dicts: list[dict[str, Any]],
    *,
    event_factory: Callable[..., Any],
    trigger_reason: str = 'consecutive_losses',
    feedback_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cycle_id = cycle_dict.get('cycle_id')
    model_name = str(cycle_dict.get('model_name') or getattr(controller, 'model_name', '') or '')
    active_config_ref = normalize_config_ref(
        cycle_dict.get('config_name')
        or getattr(controller, 'model_config_path', '')
        or ''
    )
    fitness_source_cycles = [
        int(getattr(item, 'cycle_id'))
        for item in list(getattr(controller, 'cycle_history', []) or [])[-10:]
        if getattr(item, 'cycle_id', None) is not None
    ]

    def _lineage(
        *,
        candidate_config_ref: str = '',
        deployment_stage: str = 'active',
        runtime_override_keys: list[str] | None = None,
        promotion_status: str = 'not_evaluated',
    ) -> dict[str, Any]:
        return build_optimization_event_lineage(
            cycle_id=int(cycle_id) if cycle_id is not None else None,
            model_name=model_name,
            active_config_ref=active_config_ref,
            candidate_config_ref=candidate_config_ref,
            promotion_status=promotion_status,
            deployment_stage=deployment_stage,
            review_basis_window={},
            fitness_source_cycles=fitness_source_cycles,
            runtime_override_keys=runtime_override_keys,
        )

    if trigger_reason == 'research_feedback':
        opening_message = 'ask 侧校准反馈触发自我优化...'
        opening_details = {
            'bias': dict((feedback_plan or {}).get('recommendation') or {}).get('bias'),
            'sample_count': int((feedback_plan or {}).get('sample_count') or 0),
        }
    else:
        opening_message = f"连续 {controller.consecutive_losses} 次亏损，触发自我优化..."
        opening_details = {'consecutive_losses': controller.consecutive_losses}

    logger.info('⚠️ %s', opening_message)
    controller._emit_agent_status(
        'EvolutionOptimizer',
        'running',
        opening_message,
        cycle_id=cycle_id,
        stage='optimization',
        progress_pct=90,
        step=5,
        total_steps=6,
        details=opening_details,
    )
    controller._emit_module_log(
        'optimization',
        '触发自我优化',
        opening_message,
        cycle_id=cycle_id,
        kind='optimization_start',
        level='warn',
        details=opening_details,
    )
    events: list[dict[str, Any]] = []
    queued_param_adjustments: dict[str, Any] = {}

    try:
        if feedback_plan:
            feedback_adjustments = dict(feedback_plan.get('param_adjustments') or {})
            feedback_scoring = dict(feedback_plan.get('scoring_adjustments') or {})
            feedback_proposal_refs: list[str] = []
            if feedback_adjustments:
                clean_feedback_adjustments, feedback_param_refs = _queue_runtime_adjustments(
                    controller,
                    feedback_adjustments,
                    source='optimization.research_feedback',
                    cycle_id=int(cycle_id) if cycle_id is not None else None,
                    rationale=str(feedback_plan.get('summary') or ''),
                    evidence={
                        'failed_horizons': list(feedback_plan.get('failed_horizons') or []),
                        'failed_check_names': list(feedback_plan.get('failed_check_names') or []),
                        'sample_count': int(feedback_plan.get('sample_count') or 0),
                    },
                )
                feedback_proposal_refs.extend(feedback_param_refs)
                queued_param_adjustments.update(clean_feedback_adjustments)
            if feedback_scoring:
                _, feedback_scoring_refs = _queue_scoring_adjustments(
                    controller,
                    feedback_scoring,
                    source='optimization.research_feedback_scoring',
                    cycle_id=int(cycle_id) if cycle_id is not None else None,
                    rationale=str(feedback_plan.get('summary') or ''),
                    evidence={
                        'failed_horizons': list(feedback_plan.get('failed_horizons') or []),
                        'failed_check_names': list(feedback_plan.get('failed_check_names') or []),
                        'sample_count': int(feedback_plan.get('sample_count') or 0),
                    },
                )
                feedback_proposal_refs.extend(feedback_scoring_refs)
            feedback_event = event_factory(
                cycle_id=int(cycle_id) if cycle_id is not None else None,
                trigger=trigger_reason,
                stage='research_feedback',
                decision={
                    'bias': feedback_plan.get('bias'),
                    'failed_horizons': list(feedback_plan.get('failed_horizons') or []),
                    'failed_checks': list(feedback_plan.get('failed_check_names') or []),
                    'mutation_mode': 'proposal_only',
                },
                suggestions=list(feedback_plan.get('suggestions') or []),
                applied_change={
                    'queued_params': dict(clean_feedback_adjustments if feedback_adjustments else {}),
                    'queued_scoring': dict(feedback_scoring),
                    'proposal_refs': feedback_proposal_refs,
                },
                lineage=_lineage(
                    deployment_stage='active',
                    runtime_override_keys=sorted(
                        {
                            *(str(key) for key in (clean_feedback_adjustments if feedback_adjustments else {}).keys()),
                            *(str(key) for key in feedback_scoring.keys()),
                        }
                    ),
                ),
                evidence={
                    'failed_horizons': list(feedback_plan.get('failed_horizons') or []),
                    'failed_check_names': list(feedback_plan.get('failed_check_names') or []),
                    'sample_count': int(feedback_plan.get('sample_count') or 0),
                    'severity': float(feedback_plan.get('severity') or 0.0),
                    'benchmark_context': dict(feedback_plan.get('benchmark_context') or {}),
                },
                notes=str(feedback_plan.get('summary') or 'proposal queued; active runtime unchanged'),
            )
            events.append(feedback_event.to_dict())
            controller._append_optimization_event(feedback_event)
            controller._emit_module_log(
                'optimization',
                '应用 ask 侧校准调参',
                str(feedback_plan.get('summary') or 'research feedback optimization'),
                cycle_id=cycle_id,
                kind='research_feedback_gate',
                details=feedback_plan,
                metrics={
                    'failed_check_count': len(feedback_plan.get('failed_check_names') or []),
                    'sample_count': int(feedback_plan.get('sample_count') or 0),
                    'param_adjustment_count': len(clean_feedback_adjustments if feedback_adjustments else {}),
                },
            )

        if trigger_reason == 'consecutive_losses':
            analysis = controller.llm_optimizer.analyze_loss(cycle_dict, trade_dicts)
            logger.info('LLM 分析: %s', analysis.cause)
            logger.info('建议: %s', analysis.suggestions)
            llm_event = event_factory(
                cycle_id=int(cycle_id) if cycle_id is not None else None,
                trigger='consecutive_losses',
                stage='llm_analysis',
                decision={'cause': analysis.cause, 'mutation_mode': 'proposal_only'},
                suggestions=list(getattr(analysis, 'suggestions', []) or []),
                lineage=_lineage(deployment_stage='active'),
                evidence={
                    'consecutive_losses': controller.consecutive_losses,
                    'trade_record_count': len(list(trade_dicts or [])),
                },
            )

            adjustments = controller.llm_optimizer.generate_strategy_fix(analysis) or {}
            if adjustments:
                clean_adjustments, proposal_refs = _queue_runtime_adjustments(
                    controller,
                    adjustments,
                    source='optimization.llm_analysis',
                    cycle_id=int(cycle_id) if cycle_id is not None else None,
                    rationale=str(analysis.cause or ''),
                    evidence={'suggestions': list(getattr(analysis, 'suggestions', []) or [])},
                )
                queued_param_adjustments.update(clean_adjustments)
                scoring_adjustments = derive_scoring_adjustments(controller.model_name, analysis)
                if scoring_adjustments:
                    _, scoring_proposal_refs = _queue_scoring_adjustments(
                        controller,
                        scoring_adjustments,
                        source='optimization.llm_scoring',
                        cycle_id=int(cycle_id) if cycle_id is not None else None,
                        rationale=str(analysis.cause or ''),
                        evidence={'suggestions': list(getattr(analysis, 'suggestions', []) or [])},
                    )
                    proposal_refs.extend(scoring_proposal_refs)
                llm_event.applied_change = {
                    'queued_params': dict(clean_adjustments),
                    'queued_scoring': dict(scoring_adjustments),
                    'proposal_refs': proposal_refs,
                }
                logger.info('参数提案已记录: %s', clean_adjustments)
            events.append(llm_event.to_dict())
            controller._append_optimization_event(llm_event)
            controller._emit_meeting_speech(
                'optimization',
                'EvolutionOptimizer',
                analysis.cause,
                cycle_id=cycle_id,
                role='optimizer',
                suggestions=list(getattr(analysis, 'suggestions', []) or []),
                decision={'adjustments': adjustments},
            )
            controller._emit_module_log(
                'optimization',
                'LLM 亏损分析',
                analysis.cause,
                cycle_id=cycle_id,
                kind='llm_analysis',
                details=list(getattr(analysis, 'suggestions', []) or []),
                metrics={'adjustment_count': len(adjustments)},
            )

            if len(controller.cycle_history) >= 3:
                fitness_scores = [
                    _benchmark_oriented_fitness(result)
                    for result in controller.cycle_history[-10:]
                ]
                evolution_service = _resolve_evolution_service(controller)
                if evolution_service is None:
                    raise RuntimeError("evolution runtime is unavailable")
                if _population_size(controller) == 0:
                    evolution_service.initialize_population(controller.current_params)
                pop_size = _population_size(controller)
                if len(fitness_scores) > pop_size:
                    fitness_scores = fitness_scores[-pop_size:]
                elif len(fitness_scores) < pop_size:
                    fitness_scores = fitness_scores + [0.0] * (pop_size - len(fitness_scores))

                evolution_service.evolve(fitness_scores)
                best_params = evolution_service.get_best_params()
                evo_event = event_factory(
                    cycle_id=int(cycle_id) if cycle_id is not None else None,
                    trigger='consecutive_losses',
                    stage='evolution_engine',
                    decision={
                        'fitness_scores': fitness_scores[-5:],
                        'fitness_policy': 'benchmark_oriented_v1',
                        'mutation_mode': 'proposal_only',
                    },
                    applied_change={},
                    lineage=_lineage(
                        deployment_stage='active',
                        runtime_override_keys=sorted(str(key) for key in (best_params or {}).keys()),
                    ),
                    evidence={
                        'fitness_sample_count': len(fitness_scores),
                        'population_size': _population_size(controller),
                    },
                    notes='population evolved',
                )
                if best_params:
                    clean_best_params, proposal_refs = _queue_runtime_adjustments(
                        controller,
                        best_params,
                        source='optimization.evolution_engine',
                        cycle_id=int(cycle_id) if cycle_id is not None else None,
                        rationale='population evolved',
                        evidence={
                            'fitness_scores': fitness_scores[-5:],
                            'fitness_policy': 'benchmark_oriented_v1',
                        },
                    )
                    evo_event.applied_change = {
                        'queued_params': dict(clean_best_params),
                        'proposal_refs': proposal_refs,
                    }
                    queued_param_adjustments.update(clean_best_params)
                    logger.info('遗传算法参数提案已记录: %s', clean_best_params)
                events.append(evo_event.to_dict())
                controller._append_optimization_event(evo_event)
                controller._emit_module_log(
                    'optimization',
                    '进化引擎完成一轮迭代',
                    '基于最近收益分布更新参数种群',
                    cycle_id=cycle_id,
                    kind='evolution_engine',
                    details=best_params or {},
                    metrics={'fitness_samples': fitness_scores[-5:], 'fitness_policy': 'benchmark_oriented_v1'},
                )

    except Exception as exc:
        err_event = event_factory(
            cycle_id=int(cycle_id) if cycle_id is not None else None,
            trigger=trigger_reason,
            stage='optimization_error',
            status='error',
            lineage=_lineage(deployment_stage='active'),
            evidence={'exception_type': exc.__class__.__name__},
            notes=str(exc),
        )
        events.append(err_event.to_dict())
        controller._append_optimization_event(err_event)
        controller._emit_agent_status(
            'EvolutionOptimizer',
            'error',
            f'优化过程出错: {exc}',
            cycle_id=cycle_id,
            stage='optimization',
            progress_pct=92,
            step=5,
            total_steps=6,
        )
        logger.error('优化过程出错: %s', exc)

    if trigger_reason == 'consecutive_losses':
        controller.consecutive_losses = 0
    logger.info('✅ 优化完成，继续训练...')
    controller._emit_agent_status(
        'EvolutionOptimizer',
        'completed',
        '优化完成，继续训练...',
        cycle_id=cycle_id,
        stage='optimization',
        progress_pct=94,
        step=5,
        total_steps=6,
        details={'event_count': len(events), 'trigger_reason': trigger_reason},
    )

    if controller.on_optimize:
        controller.on_optimize(dict(queued_param_adjustments or controller.current_params))
    return events
