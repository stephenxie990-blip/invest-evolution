from __future__ import annotations

from typing import Any, Dict, List

from invest.contracts import AgentContext, SignalPacket, StockSignal
from invest.foundation.compute.features import compute_market_stats, summarize_stock_batches
from invest.models.base import InvestmentModel
from invest.models.context_renderer import render_market_narrative


class MeanReversionModel(InvestmentModel):
    model_name = "mean_reversion"
    default_config_relpath = "configs/mean_reversion_v1.yaml"

    def _resolve_regime(self, market_stats: Dict[str, Any]) -> str:
        regime_hint = str(market_stats.get("regime_hint") or "oscillation")
        if regime_hint == "bear":
            return "bear"
        return "oscillation"

    def _risk_hints(self, market_stats: Dict[str, Any]) -> List[str]:
        hints: List[str] = []
        policy = self.config_section("market_hints", {}) or {}
        if market_stats.get("avg_volatility", 0.0) > float(policy.get("avg_volatility_gt", 0.035) or 0.035):
            hints.append("波动较高，抄底需分批建仓")
        if market_stats.get("market_breadth", 0.0) < float(policy.get("market_breadth_lt", 0.35) or 0.35):
            hints.append("市场普跌较广，反弹持续性存疑")
        if market_stats.get("avg_change_20d", 0.0) > float(policy.get("avg_change_20d_gt", 6.0) or 6.0):
            hints.append("市场已偏热，均值回归胜率可能下降")
        return hints

    def _reversion_score(self, item: Dict[str, Any]) -> float:
        oversold_rsi = float(self.param("oversold_rsi", 35.0))
        hot_rsi = float(self.param("rebound_rsi_cap", 60.0))
        max_5d_drop = float(self.param("max_5d_drop", -2.0))
        max_20d_drop = float(self.param("max_20d_drop", -5.0))
        scoring = self.scoring_section()
        weights = dict(scoring.get("weights", {}))
        bands = dict(scoring.get("bands", {}))
        penalties = dict(scoring.get("penalties", {}))

        rsi = float(item.get("rsi", 50.0))
        bb_pos = float(item.get("bb_pos", 0.5))
        change_5d = float(item.get("change_5d", 0.0))
        change_20d = float(item.get("change_20d", 0.0))
        vol_ratio = float(item.get("vol_ratio", 1.0))
        volatility = float(item.get("volatility", 0.0))
        ma_trend = str(item.get("ma_trend", "交叉"))

        lower_bb = float(bands.get("lower_bb_threshold", 0.35))
        upper_bb = float(bands.get("upper_bb_threshold", 0.8))
        vol_ratio_low = float(bands.get("vol_ratio_low", 0.8))
        vol_ratio_high = float(bands.get("vol_ratio_high", 1.8))
        high_volatility = float(bands.get("high_volatility_threshold", 0.05))

        score = 0.0
        score += max(0.0, oversold_rsi - rsi) / max(oversold_rsi, 1.0) * float(weights.get("oversold_rsi", 0.35))
        if bb_pos < lower_bb:
            score += max(0.0, lower_bb - bb_pos) / max(lower_bb, 1e-6) * float(weights.get("lower_bb", 0.20))
        else:
            score -= max(0.0, bb_pos - upper_bb) * float(penalties.get("upper_bb", 0.10))
        score += max(0.0, abs(min(change_5d, 0.0)) - abs(max_5d_drop)) / 10.0 * float(weights.get("drop_5d", 0.20)) if change_5d <= max_5d_drop else -float(penalties.get("insufficient_drop_5d", 0.05))
        score += max(0.0, abs(min(change_20d, 0.0)) - abs(max_20d_drop)) / 20.0 * float(weights.get("drop_20d", 0.15)) if change_20d <= max_20d_drop else -float(penalties.get("insufficient_drop_20d", 0.05))
        if ma_trend == "空头":
            score += float(weights.get("bearish_trend_bonus", 0.05))
        if vol_ratio_low <= vol_ratio <= vol_ratio_high:
            score += float(weights.get("volume_ratio_bonus", 0.08))
        if volatility > high_volatility:
            score -= float(penalties.get("high_volatility", 0.08))
        if rsi > hot_rsi:
            score -= float(penalties.get("overheat_rsi", 0.15))
        return round(score, 4)

    def build_signal_packet(self, stock_data: Dict[str, Any], cutoff_date: str) -> SignalPacket:
        params = self.effective_params()
        market_stats = compute_market_stats(stock_data, cutoff_date, regime_policy=self.config_section("market_regime", {}) or None)
        regime = self._resolve_regime(market_stats)
        stock_codes = list(stock_data.keys())[: int(self.param("candidate_pool_size"))]
        stock_batches = summarize_stock_batches(stock_data, stock_codes, cutoff_date, summary_scoring=self.config_section("summary_scoring", {}) or None)
        stock_summaries = [item.summary for item in stock_batches]
        scored: List[Dict[str, Any]] = []
        min_reversion_score = float(self.param("min_reversion_score", 0.05))
        for item in stock_summaries:
            score = self._reversion_score(item)
            if score >= min_reversion_score:
                enriched = dict(item)
                enriched["reversion_score"] = score
                scored.append(enriched)
        scored.sort(key=lambda item: (item.get("reversion_score", 0.0), -abs(item.get("change_5d", 0.0))), reverse=True)

        top_n = max(1, int(self.param("top_n")))
        max_positions = max(1, int(self.param("max_positions", min(4, top_n))))
        stop_loss = float(self.risk_param("stop_loss_pct"))
        take_profit = float(self.risk_param("take_profit_pct"))
        trailing_pct = self.risk_param("trailing_pct")
        selected = scored[:top_n] or stock_summaries[:top_n]

        signals = []
        for idx, item in enumerate(selected, start=1):
            evidence = [
                f"reversion_score={item.get('reversion_score', 0.0):.3f}",
                f"change_5d={item.get('change_5d', 0):+.2f}%",
                f"RSI={item.get('rsi', 50):.1f}",
                f"BB={item.get('bb_pos', 0.5):.2f}",
            ]
            signals.append(
                StockSignal(
                    code=item["code"],
                    score=float(item.get("reversion_score", item.get("algo_score", 0.0))),
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
                        "reversion_score": float(item.get("reversion_score", item.get("algo_score", 0.0))),
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
            cash_reserve=max(0.0, min(0.8, cash_reserve)),
            params=params,
            reasoning=f"MeanReversionModel 从 {len(stock_summaries)} 只候选中识别超跌反弹机会，当前 regime={regime}",
            metadata={"market_stats": market_stats, "stock_summaries": selected, "raw_summaries": stock_summaries},
        )

    def build_agent_context(self, stock_data: Dict[str, Any], cutoff_date: str, signal_packet: SignalPacket) -> AgentContext:
        market_stats = dict(signal_packet.metadata.get("market_stats", {}))
        stock_summaries = list(signal_packet.metadata.get("stock_summaries", []))
        risk_hints = self._risk_hints(market_stats)
        summary = render_market_narrative(signal_packet.regime, market_stats, risk_hints)
        if stock_summaries:
            candidate_lines = [
                f"{item['code']} 5日{item.get('change_5d', 0):+.1f}% / RSI {item.get('rsi', 50):.0f} / BB {item.get('bb_pos', 0.5):.2f} / 回归分 {item.get('reversion_score', 0):.2f}"
                for item in stock_summaries[:5]
            ]
            narrative = summary + " 候选重点：" + "；".join(candidate_lines)
        else:
            narrative = summary + " 当前没有满足超跌反弹条件的候选。"
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
