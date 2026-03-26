from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from invest.contracts import resolve_agent_context_confidence
from invest.models.defaults import COMMON_PARAM_DEFAULTS
from app.training.runtime_discipline import (
    apply_regime_runtime_profile,
    build_regime_runtime_profile,
    resolve_effective_runtime_params,
)

_REGIME_NAMES = {"bull", "bear", "oscillation"}


@dataclass(frozen=True)
class TrainingSelectionResult:
    model_output: Any
    regime_result: dict[str, Any]
    trading_plan: Any
    meeting_log: dict[str, Any]
    strategy_advice: dict[str, Any]
    selected: list[str]
    selected_data: dict[str, Any]
    selection_mode: str
    agent_used: bool
    regime_runtime_profile: dict[str, Any] = field(default_factory=dict, compare=False)
    selection_intercepts: dict[str, Any] = field(default_factory=dict, compare=False)


def _agent_context_confidence(agent_context: Any, default: float) -> float:
    return float(resolve_agent_context_confidence(agent_context, default=default))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _safe_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_int(value: Any, default: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return number


def _normalize_regime(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _REGIME_NAMES else "unknown"


def _position_attr(position: Any, key: str, default: Any = None) -> Any:
    if isinstance(position, dict):
        return position.get(key, default)
    return getattr(position, key, default)


def _set_position_attr(position: Any, key: str, value: Any) -> None:
    if isinstance(position, dict):
        position[key] = value
        return
    setattr(position, key, value)


class TrainingSelectionService:
    """Owns model output extraction and selection-meeting orchestration."""

    def run_selection_stage(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
    ) -> TrainingSelectionResult | None:
        requested_regime = self._requested_regime(controller)
        regime_runtime_profile = build_regime_runtime_profile(
            controller,
            regime=requested_regime,
        )
        apply_regime_runtime_profile(controller, regime_runtime_profile)

        model_output = controller.investment_model.process(stock_data, cutoff_date)
        regime_result = self._build_regime_result(controller, model_output)
        (
            model_output,
            regime_result,
            regime_runtime_profile,
            passive_refresh_summary,
        ) = self._refresh_runtime_profile_from_model_regime(
            controller,
            cutoff_date=cutoff_date,
            stock_data=stock_data,
            regime_runtime_profile=regime_runtime_profile,
            model_output=model_output,
            regime_result=regime_result,
        )
        if passive_refresh_summary.get("applied"):
            controller._emit_module_log(
                "market_regime",
                "Pinned standard 被动刷新 regime overlay",
                (
                    f"regime={passive_refresh_summary.get('resolved_regime')}, "
                    f"rerun_model={bool(passive_refresh_summary.get('rerun_model'))}"
                ),
                cycle_id=cycle_id,
                kind="passive_regime_overlay_refresh",
                details=passive_refresh_summary,
                metrics={
                    "overlay_applied": bool(regime_runtime_profile.get("applied")),
                    "rerun_model": bool(passive_refresh_summary.get("rerun_model")),
                },
            )

        controller._emit_agent_status(
            "InvestmentModel",
            "completed",
            f"{controller.model_name} 已输出结构化信号与叙事上下文",
            cycle_id=cycle_id,
            stage="model_extraction",
            progress_pct=30,
            step=2,
            total_steps=6,
            details=model_output.to_dict(),
        )
        controller._emit_module_log(
            "model_extraction",
            "模型输出完成",
            model_output.agent_context.summary,
            cycle_id=cycle_id,
            kind="model_output",
            details={
                "model_name": model_output.model_name,
                "config_name": model_output.config_name,
                "selected_codes": model_output.signal_packet.selected_codes,
            },
            metrics={
                "signal_count": len(model_output.signal_packet.signals),
                "max_positions": model_output.signal_packet.max_positions,
                "regime_overlay_applied": bool(regime_runtime_profile.get("applied")),
            },
        )
        controller._emit_agent_status(
            "MarketRegime",
            "thinking",
            f"分析当前市场状态: {regime_result.get('regime', 'unknown')}",
            cycle_id=cycle_id,
            stage="market_regime",
            progress_pct=32,
            step=2,
            total_steps=6,
            thinking=controller._thinking_excerpt(model_output.agent_context.summary),
            details=regime_result,
        )
        controller._emit_module_log(
            "market_regime",
            "市场状态识别",
            f"当前市场状态: {regime_result.get('regime', 'unknown')}",
            cycle_id=cycle_id,
            kind="market_regime",
            details=model_output.agent_context.summary,
            metrics={
                "confidence": regime_result.get("confidence"),
                "suggested_exposure": regime_result.get("suggested_exposure"),
            },
        )

        meeting_data = controller.selection_meeting_service.run_with_model_output(model_output)
        trading_plan = meeting_data["trading_plan"]
        meeting_log = dict(meeting_data.get("meeting_log", {}) or {})
        strategy_advice = dict(meeting_data.get("strategy_advice", {}) or {})
        selection_intercepts = self._apply_regime_hard_filter(
            controller,
            regime_result=regime_result,
            model_output=model_output,
            trading_plan=trading_plan,
        )
        recorded_meeting_log = dict(meeting_log)
        recorded_meeting_log["selected"] = [
            str(_position_attr(position, "code", "") or "")
            for position in list(getattr(trading_plan, "positions", []) or [])
            if str(_position_attr(position, "code", "") or "")
        ]
        recorded_meeting_log["regime_runtime_profile"] = dict(regime_runtime_profile)
        recorded_meeting_log["selection_intercepts"] = dict(selection_intercepts)
        controller.meeting_recorder.save_selection(recorded_meeting_log, cycle_id)

        for hunter in meeting_log.get("hunters", []):
            picks = hunter.get("result", {}).get("picks", [])
            if picks:
                controller.agent_tracker.record_predictions(
                    cycle_id,
                    hunter.get("name", "unknown"),
                    picks,
                )
            controller._emit_meeting_speech(
                "selection",
                hunter.get("name", "unknown"),
                hunter.get("result", {}).get("overall_view")
                or hunter.get("result", {}).get("reasoning")
                or "已完成候选输出",
                cycle_id=cycle_id,
                role="hunter",
                picks=picks[:10],
                confidence=hunter.get("result", {}).get("confidence"),
            )

        selected = [str(_position_attr(position, "code", "")) for position in list(trading_plan.positions or [])]
        selected = [code for code in selected if code]
        agent_used = bool(meeting_log.get("hunters"))
        selection_mode = "meeting" if selected else "meeting_empty"
        if selected and getattr(trading_plan, "source", "") and trading_plan.source != "llm":
            selection_mode = f"{trading_plan.source}_selection"

        if not selected:
            reason = "模型与会议未产出可交易标的"
            if selection_intercepts.get("active") and int(selection_intercepts.get("position_count_before") or 0) > 0:
                reason = "regime hard filter rejected all candidates"
            controller._mark_cycle_skipped(
                cycle_id,
                cutoff_date,
                stage="selection",
                reason=reason,
            )
            return None

        if selection_intercepts.get("active"):
            controller._emit_module_log(
                "selection",
                "Regime hard filter 调整了交易计划",
                f"拦截/调整 {int(selection_intercepts.get('intercepted_count') or 0)} 项，剩余 {len(selected)} 只股票",
                cycle_id=cycle_id,
                kind="selection_hard_filter",
                details=selection_intercepts,
                metrics={
                    "intercepted_count": int(selection_intercepts.get("intercepted_count") or 0),
                    "selected_count": len(selected),
                    "remaining_exposure": float(selection_intercepts.get("exposure_after") or 0.0),
                },
            )

        controller._emit_agent_status(
            "SelectionMeeting",
            "completed",
            f"选股完成，共选中 {len(selected)} 只股票",
            cycle_id=cycle_id,
            stage="selection_meeting",
            progress_pct=58,
            step=2,
            total_steps=6,
            selected_stocks=selected[:10],
            details=meeting_log.get("selected", []),
        )
        controller._emit_module_log(
            "selection",
            "选股会议完成",
            f"最终选中 {len(selected)} 只股票",
            cycle_id=cycle_id,
            kind="selection_result",
            details=meeting_log.get("selected", selected)[:10],
            metrics={
                "selected_count": len(selected),
                "selection_mode": selection_mode,
            },
        )
        controller.agent_tracker.mark_selected(cycle_id, selected)

        selected_data = {code: stock_data[code] for code in selected if code in stock_data}
        if not selected_data:
            controller._mark_cycle_skipped(
                cycle_id,
                cutoff_date,
                stage="selection",
                reason="选股结果在数据集中不可用",
            )
            return None

        return TrainingSelectionResult(
            model_output=model_output,
            regime_result=regime_result,
            trading_plan=trading_plan,
            meeting_log=meeting_log,
            strategy_advice=strategy_advice,
            selected=selected,
            selected_data=selected_data,
            selection_mode=selection_mode,
            agent_used=agent_used,
            regime_runtime_profile=dict(regime_runtime_profile),
            selection_intercepts=dict(selection_intercepts),
        )

    def _refresh_runtime_profile_from_model_regime(
        self,
        controller: Any,
        *,
        cutoff_date: str,
        stock_data: dict[str, Any],
        regime_runtime_profile: dict[str, Any],
        model_output: Any,
        regime_result: dict[str, Any],
    ) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
        current_regime = _normalize_regime(regime_runtime_profile.get("regime"))
        inferred_regime = _normalize_regime(
            regime_result.get("regime") or getattr(model_output.signal_packet, "regime", "")
        )
        summary = {
            "applied": False,
            "current_regime": current_regime,
            "resolved_regime": inferred_regime,
            "rerun_model": False,
            "reason": "",
        }
        if inferred_regime == "unknown":
            summary["reason"] = "model_output_regime_unknown"
            return model_output, regime_result, regime_runtime_profile, summary

        refreshed_profile = build_regime_runtime_profile(
            controller,
            regime=inferred_regime,
            base_params=dict(regime_runtime_profile.get("base_params") or {}),
        )
        refreshed_profile["regime_resolution"] = {
            "mode": "passive_model_inference",
            "requested_regime": current_regime,
            "resolved_regime": inferred_regime,
        }
        effective_before = dict(regime_runtime_profile.get("effective_params") or {})
        effective_after = dict(refreshed_profile.get("effective_params") or {})
        needs_refresh = (
            inferred_regime != current_regime
            or bool(refreshed_profile.get("applied")) != bool(regime_runtime_profile.get("applied"))
            or effective_after != effective_before
        )
        if not needs_refresh:
            summary["reason"] = "profile_already_aligned"
            return model_output, regime_result, regime_runtime_profile, summary

        apply_regime_runtime_profile(controller, refreshed_profile)
        summary["applied"] = True
        summary["reason"] = "model_output_regime_refreshed"
        rerun_model = effective_after != effective_before
        if rerun_model:
            model_output = controller.investment_model.process(stock_data, cutoff_date)
            regime_result = self._build_regime_result(controller, model_output)
            summary["rerun_model"] = True
        else:
            regime_result = self._build_regime_result(controller, model_output)
        return model_output, regime_result, refreshed_profile, summary

    @staticmethod
    def _requested_regime(controller: Any) -> str:
        return str(dict(getattr(controller, "last_routing_decision", {}) or {}).get("regime") or "")

    def _resolve_signal_scores(self, signal_packet: Any) -> dict[str, float]:
        scores: dict[str, float] = {}
        for item in list(getattr(signal_packet, "signals", []) or []):
            if isinstance(item, dict):
                code = str(item.get("code") or "")
                score = _safe_optional_float(item.get("score"))
            else:
                code = str(getattr(item, "code", "") or "")
                score = _safe_optional_float(getattr(item, "score", None))
            if code and score is not None:
                scores[code] = score
        return scores

    def _resolve_entry_threshold_policy(
        self,
        *,
        profile: dict[str, Any],
        model_output: Any,
    ) -> dict[str, Any]:
        threshold_spec = dict(profile.get("entry_threshold") or {})
        threshold_key = str(threshold_spec.get("key") or "")
        threshold_value = _safe_optional_float(threshold_spec.get("value"))
        overlay_active = bool(profile.get("applied"))
        signal_packet = getattr(model_output, "signal_packet", None)
        signal_metadata = dict(getattr(signal_packet, "metadata", {}) or {})
        policy = dict(
            signal_metadata.get("entry_threshold_policy")
            or signal_metadata.get("entry_threshold_application")
            or {}
        )
        consumed_upstream = bool(policy.get("consumed_upstream"))
        post_selection_supported = policy.get("post_selection_filter_supported")
        post_selection_required = bool(policy.get("post_selection_filter_required"))

        reason = "overlay_inactive"
        enforced = False
        if overlay_active and threshold_key and threshold_value is not None:
            if consumed_upstream and not post_selection_required:
                reason = "threshold_already_consumed_upstream"
            elif post_selection_supported is False and not post_selection_required:
                reason = "model_managed_threshold_semantics"
            else:
                reason = "regime_overlay_post_selection_veto"
                enforced = True
        elif overlay_active:
            reason = "overlay_without_threshold"

        return {
            "key": threshold_key,
            "value": threshold_value,
            "enforced": enforced,
            "reason": reason,
            "overlay_active": overlay_active,
            "consumed_upstream": consumed_upstream,
            "post_selection_filter_supported": (
                True if post_selection_supported is None else bool(post_selection_supported)
            ),
            "post_selection_filter_required": post_selection_required,
            "policy_mode": str(policy.get("mode") or ""),
        }

    def _apply_regime_hard_filter(
        self,
        controller: Any,
        *,
        regime_result: dict[str, Any],
        model_output: Any,
        trading_plan: Any,
    ) -> dict[str, Any]:
        profile = dict(getattr(controller, "current_cycle_regime_profile", {}) or {})
        effective_params = dict(profile.get("effective_params") or resolve_effective_runtime_params(controller))
        regime = str(profile.get("regime") or regime_result.get("regime") or "unknown")
        threshold_policy = self._resolve_entry_threshold_policy(
            profile=profile,
            model_output=model_output,
        )
        threshold_enforced = bool(threshold_policy.get("enforced"))
        threshold_key = str(threshold_policy.get("key") or "") if threshold_enforced else ""
        threshold_value = (
            _safe_optional_float(threshold_policy.get("value"))
            if threshold_enforced
            else None
        )
        signal_scores = self._resolve_signal_scores(model_output.signal_packet)

        original_positions = list(getattr(trading_plan, "positions", []) or [])
        ordered_positions = sorted(
            original_positions,
            key=lambda position: _safe_int(
                _position_attr(position, "priority", 0),
                len(original_positions) + 1,
            ),
        )
        kept_positions: list[Any] = []
        actions: list[dict[str, Any]] = []
        reason_counts: dict[str, int] = {}
        intercepted_codes: set[str] = set()

        def record_action(reason: str, *, code: str = "", before: Any = None, after: Any = None) -> None:
            reason_counts[reason] = int(reason_counts.get(reason, 0) or 0) + 1
            if code:
                intercepted_codes.add(code)
            actions.append(
                {
                    "reason": reason,
                    "code": code,
                    "before": before,
                    "after": after,
                }
            )

        for position in ordered_positions:
            code = str(_position_attr(position, "code", "") or "")
            if threshold_key and threshold_value is not None:
                score = signal_scores.get(code)
                if score is not None and score < threshold_value:
                    record_action(
                        "weak_signal_below_regime_threshold",
                        code=code,
                        before=round(score, 6),
                        after=round(threshold_value, 6),
                    )
                    continue
            kept_positions.append(position)

        max_positions_cap = max(
            1,
            _safe_int(
                effective_params.get("max_positions"),
                _safe_int(
                    getattr(trading_plan, "max_positions", None),
                    len(kept_positions) or COMMON_PARAM_DEFAULTS["max_positions"],
                ),
            ),
        )
        if len(kept_positions) > max_positions_cap:
            dropped_positions = kept_positions[max_positions_cap:]
            kept_positions = kept_positions[:max_positions_cap]
            for position in dropped_positions:
                record_action(
                    "max_positions_regime_cap",
                    code=str(_position_attr(position, "code", "") or ""),
                    before=len(original_positions),
                    after=max_positions_cap,
                )

        position_size_cap = _safe_optional_float(effective_params.get("position_size"))
        default_position_size = position_size_cap or _safe_float(
            COMMON_PARAM_DEFAULTS["position_size"],
            COMMON_PARAM_DEFAULTS["position_size"],
        )
        prepared_weights: list[float] = []
        for position in kept_positions:
            code = str(_position_attr(position, "code", "") or "")
            weight = _safe_optional_float(_position_attr(position, "weight", None))
            if weight is None:
                weight = default_position_size
                _set_position_attr(position, "weight", round(weight, 4))
            if position_size_cap is not None and weight > position_size_cap:
                record_action(
                    "position_size_regime_cap",
                    code=code,
                    before=round(weight, 6),
                    after=round(position_size_cap, 6),
                )
                weight = position_size_cap
                _set_position_attr(position, "weight", round(weight, 4))
            prepared_weights.append(float(weight))

        current_cash_reserve = _safe_float(getattr(trading_plan, "cash_reserve", 0.0), 0.0)
        required_cash_reserve = _safe_float(
            effective_params.get("cash_reserve"),
            current_cash_reserve,
        )
        final_cash_reserve = max(current_cash_reserve, required_cash_reserve)
        if final_cash_reserve > current_cash_reserve + 1e-9:
            record_action(
                "cash_reserve_regime_floor",
                before=round(current_cash_reserve, 6),
                after=round(final_cash_reserve, 6),
            )

        exposure_before = round(sum(prepared_weights), 4)
        exposure_cap = max(0.0, min(1.0, 1.0 - final_cash_reserve))
        exposure_after = exposure_before
        if kept_positions and exposure_before > exposure_cap + 1e-9 and exposure_before > 0.0:
            scale = exposure_cap / exposure_before
            for index, position in enumerate(kept_positions):
                current_weight = prepared_weights[index]
                new_weight = round(current_weight * scale, 4)
                if new_weight < current_weight:
                    record_action(
                        "exposure_budget_regime_cap",
                        code=str(_position_attr(position, "code", "") or ""),
                        before=round(current_weight, 6),
                        after=round(new_weight, 6),
                    )
                _set_position_attr(position, "weight", new_weight)
            exposure_after = round(sum(_safe_float(_position_attr(position, "weight", 0.0), 0.0) for position in kept_positions), 4)

        setattr(trading_plan, "positions", kept_positions)
        setattr(
            trading_plan,
            "cash_reserve",
            round(final_cash_reserve, 4),
        )
        setattr(
            trading_plan,
            "max_positions",
            min(
                _safe_int(getattr(trading_plan, "max_positions", None), max_positions_cap),
                max_positions_cap,
            ),
        )

        summary = {
            "schema_version": "training.regime_hard_filter.v1",
            "regime": regime,
            "active": bool(actions),
            "budget": {
                "cash_reserve": round(final_cash_reserve, 4),
                "position_size_cap": position_size_cap,
                "max_positions_cap": max_positions_cap,
                "entry_threshold_enforced": threshold_enforced,
                "entry_threshold_reason": str(threshold_policy.get("reason") or ""),
                "entry_threshold_policy_mode": str(threshold_policy.get("policy_mode") or ""),
                "entry_threshold": {
                    "key": str(threshold_policy.get("key") or ""),
                    "value": threshold_policy.get("value"),
                },
            },
            "position_count_before": len(original_positions),
            "position_count_after": len(kept_positions),
            "exposure_before": exposure_before,
            "exposure_after": exposure_after,
            "intercepted_count": len(actions),
            "intercepted_codes": sorted(intercepted_codes),
            "reason_counts": reason_counts,
            "top_reason": (
                sorted(reason_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))[0][0]
                if reason_counts
                else ""
            ),
            "actions": actions,
        }
        setattr(controller, "current_cycle_selection_intercepts", dict(summary))
        return summary

    def _build_regime_result(self, controller: Any, model_output: Any) -> dict[str, Any]:
        signal_packet = model_output.signal_packet
        agent_context = model_output.agent_context
        routing_snapshot = dict(getattr(controller, "last_routing_decision", {}) or {})
        active_params = resolve_effective_runtime_params(controller)
        return {
            "regime": routing_snapshot.get("regime") or signal_packet.regime,
            "confidence": float(
                routing_snapshot.get("regime_confidence")
                or _agent_context_confidence(agent_context, default=0.72)
                or 0.72
            ),
            "reasoning": routing_snapshot.get("reasoning") or agent_context.summary,
            "suggested_exposure": max(0.0, min(1.0, 1.0 - float(signal_packet.cash_reserve))),
            "decision_source": routing_snapshot.get("decision_source", "model_output"),
            "params": {
                **dict(signal_packet.params or {}),
                "top_n": max(len(signal_packet.selected_codes), len(signal_packet.signals)),
                "max_positions": signal_packet.max_positions,
                "stop_loss_pct": signal_packet.params.get(
                    "stop_loss_pct",
                    active_params.get(
                        "stop_loss_pct",
                        COMMON_PARAM_DEFAULTS["stop_loss_pct"],
                    ),
                ),
                "take_profit_pct": signal_packet.params.get(
                    "take_profit_pct",
                    active_params.get(
                        "take_profit_pct",
                        COMMON_PARAM_DEFAULTS["take_profit_pct"],
                    ),
                ),
                "position_size": signal_packet.params.get(
                    "position_size",
                    active_params.get(
                        "position_size",
                        COMMON_PARAM_DEFAULTS["position_size"],
                    ),
                ),
            },
        }
