from __future__ import annotations

from typing import Any, Dict, List

from invest.contracts import AgentContext, SignalPacket, StockSignal
from invest.foundation.compute import compute_market_stats, summarize_stocks
from invest.models.base import InvestmentModel
from invest.models.context_renderer import render_candidate_narrative, render_market_narrative


class MomentumModel(InvestmentModel):
    model_name = "momentum"
    default_config_relpath = "configs/momentum_v1.yaml"

    def _resolve_regime(self, market_stats: Dict[str, Any]) -> str:
        regime_hint = str(market_stats.get("regime_hint") or "oscillation")
        return regime_hint if regime_hint in {"bull", "bear", "oscillation"} else "oscillation"

    def _risk_hints(self, market_stats: Dict[str, Any]) -> List[str]:
        hints: List[str] = []
        if market_stats.get("avg_volatility", 0.0) > 0.03:
            hints.append("短期波动偏高，需控制仓位")
        if market_stats.get("market_breadth", 0.0) < 0.45:
            hints.append("市场广度偏弱，追高风险较大")
        if market_stats.get("above_ma20_ratio", 0.0) < 0.4:
            hints.append("强势股占比不高，注意趋势延续性")
        return hints

    def build_signal_packet(self, stock_data: Dict[str, Any], cutoff_date: str) -> SignalPacket:
        params = self.effective_params()
        market_stats = compute_market_stats(stock_data, cutoff_date)
        regime = self._resolve_regime(market_stats)
        stock_codes = list(stock_data.keys())[: int(params.get("candidate_pool_size", 100))]
        stock_summaries = summarize_stocks(stock_data, stock_codes, cutoff_date)
        top_n = max(1, int(params.get("top_n", 5)))
        max_positions = max(1, int(params.get("max_positions", min(5, top_n))))
        risk = dict(self.config.data.get("risk", {}))
        stop_loss = float(params.get("stop_loss_pct", risk.get("stop_loss_pct", 0.05)))
        take_profit = float(params.get("take_profit_pct", risk.get("take_profit_pct", 0.15)))
        trailing_pct = params.get("trailing_pct", risk.get("trailing_pct", 0.10))
        selected = stock_summaries[:top_n]
        signals = []
        for idx, item in enumerate(selected, start=1):
            evidence = [
                f"algo_score={item.get('algo_score', 0):.3f}",
                f"change_20d={item.get('change_20d', 0):+.2f}%",
                f"MACD={item.get('macd', '中性')}",
            ]
            signals.append(
                StockSignal(
                    code=item["code"],
                    score=float(item.get("algo_score", 0.0)),
                    rank=idx,
                    weight_hint=round(1 / max(top_n, 1), 3),
                    stop_loss_pct=stop_loss,
                    take_profit_pct=take_profit,
                    trailing_pct=float(trailing_pct) if trailing_pct is not None else None,
                    factor_values={
                        "change_5d": float(item.get("change_5d", 0.0)),
                        "change_20d": float(item.get("change_20d", 0.0)),
                        "rsi": float(item.get("rsi", 50.0)),
                        "bb_pos": float(item.get("bb_pos", 0.5)),
                        "vol_ratio": float(item.get("vol_ratio", 1.0)),
                    },
                    evidence=evidence,
                    metadata={"ma_trend": item.get("ma_trend"), "macd": item.get("macd")},
                )
            )

        cash_reserve = float(params.get("cash_reserve", self.config.data.get("cash_reserve", 0.2)))
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
            metadata={"market_stats": market_stats, "stock_summaries": stock_summaries},
        )

    def build_agent_context(self, stock_data: Dict[str, Any], cutoff_date: str, signal_packet: SignalPacket) -> AgentContext:
        market_stats = dict(signal_packet.metadata.get("market_stats", {}))
        stock_summaries = list(signal_packet.metadata.get("stock_summaries", []))
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
            market_stats=market_stats,
            stock_summaries=stock_summaries,
            candidate_codes=signal_packet.top_codes(),
            risk_hints=risk_hints,
            evidence=evidence,
            metadata={"params": dict(signal_packet.params)},
        )
