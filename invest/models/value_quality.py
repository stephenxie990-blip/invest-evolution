from __future__ import annotations

from typing import Any, Dict, List, cast

import pandas as pd

from invest.contracts import AgentContext, SignalPacket, StockSignal
from invest.foundation.compute.batch_snapshot import StockBatchSummary
from invest.foundation.compute.features import compute_market_stats, summarize_stock_batches
from invest.models.base import InvestmentModel
from invest.models.context_renderer import render_market_narrative
from invest.models.scorers import ValueQualityScorer


class ValueQualityModel(InvestmentModel):
    model_name = "value_quality"
    default_config_relpath = "configs/value_quality_v1.yaml"

    def _resolve_regime(self, market_stats: Dict[str, Any]) -> str:
        regime_hint = str(market_stats.get("regime_hint") or "oscillation")
        return regime_hint if regime_hint in {"bull", "bear", "oscillation"} else "oscillation"

    def _risk_hints(self, market_stats: Dict[str, Any]) -> List[str]:
        hints: List[str] = []
        policy = self.config_section("market_hints", {}) or {}
        if market_stats.get("avg_volatility", 0.0) > float(policy.get("avg_volatility_gt", 0.03) or 0.03):
            hints.append("估值修复类标的在高波动阶段需要更长持有周期")
        if market_stats.get("market_breadth", 0.0) < float(policy.get("market_breadth_lt", 0.40) or 0.40):
            hints.append("市场风险偏好不足，价值修复可能偏慢")
        return hints

    def _latest_numeric(self, df: pd.DataFrame, column: str, default: float = 0.0) -> float:
        if column not in df.columns:
            return default
        series = cast(pd.Series, pd.to_numeric(df[column], errors="coerce")).dropna()
        if series.empty:
            return default
        return float(series.iloc[-1])

    def _fundamental_snapshot(self, stock_data: Dict[str, Any], code: str) -> Dict[str, float]:
        df = stock_data.get(code)
        if df is None or df.empty:
            return {"pe_ttm": 0.0, "pb": 0.0, "roe": 0.0, "market_cap": 0.0}
        return {
            "pe_ttm": self._latest_numeric(df, "pe_ttm", self._latest_numeric(df, "pe", 0.0)),
            "pb": self._latest_numeric(df, "pb", 0.0),
            "roe": self._latest_numeric(df, "roe", 0.0),
            "market_cap": self._latest_numeric(df, "market_cap", 0.0),
        }

    def _value_score(self, item: StockBatchSummary | Dict[str, Any], fundamentals: Dict[str, float]) -> float:
        return ValueQualityScorer(self).score(item, fundamentals)

    def build_signal_packet(self, stock_data: Dict[str, Any], cutoff_date: str) -> SignalPacket:
        params = self.effective_params()
        market_stats = compute_market_stats(stock_data, cutoff_date, regime_policy=self.config_section("market_regime", {}) or None)
        regime = self._resolve_regime(market_stats)
        stock_codes = list(stock_data.keys())[: int(self.param("candidate_pool_size"))]
        stock_batches = summarize_stock_batches(stock_data, stock_codes, cutoff_date, summary_scoring=self.config_section("summary_scoring", {}) or None)
        stock_summaries = [item.summary for item in stock_batches]
        min_score = float(self.param("min_value_quality_score", 0.25))
        enriched_summaries: List[Dict[str, Any]] = []
        scorer = ValueQualityScorer(self)
        for item in stock_batches:
            fundamentals = self._fundamental_snapshot(stock_data, item.code)
            score = scorer.score(item, fundamentals)
            if score >= min_score:
                enriched = dict(item.summary)
                enriched.update(fundamentals)
                enriched["value_quality_score"] = score
                enriched_summaries.append(enriched)
        enriched_summaries.sort(
            key=lambda item: (
                item.get("value_quality_score", 0.0),
                item.get("roe", 0.0),
                -(item.get("pb", 999.0) or 999.0),
            ),
            reverse=True,
        )

        top_n = max(1, int(self.param("top_n")))
        max_positions = max(1, int(self.param("max_positions", min(4, top_n))))
        stop_loss = float(self.risk_param("stop_loss_pct"))
        take_profit = float(self.risk_param("take_profit_pct"))
        trailing_pct = self.risk_param("trailing_pct")
        selected = enriched_summaries[:top_n] or stock_summaries[:top_n]

        signals = []
        for idx, item in enumerate(selected, start=1):
            evidence = [
                f"value_quality_score={item.get('value_quality_score', 0.0):.3f}",
                f"PE={item.get('pe_ttm', 0.0):.1f}",
                f"PB={item.get('pb', 0.0):.2f}",
                f"ROE={item.get('roe', 0.0):.1f}%",
            ]
            signals.append(
                StockSignal(
                    code=item["code"],
                    score=float(item.get("value_quality_score", item.get("algo_score", 0.0))),
                    rank=idx,
                    weight_hint=round(1 / max(top_n, 1), 3),
                    stop_loss_pct=stop_loss,
                    take_profit_pct=take_profit,
                    trailing_pct=float(trailing_pct) if trailing_pct is not None else None,
                    factor_values={
                        "change_20d": float(item.get("change_20d", 0.0)),
                        "rsi": float(item.get("rsi", 50.0)),
                        "pb": float(item.get("pb", 0.0)),
                        "pe_ttm": float(item.get("pe_ttm", 0.0)),
                        "roe": float(item.get("roe", 0.0)),
                        "value_quality_score": float(item.get("value_quality_score", item.get("algo_score", 0.0))),
                    },
                    evidence=evidence,
                    metadata={"ma_trend": item.get("ma_trend"), "macd": item.get("macd"), "market_cap": item.get("market_cap", 0.0)},
                )
            )

        cash_reserve = float(self.param("cash_reserve"))
        return SignalPacket(
            as_of_date=cutoff_date,
            model_name=self.model_name,
            config_name=self.config.name,
            regime=regime,
            signals=signals,
            selected_codes=[item.code for item in signals[:max_positions]],
            max_positions=max_positions,
            cash_reserve=max(0.0, min(0.8, cash_reserve)),
            params=params,
            reasoning=f"ValueQualityModel 在 {len(stock_summaries)} 只候选中识别估值合理且质量较高的标的，当前 regime={regime}",
            metadata={"market_stats": market_stats, "stock_summaries": selected, "raw_summaries": stock_summaries},
        )

    def build_agent_context(self, stock_data: Dict[str, Any], cutoff_date: str, signal_packet: SignalPacket) -> AgentContext:
        market_stats = dict(signal_packet.metadata.get("market_stats", {}))
        stock_summaries = list(signal_packet.metadata.get("stock_summaries", []))
        risk_hints = self._risk_hints(market_stats)
        summary = render_market_narrative(signal_packet.regime, market_stats, risk_hints)
        if stock_summaries:
            candidate_lines = [
                f"{item['code']} 估值质量分 {item.get('value_quality_score', 0):.2f} / PE {item.get('pe_ttm', 0):.1f} / PB {item.get('pb', 0):.2f} / ROE {item.get('roe', 0):.1f}%"
                for item in stock_summaries[:5]
            ]
            narrative = summary + " 候选重点：" + "；".join(candidate_lines)
        else:
            narrative = summary + " 当前没有满足价值质量筛选条件的候选。"
        evidence = [item for signal in signal_packet.signals[:5] for item in signal.evidence[:3]]
        return AgentContext(
            as_of_date=cutoff_date,
            model_name=self.model_name,
            config_name=self.config.name,
            summary=summary,
            narrative=narrative,
            regime=signal_packet.regime,
            market_stats=market_stats,
            stock_summaries=stock_summaries,
            candidate_codes=signal_packet.top_codes(),
            risk_hints=risk_hints,
            evidence=evidence,
            metadata={"params": dict(signal_packet.params)},
        )
