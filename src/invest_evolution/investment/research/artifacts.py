from __future__ import annotations

# Research artifact recorder

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, cast

import pandas as pd

from invest_evolution.config import config
from invest_evolution.market_data.repository import MarketDataRepository
from invest_evolution.investment.contracts import ManagerAttribution, ManagerOutput
from .analysis import (
    DEFAULT_HORIZONS,
    OutcomeAttribution,
    ResearchHypothesis,
    ResearchSnapshot,
    stable_hash,
)

logger = logging.getLogger(__name__)


class TrainingArtifactRecorder:
    """
    训练工件持久化

    同时支持 JSON（机器读）和 Markdown（人读）
    目录结构：
        {base_dir}/
        ├── selection/
        │   ├── selection_0001.json
        │   ├── selection_0001.md
        │   └── ...
        ├── manager_review/
        │   ├── manager_review_0001.json
        │   ├── manager_review_0001.md
        │   └── ...
        └── allocation_review/
            ├── allocation_review_0001.json
            ├── allocation_review_0001.md
            └── ...
    """

    def __init__(self, base_dir: str | None = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            logs_dir = config.logs_dir or (
                config.output_dir / "logs"
                if config.output_dir is not None
                else Path("runtime/logs")
            )
            self.base_dir = logs_dir / "artifacts"

        self.selection_dir = self.base_dir / "selection"
        self.manager_review_dir = self.base_dir / "manager_review"
        self.allocation_review_dir = self.base_dir / "allocation_review"
        self.selection_dir.mkdir(parents=True, exist_ok=True)
        self.manager_review_dir.mkdir(parents=True, exist_ok=True)
        self.allocation_review_dir.mkdir(parents=True, exist_ok=True)

        self._selection_records: List[Dict] = []
        self._manager_review_records: List[Dict] = []
        self._allocation_review_records: List[Dict] = []

    def save_selection_artifact(self, selection_trace: dict, cycle: int) -> None:
        """保存 selection artifact。"""
        if not selection_trace or selection_trace.get("fallback"):
            return

        record = {
            "cycle": cycle,
            "timestamp": datetime.now().isoformat(),
            "type": "selection",
            **selection_trace,
        }
        self._selection_records.append(record)

        artifact_id = int(selection_trace.get("artifact_id") or cycle)
        self._write_json(self.selection_dir / f"selection_{artifact_id:04d}.json", record)
        self._write_text(
            self.selection_dir / f"selection_{artifact_id:04d}.md",
            self._selection_to_md(selection_trace, cycle),
        )
        logger.debug("Selection artifact saved: cycle=%s", cycle)

    def save_manager_review_artifact(self, manager_review_report: dict, cycle: int) -> None:
        record = {
            "cycle": cycle,
            "timestamp": datetime.now().isoformat(),
            "type": "manager_review",
            "report": dict(manager_review_report or {}),
        }
        self._manager_review_records.append(record)
        self._write_json(self.manager_review_dir / f"manager_review_{cycle:04d}.json", record)
        self._write_text(
            self.manager_review_dir / f"manager_review_{cycle:04d}.md",
            self._manager_review_to_md(record["report"], cycle),
        )
        logger.debug("Manager review artifact saved: cycle=%s", cycle)

    def save_allocation_review_artifact(self, allocation_review_report: dict, cycle: int) -> None:
        record = {
            "cycle": cycle,
            "timestamp": datetime.now().isoformat(),
            "type": "allocation_review",
            "report": dict(allocation_review_report or {}),
        }
        self._allocation_review_records.append(record)
        self._write_json(
            self.allocation_review_dir / f"allocation_review_{cycle:04d}.json",
            record,
        )
        self._write_text(
            self.allocation_review_dir / f"allocation_review_{cycle:04d}.md",
            self._allocation_review_to_md(record["report"], cycle),
        )
        logger.debug("Allocation review artifact saved: cycle=%s", cycle)

    def get_summary(self) -> Dict:
        return {
            "selection_artifacts": len(self._selection_records),
            "manager_review_artifacts": len(self._manager_review_records),
            "allocation_review_artifacts": len(self._allocation_review_records),
        }

    def _selection_to_md(self, log: dict, cycle: int) -> str:
        lines = [
            f"# Selection Artifact #{log.get('artifact_id', cycle)}",
            "",
            f"**训练周期**: #{cycle}",
            f"**截断日期**: {log.get('cutoff_date', '')}",
            f"**市场状态**: {log.get('regime', '')} (置信度{log.get('confidence', 0):.0%})",
            "",
            "## 最终选股",
        ]
        for code in log.get("selected", []):
            lines.append(f"- {code}")
        lines.append(f"\n**来源**: {log.get('source', '')}")
        selected_roster = list(log.get("selected_roster", []) or [])
        observability = dict(log.get("observability", {}) or {})
        if selected_roster:
            lines.append("\n## 主猎手编排")
            for item in selected_roster:
                lines.append(
                    f"- {item.get('name', 'unknown')} ({item.get('role', '')}) 成本 {float(item.get('cost', 0.0) or 0.0):.2f}"
                )
        budget = dict(observability.get("budget", {}) or {})
        llm = dict(observability.get("llm", {}) or {})
        timings = dict(observability.get("timings_ms", {}) or {})
        if budget or llm or timings:
            lines.append("\n## 可观测性")
            if budget:
                lines.append(
                    f"- 猎手预算: {budget.get('selected_hunters', 0)}/{budget.get('target_hunters', 0)}，已用 {budget.get('budget_used', 0)} / {budget.get('budget_limit', 0)}"
                )
            if llm:
                lines.append(
                    f"- LLM: mode={llm.get('mode', 'unknown')}，calls={llm.get('call_count', 0)}，tokens(in/out)={llm.get('input_tokens', 0)}/{llm.get('output_tokens', 0)}"
                )
            if timings:
                lines.append(
                    f"- 耗时(ms): total={timings.get('total', 0)}，agents={timings.get('agents', 0)}，debate={timings.get('debate', 0)}"
                )
        for hunter in log.get("hunters", []):
            picks = hunter.get("result", {}).get("picks", [])
            lines.append(f"\n### {hunter.get('name', 'unknown')}")
            for p in picks:
                lines.append(f"- {p.get('code', '')} 评分{p.get('score', 0):.2f}: {p.get('reasoning', '')}")
        return "\n".join(lines)

    def _manager_review_to_md(self, report: dict, cycle: int) -> str:
        lines = [
            f"# Manager Review Artifact (Cycle #{cycle})",
            "",
            f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            f"**裁决**: {report.get('verdict', '')}",
            f"**主经理**: {report.get('dominant_manager_id', '')}",
            "",
            "## Summary",
        ]
        summary = dict(report.get("summary", {}) or {})
        for key, value in summary.items():
            lines.append(f"- {key}: {value}")
        reports = list(report.get("reports", []) or [])
        if reports:
            lines.append("\n## Reports")
            for item in reports:
                manager_id = item.get("manager_id", "unknown")
                verdict = item.get("verdict", "")
                lines.append(f"- {manager_id}: {verdict}")
        return "\n".join(lines)

    def _allocation_review_to_md(self, report: dict, cycle: int) -> str:
        lines = [
            f"# Allocation Review Artifact (Cycle #{cycle})",
            "",
            f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"**裁决**: {report.get('verdict', '')}",
            f"**市场状态**: {report.get('regime', '')}",
            "",
            "## Active Managers",
        ]
        for manager_id in report.get("active_manager_ids", []) or []:
            lines.append(f"- {manager_id}")
        weights = dict(report.get("manager_budget_weights", {}) or {})
        if weights:
            lines.append("\n## Budget Weights")
            for manager_id, weight in weights.items():
                lines.append(f"- {manager_id}: {weight}")
        findings = list(report.get("findings", []) or [])
        if findings:
            lines.append("\n## Findings")
            for item in findings:
                lines.append(f"- {item}")
        return "\n".join(lines)

    def _write_json(self, path: Path, data: dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def _write_text(self, path: Path, content: str):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


# Research attribution engine


class AttributionService:
    """Builds manager-aware and portfolio-aware contribution summaries."""

    def build_manager_attributions(
        self,
        manager_plans: Any,
        *,
        manager_weights: Dict[str, float] | None = None,
    ) -> list[Any]:
        weight_map = {
            str(key): float(value)
            for key, value in dict(manager_weights or {}).items()
        }
        attributions: list[ManagerAttribution] = []
        for plan in list(manager_plans or []):
            gross_budget = weight_map.get(plan.manager_id, float(plan.budget_weight or 0.0))
            contributions = {
                position.code: round(float(position.target_weight) * gross_budget, 8)
                for position in list(plan.positions or [])
            }
            attributions.append(
                ManagerAttribution(
                    manager_id=plan.manager_id,
                    selected_codes=list(plan.selected_codes),
                    gross_budget_weight=gross_budget,
                    active_exposure=round(sum(contributions.values()), 8),
                    code_contributions=contributions,
                    evidence={"regime": plan.regime},
                )
            )
        return attributions

    def build_portfolio_attribution(self, portfolio_plan: Any) -> Dict[str, float]:
        return {
            position.code: round(float(position.target_weight or 0.0), 8)
            for position in list(portfolio_plan.positions or [])
        }


class ResearchAttributionEngine:
    def __init__(self, repository: MarketDataRepository):
        self.repository = repository

    def evaluate_case(self, case_record: Dict[str, Any]) -> OutcomeAttribution:
        snapshot = dict(case_record.get("snapshot") or {})
        hypothesis = dict(case_record.get("hypothesis") or {})
        security = dict(snapshot.get("security") or {})
        code = str(security.get("code") or snapshot.get("metadata", {}).get("query_code") or "")
        as_of_date = str(snapshot.get("as_of_date") or "")
        entry_rule = dict(hypothesis.get("entry_rule") or {})
        invalidation_rule = dict(hypothesis.get("invalidation_rule") or {})
        de_risk_rule = dict(hypothesis.get("de_risk_rule") or {})
        scenario = dict(hypothesis.get("scenario_distribution") or {})
        positive_prob = float(dict((scenario.get("horizons") or {}).get("T+20") or {}).get("positive_return_probability") or 0.5)
        price_frame = self.repository.get_stock(code)
        if price_frame.empty:
            return OutcomeAttribution(
                attribution_id=f"attribution_{stable_hash({'hypothesis_id': hypothesis.get('hypothesis_id', ''), 'code': code})[:16]}",
                hypothesis_id=str(hypothesis.get("hypothesis_id") or ""),
                thesis_result="timeout",
                horizon_results={},
                metadata={"reason": "price_frame_missing", "code": code},
            )
        frame = price_frame.copy()
        frame["trade_date"] = frame["trade_date"].astype(str)
        frame = frame.sort_values("trade_date")
        future = frame[frame["trade_date"] > as_of_date].reset_index(drop=True)
        base_frame = frame[frame["trade_date"] <= as_of_date]
        if base_frame.empty:
            return OutcomeAttribution(
                attribution_id=f"attribution_{stable_hash({'hypothesis_id': hypothesis.get('hypothesis_id', ''), 'code': code, 'as_of_date': as_of_date})[:16]}",
                hypothesis_id=str(hypothesis.get("hypothesis_id") or ""),
                thesis_result="timeout",
                horizon_results={},
                metadata={"reason": "as_of_date_not_found", "code": code, "as_of_date": as_of_date},
            )
        base_closes = cast(pd.Series, pd.to_numeric(base_frame["close"], errors="coerce"))
        base_close = float(base_closes.iloc[-1])
        entry_price = entry_rule.get("price")
        invalidation_price = invalidation_rule.get("price")
        de_risk_price = de_risk_rule.get("price")
        horizon_results: Dict[str, Dict[str, Any]] = {}
        labels = []
        for horizon in DEFAULT_HORIZONS:
            key = f"T+{horizon}"
            window = future.head(horizon).copy()
            if window.empty:
                horizon_results[key] = {
                    "label": "timeout",
                    "return_pct": None,
                    "excess_return_pct": None,
                    "entry_triggered": False,
                    "invalidation_triggered": False,
                    "de_risk_triggered": False,
                }
                labels.append("timeout")
                continue
            closes = cast(pd.Series, pd.to_numeric(window["close"], errors="coerce"))
            highs = (
                cast(pd.Series, pd.to_numeric(window["high"], errors="coerce"))
                if "high" in window.columns
                else closes
            )
            lows = (
                cast(pd.Series, pd.to_numeric(window["low"], errors="coerce"))
                if "low" in window.columns
                else closes
            )
            last_close = float(closes.iloc[-1])
            max_high = float(highs.max()) if not highs.empty else last_close
            min_low = float(lows.min()) if not lows.empty else last_close
            entry_triggered = True
            if entry_price not in (None, ""):
                entry_triggered = bool(min_low <= float(entry_price))
            invalidation_triggered = False if invalidation_price in (None, "") else bool(min_low <= float(invalidation_price))
            de_risk_triggered = False if de_risk_price in (None, "") else bool(max_high >= float(de_risk_price))
            effective_entry = float(entry_price) if entry_triggered and entry_price not in (None, "") else base_close
            return_pct = round((last_close / effective_entry - 1.0) * 100.0, 4) if effective_entry > 0 else None
            if not entry_triggered:
                label = "not_triggered"
            elif invalidation_triggered:
                label = "invalidated"
            elif return_pct is not None and return_pct > 0:
                label = "hit"
            else:
                label = "miss"
            horizon_results[key] = {
                "label": label,
                "return_pct": return_pct,
                "excess_return_pct": return_pct,
                "max_favorable_excursion": round((max_high / effective_entry - 1.0) * 100.0, 4) if effective_entry > 0 else None,
                "max_adverse_excursion": round((min_low / effective_entry - 1.0) * 100.0, 4) if effective_entry > 0 else None,
                "entry_triggered": entry_triggered,
                "invalidation_triggered": invalidation_triggered,
                "de_risk_triggered": de_risk_triggered,
                "end_trade_date": str(cast(pd.Series, window["trade_date"]).iloc[-1]),
            }
            labels.append(label)
        aggregate = "timeout"
        if any(label == "hit" for label in labels):
            aggregate = "hit"
        elif any(label == "invalidated" for label in labels):
            aggregate = "invalidated"
        elif any(label == "miss" for label in labels):
            aggregate = "miss"
        elif any(label == "not_triggered" for label in labels):
            aggregate = "not_triggered"
        y_true = 1.0 if aggregate == "hit" else 0.0
        brier = round((positive_prob - y_true) ** 2, 6)
        return OutcomeAttribution(
            attribution_id=f"attribution_{stable_hash({'hypothesis_id': hypothesis.get('hypothesis_id', ''), 'aggregate': aggregate, 'code': code})[:16]}",
            hypothesis_id=str(hypothesis.get("hypothesis_id") or ""),
            thesis_result=aggregate,
            horizon_results=horizon_results,
            factor_attribution={
                "supporting_factors": list(hypothesis.get("supporting_factors") or []),
                "contradicting_factors": list(hypothesis.get("contradicting_factors") or []),
            },
            timing_attribution={
                "entry_rule_kind": entry_rule.get("kind"),
                "entry_price": entry_price,
            },
            risk_attribution={
                "invalidation_rule_kind": invalidation_rule.get("kind"),
                "invalidation_price": invalidation_price,
                "de_risk_rule_kind": de_risk_rule.get("kind"),
                "de_risk_price": de_risk_price,
            },
            execution_attribution={
                "clock": list((hypothesis.get("evaluation_protocol") or {}).get("clock") or []),
            },
            calibration_metrics={
                "positive_return_brier": brier,
                "predicted_positive_return_probability": positive_prob,
                "actual_positive_return": y_true,
            },
            policy_update_candidates={
                "review_needed": aggregate in {"invalidated", "miss"},
                "selected_by_policy": bool(hypothesis.get("selected_by_policy")),
            },
            metadata={
                "code": code,
                "as_of_date": as_of_date,
            },
        )


# Research renderers


def build_dashboard_projection(
    *,
    hypothesis: ResearchHypothesis,
    matched_signals: list[str],
    core_rules: list[str],
    entry_conditions: list[str],
    supplemental_reason: str = "",
) -> Dict[str, Any]:
    reason_parts = []
    if supplemental_reason:
        reason_parts.append(str(supplemental_reason))
    if hypothesis.supporting_factors:
        reason_parts.append("支持因素: " + "、".join(hypothesis.supporting_factors[:4]))
    if hypothesis.contradicting_factors:
        reason_parts.append("风险因素: " + "、".join(hypothesis.contradicting_factors[:3]))
    return {
        "signal": hypothesis.stance,
        "score": float(hypothesis.score),
        "entry_price": dict(hypothesis.entry_rule or {}).get("price"),
        "stop_loss": dict(hypothesis.invalidation_rule or {}).get("price"),
        "reason": "；".join(part for part in reason_parts if part),
        "matched_signals": sorted(set(matched_signals or [])),
        "core_rules": list(core_rules or []),
        "entry_conditions": list(entry_conditions or []),
    }


# Research snapshot builder


_MODEL_SCORE_KEYS = {
    "momentum": ["algo_score"],
    "mean_reversion": ["reversion_score", "algo_score"],
    "value_quality": ["value_quality_score", "algo_score"],
    "defensive_low_vol": ["defensive_score", "algo_score"],
}
_DERIVED_SIGNAL_KEYS = {
    "flags",
    "matched_signals",
    "latest_close",
    "ma20",
    "rsi",
}


def _coerce_float(value: Any) -> float | None:
    try:
        if value in (None, "", "None"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_score(manager_id: str, item: Dict[str, Any]) -> float | None:
    for key in _MODEL_SCORE_KEYS.get(str(manager_id or "").strip().lower(), ["algo_score"]):
        value = _coerce_float(item.get(key))
        if value is not None:
            return value
    return _coerce_float(item.get("algo_score"))


def _normalize_universe(manager_id: str, stock_summaries: Iterable[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    ranked: list[Dict[str, Any]] = []
    for item in list(stock_summaries or []):
        row = dict(item or {})
        row["manager_relative_score"] = _pick_score(manager_id, row)
        ranked.append(row)
    ranked.sort(
        key=lambda row: (
            row.get("manager_relative_score") if row.get("manager_relative_score") is not None else float("-inf"),
            row.get("algo_score") if row.get("algo_score") is not None else float("-inf"),
        ),
        reverse=True,
    )
    return ranked


def _normalize_derived_signals(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in dict(payload or {}).items():
        if key in _DERIVED_SIGNAL_KEYS and value not in (None, "", [], {}):
            normalized[key] = value
    return normalized


def build_research_snapshot(
    *,
    manager_output: ManagerOutput,
    security: Dict[str, Any],
    query_code: str,
    stock_data: Dict[str, Any],
    governance_context: Dict[str, Any] | None = None,
    data_lineage: Dict[str, Any] | None = None,
    derived_signals: Dict[str, Any] | None = None,
) -> ResearchSnapshot:
    resolved_governance_context = dict(governance_context or {})
    signal_packet = manager_output.signal_packet
    packet_context = signal_packet.context
    manager_id = str(
        getattr(signal_packet, "manager_id", "")
        or getattr(manager_output, "manager_id", "")
        or "unknown"
    )
    manager_config_ref = str(
        getattr(signal_packet, "manager_config_ref", "")
        or getattr(manager_output, "manager_config_ref", "")
        or "unknown"
    )
    universe_rows = _normalize_universe(
        manager_id,
        packet_context.raw_summaries or packet_context.stock_summaries or [],
    )
    selected_map = {item.code: item.to_dict() for item in list(signal_packet.signals or [])}
    summary = next((dict(item) for item in universe_rows if str(item.get("code") or "") == query_code), None)
    selected_signal = dict(selected_map.get(query_code) or {})
    universe_size = len(universe_rows)
    rank = None
    percentile = None
    threshold_score = None
    threshold_gap = None
    approximate = False
    if universe_rows:
        for idx, item in enumerate(universe_rows, start=1):
            if str(item.get("code") or "") == query_code:
                rank = idx
                percentile = round((universe_size - idx + 1) / max(universe_size, 1), 4)
                break
    selected_scores = [float(item.get("score", 0.0) or 0.0) for item in list(selected_map.values())]
    if selected_scores:
        threshold_score = min(selected_scores)
    if summary is not None and threshold_score is not None:
        manager_score = _coerce_float(summary.get("manager_relative_score"))
        if manager_score is None:
            manager_score = _coerce_float(summary.get("algo_score"))
            approximate = True
        if manager_score is not None:
            threshold_gap = round(manager_score - threshold_score, 4)
    market_context = {
        "regime": str(signal_packet.regime or resolved_governance_context.get("regime") or "unknown"),
        "cash_reserve": float(signal_packet.cash_reserve or 0.0),
        "manager_id": manager_id,
        "manager_config_ref": manager_config_ref,
        "market_stats": dict(packet_context.market_stats or {}),
        "governance_context": resolved_governance_context,
    }
    cross_section_context = {
        "selected_by_policy": query_code in set(signal_packet.selected_codes or []),
        "rank": rank,
        "percentile": percentile,
        "threshold_score": threshold_score,
        "threshold_gap": threshold_gap,
        "threshold_gap_is_approximate": approximate,
        "selected_count": len(signal_packet.selected_codes or []),
        "universe_size": universe_size,
        "top_selected_codes": list(signal_packet.selected_codes or []),
    }
    factor_values = dict(selected_signal.get("factor_values") or {})
    signal_metadata = dict(selected_signal.get("metadata") or {})
    derived_payload = _normalize_derived_signals(derived_signals)
    derived_flags = dict(derived_payload.get("flags") or {})
    if derived_flags and "flags" not in signal_metadata:
        signal_metadata["flags"] = derived_flags
    if derived_payload.get("matched_signals") and "matched_signals" not in signal_metadata:
        signal_metadata["matched_signals"] = list(derived_payload.get("matched_signals") or [])
    if derived_payload.get("latest_close") is not None and "latest_close" not in signal_metadata:
        signal_metadata["latest_close"] = derived_payload.get("latest_close")
    if derived_payload.get("ma20") is not None and factor_values.get("ma20") is None:
        factor_values["ma20"] = derived_payload.get("ma20")
    if derived_payload.get("rsi") is not None and factor_values.get("rsi") is None:
        factor_values["rsi"] = derived_payload.get("rsi")
    feature_snapshot = {
        "summary": dict(summary or {}),
        "signal": selected_signal,
        "evidence": list(selected_signal.get("evidence") or []),
        "factor_values": factor_values,
        "metadata": signal_metadata,
    }
    universe = {
        "size": universe_size,
        "available_codes": sorted(list(stock_data.keys())),
        "summary_top5": [dict(item) for item in universe_rows[:5]],
    }
    payload = {
        "as_of_date": signal_packet.as_of_date,
        "scope": "single_security",
        "query_code": query_code,
        "market_context": market_context,
        "cross_section_context": cross_section_context,
        "feature_snapshot": feature_snapshot,
    }
    snapshot_hash = stable_hash(payload)
    readiness = {
        "has_manager_output": True,
        "has_universe_summary": bool(universe_rows),
        "has_selected_signal": bool(selected_signal),
        "has_query_summary": summary is not None,
    }
    return ResearchSnapshot(
        snapshot_id=f"snapshot_{snapshot_hash[:16]}",
        as_of_date=str(signal_packet.as_of_date),
        scope="single_security",
        security=dict(security or {}),
        universe=universe,
        market_context=market_context,
        cross_section_context=cross_section_context,
        feature_snapshot=feature_snapshot,
        data_lineage=dict(data_lineage or {}),
        readiness=readiness,
        metadata={
            "query_code": query_code,
            "manager_reasoning": str(signal_packet.reasoning or ""),
            "agent_context_summary": str(getattr(manager_output.agent_context, "summary", "") or ""),
        },
    )

__all__ = [name for name in globals() if not name.startswith('_')]
