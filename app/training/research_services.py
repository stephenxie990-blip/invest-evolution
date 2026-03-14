from __future__ import annotations

import logging
from typing import Any

from config import config
from invest.research import (
    build_research_hypothesis,
    build_research_snapshot,
    resolve_policy_snapshot,
)

logger = logging.getLogger(__name__)


class TrainingResearchService:
    """Persists training-cycle evidence into the shared research case store."""

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

    @staticmethod
    def _security_payload(controller: Any, code: str, stock_data: dict[str, Any]) -> dict[str, Any]:
        repository = getattr(controller, "research_market_repository", None)
        if repository is not None:
            try:
                matches = repository.query_securities([code])
            except Exception:
                logger.debug("research security lookup failed for %s", code, exc_info=True)
                matches = []
            if matches:
                return dict(matches[0] or {})

        frame = stock_data.get(code)
        name = ""
        if hasattr(frame, "empty") and not frame.empty and "name" in getattr(frame, "columns", []):
            try:
                name = str(frame.iloc[-1].get("name") or "")
            except Exception:
                name = ""
        return {"code": code, "name": name, "industry": "", "source": "training_cycle"}

    @staticmethod
    def _has_scored_horizon(attribution_payload: dict[str, Any]) -> bool:
        horizon_results = dict(attribution_payload.get("horizon_results") or {})
        return any(
            str(dict(item or {}).get("label") or "") != "timeout"
            for item in horizon_results.values()
        )

    def persist_cycle_research_artifacts(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        model_output: Any | None,
        stock_data: dict[str, Any],
        selected: list[str],
        regime_result: dict[str, Any] | None = None,
        selection_mode: str = "",
    ) -> dict[str, Any]:
        if model_output is None or not selected:
            return {
                "saved_case_count": 0,
                "saved_attribution_count": 0,
                "case_ids": [],
                "attribution_ids": [],
                "policy_id": "",
            }

        case_store = getattr(controller, "research_case_store", None)
        scenario_engine = getattr(controller, "research_scenario_engine", None)
        attribution_engine = getattr(controller, "research_attribution_engine", None)
        repository = getattr(controller, "research_market_repository", None)
        if case_store is None or scenario_engine is None or attribution_engine is None:
            return {
                "saved_case_count": 0,
                "saved_attribution_count": 0,
                "case_ids": [],
                "attribution_ids": [],
                "policy_id": "",
                "skipped_reason": "research_runtime_unavailable",
            }

        routing_context = dict(getattr(controller, "last_routing_decision", {}) or {})
        if not routing_context:
            routing_context = dict(regime_result or {})

        policy = resolve_policy_snapshot(
            investment_model=controller.investment_model,
            routing_context=routing_context,
            data_window={
                "as_of_date": str(cutoff_date or ""),
                "lookback_days": int(
                    controller.experiment_min_history_days
                    or getattr(config, "min_history_days", 750)
                    or 750
                ),
                "simulation_days": int(
                    controller.experiment_simulation_days
                    or getattr(config, "simulation_days", 30)
                    or 30
                ),
                "universe_definition": (
                    f"stock_count={len(stock_data)}|selection_mode={selection_mode or 'unknown'}"
                ),
                "stock_universe_size": len(stock_data),
            },
            metadata={
                "source": "training_cycle",
                "cycle_id": int(cycle_id),
                "cutoff_date": str(cutoff_date or ""),
                "selection_mode": str(selection_mode or ""),
            },
        )

        data_lineage = {
            "db_path": str(getattr(repository, "db_path", "") or ""),
            "effective_as_of_date": str(cutoff_date or ""),
            "data_source": "training_cycle",
            "stock_count": len(stock_data),
        }
        case_ids: list[str] = []
        attribution_ids: list[str] = []
        attributed_codes: list[str] = []

        for code in [str(item).strip() for item in list(selected or []) if str(item).strip()]:
            try:
                security = self._security_payload(controller, code, stock_data)
                snapshot = build_research_snapshot(
                    model_output=model_output,
                    security=security,
                    query_code=code,
                    stock_data=stock_data,
                    routing_context=routing_context,
                    data_lineage=data_lineage,
                )
                scenario = scenario_engine.estimate(
                    snapshot=snapshot,
                    policy=policy,
                    stance=self.estimate_preliminary_stance(snapshot),
                )
                hypothesis = build_research_hypothesis(
                    snapshot=snapshot,
                    policy=policy,
                    scenario=scenario,
                    strategy_name="training_cycle",
                    strategy_display_name="Training Cycle",
                )
                case_record = case_store.save_case(
                    snapshot=snapshot,
                    policy=policy,
                    hypothesis=hypothesis,
                    metadata={
                        "source": "training_cycle",
                        "cycle_id": int(cycle_id),
                        "cutoff_date": str(cutoff_date or ""),
                        "selection_mode": str(selection_mode or ""),
                        "code": code,
                    },
                )
                case_ids.append(str(case_record.get("research_case_id") or ""))
                attribution = attribution_engine.evaluate_case(case_record)
                attribution_payload = attribution.to_dict()
                if self._has_scored_horizon(attribution_payload):
                    attribution_record = case_store.save_attribution(
                        attribution,
                        metadata={
                            "source": "training_cycle",
                            "cycle_id": int(cycle_id),
                            "cutoff_date": str(cutoff_date or ""),
                            "policy_id": policy.policy_id,
                            "research_case_id": str(case_record.get("research_case_id") or ""),
                            "code": code,
                            "regime": str(routing_context.get("regime") or ""),
                        },
                    )
                    attribution_ids.append(str(attribution_record.get("attribution_id") or ""))
                    attributed_codes.append(code)
            except Exception:
                logger.warning(
                    "failed to persist training research artifact for cycle=%s code=%s",
                    cycle_id,
                    code,
                    exc_info=True,
                )

        calibration_report = {}
        if attribution_ids:
            try:
                calibration_report = case_store.write_calibration_report(policy_id=policy.policy_id)
            except Exception:
                logger.debug("failed to write training calibration report", exc_info=True)

        return {
            "policy_id": str(policy.policy_id or ""),
            "saved_case_count": len(case_ids),
            "saved_attribution_count": len(attribution_ids),
            "case_ids": case_ids,
            "attribution_ids": attribution_ids,
            "attributed_codes": attributed_codes,
            "selected_count": len(selected),
            "requested_regime": str(routing_context.get("regime") or ""),
            "calibration_report_path": str(calibration_report.get("path") or ""),
        }
