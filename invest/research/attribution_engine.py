from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from market_data.repository import MarketDataRepository
from .contracts import DEFAULT_HORIZONS, OutcomeAttribution, stable_hash


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
        base_close = float(pd.to_numeric(base_frame["close"], errors="coerce").iloc[-1])
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
            closes = pd.to_numeric(window["close"], errors="coerce")
            highs = pd.to_numeric(window.get("high"), errors="coerce") if "high" in window.columns else closes
            lows = pd.to_numeric(window.get("low"), errors="coerce") if "low" in window.columns else closes
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
                "end_trade_date": str(window["trade_date"].iloc[-1]),
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
