from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from config import normalize_date
from invest.foundation.compute.batch_snapshot import build_batch_indicator_snapshot, build_batch_summary
from invest.research import ResearchHypothesis, build_dashboard_projection, build_research_hypothesis


class BatchAnalysisViewService:
    def __init__(self, *, humanize_macd_cross: Callable[[str], str]):
        self._humanize_macd_cross = humanize_macd_cross

    @staticmethod
    def empty_snapshot() -> dict[str, Any]:
        return {
            "samples": 0,
            "latest_trade_date": None,
            "latest_close": None,
            "indicators": {},
            "ready": False,
        }

    def build_batch_analysis_context(self, frame: pd.DataFrame, code: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        cutoff = normalize_date(str(frame["trade_date"].max()))
        batch = build_batch_indicator_snapshot(frame, cutoff)
        summary = build_batch_summary(frame, code, cutoff) or {}
        snapshot = dict(batch.streaming_snapshot) if batch is not None else self.empty_snapshot()
        return summary, snapshot, {"cutoff": cutoff, "batch": batch}

    def view_from_snapshot(self, summary: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        indicators = dict(snapshot.get("indicators") or {})
        macd = dict(indicators.get("macd_12_26_9") or {})
        boll = dict(indicators.get("bollinger_20") or {})
        latest = float(snapshot.get("latest_close") or 0.0)
        ma5 = float(indicators.get("sma_5") or latest or 0.0)
        ma10 = float(indicators.get("sma_10") or latest or 0.0)
        ma20 = float(indicators.get("sma_20") or latest or 0.0)
        ma60 = float(indicators.get("sma_60") or ma20 or 0.0)
        volume_ratio = indicators.get("volume_ratio_5_20")
        rsi = float(indicators.get("rsi_14") or summary.get("rsi") or 50.0)
        ma_stack = str(indicators.get("ma_stack") or "mixed")
        macd_cross = str(macd.get("cross") or "neutral")
        signal = "observe"
        if ma_stack == "bullish" and macd_cross in {"golden_cross", "bullish"}:
            signal = "bullish"
        elif ma_stack == "bearish" and macd_cross in {"dead_cross", "bearish"}:
            signal = "bearish"
        structure = "range"
        if latest > ma20 and ma20 >= ma60:
            structure = "uptrend"
        elif latest < ma20 and ma20 <= ma60:
            structure = "downtrend"
        summary_view = dict(summary)
        summary_view.update({
            "close": round(latest, 2) if latest else summary_view.get("close"),
            "rsi": round(rsi, 1),
            "macd": self._humanize_macd_cross(macd_cross),
            "ma_trend": "多头" if ma_stack == "bullish" else "空头" if ma_stack == "bearish" else "交叉",
            "bb_pos": boll.get("position", summary_view.get("bb_pos", 0.5)),
            "vol_ratio": volume_ratio if volume_ratio is not None else summary_view.get("vol_ratio"),
        })
        trend_view = {
            "latest_close": round(latest, 2) if latest else None,
            "ma5": round(ma5, 2) if ma5 else None,
            "ma10": round(ma10, 2) if ma10 else None,
            "ma20": round(ma20, 2) if ma20 else None,
            "ma60": round(ma60, 2) if ma60 else None,
            "volume_ratio": round(float(volume_ratio), 3) if volume_ratio is not None else None,
            "macd_cross": macd_cross,
            "rsi_14": round(rsi, 2),
            "bollinger_position": boll.get("position"),
            "atr_14": indicators.get("atr_14"),
        }
        return {
            "summary": summary_view,
            "trend": trend_view,
            "signal": signal,
            "structure": structure,
            "indicators": indicators,
            "macd": macd,
            "boll": boll,
        }


class ResearchResolutionService:
    def __init__(self, *, case_store: Any, scenario_engine: Any, attribution_engine: Any, logger: Any):
        self.case_store = case_store
        self.scenario_engine = scenario_engine
        self.attribution_engine = attribution_engine
        self._logger = logger

    @staticmethod
    def normalize_as_of_date(value: str | None = None) -> str:
        raw = str(value or "").strip()
        return normalize_date(raw) if raw else ""

    def build_research_payload_bases(
        self,
        *,
        research_bridge: dict[str, Any],
        requested_as_of_date: str,
        effective_as_of_date: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        status = str(research_bridge.get("status") or "unavailable")
        base_payload = {
            "status": status,
            "requested_as_of_date": self.normalize_as_of_date(requested_as_of_date),
            "as_of_date": effective_as_of_date,
        }
        return dict(base_payload), dict(base_payload)

    def persist_research_case_artifacts(
        self,
        *,
        snapshot: Any,
        policy: Any,
        hypothesis: Any,
        question: str,
        query: str,
        strategy: Any,
        strategy_source: str,
        execution_mode: str,
        code: str,
        effective_as_of_date: str,
    ) -> dict[str, Any]:
        case_record = None
        attribution_preview = None
        attribution_record = None
        calibration_report = None
        research_case_id = ""
        attribution_id = ""
        try:
            case_record = self.case_store.save_case(
                snapshot=snapshot,
                policy=policy,
                hypothesis=hypothesis,
                metadata={
                    "question": question,
                    "query": query,
                    "strategy": strategy.name,
                    "strategy_source": strategy_source,
                    "execution_mode": execution_mode,
                },
            )
            research_case_id = str(case_record.get("research_case_id") or "")
            attribution = self.attribution_engine.evaluate_case(case_record)
            attribution_preview = attribution.to_dict()
            has_scored_horizon = any(
                str((result or {}).get("label") or "") != "timeout"
                for result in dict(attribution.horizon_results or {}).values()
            )
            if has_scored_horizon:
                attribution_record = self.case_store.save_attribution(
                    attribution,
                    metadata={
                        "policy_id": policy.policy_id,
                        "research_case_id": research_case_id,
                        "code": code,
                        "as_of_date": effective_as_of_date,
                    },
                )
                attribution_id = str(attribution_record.get("attribution_id") or "")
                calibration_report = self.case_store.write_calibration_report(policy_id=policy.policy_id)
        except Exception:
            self._logger.warning("Failed to persist/evaluate research case for %s", code, exc_info=True)
        return {
            "case": dict(case_record or {}),
            "research_case_id": research_case_id,
            "attribution": {
                "saved": bool(attribution_record),
                "record": dict(attribution_record or {}),
                "preview": dict(attribution_preview or {}),
            },
            "attribution_id": attribution_id,
            "calibration_report": dict(calibration_report or {}),
        }

    @staticmethod
    def estimate_preliminary_stance(snapshot: Any) -> str:
        cross = dict(getattr(snapshot, "cross_section_context", {}) or {})
        percentile = cross.get("percentile")
        percentile_f = float(percentile or 0.0) if percentile is not None else 0.0
        selected_by_policy = bool(cross.get("selected_by_policy"))
        raw_score = 50.0 + percentile_f * 40.0 + (8.0 if selected_by_policy else 0.0)
        if raw_score >= 82:
            return "候选买入"
        if raw_score >= 68:
            return "偏强关注"
        if raw_score <= 35:
            return "减仓/回避"
        if raw_score <= 45:
            return "偏弱回避"
        return "持有观察"

    def build_canonical_fallback_projection(
        self,
        *,
        strategy: Any,
        derived: dict[str, Any],
        execution: dict[str, Any],
        dashboard_projection_builder: Callable[..., dict[str, Any]] = build_dashboard_projection,
    ) -> dict[str, Any]:
        score = 50.0
        reason_parts: list[str] = []
        flags = dict(derived.get("flags") or {})
        matched_signals = list(derived.get("matched_signals") or [])
        for label, delta in strategy.scoring.items():
            if flags.get(label):
                score += float(delta)
                reason_parts.append(f"{label}{'+' if delta >= 0 else ''}{delta:g}")
        algo_score = float(derived.get("algo_score") or 0.0)
        score += max(-10.0, min(10.0, algo_score * 2.0))
        if algo_score:
            reason_parts.append(f"algo_score 调整 {max(-10.0, min(10.0, algo_score * 2.0)):+.1f}")
        final_reasoning = str(execution.get("final_reasoning") or "").strip()
        if final_reasoning:
            reason_parts.append(f"分析摘要: {final_reasoning[:120]}")
        stance = "持有观察"
        if score >= 82:
            stance = "候选买入"
        elif score >= 70:
            stance = "偏强关注"
        elif score <= 35:
            stance = "减仓/回避"
        elif score <= 45:
            stance = "偏弱回避"
        latest_price = float(derived.get("latest_close") or 0.0)
        entry_price = round(latest_price * 0.99, 2) if latest_price and stance in {"候选买入", "偏强关注"} else None
        stop_loss = round(latest_price * 0.94, 2) if latest_price else None
        contradicting_factors = [
            label
            for label in ("空头排列", "MACD死叉", "RSI超买", "趋势向下", "结构走弱", "逼近阻力", "跌破MA20")
            if flags.get(label)
        ]
        fallback_hypothesis = ResearchHypothesis(
            hypothesis_id="hypothesis_dashboard_fallback",
            snapshot_id="snapshot_dashboard_fallback",
            policy_id="policy_dashboard_fallback",
            stance=stance,
            score=round(max(0.0, min(100.0, score)), 1),
            entry_rule={"kind": "limit_pullback" if entry_price is not None else "observe_only", "price": entry_price, "source": strategy.display_name},
            invalidation_rule={"kind": "stop_loss", "price": stop_loss, "source": strategy.name},
            supporting_factors=matched_signals,
            contradicting_factors=contradicting_factors,
            metadata={"source": "dashboard_fallback", "strategy_name": strategy.name},
        )
        return dashboard_projection_builder(
            hypothesis=fallback_hypothesis,
            matched_signals=matched_signals,
            core_rules=list(strategy.core_rules),
            entry_conditions=list(strategy.entry_conditions),
            supplemental_reason="；".join(reason_parts),
        )

    def resolve_outputs(
        self,
        *,
        research_bridge: dict[str, Any],
        question: str,
        query: str,
        strategy: Any,
        strategy_source: str,
        code: str,
        requested_as_of_date: str,
        effective_as_of_date: str,
        execution: dict[str, Any],
        derived: dict[str, Any],
        dashboard_projection_builder: Callable[..., dict[str, Any]] = build_dashboard_projection,
    ) -> dict[str, Any]:
        research_payload, model_bridge_payload = self.build_research_payload_bases(
            research_bridge=research_bridge,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
        )
        if research_bridge.get("status") != "ok":
            fallback_details = {
                "error": str(research_bridge.get("error") or ""),
                "fallback": "canonical_dashboard_fallback",
                "details": dict(research_bridge.get("details") or {}),
            }
            research_payload.update(fallback_details)
            model_bridge_payload.update(fallback_details)
            return {
                "dashboard": self.build_canonical_fallback_projection(
                    strategy=strategy,
                    derived=derived,
                    execution=execution,
                    dashboard_projection_builder=dashboard_projection_builder,
                ),
                "research": research_payload,
                "model_bridge": model_bridge_payload,
                "policy_id": "",
                "research_case_id": "",
                "attribution_id": "",
            }

        snapshot = research_bridge["snapshot"]
        policy = research_bridge["policy"]
        preliminary_stance = self.estimate_preliminary_stance(snapshot)
        scenario = self.scenario_engine.estimate(
            snapshot=snapshot,
            policy=policy,
            stance=preliminary_stance,
        )
        hypothesis = build_research_hypothesis(
            snapshot=snapshot,
            policy=policy,
            scenario=scenario,
            strategy_name=strategy.name,
            strategy_display_name=strategy.display_name,
        )
        persistence = self.persist_research_case_artifacts(
            snapshot=snapshot,
            policy=policy,
            hypothesis=hypothesis,
            question=question,
            query=query,
            strategy=strategy,
            strategy_source=strategy_source,
            execution_mode=str(execution.get("mode") or ""),
            code=code,
            effective_as_of_date=effective_as_of_date,
        )
        policy_id = str(policy.policy_id or "")
        research_case_id = str(persistence.get("research_case_id") or "")
        attribution_id = str(persistence.get("attribution_id") or "")
        model_bridge_payload.update(
            {
                "status": "ok",
                "controller_bound": bool(research_bridge.get("controller_bound")),
                "replay_mode": bool(research_bridge.get("replay_mode")),
                "parameter_source": str(research_bridge.get("parameter_source") or ""),
                "routing_decision": dict(research_bridge.get("routing_decision") or {}),
                "model_output": research_bridge["model_output"].to_dict(),
                "policy_id": policy_id,
                "research_case_id": research_case_id,
                "attribution_id": attribution_id,
            }
        )
        research_payload.update(
            {
                "status": "ok",
                "snapshot": snapshot.to_dict(),
                "policy": policy.to_dict(),
                "hypothesis": hypothesis.to_dict(),
                "scenario": dict(scenario or {}),
                "case": dict(persistence.get("case") or {}),
                "attribution": dict(persistence.get("attribution") or {}),
                "calibration_report": dict(persistence.get("calibration_report") or {}),
            }
        )
        return {
            "dashboard": dashboard_projection_builder(
                hypothesis=hypothesis,
                matched_signals=list(derived.get("matched_signals") or []),
                core_rules=list(strategy.core_rules),
                entry_conditions=list(strategy.entry_conditions),
            ),
            "research": research_payload,
            "model_bridge": model_bridge_payload,
            "policy_id": policy_id,
            "research_case_id": research_case_id,
            "attribution_id": attribution_id,
        }


__all__ = ["BatchAnalysisViewService", "ResearchResolutionService"]
