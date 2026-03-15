from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import pandas as pd

from invest.allocator import build_allocation_plan, load_leaderboard
from invest.contracts import ModelRoutingDecision
from invest.foundation.compute.features import compute_market_stats
from invest.models import list_models, resolve_model_config_path
from invest.shared.model_regime import regime_compatibility

DEFAULT_ROUTING_POLICY: Dict[str, Any] = {
    "bull_avg_change_20d": 3.0,
    "bull_above_ma20_ratio": 0.55,
    "bear_avg_change_20d": -3.0,
    "bear_above_ma20_ratio": 0.45,
    "high_volatility_threshold": 0.028,
    "weak_breadth_threshold": 0.42,
    "strong_breadth_threshold": 0.58,
    "index_bull_change_20d": 2.0,
    "index_bear_change_20d": -2.0,
    "default_regime": "oscillation",
    "cooldown_exception_min_candidate_weight_gap": 0.10,
    "cooldown_exception_min_confidence": 0.72,
    "cooldown_exception_min_benchmark_gap": 0.15,
    "cooldown_exception_min_return_gap": 0.75,
    "cooldown_exception_current_benchmark_pass_rate_max": 0.45,
    "cooldown_exception_current_avg_return_pct_max": 0.0,
    "min_regime_style_compatibility": 0.40,
}

DEFAULT_ROUTING_ALLOWED_MODELS = list_models()


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    return cast(pd.Series, pd.to_numeric(frame[column], errors="coerce"))


@dataclass
class MarketObservation:
    as_of_date: str
    stats: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "stats": dict(self.stats),
            "evidence": dict(self.evidence),
        }


class MarketObservationService:
    def __init__(self, routing_policy: Optional[Dict[str, Any]] = None):
        self.routing_policy = dict(DEFAULT_ROUTING_POLICY)
        self.routing_policy.update(dict(routing_policy or {}))

    def observe(
        self,
        stock_data: Dict[str, pd.DataFrame],
        cutoff_date: str,
        *,
        data_manager: Any = None,
    ) -> MarketObservation:
        stats = compute_market_stats(stock_data, cutoff_date, regime_policy=self.routing_policy)
        evidence: Dict[str, Any] = {
            "stock_universe_size": len(stock_data or {}),
            "valid_stocks": int(stats.get("valid_stocks", 0) or 0),
        }
        index_frame = pd.DataFrame()
        if data_manager is not None and hasattr(data_manager, "get_market_index_frame"):
            try:
                index_frame = data_manager.get_market_index_frame(index_code="sh.000300")
            except Exception:
                index_frame = pd.DataFrame()
        if not index_frame.empty:
            enriched = self._summarize_index_frame(index_frame, cutoff_date)
            stats.update(enriched)
            evidence["index_metrics"] = enriched
        stats["observation_source"] = "market_observer"
        return MarketObservation(as_of_date=cutoff_date, stats=stats, evidence=evidence)

    @staticmethod
    def _summarize_index_frame(index_frame: pd.DataFrame, cutoff_date: str) -> Dict[str, Any]:
        frame = index_frame.copy()
        if "trade_date" not in frame.columns or "close" not in frame.columns:
            return {}
        frame["trade_date"] = frame["trade_date"].astype(str)
        frame = cast(
            pd.DataFrame,
            frame.loc[frame["trade_date"] <= str(cutoff_date)].copy(),
        )
        frame = cast(
            pd.DataFrame,
            frame.set_index("trade_date").sort_index().reset_index(),
        )
        if len(frame) < 25:
            return {}
        close = _numeric_series(frame, "close").dropna()
        if len(close) < 25:
            return {}
        latest = float(close.iloc[-1])
        prev20 = float(close.iloc[-21]) if len(close) >= 21 else float(close.iloc[0])
        ma20 = float(close.iloc[-20:].mean())
        index_change_20d = 0.0 if prev20 == 0 else round((latest / prev20 - 1.0) * 100.0, 4)
        return {
            "index_change_20d": index_change_20d,
            "index_above_ma20": latest > ma20,
            "index_ma20_gap_pct": round((latest / ma20 - 1.0) * 100.0, 4) if ma20 else 0.0,
        }


class RegimeClassifier:
    def __init__(self, routing_policy: Optional[Dict[str, Any]] = None):
        self.routing_policy = dict(DEFAULT_ROUTING_POLICY)
        self.routing_policy.update(dict(routing_policy or {}))

    def classify(self, observation: MarketObservation, *, agent: Any = None, mode: str = "rule") -> Dict[str, Any]:
        rule_result = self._rule_based(observation.stats)
        if mode not in {"hybrid", "agent"} or agent is None:
            return {**rule_result, "rule_result": rule_result, "agent_result": {}}
        agent_result = agent.analyze(dict(observation.stats or {}))
        final = dict(rule_result)
        agent_confidence = float(agent_result.get("confidence", 0.0) or 0.0)
        if mode == "agent" and agent_result.get("regime"):
            final.update({
                "regime": str(agent_result.get("regime") or rule_result["regime"]),
                "confidence": max(rule_result["confidence"], agent_confidence),
                "reasoning": str(agent_result.get("reasoning") or rule_result["reasoning"]),
                "suggested_exposure": float(agent_result.get("suggested_exposure", rule_result["suggested_exposure"])),
                "source": str(agent_result.get("source") or "agent"),
            })
        elif mode == "hybrid" and agent_result.get("regime") == rule_result.get("regime"):
            final.update({
                "confidence": round(min(0.95, max(rule_result["confidence"], agent_confidence) + 0.05), 4),
                "reasoning": f"{rule_result['reasoning']} Agent 校验一致。",
                "source": "hybrid_consensus",
            })
        return {**final, "rule_result": rule_result, "agent_result": agent_result}

    def _rule_based(self, stats: Dict[str, Any]) -> Dict[str, Any]:
        avg_change_20d = float(stats.get("avg_change_20d", 0.0) or 0.0)
        above_ma20_ratio = float(stats.get("above_ma20_ratio", 0.5) or 0.5)
        avg_volatility = float(stats.get("avg_volatility", 0.0) or 0.0)
        market_breadth = float(stats.get("market_breadth", 0.5) or 0.5)
        index_change_20d = float(stats.get("index_change_20d", 0.0) or 0.0)
        bull = avg_change_20d >= float(self.routing_policy["bull_avg_change_20d"]) and above_ma20_ratio >= float(self.routing_policy["bull_above_ma20_ratio"]) and market_breadth >= float(self.routing_policy["strong_breadth_threshold"])
        bear = avg_change_20d <= float(self.routing_policy["bear_avg_change_20d"]) and above_ma20_ratio <= float(self.routing_policy["bear_above_ma20_ratio"]) and market_breadth <= float(self.routing_policy["weak_breadth_threshold"])
        regime = str(self.routing_policy.get("default_regime", "oscillation"))
        confidence = 0.58
        reasoning = "市场特征分布接近震荡区间，维持均衡模型选择。"
        if bull or index_change_20d >= float(self.routing_policy["index_bull_change_20d"]):
            regime = "bull"
            confidence = 0.72 + min(0.18, max(0.0, above_ma20_ratio - 0.55))
            reasoning = "市场涨幅、广度和均线占优，趋势延续特征更明显。"
        elif bear or index_change_20d <= float(self.routing_policy["index_bear_change_20d"]):
            regime = "bear"
            confidence = 0.74 + min(0.16, max(0.0, 0.45 - above_ma20_ratio))
            reasoning = "市场回撤、广度与均线结构偏弱，防御模型更合适。"
        elif avg_volatility >= float(self.routing_policy["high_volatility_threshold"]):
            regime = "bear"
            confidence = 0.63
            reasoning = "波动显著抬升，优先采用防御式模型降低回撤。"
        suggested_exposure = {"bull": 0.85, "oscillation": 0.55, "bear": 0.25}.get(regime, 0.5)
        return {
            "regime": regime,
            "confidence": round(max(0.0, min(0.95, confidence)), 4),
            "reasoning": reasoning,
            "suggested_exposure": suggested_exposure,
            "source": "rule",
        }


class ModelRoutingCoordinator:
    def __init__(
        self,
        *,
        routing_policy: Optional[Dict[str, Any]] = None,
        min_confidence: float = 0.6,
        cooldown_cycles: int = 2,
        hysteresis_margin: float = 0.08,
        agent_override_max_gap: float = 0.18,
    ):
        self.routing_policy = dict(DEFAULT_ROUTING_POLICY)
        self.routing_policy.update(dict(routing_policy or {}))
        self.observer = MarketObservationService(self.routing_policy)
        self.classifier = RegimeClassifier(self.routing_policy)
        self.min_confidence = float(min_confidence)
        self.cooldown_cycles = max(0, int(cooldown_cycles))
        self.hysteresis_margin = float(hysteresis_margin)
        self.agent_override_max_gap = float(agent_override_max_gap)

    @staticmethod
    def _lookup_leaderboard_entry(
        leaderboard: Dict[str, Any],
        model_name: str,
    ) -> Dict[str, Any]:
        for entry in list(leaderboard.get("entries") or []):
            if str(entry.get("model_name") or "") == model_name:
                return dict(entry)
        return {}

    def _evaluate_cooldown_exception(
        self,
        *,
        leaderboard_path: str | Path,
        current_model: str,
        candidate_model: str,
        current_weight: float,
        candidate_weight: float,
        decision_confidence: float,
    ) -> Dict[str, Any]:
        if candidate_model == current_model:
            return {"applied": False, "reason": "", "details": {}}
        try:
            leaderboard = load_leaderboard(leaderboard_path)
        except Exception:
            return {"applied": False, "reason": "", "details": {}}
        current_entry = self._lookup_leaderboard_entry(leaderboard, current_model)
        candidate_entry = self._lookup_leaderboard_entry(leaderboard, candidate_model)
        if not current_entry or not candidate_entry:
            return {"applied": False, "reason": "", "details": {}}

        weight_gap = round(candidate_weight - current_weight, 4)
        benchmark_gap = round(
            float(candidate_entry.get("benchmark_pass_rate", 0.0) or 0.0)
            - float(current_entry.get("benchmark_pass_rate", 0.0) or 0.0),
            4,
        )
        return_gap = round(
            float(candidate_entry.get("avg_return_pct", 0.0) or 0.0)
            - float(current_entry.get("avg_return_pct", 0.0) or 0.0),
            4,
        )
        current_benchmark = float(current_entry.get("benchmark_pass_rate", 0.0) or 0.0)
        current_return = float(current_entry.get("avg_return_pct", 0.0) or 0.0)

        current_is_weak = (
            current_benchmark
            <= float(self.routing_policy["cooldown_exception_current_benchmark_pass_rate_max"])
            or current_return
            <= float(self.routing_policy["cooldown_exception_current_avg_return_pct_max"])
        )
        candidate_is_strong = (
            decision_confidence
            >= float(self.routing_policy["cooldown_exception_min_confidence"])
            and weight_gap
            >= float(self.routing_policy["cooldown_exception_min_candidate_weight_gap"])
            and (
                benchmark_gap
                >= float(self.routing_policy["cooldown_exception_min_benchmark_gap"])
                or return_gap
                >= float(self.routing_policy["cooldown_exception_min_return_gap"])
            )
        )
        if not (current_is_weak and candidate_is_strong):
            return {
                "applied": False,
                "reason": "",
                "details": {
                    "current_benchmark_pass_rate": current_benchmark,
                    "candidate_benchmark_pass_rate": float(candidate_entry.get("benchmark_pass_rate", 0.0) or 0.0),
                    "benchmark_gap": benchmark_gap,
                    "current_avg_return_pct": current_return,
                    "candidate_avg_return_pct": float(candidate_entry.get("avg_return_pct", 0.0) or 0.0),
                    "return_gap": return_gap,
                    "weight_gap": weight_gap,
                    "decision_confidence": decision_confidence,
                },
            }
        return {
            "applied": True,
            "reason": "candidate_outperforms_weak_current",
            "details": {
                "current_benchmark_pass_rate": current_benchmark,
                "candidate_benchmark_pass_rate": float(candidate_entry.get("benchmark_pass_rate", 0.0) or 0.0),
                "benchmark_gap": benchmark_gap,
                "current_avg_return_pct": current_return,
                "candidate_avg_return_pct": float(candidate_entry.get("avg_return_pct", 0.0) or 0.0),
                "return_gap": return_gap,
                "weight_gap": weight_gap,
                "decision_confidence": decision_confidence,
            },
        }

    def route(
        self,
        *,
        stock_data: Dict[str, pd.DataFrame],
        cutoff_date: str,
        current_model: str,
        leaderboard_path: str | Path,
        allocator_top_n: int = 3,
        allowed_models: Optional[List[str]] = None,
        routing_mode: str = "rule",
        regime_agent: Any = None,
        selector_agent: Any = None,
        previous_decision: Optional[Dict[str, Any]] = None,
        current_cycle_id: Optional[int] = None,
        last_switch_cycle_id: Optional[int] = None,
        data_manager: Any = None,
    ) -> ModelRoutingDecision:
        allowed = [str(item).strip() for item in (allowed_models or DEFAULT_ROUTING_ALLOWED_MODELS) if str(item).strip()]
        if routing_mode == "off":
            selected_config = str(resolve_model_config_path(current_model))
            return ModelRoutingDecision(
                as_of_date=cutoff_date,
                current_model=current_model,
                selected_model=current_model,
                selected_config=selected_config,
                regime="unknown",
                regime_confidence=0.0,
                decision_confidence=0.0,
                candidate_models=[current_model],
                candidate_weights={current_model: 1.0},
                cash_reserve_hint=0.0,
                decision_source="disabled",
                regime_source="disabled",
                switch_applied=False,
                hold_current=True,
                hold_reason="model_routing_disabled",
                reasoning="模型路由已关闭，维持当前模型。",
                evidence={},
                agent_advice={},
                allocation_plan={},
                guardrail_checks=[],
                metadata={"routing_mode": routing_mode, "allowed_models": allowed},
            )
        if current_model not in allowed:
            allowed.insert(0, current_model)
        observation = self.observer.observe(stock_data, cutoff_date, data_manager=data_manager)
        regime_payload = self.classifier.classify(observation, agent=regime_agent, mode="hybrid" if routing_mode in {"hybrid", "agent"} else "rule")
        allocation = build_allocation_plan(
            str(regime_payload.get("regime") or "unknown"),
            leaderboard_path,
            as_of_date=cutoff_date,
            top_n=max(1, int(allocator_top_n)),
        )
        filtered_models = [name for name in allocation.active_models if name in allowed]
        filtered_weights = {name: weight for name, weight in allocation.model_weights.items() if name in allowed}
        no_qualified_candidates = not filtered_models
        if not filtered_weights and filtered_models:
            filtered_weights = {filtered_models[0]: 1.0}
        rule_selected = filtered_models[0] if filtered_models else current_model
        agent_advice: Dict[str, Any] = {}
        candidate_model = rule_selected
        decision_source = "rule_allocator"
        if not no_qualified_candidates and routing_mode in {"hybrid", "agent"} and selector_agent is not None:
            agent_advice = selector_agent.analyze({
                "regime": regime_payload.get("regime"),
                "current_model": current_model,
                "allowed_models": allowed,
                "candidate_models": filtered_models,
                "candidate_weights": filtered_weights,
                "market_stats": observation.stats,
            })
            advised = str(agent_advice.get("selected_model") or "").strip()
            if advised and advised in allowed:
                rule_weight = float(filtered_weights.get(rule_selected, 0.0) or 0.0)
                advised_weight = float(filtered_weights.get(advised, 0.0) or 0.0)
                gap = abs(rule_weight - advised_weight)
                if routing_mode == "agent" or gap <= self.agent_override_max_gap:
                    candidate_model = advised
                    decision_source = "hybrid_agent" if advised != rule_selected else "hybrid_consensus"
        current_weight = float(filtered_weights.get(current_model, 0.0) or 0.0)
        candidate_weight = float(filtered_weights.get(candidate_model, 0.0) or 0.0)
        hold_current = False
        hold_reason = ""
        guardrail_checks: List[Dict[str, Any]] = []
        regime_confidence = float(regime_payload.get("confidence", 0.0) or 0.0)
        decision_confidence = round(max(regime_confidence, float(allocation.confidence or 0.0)), 4)
        cooldown_exception = {"applied": False, "reason": "", "details": {}}
        if no_qualified_candidates:
            hold_current = True
            hold_reason = "no_qualified_routing_candidates"
            candidate_model = current_model
            current_weight = 1.0
            candidate_weight = 1.0
            filtered_models = [current_model]
            filtered_weights = {current_model: 1.0}
        elif candidate_model != current_model:
            min_style_compatibility = float(
                self.routing_policy.get("min_regime_style_compatibility", 0.40) or 0.40
            )
            candidate_style_compatibility = regime_compatibility(
                candidate_model,
                str(regime_payload.get("regime") or "unknown"),
            )
            current_style_compatibility = regime_compatibility(
                current_model,
                str(regime_payload.get("regime") or "unknown"),
            )
            if (
                candidate_style_compatibility < min_style_compatibility
                and current_style_compatibility >= candidate_style_compatibility
            ):
                hold_current = True
                hold_reason = "regime_style_mismatch"
            if decision_confidence < self.min_confidence:
                hold_current = True
                hold_reason = "routing_confidence_below_threshold"
            elif (candidate_weight - current_weight) < self.hysteresis_margin:
                hold_current = True
                hold_reason = "routing_hysteresis_hold"
            elif current_cycle_id is not None and last_switch_cycle_id is not None and (current_cycle_id - last_switch_cycle_id) <= self.cooldown_cycles:
                cooldown_exception = self._evaluate_cooldown_exception(
                    leaderboard_path=leaderboard_path,
                    current_model=current_model,
                    candidate_model=candidate_model,
                    current_weight=current_weight,
                    candidate_weight=candidate_weight,
                    decision_confidence=decision_confidence,
                )
                if cooldown_exception.get("applied"):
                    hold_current = False
                else:
                    hold_current = True
                    hold_reason = "routing_cooldown_active"
        guardrail_checks.append({"name": "qualified_candidates_available", "passed": not no_qualified_candidates, "actual": 0 if no_qualified_candidates else len(filtered_models), "threshold": 1})
        guardrail_checks.append({"name": "min_confidence", "passed": decision_confidence >= self.min_confidence, "actual": decision_confidence, "threshold": self.min_confidence})
        guardrail_checks.append({"name": "hysteresis_margin", "passed": (candidate_weight - current_weight) >= self.hysteresis_margin or candidate_model == current_model, "actual": round(candidate_weight - current_weight, 4), "threshold": self.hysteresis_margin})
        guardrail_checks.append(
            {
                "name": "regime_compatibility",
                "passed": regime_compatibility(candidate_model, str(regime_payload.get("regime") or "unknown"))
                >= float(self.routing_policy.get("min_regime_style_compatibility", 0.40) or 0.40)
                or candidate_model == current_model,
                "actual": regime_compatibility(candidate_model, str(regime_payload.get("regime") or "unknown")),
                "threshold": float(self.routing_policy.get("min_regime_style_compatibility", 0.40) or 0.40),
            }
        )
        if current_cycle_id is not None and last_switch_cycle_id is not None:
            cycles_since_switch = current_cycle_id - last_switch_cycle_id
            guardrail_checks.append({"name": "cooldown_cycles", "passed": cycles_since_switch > self.cooldown_cycles or candidate_model == current_model or bool(cooldown_exception.get("applied")), "actual": cycles_since_switch, "threshold": self.cooldown_cycles, "exception_applied": bool(cooldown_exception.get("applied"))})
        selected_model = current_model if hold_current else candidate_model
        selected_config = str(resolve_model_config_path(selected_model))
        reasoning = self._build_reasoning(
            current_model=current_model,
            selected_model=selected_model,
            regime_payload=regime_payload,
            allocation=allocation.to_dict(),
            agent_advice=agent_advice,
            hold_reason=hold_reason,
        )
        if cooldown_exception.get("applied"):
            reasoning = f"{reasoning} 满足 cooldown 切换例外，允许提前切换到 {selected_model}。".strip()
        metadata = {
            "routing_mode": routing_mode,
            "allowed_models": allowed,
            "observation_version": "routing_v1",
            "previous_decision": dict(previous_decision or {}),
            "cooldown_exception": cooldown_exception,
        }
        return ModelRoutingDecision(
            as_of_date=cutoff_date,
            current_model=current_model,
            selected_model=selected_model,
            selected_config=selected_config,
            regime=str(regime_payload.get("regime") or "unknown"),
            regime_confidence=regime_confidence,
            decision_confidence=decision_confidence,
            candidate_models=filtered_models,
            candidate_weights={name: round(float(weight), 4) for name, weight in filtered_weights.items()},
            cash_reserve_hint=float(allocation.cash_reserve or 0.0),
            decision_source=decision_source,
            regime_source=str(regime_payload.get("source") or "rule"),
            switch_applied=selected_model != current_model,
            hold_current=hold_current,
            hold_reason=hold_reason,
            reasoning=reasoning,
            evidence={
                "market_observation": observation.to_dict(),
                "rule_result": dict(regime_payload.get("rule_result") or {}),
                "agent_result": dict(regime_payload.get("agent_result") or {}),
                "allocator_quality": {
                    "qualified_candidate_count": int(allocation.metadata.get("qualified_candidate_count", 0) or 0),
                    "failed_quality_entries": list(allocation.metadata.get("failed_quality_entries") or []),
                    "top_candidates": list(allocation.metadata.get("top_candidates") or []),
                    "current_model_compatibility": regime_compatibility(
                        current_model,
                        str(regime_payload.get("regime") or "unknown"),
                    ),
                    "selected_model_compatibility": regime_compatibility(
                        selected_model,
                        str(regime_payload.get("regime") or "unknown"),
                    ),
                },
            },
            agent_advice=agent_advice,
            allocation_plan=allocation.to_dict(),
            guardrail_checks=guardrail_checks,
            metadata=metadata,
        )

    @staticmethod
    def _build_reasoning(
        *,
        current_model: str,
        selected_model: str,
        regime_payload: Dict[str, Any],
        allocation: Dict[str, Any],
        agent_advice: Dict[str, Any],
        hold_reason: str,
    ) -> str:
        regime = regime_payload.get("regime", "unknown")
        regime_reasoning = str(regime_payload.get("reasoning") or "")
        allocation_reasoning = str(allocation.get("reasoning") or "")
        if hold_reason:
            return f"检测到 regime={regime}，但因 {hold_reason} 继续持有 {current_model}。{regime_reasoning} {allocation_reasoning}".strip()
        if selected_model == current_model:
            return f"检测到 regime={regime}，维持当前模型 {current_model}。{regime_reasoning} {allocation_reasoning}".strip()
        agent_reason = str(agent_advice.get("reasoning") or "").strip()
        tail = f" Agent 建议：{agent_reason}" if agent_reason else ""
        return f"检测到 regime={regime}，从 {current_model} 切换至 {selected_model}。{regime_reasoning} {allocation_reasoning}{tail}".strip()
