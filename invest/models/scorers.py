from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from invest.contracts import StockSignal
from invest.foundation.compute.batch_snapshot import BatchIndicatorSnapshot, StockBatchSummary


def coerce_batch(item: StockBatchSummary | Dict[str, Any]) -> BatchIndicatorSnapshot:
    if isinstance(item, StockBatchSummary):
        return item.batch
    return BatchIndicatorSnapshot(
        samples=0,
        latest_trade_date=None,
        latest_close=float(item.get("close", 0.0) or 0.0),
        change_5d=float(item.get("change_5d", 0.0) or 0.0),
        change_20d=float(item.get("change_20d", 0.0) or 0.0),
        sma_5=0.0,
        sma_20=0.0,
        ma_trend=str(item.get("ma_trend", "交叉") or "交叉"),
        rsi=float(item.get("rsi", 50.0) or 50.0),
        macd=str(item.get("macd", "中性") or "中性"),
        bb_pos=float(item.get("bb_pos", 0.5) or 0.5),
        vol_ratio=float(item.get("vol_ratio", 1.0) or 1.0),
        volatility=float(item.get("volatility", 0.0) or 0.0),
        above_ma20=False,
        streaming_snapshot={},
    )


@dataclass(frozen=True)
class MomentumScorer:
    def build_signal(
        self,
        item: StockBatchSummary,
        *,
        idx: int,
        top_n: int,
        stop_loss: float,
        take_profit: float,
        trailing_pct: Any,
    ) -> StockSignal:
        summary = item.summary
        batch = item.batch
        evidence = [
            f"algo_score={summary.get('algo_score', 0):.3f}",
            f"change_20d={batch.change_20d:+.2f}%",
            f"MACD={batch.macd}",
        ]
        return StockSignal(
            code=item.code,
            score=float(summary.get("algo_score", 0.0)),
            rank=idx,
            weight_hint=round(1 / max(top_n, 1), 3),
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            trailing_pct=float(trailing_pct) if trailing_pct is not None else None,
            factor_values={
                "change_5d": batch.change_5d,
                "change_20d": batch.change_20d,
                "rsi": batch.rsi,
                "bb_pos": batch.bb_pos,
                "vol_ratio": batch.vol_ratio,
            },
            evidence=evidence,
            metadata={"ma_trend": batch.ma_trend, "macd": batch.macd},
        )


@dataclass(frozen=True)
class MeanReversionScorer:
    model: Any

    def score(self, item: StockBatchSummary | Dict[str, Any]) -> float:
        oversold_rsi = float(self.model.param("oversold_rsi", 35.0))
        hot_rsi = float(self.model.param("rebound_rsi_cap", 60.0))
        max_5d_drop = float(self.model.param("max_5d_drop", -2.0))
        max_20d_drop = float(self.model.param("max_20d_drop", -5.0))
        scoring = self.model.scoring_section()
        weights = dict(scoring.get("weights", {}))
        bands = dict(scoring.get("bands", {}))
        penalties = dict(scoring.get("penalties", {}))
        batch = coerce_batch(item)
        lower_bb = float(bands.get("lower_bb_threshold", 0.35))
        upper_bb = float(bands.get("upper_bb_threshold", 0.8))
        vol_ratio_low = float(bands.get("vol_ratio_low", 0.8))
        vol_ratio_high = float(bands.get("vol_ratio_high", 1.8))
        high_volatility = float(bands.get("high_volatility_threshold", 0.05))
        score = 0.0
        score += max(0.0, oversold_rsi - batch.rsi) / max(oversold_rsi, 1.0) * float(weights.get("oversold_rsi", 0.35))
        if batch.bb_pos < lower_bb:
            score += max(0.0, lower_bb - batch.bb_pos) / max(lower_bb, 1e-6) * float(weights.get("lower_bb", 0.20))
        else:
            score -= max(0.0, batch.bb_pos - upper_bb) * float(penalties.get("upper_bb", 0.10))
        score += max(0.0, abs(min(batch.change_5d, 0.0)) - abs(max_5d_drop)) / 10.0 * float(weights.get("drop_5d", 0.20)) if batch.change_5d <= max_5d_drop else -float(penalties.get("insufficient_drop_5d", 0.05))
        score += max(0.0, abs(min(batch.change_20d, 0.0)) - abs(max_20d_drop)) / 20.0 * float(weights.get("drop_20d", 0.15)) if batch.change_20d <= max_20d_drop else -float(penalties.get("insufficient_drop_20d", 0.05))
        if batch.ma_trend == "空头":
            score += float(weights.get("bearish_trend_bonus", 0.05))
        if vol_ratio_low <= batch.vol_ratio <= vol_ratio_high:
            score += float(weights.get("volume_ratio_bonus", 0.08))
        if batch.volatility > high_volatility:
            score -= float(penalties.get("high_volatility", 0.08))
        if batch.rsi > hot_rsi:
            score -= float(penalties.get("overheat_rsi", 0.15))
        return round(score, 4)


@dataclass(frozen=True)
class DefensiveLowVolScorer:
    model: Any

    def score(self, item: StockBatchSummary | Dict[str, Any]) -> float:
        batch = coerce_batch(item)
        scoring = self.model.scoring_section()
        weights = dict(scoring.get("weights", {}))
        bands = dict(scoring.get("bands", {}))
        penalties = dict(scoring.get("penalties", {}))
        max_volatility = float(self.model.param("max_volatility", 0.035))
        preferred_rsi_low = float(self.model.param("preferred_rsi_low", 42.0))
        preferred_rsi_high = float(self.model.param("preferred_rsi_high", 62.0))
        min_change_20d = float(self.model.param("min_change_20d", -2.0))
        max_change_20d = float(self.model.param("max_change_20d", 12.0))
        rsi_soft_low = float(bands.get("rsi_soft_low", 35.0))
        rsi_soft_high = float(bands.get("rsi_soft_high", 70.0))
        change_5d_low = float(bands.get("change_5d_low", -2.0))
        change_5d_high = float(bands.get("change_5d_high", 4.0))
        bb_pos_low = float(bands.get("bb_pos_low", 0.35))
        bb_pos_high = float(bands.get("bb_pos_high", 0.75))
        vol_ratio_low = float(bands.get("vol_ratio_low", 0.8))
        vol_ratio_high = float(bands.get("vol_ratio_high", 1.5))
        score = 0.0
        score += max(0.0, (max_volatility - min(batch.volatility, max_volatility)) / max_volatility) * float(weights.get("low_volatility", 0.35))
        if preferred_rsi_low <= batch.rsi <= preferred_rsi_high:
            score += float(weights.get("preferred_rsi", 0.20))
        elif rsi_soft_low <= batch.rsi < preferred_rsi_low or preferred_rsi_high < batch.rsi <= rsi_soft_high:
            score += float(weights.get("soft_rsi", 0.08))
        else:
            score -= float(penalties.get("bad_rsi", 0.08))
        if min_change_20d <= batch.change_20d <= max_change_20d:
            score += float(weights.get("change_20d_band", 0.15))
        elif batch.change_20d < min_change_20d:
            score -= float(penalties.get("weak_change_20d", 0.08))
        if change_5d_low <= batch.change_5d <= change_5d_high:
            score += float(weights.get("change_5d_band", 0.10))
        if batch.ma_trend == "多头":
            score += float(weights.get("bullish_trend", 0.12))
        elif batch.ma_trend == "空头":
            score -= float(penalties.get("bearish_trend", 0.08))
        if bb_pos_low <= batch.bb_pos <= bb_pos_high:
            score += float(weights.get("bb_band", 0.05))
        if vol_ratio_low <= batch.vol_ratio <= vol_ratio_high:
            score += float(weights.get("volume_ratio_band", 0.03))
        return round(score, 4)


@dataclass(frozen=True)
class ValueQualityScorer:
    model: Any

    def score(self, item: StockBatchSummary | Dict[str, Any], fundamentals: Dict[str, float]) -> float:
        batch = coerce_batch(item)
        pe = fundamentals.get("pe_ttm", 0.0)
        pb = fundamentals.get("pb", 0.0)
        roe = fundamentals.get("roe", 0.0)
        market_cap = fundamentals.get("market_cap", 0.0)
        scoring = self.model.scoring_section()
        weights = dict(scoring.get("weights", {}))
        bands = dict(scoring.get("bands", {}))
        max_pe = float(self.model.param("max_pe_ttm", 30.0))
        max_pb = float(self.model.param("max_pb", 3.0))
        min_roe = float(self.model.param("min_roe", 8.0))
        min_market_cap = float(self.model.param("min_market_cap", 0.0))
        rsi_low = float(bands.get("rsi_low", 40.0))
        rsi_high = float(bands.get("rsi_high", 65.0))
        change_20d_low = float(bands.get("change_20d_low", -10.0))
        change_20d_high = float(bands.get("change_20d_high", 15.0))
        low_volatility = float(bands.get("low_volatility_threshold", 0.04))
        score = 0.0
        if pe > 0:
            score += max(0.0, (max_pe - min(pe, max_pe)) / max_pe) * float(weights.get("pe", 0.25))
        if pb > 0:
            score += max(0.0, (max_pb - min(pb, max_pb)) / max_pb) * float(weights.get("pb", 0.20))
        if roe > 0:
            score += min(roe / max(min_roe * 2, 1.0), 1.0) * float(weights.get("roe", 0.30))
        if market_cap >= min_market_cap:
            score += float(weights.get("market_cap", 0.10))
        if rsi_low <= batch.rsi <= rsi_high:
            score += float(weights.get("rsi_band", 0.08))
        if change_20d_low <= batch.change_20d <= change_20d_high:
            score += float(weights.get("change_20d_band", 0.05))
        if batch.volatility < low_volatility:
            score += float(weights.get("low_volatility", 0.05))
        return round(score, 4)


__all__ = [
    "DefensiveLowVolScorer",
    "MeanReversionScorer",
    "MomentumScorer",
    "ValueQualityScorer",
    "coerce_batch",
]
