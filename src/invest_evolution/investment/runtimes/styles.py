from __future__ import annotations

from typing import Any, Dict, List, cast

import pandas as pd

from invest_evolution.investment.contracts import AgentContext, SignalPacket, SignalPacketContext, StockSignal
from invest_evolution.investment.foundation.compute import (
    StockBatchSummary,
    compute_market_stats,
    summarize_stock_batches,
)
from .base import ManagerRuntime
from .catalog import render_candidate_narrative, render_market_narrative
from .ops import DefensiveLowVolScorer, MeanReversionScorer, MomentumScorer, ValueQualityScorer

# Momentum runtime


class MomentumRuntime(ManagerRuntime):
    runtime_id = "momentum"
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
        signal_threshold = float(self.param("signal_threshold", 0.0))
        stop_loss = float(self.risk_param("stop_loss_pct"))
        take_profit = float(self.risk_param("take_profit_pct"))
        trailing_pct = self.risk_param("trailing_pct")
        selected = [
            item
            for item in sorted(
                stock_batches,
                key=lambda batch: (
                    float(batch.summary.get("algo_score", 0.0) or 0.0),
                    float(batch.summary.get("change_20d", 0.0) or 0.0),
                ),
                reverse=True,
            )
            if float(item.summary.get("algo_score", 0.0) or 0.0) >= signal_threshold
        ][:top_n]
        scorer = MomentumScorer()
        signals = []
        for idx, item in enumerate(selected, start=1):
            signals.append(scorer.build_signal(item, idx=idx, top_n=top_n, stop_loss=stop_loss, take_profit=take_profit, trailing_pct=trailing_pct))

        cash_reserve = float(self.param("cash_reserve"))
        return SignalPacket(
            as_of_date=cutoff_date,
            manager_id=self.runtime_id,
            manager_config_ref=self.config.name,
            regime=regime,
            signals=signals,
            selected_codes=[item.code for item in signals[:max_positions]],
            max_positions=max_positions,
            cash_reserve=max(0.0, min(0.7, cash_reserve)),
            params=params,
            reasoning=(
                f"MomentumRuntime 根据 {len(stock_summaries)} 只候选提取动量信号，"
                f"阈值 algo_score>={signal_threshold:.2f}，当前 regime={regime}"
            ),
            context=SignalPacketContext(
                market_stats=market_stats,
                stock_summaries=self.build_stock_summary_views(item.summary for item in selected),
                raw_summaries=stock_summaries,
            ),
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
            manager_id=self.runtime_id,
            manager_config_ref=self.config.name,
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


# Mean reversion runtime

class MeanReversionRuntime(ManagerRuntime):
    runtime_id = "mean_reversion"
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

    def _reversion_score(self, item: StockBatchSummary | Dict[str, Any]) -> float:
        return MeanReversionScorer(self).score(item)

    def build_signal_packet(self, stock_data: Dict[str, Any], cutoff_date: str) -> SignalPacket:
        params = self.effective_params()
        market_stats = compute_market_stats(stock_data, cutoff_date, regime_policy=self.config_section("market_regime", {}) or None)
        regime = self._resolve_regime(market_stats)
        stock_codes = list(stock_data.keys())[: int(self.param("candidate_pool_size"))]
        stock_batches = summarize_stock_batches(stock_data, stock_codes, cutoff_date, summary_scoring=self.config_section("summary_scoring", {}) or None)
        stock_summaries = self.build_stock_summary_views(item.summary for item in stock_batches)
        scored: List[Dict[str, Any]] = []
        min_reversion_score = float(self.param("min_reversion_score", 0.05))
        scorer = MeanReversionScorer(self)
        for item in stock_batches:
            score = scorer.score(item)
            if score >= min_reversion_score:
                enriched = dict(item.summary)
                enriched["reversion_score"] = score
                scored.append(enriched)
        scored.sort(key=lambda item: (item.get("reversion_score", 0.0), -abs(item.get("change_5d", 0.0))), reverse=True)

        top_n = max(1, int(self.param("top_n")))
        max_positions = max(1, int(self.param("max_positions", min(4, top_n))))
        stop_loss = float(self.risk_param("stop_loss_pct"))
        take_profit = float(self.risk_param("take_profit_pct"))
        trailing_pct = self.risk_param("trailing_pct")
        selected = self.build_stock_summary_views(scored[:top_n])

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
            manager_id=self.runtime_id,
            manager_config_ref=self.config.name,
            regime=regime,
            signals=signals,
            selected_codes=[item.code for item in signals[:max_positions]],
            max_positions=max_positions,
            cash_reserve=max(0.0, min(0.8, cash_reserve)),
            params=params,
            reasoning=f"MeanReversionRuntime 从 {len(stock_summaries)} 只候选中识别超跌反弹机会，当前 regime={regime}",
            context=SignalPacketContext(
                market_stats=market_stats,
                stock_summaries=selected,
                raw_summaries=stock_summaries,
            ),
        )

    def build_agent_context(self, stock_data: Dict[str, Any], cutoff_date: str, signal_packet: SignalPacket) -> AgentContext:
        market_stats = dict(signal_packet.context.market_stats)
        stock_summaries = list(signal_packet.context.stock_summaries)
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
            manager_id=self.runtime_id,
            manager_config_ref=self.config.name,
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


# Defensive runtime

class DefensiveLowVolRuntime(ManagerRuntime):
    runtime_id = "defensive_low_vol"
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

    def _defensive_score(self, item: StockBatchSummary | Dict[str, Any]) -> float:
        return DefensiveLowVolScorer(self).score(item)

    def build_signal_packet(self, stock_data: Dict[str, Any], cutoff_date: str) -> SignalPacket:
        params = self.effective_params()
        market_stats = compute_market_stats(stock_data, cutoff_date, regime_policy=self.config_section("market_regime", {}) or None)
        regime = self._resolve_regime(market_stats)
        stock_codes = list(stock_data.keys())[: int(self.param("candidate_pool_size"))]
        stock_batches = summarize_stock_batches(stock_data, stock_codes, cutoff_date, summary_scoring=self.config_section("summary_scoring", {}) or None)
        stock_summaries = self.build_stock_summary_views(item.summary for item in stock_batches)
        min_score = float(self.param("min_defensive_score", 0.15))
        selected_pool: List[Dict[str, Any]] = []
        scorer = DefensiveLowVolScorer(self)
        for item in stock_batches:
            score = scorer.score(item)
            if score >= min_score:
                enriched = dict(item.summary)
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
        selected = self.build_stock_summary_views(selected_pool[:top_n])

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
            manager_id=self.runtime_id,
            manager_config_ref=self.config.name,
            regime=regime,
            signals=signals,
            selected_codes=[item.code for item in signals[:max_positions]],
            max_positions=max_positions,
            cash_reserve=max(0.0, min(0.85, cash_reserve)),
            params=params,
            reasoning=f"DefensiveLowVolRuntime 从 {len(stock_summaries)} 只候选中筛选低波防御标的，当前 regime={regime}",
            context=SignalPacketContext(
                market_stats=market_stats,
                stock_summaries=selected,
                raw_summaries=stock_summaries,
            ),
        )

    def build_agent_context(self, stock_data: Dict[str, Any], cutoff_date: str, signal_packet: SignalPacket) -> AgentContext:
        market_stats = dict(signal_packet.context.market_stats)
        stock_summaries = list(signal_packet.context.stock_summaries)
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
            manager_id=self.runtime_id,
            manager_config_ref=self.config.name,
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


# Value quality runtime

class ValueQualityRuntime(ManagerRuntime):
    runtime_id = "value_quality"
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
        stock_summaries = self.build_stock_summary_views(item.summary for item in stock_batches)
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
        selected = self.build_stock_summary_views(enriched_summaries[:top_n])

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
            manager_id=self.runtime_id,
            manager_config_ref=self.config.name,
            regime=regime,
            signals=signals,
            selected_codes=[item.code for item in signals[:max_positions]],
            max_positions=max_positions,
            cash_reserve=max(0.0, min(0.8, cash_reserve)),
            params=params,
            reasoning=f"ValueQualityRuntime 在 {len(stock_summaries)} 只候选中识别估值合理且质量较高的标的，当前 regime={regime}",
            context=SignalPacketContext(
                market_stats=market_stats,
                stock_summaries=selected,
                raw_summaries=stock_summaries,
            ),
        )

    def build_agent_context(self, stock_data: Dict[str, Any], cutoff_date: str, signal_packet: SignalPacket) -> AgentContext:
        market_stats = dict(signal_packet.context.market_stats)
        stock_summaries = list(signal_packet.context.stock_summaries)
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
            manager_id=self.runtime_id,
            manager_config_ref=self.config.name,
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

__all__ = ['MomentumRuntime', 'MeanReversionRuntime', 'DefensiveLowVolRuntime', 'ValueQualityRuntime']
