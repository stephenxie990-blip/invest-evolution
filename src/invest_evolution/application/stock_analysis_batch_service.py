"""Batch analysis orchestration helpers for stock analysis."""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from invest_evolution.config import normalize_date
from invest_evolution.investment.foundation.compute import (
    build_batch_indicator_snapshot,
    build_batch_summary,
)


def project_snapshot_fields(
    snapshot: dict[str, Any],
    *,
    summary: dict[str, Any] | None = None,
    trend_metrics: dict[str, Any] | None = None,
    quote_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = dict(summary or {})
    trend_metrics = dict(trend_metrics or {})
    quote_row = dict(quote_row or {})
    snapshot_payload = dict(snapshot or {})
    indicators = dict(snapshot_payload.get("indicators") or {})
    macd_payload = dict(indicators.get("macd_12_26_9") or {})
    boll = dict(indicators.get("bollinger_20") or {})
    latest_close = float(
        snapshot_payload.get("latest_close")
        or trend_metrics.get("latest_close")
        or quote_row.get("close")
        or summary.get("close")
        or 0.0
    )
    ma5 = float(
        indicators.get("sma_5") or trend_metrics.get("ma5") or latest_close or 0.0
    )
    ma10 = float(
        indicators.get("sma_10") or trend_metrics.get("ma10") or latest_close or 0.0
    )
    ma20 = float(
        indicators.get("sma_20") or trend_metrics.get("ma20") or latest_close or 0.0
    )
    ma60 = float(indicators.get("sma_60") or trend_metrics.get("ma60") or ma20 or 0.0)
    volume_ratio = indicators.get("volume_ratio_5_20") or trend_metrics.get(
        "volume_ratio"
    )
    rsi = float(
        indicators.get("rsi_14")
        or trend_metrics.get("rsi_14")
        or summary.get("rsi")
        or 50.0
    )
    ma_stack = str(indicators.get("ma_stack") or "mixed")
    macd_cross = str(
        macd_payload.get("cross") or trend_metrics.get("macd_cross") or "neutral"
    )
    return {
        "snapshot": snapshot_payload,
        "indicators": indicators,
        "macd_payload": macd_payload,
        "boll": boll,
        "latest_close": latest_close,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "volume_ratio": volume_ratio,
        "rsi": rsi,
        "ma_stack": ma_stack,
        "macd_cross": macd_cross,
        "atr_14": indicators.get("atr_14") or trend_metrics.get("atr_14"),
    }


class BatchAnalysisViewService:
    def __init__(
        self,
        *,
        humanize_macd_cross: Callable[[str], str],
        snapshot_projector: Callable[..., dict[str, Any]] = project_snapshot_fields,
    ):
        self._humanize_macd_cross = humanize_macd_cross
        self._snapshot_projector = snapshot_projector

    @staticmethod
    def empty_snapshot() -> dict[str, Any]:
        return {
            "samples": 0,
            "latest_trade_date": None,
            "latest_close": None,
            "indicators": {},
            "ready": False,
        }

    def build_batch_analysis_context(
        self, frame: pd.DataFrame, code: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        cutoff = normalize_date(str(frame["trade_date"].max()))
        batch = build_batch_indicator_snapshot(frame, cutoff)
        summary = build_batch_summary(frame, code, cutoff) or {}
        snapshot = (
            dict(batch.streaming_snapshot)
            if batch is not None
            else self.empty_snapshot()
        )
        return summary, snapshot, {"cutoff": cutoff, "batch": batch}

    def view_from_snapshot(
        self, summary: dict[str, Any], snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        fields = self._snapshot_projector(snapshot, summary=summary)
        indicators = dict(fields["indicators"])
        macd = dict(fields["macd_payload"])
        boll = dict(fields["boll"])
        latest = float(fields["latest_close"] or 0.0)
        ma5 = float(fields["ma5"] or 0.0)
        ma10 = float(fields["ma10"] or 0.0)
        ma20 = float(fields["ma20"] or 0.0)
        ma60 = float(fields["ma60"] or 0.0)
        volume_ratio = fields["volume_ratio"]
        rsi = float(fields["rsi"] or 50.0)
        ma_stack = str(fields["ma_stack"] or "mixed")
        macd_cross = str(fields["macd_cross"] or "neutral")
        signal = "observe"
        if ma_stack == "bullish" and macd_cross in {"golden_cross", "bullish"}:
            signal = "bullish"
        elif ma_stack == "bearish" and macd_cross in {"dead_cross", "bearish"}:
            signal = "bearish"
        structure = "range"
        if latest > ma20 and ma20 >= ma60:
            structure = "uptrend"
        elif latest < ma20 and ma20 <= ma60:
            structure = "downtrend"
        summary_view = dict(summary)
        summary_view.update(
            {
                "close": round(latest, 2) if latest else summary_view.get("close"),
                "rsi": round(rsi, 1),
                "macd": self._humanize_macd_cross(macd_cross),
                "ma_trend": "多头"
                if ma_stack == "bullish"
                else "空头"
                if ma_stack == "bearish"
                else "交叉",
                "bb_pos": boll.get("position", summary_view.get("bb_pos", 0.5)),
                "vol_ratio": volume_ratio
                if volume_ratio is not None
                else summary_view.get("vol_ratio"),
            }
        )
        trend_view = {
            "latest_close": round(latest, 2) if latest else None,
            "ma5": round(ma5, 2) if ma5 else None,
            "ma10": round(ma10, 2) if ma10 else None,
            "ma20": round(ma20, 2) if ma20 else None,
            "ma60": round(ma60, 2) if ma60 else None,
            "volume_ratio": round(float(volume_ratio), 3)
            if volume_ratio is not None
            else None,
            "macd_cross": macd_cross,
            "rsi_14": round(rsi, 2),
            "bollinger_position": boll.get("position"),
            "atr_14": indicators.get("atr_14"),
        }
        return {
            "summary": summary_view,
            "trend": trend_view,
            "signal": signal,
            "structure": structure,
            "indicators": indicators,
            "macd": macd,
            "boll": boll,
        }


__all__ = ["BatchAnalysisViewService", "project_snapshot_fields"]
