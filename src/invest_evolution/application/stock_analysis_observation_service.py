"""Shared stock-analysis observation projection helpers."""

from __future__ import annotations

from typing import Any, Callable

from invest_evolution.application.stock_analysis_response_contracts import (
    ToolObservationEnvelope,
)


def observation_envelope(
    result: Any,
    *,
    summary_keys: tuple[str, ...] = ("summary",),
) -> ToolObservationEnvelope:
    return ToolObservationEnvelope.from_result(result, summary_keys=summary_keys)


def observation_section(result: dict[str, Any], key: str) -> dict[str, Any]:
    payload = result.get(key)
    return dict(payload or {}) if isinstance(payload, dict) else {}


def project_tool_observation(
    result: dict[str, Any],
    *,
    summary_keys: tuple[str, ...] = ("summary",),
    **payload: Any,
) -> dict[str, Any]:
    return observation_envelope(
        result,
        summary_keys=summary_keys,
    ).to_dict(**payload)


class StockAnalysisObservationService:
    def __init__(
        self,
        *,
        build_indicator_projection: Callable[..., Any],
    ) -> None:
        self.build_indicator_projection = build_indicator_projection

    def summarize_observation(
        self,
        tool_name: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        envelope = observation_envelope(result)
        if not isinstance(result, dict):
            return envelope.to_dict()
        if tool_name == "get_daily_history":
            items = list(result.get("items") or [])
            last_trade_date = items[-1].get("trade_date") if items else None
            return envelope.to_dict(
                count=int(result.get("count") or len(items)),
                last_trade_date=last_trade_date,
            )
        if tool_name == "get_indicator_snapshot":
            snapshot_payload = observation_section(result, "snapshot")
            indicator_projection = self.build_indicator_projection(snapshot_payload)
            return project_tool_observation(
                result,
                summary_keys=("observation_summary", "summary"),
                latest_close=indicator_projection.snapshot.get("latest_close"),
                rsi_14=indicator_projection.indicators.get("rsi_14"),
                ma_stack=indicator_projection.indicators.get("ma_stack"),
                macd_cross=indicator_projection.macd_payload.get("cross"),
            )
        if tool_name == "analyze_trend":
            trend = observation_section(result, "trend")
            return project_tool_observation(
                result,
                summary_keys=("observation_summary", "summary"),
                signal=result.get("signal"),
                structure=result.get("structure"),
                latest_close=trend.get("latest_close"),
                ma20=trend.get("ma20"),
                volume_ratio=trend.get("volume_ratio"),
                macd_cross=trend.get("macd_cross"),
            )
        if tool_name == "analyze_support_resistance":
            levels = observation_section(result, "levels")
            return project_tool_observation(
                result,
                summary_keys=("observation_summary", "summary"),
                support_20=levels.get("support_20"),
                resistance_20=levels.get("resistance_20"),
                bias=levels.get("bias"),
            )
        if tool_name == "get_capital_flow":
            metrics = observation_section(result, "metrics")
            return project_tool_observation(
                result,
                summary_keys=("observation_summary", "summary"),
                direction=metrics.get("direction"),
                main_net_inflow_sum=metrics.get("main_net_inflow_sum"),
            )
        if tool_name == "get_intraday_context":
            metrics = observation_section(result, "metrics")
            return project_tool_observation(
                result,
                summary_keys=("observation_summary", "summary"),
                intraday_bias=metrics.get("intraday_bias"),
                latest_trade_date=metrics.get("latest_trade_date"),
            )
        if tool_name in {"get_realtime_quote", "get_latest_quote"}:
            quote = observation_section(result, "quote")
            return envelope.to_dict(
                close=quote.get("close"),
                trade_date=quote.get("trade_date"),
            )
        return envelope.to_dict()


__all__ = [
    "StockAnalysisObservationService",
    "observation_envelope",
    "observation_section",
    "project_tool_observation",
]
