from __future__ import annotations

import logging
from typing import Any, Callable

from invest.evolution import derive_scoring_adjustments

logger = logging.getLogger(__name__)


def trigger_loss_optimization(
    controller: Any,
    cycle_dict: dict[str, Any],
    trade_dicts: list[dict[str, Any]],
    *,
    event_factory: Callable[..., Any],
) -> list[dict[str, Any]]:
    logger.info(f"⚠️ 连续 {controller.consecutive_losses} 次亏损，触发自我优化...")
    cycle_id = cycle_dict.get('cycle_id')
    controller._emit_agent_status(
        'EvolutionOptimizer',
        'running',
        f"连续 {controller.consecutive_losses} 次亏损，触发自我优化...",
        cycle_id=cycle_id,
        stage='optimization',
        progress_pct=90,
        step=5,
        total_steps=6,
        details={'consecutive_losses': controller.consecutive_losses},
    )
    controller._emit_module_log(
        'optimization',
        '触发自我优化',
        f"连续 {controller.consecutive_losses} 次亏损，开始诊断并调整参数",
        cycle_id=cycle_id,
        kind='optimization_start',
        level='warn',
    )
    events: list[dict[str, Any]] = []
    config_adjustments: dict[str, Any] = {}
    scoring_adjustments: dict[str, Any] = {}

    try:
        analysis = controller.llm_optimizer.analyze_loss(cycle_dict, trade_dicts)
        logger.info("LLM 分析: %s", analysis.cause)
        logger.info("建议: %s", analysis.suggestions)
        llm_event = event_factory(
            trigger='consecutive_losses',
            stage='llm_analysis',
            decision={'cause': analysis.cause},
            suggestions=list(getattr(analysis, 'suggestions', []) or []),
        )

        adjustments = controller.llm_optimizer.generate_strategy_fix(analysis) or {}
        if adjustments:
            controller.current_params.update(adjustments)
            if getattr(controller, 'investment_model', None) is not None:
                controller.investment_model.update_runtime_overrides(adjustments)
            config_adjustments.update(adjustments)
            scoring_adjustments.update(derive_scoring_adjustments(controller.model_name, analysis))
            llm_event.applied_change = dict(adjustments)
            logger.info("参数已更新: %s", controller.current_params)
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
            fitness_scores = [max(result.return_pct, -50) for result in controller.cycle_history[-10:]]
            if len(controller.evolution_engine.population) == 0:
                controller.evolution_engine.initialize_population(controller.current_params)

            pop_size = len(controller.evolution_engine.population)
            if len(fitness_scores) > pop_size:
                fitness_scores = fitness_scores[-pop_size:]
            elif len(fitness_scores) < pop_size:
                fitness_scores = fitness_scores + [0.0] * (pop_size - len(fitness_scores))

            controller.evolution_engine.evolve(fitness_scores)
            best_params = controller.evolution_engine.get_best_params()
            evo_event = event_factory(
                trigger='consecutive_losses',
                stage='evolution_engine',
                decision={'fitness_scores': fitness_scores[-5:]},
                applied_change=dict(best_params or {}),
                notes='population evolved',
            )
            if best_params:
                controller.current_params.update(best_params)
                if getattr(controller, 'investment_model', None) is not None:
                    controller.investment_model.update_runtime_overrides(best_params)
                config_adjustments.update(best_params)
                logger.info("遗传算法优化参数: %s", best_params)
            events.append(evo_event.to_dict())
            controller._append_optimization_event(evo_event)
            controller._emit_module_log(
                'optimization',
                '进化引擎完成一轮迭代',
                '基于最近收益分布更新参数种群',
                cycle_id=cycle_id,
                kind='evolution_engine',
                details=best_params or {},
                metrics={'fitness_samples': fitness_scores[-5:]},
            )

        if config_adjustments:
            mutation = controller.model_mutator.mutate(
                controller.model_config_path,
                param_adjustments=config_adjustments,
                scoring_adjustments=scoring_adjustments or None,
                narrative_adjustments={'last_trigger': 'consecutive_losses'},
                generation_label=f"cycle_{int(cycle_id or 0):04d}",
                parent_meta={'cycle_id': cycle_id, 'trigger': 'consecutive_losses', 'auto_apply': controller.auto_apply_mutation},
            )
            auto_applied = bool(controller.auto_apply_mutation)
            if auto_applied:
                controller._reload_investment_model(mutation['config_path'])
            mutation_event = event_factory(
                trigger='consecutive_losses',
                stage='yaml_mutation',
                decision={'config_path': mutation['config_path'], 'auto_applied': auto_applied},
                applied_change={'params': dict(config_adjustments), 'scoring': dict(scoring_adjustments)},
                notes='active model config mutated' if auto_applied else 'candidate model config generated; active config unchanged',
            )
            events.append(mutation_event.to_dict())
            controller._append_optimization_event(mutation_event)
            controller._emit_module_log(
                'optimization',
                '模型配置已变异',
                (
                    f"新的模型配置已生成并已接管 active：{mutation['config_path']}"
                    if auto_applied
                    else f"新的候选模型配置已生成（未自动接管 active）：{mutation['config_path']}"
                ),
                cycle_id=cycle_id,
                kind='yaml_mutation',
                details=mutation['meta'],
                metrics={'adjustment_count': len(config_adjustments)},
            )

    except Exception as exc:
        err_event = event_factory(
            trigger='consecutive_losses',
            stage='optimization_error',
            status='error',
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
        details={'event_count': len(events)},
    )

    if controller.on_optimize:
        controller.on_optimize(controller.current_params)
    return events
