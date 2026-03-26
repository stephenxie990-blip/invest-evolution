from __future__ import annotations

import logging
from typing import Any

from config import config
from app.training.candidate_builder import build_cycle_candidate_from_proposals
from app.training.experiment_protocol import build_cycle_run_context, build_execution_snapshot
from app.training.proposal_store import persist_cycle_proposal_bundle
from app.training.runtime_discipline import begin_cycle_runtime_window, finalize_cycle_runtime_window
from invest.models import resolve_model_config_path

logger = logging.getLogger(__name__)


class TrainingExecutionService:
    """Owns the main training-cycle execution path after data loading."""

    @staticmethod
    def _reload_investment_model(controller: Any, config_path: str) -> None:
        routing_service = getattr(controller, "training_routing_service", None)
        if routing_service is not None:
            routing_service.reload_investment_model(controller, config_path)
            return
        controller._reload_investment_model(config_path)

    def execute_loaded_cycle(
        self,
        controller: Any,
        *,
        result_factory: Any,
        optimization_event_factory: Any,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
        diagnostics: dict[str, Any],
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        data_mode: str,
        llm_used: bool,
        optimization_events: list[dict[str, Any]],
    ) -> Any | None:
        del diagnostics
        if controller.experiment_allowed_models and controller.model_name not in controller.experiment_allowed_models:
            controller.model_name = controller.experiment_allowed_models[0]
            controller.model_config_path = str(resolve_model_config_path(controller.model_name))
            controller.current_params = {}
            self._reload_investment_model(controller, controller.model_config_path)

        controller._maybe_apply_allocator(stock_data, cutoff_date, cycle_id)
        if controller.experiment_allowed_models and controller.model_name not in controller.experiment_allowed_models:
            controller.model_name = controller.experiment_allowed_models[0]
            controller.model_config_path = str(resolve_model_config_path(controller.model_name))
            controller.current_params = {}
            self._reload_investment_model(controller, controller.model_config_path)
        begin_cycle_runtime_window(controller, cycle_id=cycle_id)
        try:
            logger.info("Agent 开会讨论选股...")
            controller._emit_agent_status(
                "SelectionMeeting",
                "running",
                "Agent 开会讨论选股...",
                cycle_id=cycle_id,
                stage="selection_meeting",
                progress_pct=26,
                step=2,
                total_steps=6,
            )
            controller._emit_module_log(
                "selection",
                "进入选股会议",
                "系统开始汇总市场状态和候选标的",
                cycle_id=cycle_id,
                kind="phase_start",
            )
            selection_result = controller.training_selection_service.run_selection_stage(
                controller,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                stock_data=stock_data,
            )
            if selection_result is None:
                return None

            model_output = selection_result.model_output
            regime_result = selection_result.regime_result
            trading_plan = selection_result.trading_plan
            selected = selection_result.selected
            selected_data = selection_result.selected_data
            selection_mode = selection_result.selection_mode
            agent_used = selection_result.agent_used
            regime_runtime_profile = dict(selection_result.regime_runtime_profile or {})
            selection_intercepts = dict(selection_result.selection_intercepts or {})
            logger.info("市场状态(v2): %s", regime_result.get("regime", "unknown"))
            logger.info("最终选中股票: %s", selected)

            trader = controller.training_simulation_service.build_trader(
                controller,
                selected_data=selected_data,
                trading_plan=trading_plan,
            )
            simulation_days = max(
                1,
                int(controller.experiment_simulation_days or getattr(config, "simulation_days", 30)),
            )
            trading_dates = controller.training_simulation_service.resolve_trading_dates(
                selected_data=selected_data,
                cutoff_date=cutoff_date,
                simulation_days=simulation_days,
            )
            if len(trading_dates) < simulation_days:
                logger.warning("截断日期后交易日不足: %s < %s", len(trading_dates), simulation_days)
                controller._mark_cycle_skipped(
                    cycle_id,
                    cutoff_date,
                    stage="simulation",
                    reason=f"截断日期后交易日不足: {len(trading_dates)} < {simulation_days}",
                )
                return None

            controller._emit_agent_status(
                "SimulatedTrader",
                "running",
                f"模拟交易中... 初始资金 {trader.initial_capital:.2f}",
                cycle_id=cycle_id,
                stage="simulation",
                progress_pct=68,
                step=3,
                total_steps=6,
                details={"simulation_days": simulation_days, "selected_count": len(selected)},
            )
            controller._emit_module_log(
                "simulation",
                "开始模拟交易",
                f"模拟 {simulation_days} 个交易日，标的 {', '.join(selected[:5])}",
                cycle_id=cycle_id,
                kind="simulation_start",
                metrics={"simulation_days": simulation_days, "selected_count": len(selected)},
            )

            benchmark_daily_values, market_index_frame = controller.training_simulation_service.build_benchmark_context(
                controller,
                cutoff_date=cutoff_date,
                trading_dates=trading_dates,
            )
            if market_index_frame is not None and not market_index_frame.empty:
                trader.set_market_index_data(market_index_frame)
            sim_result = trader.run_simulation(trading_dates[0], trading_dates)
            is_profit = sim_result.return_pct > 0

            controller.agent_tracker.record_outcomes(cycle_id, sim_result.per_stock_pnl)
            cycle_dict = controller.training_simulation_service.build_cycle_dict(
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                sim_result=sim_result,
                selected=selected,
                is_profit=is_profit,
                regime_result=regime_result,
                routing_decision=dict(controller.last_routing_decision or {}),
                trading_plan=trading_plan,
                data_mode=data_mode,
                requested_data_mode=requested_data_mode,
                effective_data_mode=effective_data_mode,
                llm_mode=llm_mode,
                degraded=degraded,
                degrade_reason=degrade_reason,
                selection_mode=selection_mode,
                agent_used=agent_used,
                llm_used=llm_used,
            )
            cycle_dict["regime_runtime_profile"] = regime_runtime_profile
            cycle_dict["selection_intercepts"] = selection_intercepts
            trade_dicts = controller.training_simulation_service.build_trade_dicts(sim_result)
            benchmark_passed = controller.training_simulation_service.evaluate_cycle(
                controller,
                cycle_dict=cycle_dict,
                trade_dicts=trade_dicts,
                sim_result=sim_result,
                benchmark_daily_values=benchmark_daily_values,
            )
            research_artifacts = controller.training_research_service.persist_cycle_research_artifacts(
                controller,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                model_output=model_output,
                stock_data=stock_data,
                selected=selected,
                regime_result=regime_result,
                selection_mode=selection_mode,
            )
            cycle_dict["research_artifacts"] = dict(research_artifacts or {})
            research_feedback = controller._load_research_feedback(
                cutoff_date=cutoff_date,
                model_name=getattr(model_output, "model_name", controller.model_name),
                config_name=getattr(model_output, "config_name", controller.model_config_path),
                regime=str(regime_result.get("regime") or ""),
            )
            cycle_dict["research_feedback"] = dict(research_feedback or {})
            if research_feedback:
                controller._emit_module_log(
                    "review",
                    "载入 ask 侧校准反馈",
                    dict(research_feedback.get("recommendation") or {}).get(
                        "summary",
                        "research feedback loaded",
                    ),
                    cycle_id=cycle_id,
                    kind="research_feedback",
                    details=research_feedback,
                    metrics=controller._research_feedback_brief(research_feedback),
                )
            if research_artifacts:
                controller._emit_module_log(
                    "research",
                    "训练样本已写入研究归因库",
                    f"cases={int(research_artifacts.get('saved_case_count') or 0)}, attributions={int(research_artifacts.get('saved_attribution_count') or 0)}",
                    cycle_id=cycle_id,
                    kind="research_persisted",
                    details=research_artifacts,
                    metrics={
                        "saved_case_count": int(research_artifacts.get("saved_case_count") or 0),
                        "saved_attribution_count": int(research_artifacts.get("saved_attribution_count") or 0),
                    },
                )
            cycle_dict["execution_snapshot"] = build_execution_snapshot(
                controller,
                cycle_id=cycle_id,
                model_output=model_output,
                selection_mode=selection_mode,
                benchmark_passed=benchmark_passed,
                return_pct=sim_result.return_pct,
                is_profit=is_profit,
            )
            if getattr(controller, "last_cutoff_policy_context", None):
                cycle_dict["execution_snapshot"]["cutoff_policy_context"] = dict(
                    controller.last_cutoff_policy_context or {}
                )
            controller._emit_agent_status(
                "SimulatedTrader",
                "completed",
                f"模拟完成，收益 {sim_result.return_pct:+.2f}% ，共 {sim_result.total_trades} 笔交易",
                cycle_id=cycle_id,
                stage="simulation",
                progress_pct=78,
                step=3,
                total_steps=6,
                details={"final_value": sim_result.final_value, "win_rate": sim_result.win_rate},
            )
            controller._emit_module_log(
                "simulation",
                "模拟交易完成",
                f"期末资金 {sim_result.final_value:.2f}，收益 {sim_result.return_pct:+.2f}%",
                cycle_id=cycle_id,
                kind="simulation_result",
                details=trade_dicts[:12],
                metrics={
                    "return_pct": sim_result.return_pct,
                    "trade_count": sim_result.total_trades,
                    "win_rate": sim_result.win_rate,
                },
            )

            feedback_plan = controller._build_feedback_optimization_plan(
                research_feedback,
                cycle_id=cycle_id,
            )
            controller.last_feedback_optimization = controller._feedback_optimization_brief(
                feedback_plan,
                triggered=False,
            )
            if feedback_plan:
                cycle_dict["research_feedback_optimization"] = dict(controller.last_feedback_optimization)

            if not is_profit:
                controller.consecutive_losses += 1
                logger.warning("亏损！连续亏损: %s", controller.consecutive_losses)
                if controller.consecutive_losses >= controller.max_losses_before_optimize:
                    optimization_events.extend(
                        controller._trigger_optimization(
                            cycle_dict,
                            trade_dicts,
                            trigger_reason="consecutive_losses",
                            feedback_plan=feedback_plan or None,
                        )
                    )
                    if feedback_plan:
                        controller.last_feedback_optimization_cycle_id = cycle_id
                        controller.last_feedback_optimization = controller._feedback_optimization_brief(
                            feedback_plan,
                            triggered=True,
                        )
                        cycle_dict["research_feedback_optimization"] = dict(
                            controller.last_feedback_optimization
                        )
                        feedback_plan = {}
            else:
                controller.consecutive_losses = 0
                logger.info("盈利！收益率: %.2f%%", sim_result.return_pct)

            if feedback_plan:
                optimization_events.extend(
                    controller._trigger_optimization(
                        cycle_dict,
                        trade_dicts,
                        trigger_reason="research_feedback",
                        feedback_plan=feedback_plan,
                    )
                )
                controller.last_feedback_optimization_cycle_id = cycle_id
                controller.last_feedback_optimization = controller._feedback_optimization_brief(
                    feedback_plan,
                    triggered=True,
                )
                cycle_dict["research_feedback_optimization"] = dict(controller.last_feedback_optimization)

            logger.info("周期结语：复盘会议自省...")
            review_stage_result = controller.training_review_stage_service.run_review_stage(
                controller,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                sim_result=sim_result,
                regime_result=regime_result,
                selected=selected,
                cycle_dict=cycle_dict,
                trade_dicts=trade_dicts,
                requested_data_mode=requested_data_mode,
                effective_data_mode=effective_data_mode,
                llm_mode=llm_mode,
                degraded=degraded,
                degrade_reason=degrade_reason,
                data_mode=data_mode,
                selection_mode=selection_mode,
                agent_used=agent_used,
                llm_used=llm_used,
                model_output=model_output,
                research_feedback=research_feedback,
                optimization_event_factory=optimization_event_factory,
            )
            review_decision = review_stage_result.review_decision
            review_applied = review_stage_result.review_applied
            review_event = review_stage_result.review_event
            optimization_events.append(review_event.to_dict())
            proposal_bundle = persist_cycle_proposal_bundle(
                controller,
                cycle_id=cycle_id,
                execution_snapshot=dict(cycle_dict.get("execution_snapshot") or {}),
            )
            cycle_dict["proposal_bundle"] = dict(proposal_bundle)
            candidate_event = build_cycle_candidate_from_proposals(
                controller,
                cycle_id=cycle_id,
                proposal_bundle=proposal_bundle,
                event_factory=optimization_event_factory,
                trigger_reason="cycle_review_completed",
            )
            refreshed_proposal_bundle = dict(
                getattr(controller, "last_cycle_proposal_bundle", {}) or proposal_bundle
            )
            if refreshed_proposal_bundle:
                cycle_dict["proposal_bundle"] = refreshed_proposal_bundle
            if candidate_event is not None:
                optimization_events.append(candidate_event.to_dict())
            run_context = build_cycle_run_context(
                controller,
                cycle_id=cycle_id,
                model_output=model_output,
                optimization_events=optimization_events,
                execution_snapshot=dict(cycle_dict.get("execution_snapshot") or {}),
            )
            ab_comparison = controller.training_ab_service.run_candidate_ab_comparison(
                controller,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                stock_data=stock_data,
                model_name=str(getattr(model_output, "model_name", controller.model_name) or controller.model_name),
                active_config_ref=str(run_context.get("active_config_ref") or ""),
                candidate_config_ref=str(run_context.get("candidate_config_ref") or ""),
                baseline_regime=str(regime_result.get("regime") or ""),
            )
            if ab_comparison:
                cycle_dict["ab_comparison"] = dict(ab_comparison)
                controller._emit_module_log(
                    "promotion",
                    "候选策略 A/B 对照完成",
                    f"winner={dict(ab_comparison.get('comparison') or {}).get('winner', 'inconclusive')}",
                    cycle_id=cycle_id,
                    kind="candidate_ab_comparison",
                    details=ab_comparison,
                    metrics={
                        "return_lift_pct": dict(ab_comparison.get("comparison") or {}).get("return_lift_pct"),
                        "strategy_score_lift": dict(ab_comparison.get("comparison") or {}).get("strategy_score_lift"),
                        "benchmark_lift": dict(ab_comparison.get("comparison") or {}).get("benchmark_lift"),
                    },
                )

            config_snapshot_path = str(
                controller.config_service.write_runtime_snapshot(
                    cycle_id=cycle_id,
                    output_dir=controller.output_dir,
                )
            )
            cycle_dict["analysis"] = review_decision.get("reasoning", "")
            cycle_dict["review_decision"] = dict(review_decision or {})
            cycle_dict["causal_diagnosis"] = dict(review_decision.get("causal_diagnosis") or {})
            cycle_dict["similarity_summary"] = dict(review_decision.get("similarity_summary") or {})
            cycle_dict["similar_results"] = [
                dict(item) for item in list(review_decision.get("similar_results") or [])
            ]
            audit_tags = controller.training_outcome_service.build_audit_tags(
                controller,
                data_mode=data_mode,
                requested_data_mode=requested_data_mode,
                effective_data_mode=effective_data_mode,
                llm_mode=llm_mode,
                degraded=degraded,
                degrade_reason=degrade_reason,
                selection_mode=selection_mode,
                agent_used=agent_used,
                llm_used=llm_used,
                benchmark_passed=benchmark_passed,
                review_applied=review_applied,
                regime_result=regime_result,
            )
            cycle_result = controller.training_outcome_service.build_cycle_result(
                controller,
                result_factory=result_factory,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                selected=selected,
                sim_result=sim_result,
                is_profit=is_profit,
                trade_dicts=trade_dicts,
                data_mode=data_mode,
                requested_data_mode=requested_data_mode,
                effective_data_mode=effective_data_mode,
                llm_mode=llm_mode,
                degraded=degraded,
                degrade_reason=degrade_reason,
                selection_mode=selection_mode,
                agent_used=agent_used,
                llm_used=llm_used,
                benchmark_passed=benchmark_passed,
                cycle_dict=cycle_dict,
                review_applied=review_applied,
                config_snapshot_path=config_snapshot_path,
                optimization_events=optimization_events,
                audit_tags=audit_tags,
                model_output=model_output,
                research_feedback=research_feedback,
                research_artifacts=research_artifacts,
                ab_comparison=dict(cycle_dict.get("ab_comparison") or {}),
            )
            controller.training_lifecycle_service.finalize_cycle(
                controller,
                cycle_result=cycle_result,
                cycle_dict=cycle_dict,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                sim_result=sim_result,
                is_profit=is_profit,
                selected=selected,
                trade_dicts=trade_dicts,
                review_applied=review_applied,
                selection_mode=selection_mode,
                requested_data_mode=requested_data_mode,
                effective_data_mode=effective_data_mode,
                llm_mode=llm_mode,
                degraded=degraded,
                degrade_reason=degrade_reason,
                research_feedback=research_feedback,
            )
            return cycle_result
        finally:
            discipline_summary = finalize_cycle_runtime_window(controller)
            if discipline_summary.get("violation_count"):
                controller._emit_module_log(
                    "runtime_discipline",
                    "检测到 cycle 内非法 runtime 变更",
                    "已回滚到 cycle 起点 active 参数",
                    cycle_id=cycle_id,
                    kind="runtime_mutation_violation",
                    details=discipline_summary,
                    metrics={
                        "violation_count": int(discipline_summary.get("violation_count") or 0),
                        "proposal_count": int(discipline_summary.get("proposal_count") or 0),
                    },
                )
