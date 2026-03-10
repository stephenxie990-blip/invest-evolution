from __future__ import annotations

from typing import Any, Dict, List

from invest.contracts import AgentContext, SignalPacket, StockSignal
from invest.foundation.compute import compute_market_stats, summarize_stocks
from invest.models.base import InvestmentModel
from invest.models.context_renderer import render_market_narrative


class DefensiveLowVolModel(InvestmentModel):
    model_name = "defensive_low_vol"
    default_config_relpath = "configs/defensive_low_vol_v1.yaml"

    def _resolve_regime(self, market_stats: Dict[str, Any]) -> str:
        regime_hint = str(market_stats.get("regime_hint") or "oscillation")
        policy = self.config_section("market_hints", {}) or {}
        if regime_hint == "bull" and market_stats.get("avg_volatility", 0.0) < float(policy.get("bull_low_vol_oscillation_lt", 0.02) or 0.02):
            return "oscillation"
        return regime_hint if regime_hint in {"bull", "bear", "oscillation"} else "oscillation"

    def _risk_hints(self, market_stats: Dict[str, Any]) -> List[str]:
        hints: List[str] = []
        policy = self.config_section("market_hints", {}) or {}
        if market_stats.get("avg_volatility", 0.0) > float(policy.get("avg_volatility_gt", 0.03) or 0.03):
            hints.append("市场波动偏高，优先低波与防御性配置")
        if market_stats.get("above_ma20_ratio", 0.0) < float(policy.get("above_ma20_ratio_lt", 0.45) or 0.45):
            hints.append("强势股占比不足，避免高弹性追涨")
        if market_stats.get("market_breadth", 0.0) < float(policy.get("market_breadth_lt", 0.40) or 0.40):
            hints.append("市场广度偏弱，保留更高现金储备")
        return hints

    def _defensive_score(self, item: Dict[str, Any]) -> float:
        volatility = float(item.get("volatility", 0.0))
        rsi = float(item.get("rsi", 50.0))
        change_20d = float(item.get("change_20d", 0.0))
        change_5d = float(item.get("change_5d", 0.0))
        bb_pos = float(item.get("bb_pos", 0.5))
        vol_ratio = float(item.get("vol_ratio", 1.0))
        ma_trend = str(item.get("ma_trend", "交叉"))
        scoring = self.scoring_section()
        weights = dict(scoring.get("weights", {}))
        bands = dict(scoring.get("bands", {}))
        penalties = dict(scoring.get("penalties", {}))

        max_volatility = float(self.param("max_volatility", 0.035))
        preferred_rsi_low = float(self.param("preferred_rsi_low", 42.0))
        preferred_rsi_high = float(self.param("preferred_rsi_high", 62.0))
        min_change_20d = float(self.param("min_change_20d", -2.0))
        max_change_20d = float(self.param("max_change_20d", 12.0))
        rsi_soft_low = float(bands.get("rsi_soft_low", 35.0))
        rsi_soft_high = float(bands.get("rsi_soft_high", 70.0))
        change_5d_low = float(bands.get("change_5d_low", -2.0))
        change_5d_high = float(bands.get("change_5d_high", 4.0))
        bb_pos_low = float(bands.get("bb_pos_low", 0.35))
        bb_pos_high = float(bands.get("bb_pos_high", 0.75))
        vol_ratio_low = float(bands.get("vol_ratio_low", 0.8))
        vol_ratio_high = float(bands.get("vol_ratio_high", 1.5))

        score = 0.0
        score += max(0.0, (max_volatility - min(volatility, max_volatility)) / max_volatility) * float(weights.get("low_volatility", 0.35))
        if preferred_rsi_low <= rsi <= preferred_rsi_high:
            score += float(weights.get("preferred_rsi", 0.20))
        elif rsi_soft_low <= rsi < preferred_rsi_low or preferred_rsi_high < rsi <= rsi_soft_high:
            score += float(weights.get("soft_rsi", 0.08))
        else:
            score -= float(penalties.get("bad_rsi", 0.08))
        if min_change_20d <= change_20d <= max_change_20d:
            score += float(weights.get("change_20d_band", 0.15))
        elif change_20d < min_change_20d:
            score -= float(penalties.get("weak_change_20d", 0.08))
        if change_5d_low <= change_5d <= change_5d_high:
            score += float(weights.get("change_5d_band", 0.10))
        if ma_trend == "多头":
            score += float(weights.get("bullish_trend", 0.12))
        elif ma_trend == "空头":
            score -= float(penalties.get("bearish_trend", 0.08))
        if bb_pos_low <= bb_pos <= bb_pos_high:
            score += float(weights.get("bb_band", 0.05))
        if vol_ratio_low <= vol_ratio <= vol_ratio_high:
            score += float(weights.get("volume_ratio_band", 0.03))
        return round(score, 4)

    def build_signal_packet(self, stock_data: Dict[str, Any], cutoff_date: str) -> SignalPacket:
        params = self.effective_params()
        market_stats = compute_market_stats(stock_data, cutoff_date, regime_policy=self.config_section("market_regime", {}) or None)
        regime = self._resolve_regime(market_stats)
        stock_codes = list(stock_data.keys())[: int(self.param("candidate_pool_size"))]
        stock_summaries = summarize_stocks(stock_data, stock_codes, cutoff_date, summary_scoring=self.config_section("summary_scoring", {}) or None)
        min_score = float(self.param("min_defensive_score", 0.15))
        selected_pool: List[Dict[str, Any]] = []
        for item in stock_summaries:
            score = self._defensive_score(item)
            if score >= min_score:
                enriched = dict(item)
                enriched["defensive_score"] = score
                selected_pool.append(enriched)
        selected_pool.sort(
            key=lambda item: (item.get("defensive_score", 0.0), -item.get("volatility", 0.0), item.get("change_20d", 0.0)),
            reverse=True,
        )

        top_n = max(1, int(self.param("top_n")))
        max_positions = max(1, int(self.param("max_positions", min(4, top_n))))
        stop_loss = float(self.risk_param("stop_loss_pct"))
        take_profit = float(self.risk_param("take_profit_pct"))
        trailing_pct = self.risk_param("trailing_pct")
        selected = selected_pool[:top_n] or stock_summaries[:top_n]

        signals = []
        for idx, item in enumerate(selected, start=1):
            evidence = [
                f"defensive_score={item.get('defensive_score', 0.0):.3f}",
                f"volatility={item.get('volatility', 0.0):.4f}",
                f"RSI={item.get('rsi', 50.0):.1f}",
                f"change_20d={item.get('change_20d', 0.0):+.2f}%",
            ]
            signals.append(
                StockSignal(
                    code=item["code"],
                    score=float(item.get("defensive_score", item.get("algo_score", 0.0))),
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
                        "volatility": float(item.get("volatility", 0.0)),
                        "defensive_score": float(item.get("defensive_score", item.get("algo_score", 0.0))),
                    },
                    evidence=evidence,
                    metadata={"ma_trend": item.get("ma_trend"), "macd": item.get("macd")},
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
            cash_reserve=max(0.0, min(0.85, cash_reserve)),
            params=params,
            reasoning=f"DefensiveLowVolModel 从 {len(stock_summaries)} 只候选中筛选低波防御标的，当前 regime={regime}",
            metadata={"market_stats": market_stats, "stock_summaries": selected, "raw_summaries": stock_summaries},
        )

    def build_agent_context(self, stock_data: Dict[str, Any], cutoff_date: str, signal_packet: SignalPacket) -> AgentContext:
        market_stats = dict(signal_packet.metadata.get("market_stats", {}))
        stock_summaries = list(signal_packet.metadata.get("stock_summaries", []))
        risk_hints = self._risk_hints(market_stats)
        summary = render_market_narrative(signal_packet.regime, market_stats, risk_hints)
        if stock_summaries:
            candidate_lines = [
                f"{item['code']} 防御分 {item.get('defensive_score', 0):.2f} / 波动 {item.get('volatility', 0):.4f} / RSI {item.get('rsi', 50):.0f} / 20日 {item.get('change_20d', 0):+.1f}%"
                for item in stock_summaries[:5]
            ]
            narrative = summary + " 候选重点：" + "；".join(candidate_lines)
        else:
            narrative = summary + " 当前没有满足低波防御条件的候选。"
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
