from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, cast

import pandas as pd

from invest.contracts import AgentContext, SignalPacket, SignalPacketContext, StockSignal
from invest.foundation.compute.batch_snapshot import StockBatchSummary
from invest.foundation.compute.features import compute_market_stats, summarize_stock_batches
from invest.models.base import InvestmentModel
from invest.models.context_renderer import render_market_narrative
from invest.models.scorers import ValueQualityScorer


class ValueQualityModel(InvestmentModel):
    model_name = "value_quality"
    default_config_relpath = "configs/value_quality_v1.yaml"

    @staticmethod
    def _code_bucket(code: str) -> str:
        normalized = str(code or "").strip().lower()
        if normalized.startswith("sh.688"):
            return "sh.688"
        if normalized.startswith("sz.300"):
            return "sz.300"
        if normalized.startswith("bj."):
            return "bj"
        return "main_board"

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
            return {
                "pe_ttm": 0.0,
                "pb": 0.0,
                "roe": 0.0,
                "market_cap": 0.0,
                "relative_strength_hs300": 0.0,
                "breakout20": 0.0,
            }
        return {
            "pe_ttm": self._latest_numeric(df, "pe_ttm", self._latest_numeric(df, "pe", 0.0)),
            "pb": self._latest_numeric(df, "pb", 0.0),
            "roe": self._latest_numeric(df, "roe", 0.0),
            "market_cap": self._latest_numeric(df, "market_cap", 0.0),
            "relative_strength_hs300": self._latest_numeric(df, "relative_strength_hs300", 0.0),
            "breakout20": self._latest_numeric(df, "breakout20", 0.0),
        }

    def _value_score(self, item: StockBatchSummary | Dict[str, Any], fundamentals: Dict[str, float]) -> float:
        return ValueQualityScorer(self).score(item, fundamentals)

    def _regime_adjusted_score(
        self,
        item: StockBatchSummary | Dict[str, Any],
        *,
        base_score: float,
        regime: str,
        fundamentals: Dict[str, float] | None = None,
    ) -> float:
        score = float(base_score)
        if regime != "oscillation":
            return round(score, 4)

        batch = item.batch if isinstance(item, StockBatchSummary) else None
        if batch is None:
            return round(score, 4)

        regime_adjustments = dict(self.scoring_section().get("regime_adjustments", {}) or {})
        oscillation = dict(regime_adjustments.get("oscillation", {}) or {})

        high_vol_threshold = float(oscillation.get("high_volatility_threshold", 0.032) or 0.032)
        high_vol_penalty = float(oscillation.get("high_volatility_penalty", 0.10) or 0.10)
        upper_bb_threshold = float(oscillation.get("upper_bb_penalty_threshold", 0.72) or 0.72)
        upper_bb_penalty = float(oscillation.get("upper_bb_penalty", 0.08) or 0.08)
        overheat_rsi_threshold = float(oscillation.get("overheat_rsi_threshold", 62.0) or 62.0)
        overheat_rsi_penalty = float(oscillation.get("overheat_rsi_penalty", 0.08) or 0.08)
        trend_chase_change_20d = float(oscillation.get("trend_chase_change_20d", 12.0) or 12.0)
        trend_chase_penalty = float(oscillation.get("trend_chase_penalty", 0.06) or 0.06)
        preferred_rsi_low = float(oscillation.get("preferred_rsi_low", 45.0) or 45.0)
        preferred_rsi_high = float(oscillation.get("preferred_rsi_high", 58.0) or 58.0)
        preferred_rsi_bonus = float(oscillation.get("preferred_rsi_bonus", 0.04) or 0.04)
        preferred_bb_low = float(oscillation.get("preferred_bb_low", 0.35) or 0.35)
        preferred_bb_high = float(oscillation.get("preferred_bb_high", 0.65) or 0.65)
        preferred_bb_bonus = float(oscillation.get("preferred_bb_bonus", 0.04) or 0.04)
        preferred_change_20d_low = float(oscillation.get("preferred_change_20d_low", -6.0) or -6.0)
        preferred_change_20d_high = float(oscillation.get("preferred_change_20d_high", 8.0) or 8.0)
        preferred_change_20d_bonus = float(oscillation.get("preferred_change_20d_bonus", 0.03) or 0.03)
        cross_trend_bonus = float(oscillation.get("cross_trend_bonus", 0.02) or 0.02)
        relative_strength_floor = float(oscillation.get("relative_strength_floor", -1.0) or -1.0)
        relative_strength_penalty = float(oscillation.get("relative_strength_penalty", 0.05) or 0.05)
        relative_strength_bonus_threshold = float(
            oscillation.get("relative_strength_bonus_threshold", 1.5) or 1.5
        )
        relative_strength_bonus = float(oscillation.get("relative_strength_bonus", 0.025) or 0.025)
        main_board_bonus = float(oscillation.get("main_board_bonus", 0.02) or 0.02)
        main_board_bonus_min_relative_strength = float(
            oscillation.get("main_board_bonus_min_relative_strength", 0.5) or 0.5
        )
        main_board_bonus_max_volatility = float(
            oscillation.get("main_board_bonus_max_volatility", 0.03) or 0.03
        )
        quality_trap_relative_strength_max = float(
            oscillation.get("quality_trap_relative_strength_max", -0.5) or -0.5
        )
        quality_trap_change_20d_max = float(oscillation.get("quality_trap_change_20d_max", 4.0) or 4.0)
        quality_trap_bb_low = float(oscillation.get("quality_trap_bb_low", 0.35) or 0.35)
        quality_trap_bb_high = float(oscillation.get("quality_trap_bb_high", 0.68) or 0.68)
        quality_trap_rsi_low = float(oscillation.get("quality_trap_rsi_low", 45.0) or 45.0)
        quality_trap_rsi_high = float(oscillation.get("quality_trap_rsi_high", 58.0) or 58.0)
        quality_trap_penalty = float(oscillation.get("quality_trap_penalty", 0.06) or 0.06)
        raw_bucket_bonus = dict(oscillation.get("bucket_bonus", {}) or {})
        bucket_bonus = {
            str(key): float(value)
            for key, value in raw_bucket_bonus.items()
            if str(key).strip() and value is not None
        }
        snapshot = dict(fundamentals or {})
        relative_strength = float(snapshot.get("relative_strength_hs300", 0.0) or 0.0)
        breakout20 = int(round(float(snapshot.get("breakout20", 0.0) or 0.0)))
        code_bucket = self._code_bucket(getattr(item, "code", ""))

        if batch.volatility > high_vol_threshold:
            ratio = min(1.0, (batch.volatility - high_vol_threshold) / max(high_vol_threshold, 1e-6))
            score -= high_vol_penalty * ratio
        if batch.bb_pos > upper_bb_threshold:
            ratio = min(1.0, (batch.bb_pos - upper_bb_threshold) / max(1.0 - upper_bb_threshold, 1e-6))
            score -= upper_bb_penalty * ratio
        if batch.rsi > overheat_rsi_threshold:
            ratio = min(1.0, (batch.rsi - overheat_rsi_threshold) / max(100.0 - overheat_rsi_threshold, 1.0))
            score -= overheat_rsi_penalty * ratio
        if batch.ma_trend == "多头" and batch.change_20d > trend_chase_change_20d:
            ratio = min(1.0, (batch.change_20d - trend_chase_change_20d) / max(trend_chase_change_20d, 1.0))
            score -= trend_chase_penalty * ratio
        if relative_strength < relative_strength_floor:
            ratio = min(
                1.0,
                (relative_strength_floor - relative_strength)
                / max(abs(relative_strength_floor) + 2.0, 1.0),
            )
            score -= relative_strength_penalty * ratio
        elif relative_strength >= relative_strength_bonus_threshold:
            ratio = min(
                1.0,
                (relative_strength - relative_strength_bonus_threshold)
                / max(relative_strength_bonus_threshold + 2.0, 1.0),
            )
            score += relative_strength_bonus * ratio
        if preferred_rsi_low <= batch.rsi <= preferred_rsi_high:
            score += preferred_rsi_bonus
        if preferred_bb_low <= batch.bb_pos <= preferred_bb_high:
            score += preferred_bb_bonus
        if preferred_change_20d_low <= batch.change_20d <= preferred_change_20d_high:
            score += preferred_change_20d_bonus
        if batch.ma_trend == "交叉":
            score += cross_trend_bonus
        quality_trap_candidate = (
            relative_strength <= quality_trap_relative_strength_max
            and breakout20 <= 0
            and batch.change_20d <= quality_trap_change_20d_max
            and quality_trap_bb_low <= batch.bb_pos <= quality_trap_bb_high
            and quality_trap_rsi_low <= batch.rsi <= quality_trap_rsi_high
        )
        if quality_trap_candidate:
            score -= quality_trap_penalty
        if code_bucket == "main_board":
            if (
                relative_strength >= main_board_bonus_min_relative_strength
                and batch.volatility <= main_board_bonus_max_volatility
                and not quality_trap_candidate
            ):
                score += main_board_bonus
        else:
            score += bucket_bonus.get(code_bucket, 0.0)
        return round(score, 4)

    def _select_diversified_candidates(
        self,
        ranked: List[Dict[str, Any]],
        *,
        regime: str,
        top_n: int,
    ) -> List[Dict[str, Any]]:
        if regime != "oscillation":
            return list(ranked[:top_n])

        selection_policy = dict(self.config_section("selection_policy", {}) or {})
        oscillation_policy = dict(selection_policy.get("oscillation", {}) or {})
        raw_bucket_limits = dict(oscillation_policy.get("max_per_code_bucket", {}) or {})
        bucket_limits = {
            str(key): max(1, int(value))
            for key, value in raw_bucket_limits.items()
            if str(key).strip() and value is not None
        }
        if not bucket_limits:
            return list(ranked[:top_n])

        selected: List[Dict[str, Any]] = []
        bucket_counts: dict[str, int] = defaultdict(int)
        deferred: List[Dict[str, Any]] = []
        for item in ranked:
            bucket = self._code_bucket(str(item.get("code") or ""))
            bucket_limit = bucket_limits.get(bucket)
            if bucket_limit is not None and bucket_counts[bucket] >= bucket_limit:
                deferred.append(item)
                continue
            selected.append(item)
            bucket_counts[bucket] += 1
            if len(selected) >= top_n:
                return selected

        for item in deferred:
            selected.append(item)
            if len(selected) >= top_n:
                break
        return selected[:top_n]

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
                enriched["regime_adjusted_score"] = self._regime_adjusted_score(
                    item,
                    base_score=score,
                    regime=regime,
                    fundamentals=fundamentals,
                )
                enriched["code_bucket"] = self._code_bucket(item.code)
                enriched_summaries.append(enriched)
        enriched_summaries.sort(
            key=lambda item: (
                item.get("regime_adjusted_score", item.get("value_quality_score", 0.0)),
                item.get("relative_strength_hs300", 0.0),
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
        selected_pool = self._select_diversified_candidates(
            enriched_summaries or stock_summaries,
            regime=regime,
            top_n=top_n,
        )
        selected = self.build_stock_summary_views(selected_pool or stock_summaries[:top_n])

        signals = []
        for idx, item in enumerate(selected, start=1):
            evidence = [
                f"value_quality_score={item.get('value_quality_score', 0.0):.3f}",
                f"regime_adjusted_score={item.get('regime_adjusted_score', item.get('value_quality_score', 0.0)):.3f}",
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
                        "bb_pos": float(item.get("bb_pos", 0.5)),
                        "volatility": float(item.get("volatility", 0.0)),
                        "pb": float(item.get("pb", 0.0)),
                        "pe_ttm": float(item.get("pe_ttm", 0.0)),
                        "roe": float(item.get("roe", 0.0)),
                        "relative_strength_hs300": float(item.get("relative_strength_hs300", 0.0)),
                        "breakout20": float(item.get("breakout20", 0.0)),
                        "value_quality_score": float(item.get("value_quality_score", item.get("algo_score", 0.0))),
                        "regime_adjusted_score": float(
                            item.get("regime_adjusted_score", item.get("value_quality_score", item.get("algo_score", 0.0)))
                        ),
                    },
                    evidence=evidence,
                    metadata={
                        "ma_trend": item.get("ma_trend"),
                        "macd": item.get("macd"),
                        "market_cap": item.get("market_cap", 0.0),
                        "code_bucket": item.get("code_bucket"),
                    },
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
            context=SignalPacketContext(
                market_stats=market_stats,
                stock_summaries=selected,
                raw_summaries=stock_summaries,
            ),
            metadata={
                "entry_threshold_policy": {
                    "mode": "upstream_signal_filter",
                    "key": "min_value_quality_score",
                    "consumed_upstream": True,
                    "post_selection_filter_supported": False,
                }
            },
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
