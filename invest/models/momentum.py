from __future__ import annotations

from typing import Any, Dict, List

from invest.contracts import AgentContext, SignalPacket, SignalPacketContext
from invest.foundation.compute.features import compute_market_stats, summarize_stock_batches
from invest.models.base import InvestmentModel
from invest.models.context_renderer import render_candidate_narrative, render_market_narrative
from invest.models.scorers import MomentumScorer


class MomentumModel(InvestmentModel):
    model_name = "momentum"
    default_config_relpath = "configs/momentum_v1.yaml"

    def _resolve_regime(self, market_stats: Dict[str, Any]) -> str:
        regime_hint = str(market_stats.get("regime_hint") or "oscillation")
        return regime_hint if regime_hint in {"bull", "bear", "oscillation"} else "oscillation"

    def _risk_hints(self, market_stats: Dict[str, Any]) -> List[str]:
        hints: List[str] = []
        policy = self.config_section("market_hints", {}) or {}
        if market_stats.get("avg_volatility", 0.0) > float(policy.get("avg_volatility_gt", 0.03) or 0.03):
            hints.append("短期波动偏高，需控制仓位")
        if market_stats.get("market_breadth", 0.0) < float(policy.get("market_breadth_lt", 0.45) or 0.45):
            hints.append("市场广度偏弱，追高风险较大")
        if market_stats.get("above_ma20_ratio", 0.0) < float(policy.get("above_ma20_ratio_lt", 0.40) or 0.40):
            hints.append("强势股占比不高，注意趋势延续性")
        return hints

    def build_signal_packet(self, stock_data: Dict[str, Any], cutoff_date: str) -> SignalPacket:
        params = self.effective_params()
        market_stats = compute_market_stats(stock_data, cutoff_date, regime_policy=self.config_section("market_regime", {}) or None)
        regime = self._resolve_regime(market_stats)
        stock_codes = list(stock_data.keys())[: int(self.param("candidate_pool_size"))]
        stock_batches = summarize_stock_batches(stock_data, stock_codes, cutoff_date, summary_scoring=self.config_section("summary_scoring", {}) or None)
        stock_summaries = self.build_stock_summary_views(item.summary for item in stock_batches)
        top_n = max(1, int(self.param("top_n")))
        max_positions = max(1, int(self.param("max_positions", min(5, top_n))))
        stop_loss = float(self.risk_param("stop_loss_pct"))
        take_profit = float(self.risk_param("take_profit_pct"))
        trailing_pct = self.risk_param("trailing_pct")
        selected = stock_batches[:top_n]
        scorer = MomentumScorer()
        signals = []
        for idx, item in enumerate(selected, start=1):
            signals.append(scorer.build_signal(item, idx=idx, top_n=top_n, stop_loss=stop_loss, take_profit=take_profit, trailing_pct=trailing_pct))

        cash_reserve = float(self.param("cash_reserve"))
        return SignalPacket(
            as_of_date=cutoff_date,
            model_name=self.model_name,
            config_name=self.config.name,
            regime=regime,
            signals=signals,
            selected_codes=[item.code for item in signals[:max_positions]],
            max_positions=max_positions,
            cash_reserve=max(0.0, min(0.7, cash_reserve)),
            params=params,
            reasoning=f"MomentumModel 根据 {len(stock_summaries)} 只候选提取动量信号，当前 regime={regime}",
            context=SignalPacketContext(
                market_stats=market_stats,
                stock_summaries=self.build_stock_summary_views(item.summary for item in selected),
                raw_summaries=stock_summaries,
            ),
            metadata={
                "entry_threshold_policy": {
                    "mode": "model_managed",
                    "key": "signal_threshold",
                    "consumed_upstream": False,
                    "post_selection_filter_supported": False,
                }
            },
        )

    def build_agent_context(self, stock_data: Dict[str, Any], cutoff_date: str, signal_packet: SignalPacket) -> AgentContext:
        market_stats = dict(signal_packet.context.market_stats)
        stock_summaries = list(signal_packet.context.stock_summaries)
        risk_hints = self._risk_hints(market_stats)
        summary = render_market_narrative(signal_packet.regime, market_stats, risk_hints)
        narrative = summary + " " + render_candidate_narrative(stock_summaries, signal_packet.top_codes(limit=signal_packet.max_positions))
        evidence = [item for signal in signal_packet.signals[:5] for item in signal.evidence[:2]]
        return AgentContext(
            as_of_date=cutoff_date,
            model_name=self.model_name,
            config_name=self.config.name,
            summary=summary,
            narrative=narrative,
            regime=signal_packet.regime,
            confidence=self.estimate_context_confidence(signal_packet),
            market_stats=market_stats,
            stock_summaries=stock_summaries,
            candidate_codes=signal_packet.top_codes(),
            risk_hints=risk_hints,
            evidence=evidence,
            metadata={"params": dict(signal_packet.params)},
        )
