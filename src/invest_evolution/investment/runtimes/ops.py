from __future__ import annotations

# Runtime execution adapter

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Set

from invest_evolution.investment.contracts import PortfolioPlan, SignalPacket, StockSignal
from invest_evolution.investment.foundation.compute import BatchIndicatorSnapshot, StockBatchSummary


class SimulationService:
    """Adapter from portfolio plan to downstream execution payloads."""

    def build_execution_payload(self, portfolio_plan: PortfolioPlan) -> Dict[str, Any]:
        trading_plan = portfolio_plan.to_trading_plan()
        return {
            "selected_codes": list(portfolio_plan.selected_codes),
            "cash_reserve": float(portfolio_plan.cash_reserve or 0.0),
            "trading_plan": {
                "date": trading_plan.date,
                "positions": [
                    {
                        "code": position.code,
                        "priority": position.priority,
                        "weight": position.weight,
                        "entry_method": position.entry_method,
                        "entry_price": position.entry_price,
                        "stop_loss_pct": position.stop_loss_pct,
                        "take_profit_pct": position.take_profit_pct,
                        "trailing_pct": position.trailing_pct,
                        "expire_days": position.expire_days,
                        "max_hold_days": position.max_hold_days,
                        "reason": position.reason,
                        "source": position.source,
                    }
                    for position in list(trading_plan.positions or [])
                ],
                "cash_reserve": trading_plan.cash_reserve,
                "max_positions": trading_plan.max_positions,
                "source": trading_plan.source,
                "reasoning": trading_plan.reasoning,
            },
            "manager_weights": dict(portfolio_plan.manager_weights or {}),
            "active_manager_ids": list(portfolio_plan.active_manager_ids or []),
        }


# Runtime validation


class RuntimeConfigValidationError(ValueError):
    pass


_REQUIRED_TOP_LEVEL = {"name", "kind", "params", "risk", "execution", "benchmark"}
_REQUIRED_SCORING_MODELS = {"mean_reversion", "value_quality", "defensive_low_vol"}
_REQUIRED_SCORING_SHAPE = {
    "mean_reversion": {"weights", "bands", "penalties"},
    "value_quality": {"weights", "bands"},
    "defensive_low_vol": {"weights", "bands", "penalties"},
}


def _ensure_numeric_dict(name: str, payload: Dict[str, Any]) -> None:
    for key, value in payload.items():
        if not isinstance(value, (int, float)):
            raise RuntimeConfigValidationError(f"{name}.{key} must be numeric")


def _ensure_range_dict(name: str, payload: Dict[str, Any]) -> None:
    for key, value in payload.items():
        if not isinstance(value, dict):
            raise RuntimeConfigValidationError(f"{name}.{key} must be a dict")
        if "min" not in value or "max" not in value:
            raise RuntimeConfigValidationError(f"{name}.{key} must define min/max")
        if not isinstance(value["min"], (int, float)) or not isinstance(value["max"], (int, float)):
            raise RuntimeConfigValidationError(f"{name}.{key}.min/max must be numeric")
        if float(value["min"]) > float(value["max"]):
            raise RuntimeConfigValidationError(f"{name}.{key}.min must be <= max")


def validate_runtime_config(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise RuntimeConfigValidationError("runtime config must be a dict")

    missing = [key for key in _REQUIRED_TOP_LEVEL if key not in data]
    if missing:
        raise RuntimeConfigValidationError(f"missing required top-level keys: {', '.join(missing)}")

    for section in ["params", "risk", "execution", "benchmark"]:
        if not isinstance(data.get(section), dict):
            raise RuntimeConfigValidationError(f"{section} must be a dict")

    params = data.get("params", {}) or {}
    if "top_n" in params and int(params["top_n"]) <= 0:
        raise RuntimeConfigValidationError("params.top_n must be > 0")
    if "max_positions" in params and int(params["max_positions"]) <= 0:
        raise RuntimeConfigValidationError("params.max_positions must be > 0")
    if "cash_reserve" in params and not (0.0 <= float(params["cash_reserve"]) <= 1.0):
        raise RuntimeConfigValidationError("params.cash_reserve must be within [0, 1]")

    kind = str(data.get("kind") or "")
    scoring = data.get("scoring")
    if kind in _REQUIRED_SCORING_MODELS:
        if not isinstance(scoring, dict):
            raise RuntimeConfigValidationError(f"scoring section is required for runtime kind={kind}")
        missing_scoring = [key for key in _REQUIRED_SCORING_SHAPE[kind] if key not in scoring]
        if missing_scoring:
            raise RuntimeConfigValidationError(f"scoring missing keys for {kind}: {', '.join(missing_scoring)}")
        for key in _REQUIRED_SCORING_SHAPE[kind]:
            section = scoring.get(key)
            if not isinstance(section, dict):
                raise RuntimeConfigValidationError(f"scoring.{key} must be a dict")
            _ensure_numeric_dict(f"scoring.{key}", section)

    summary_scoring = data.get("summary_scoring")
    if summary_scoring is not None:
        if not isinstance(summary_scoring, dict):
            raise RuntimeConfigValidationError("summary_scoring must be a dict")
        for section_name in ["weights", "bands"]:
            section = summary_scoring.get(section_name)
            if section is not None and not isinstance(section, dict):
                raise RuntimeConfigValidationError(f"summary_scoring.{section_name} must be a dict")
        if "logic" in summary_scoring:
            if not isinstance(summary_scoring["logic"], dict):
                raise RuntimeConfigValidationError("summary_scoring.logic must be a dict")
            _ensure_numeric_dict("summary_scoring.logic", summary_scoring["logic"])

    market_hints = data.get("market_hints")
    if market_hints is not None:
        if not isinstance(market_hints, dict):
            raise RuntimeConfigValidationError("market_hints must be a dict")
        _ensure_numeric_dict("market_hints", market_hints)

    review_policy = data.get("review_policy")
    if review_policy is not None and not isinstance(review_policy, dict):
        raise RuntimeConfigValidationError("review_policy must be a dict")

    market_regime = data.get("market_regime")
    if market_regime is not None:
        if not isinstance(market_regime, dict):
            raise RuntimeConfigValidationError("market_regime must be a dict")
        numeric_fields = {k: v for k, v in market_regime.items() if k != "default_regime"}
        _ensure_numeric_dict("market_regime", numeric_fields)
        if "default_regime" in market_regime and not isinstance(market_regime["default_regime"], str):
            raise RuntimeConfigValidationError("market_regime.default_regime must be a string")

    mutation_space = data.get("mutation_space")
    if mutation_space is not None:
        if not isinstance(mutation_space, dict):
            raise RuntimeConfigValidationError("mutation_space must be a dict")
        if "params" in mutation_space:
            if not isinstance(mutation_space["params"], dict):
                raise RuntimeConfigValidationError("mutation_space.params must be a dict")
            _ensure_range_dict("mutation_space.params", mutation_space["params"])
        if "scoring" in mutation_space:
            if not isinstance(mutation_space["scoring"], dict):
                raise RuntimeConfigValidationError("mutation_space.scoring must be a dict")
            for section_name, section_ranges in mutation_space["scoring"].items():
                if not isinstance(section_ranges, dict):
                    raise RuntimeConfigValidationError(f"mutation_space.scoring.{section_name} must be a dict")
                _ensure_range_dict(f"mutation_space.scoring.{section_name}", section_ranges)

    return data


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
    runtime: Any

    def score(self, item: StockBatchSummary | Dict[str, Any]) -> float:
        oversold_rsi = float(self.runtime.param("oversold_rsi", 35.0))
        hot_rsi = float(self.runtime.param("rebound_rsi_cap", 60.0))
        max_5d_drop = float(self.runtime.param("max_5d_drop", -2.0))
        max_20d_drop = float(self.runtime.param("max_20d_drop", -5.0))
        scoring = self.runtime.scoring_section()
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
    runtime: Any

    def score(self, item: StockBatchSummary | Dict[str, Any]) -> float:
        batch = coerce_batch(item)
        scoring = self.runtime.scoring_section()
        weights = dict(scoring.get("weights", {}))
        bands = dict(scoring.get("bands", {}))
        penalties = dict(scoring.get("penalties", {}))
        max_volatility = float(self.runtime.param("max_volatility", 0.035))
        preferred_rsi_low = float(self.runtime.param("preferred_rsi_low", 42.0))
        preferred_rsi_high = float(self.runtime.param("preferred_rsi_high", 62.0))
        min_change_20d = float(self.runtime.param("min_change_20d", -2.0))
        max_change_20d = float(self.runtime.param("max_change_20d", 12.0))
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
    runtime: Any

    def score(self, item: StockBatchSummary | Dict[str, Any], fundamentals: Dict[str, float]) -> float:
        batch = coerce_batch(item)
        pe = fundamentals.get("pe_ttm", 0.0)
        pb = fundamentals.get("pb", 0.0)
        roe = fundamentals.get("roe", 0.0)
        market_cap = fundamentals.get("market_cap", 0.0)
        scoring = self.runtime.scoring_section()
        weights = dict(scoring.get("weights", {}))
        bands = dict(scoring.get("bands", {}))
        max_pe = float(self.runtime.param("max_pe_ttm", 30.0))
        max_pb = float(self.runtime.param("max_pb", 3.0))
        min_roe = float(self.runtime.param("min_roe", 8.0))
        min_market_cap = float(self.runtime.param("min_market_cap", 0.0))
        rsi_low = float(bands.get("rsi_low", 40.0))
        rsi_high = float(bands.get("rsi_high", 65.0))
        change_20d_low = float(bands.get("change_20d_low", -10.0))
        change_20d_high = float(bands.get("change_20d_high", 15.0))
        low_volatility = float(bands.get("low_volatility_threshold", 0.04))
        has_valuation_metric = pe > 0 or pb > 0
        has_quality_metric = roe > 0
        if not has_valuation_metric or not has_quality_metric:
            return 0.0
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


class ScoringService:
    """Normalizes signal strengths into stable manager-level weights."""

    def rank_signals(self, signals: Iterable[StockSignal]) -> List[StockSignal]:
        return sorted(
            list(signals or []),
            key=lambda item: (-float(item.score), int(item.rank)),
        )

    def normalize_signal_weights(
        self,
        signals: Iterable[StockSignal],
        *,
        total_exposure: float = 1.0,
    ) -> Dict[str, float]:
        ranked = self.rank_signals(signals)
        if not ranked:
            return {}
        hinted = [max(0.0, float(item.weight_hint or 0.0)) for item in ranked]
        if sum(hinted) > 0:
            raw = hinted
        else:
            min_score = min(float(item.score) for item in ranked)
            offset = abs(min_score) + 1.0 if min_score <= 0 else 0.0
            raw = [float(item.score) + offset for item in ranked]
            if sum(raw) <= 0:
                raw = [1.0 for _ in ranked]
        scale = float(total_exposure or 0.0) / max(sum(raw), 1e-9)
        return {
            item.code: round(weight * scale, 8)
            for item, weight in zip(ranked, raw)
        }


class ScreeningService:
    """Reusable stock pre-screening for manager runtime."""

    def select_signals(
        self,
        signal_packet: SignalPacket,
        *,
        top_n: int | None = None,
        allow_codes: Iterable[str] | None = None,
    ) -> List[StockSignal]:
        allow_set: Set[str] | None = None
        if allow_codes is not None:
            allow_set = {str(item).strip() for item in allow_codes if str(item).strip()}
        signal_map = {item.code: item for item in list(signal_packet.signals or []) if item.code}
        ordered_codes = signal_packet.top_codes(limit=None)
        ranked_signals: List[StockSignal] = []
        seen: Set[str] = set()
        for code in ordered_codes:
            signal = signal_map.get(code)
            if signal is None or code in seen:
                continue
            if allow_set is not None and code not in allow_set:
                continue
            seen.add(code)
            ranked_signals.append(signal)
        for signal in sorted(
            signal_packet.signals or [],
            key=lambda item: (-float(item.score), int(item.rank)),
        ):
            if signal.code in seen:
                continue
            if allow_set is not None and signal.code not in allow_set:
                continue
            seen.add(signal.code)
            ranked_signals.append(signal)
        if top_n is None or top_n <= 0:
            return ranked_signals
        return ranked_signals[:top_n]

__all__ = [name for name in globals() if not name.startswith('_')]
