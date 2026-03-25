"""Shared stock-analysis snapshot and indicator projection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class StockIndicatorProjection:
    snapshot: dict[str, Any]
    indicators: dict[str, Any]
    macd_payload: dict[str, Any]
    boll: dict[str, Any]
    projected_fields: dict[str, Any]


def build_indicator_projection(
    snapshot: dict[str, Any] | None,
    *,
    snapshot_projector: Callable[..., dict[str, Any]],
    summary: dict[str, Any] | None = None,
    trend_metrics: dict[str, Any] | None = None,
    quote_row: dict[str, Any] | None = None,
) -> StockIndicatorProjection:
    projected_fields = snapshot_projector(
        dict(snapshot or {}),
        summary=dict(summary or {}),
        trend_metrics=dict(trend_metrics or {}),
        quote_row=dict(quote_row or {}),
    )
    indicators = dict(projected_fields["indicators"])
    return StockIndicatorProjection(
        snapshot=dict(projected_fields["snapshot"]),
        indicators=indicators,
        macd_payload=dict(projected_fields["macd_payload"]),
        boll=dict(projected_fields["boll"]),
        projected_fields=projected_fields,
    )


class StockAnalysisProjectionService:
    def __init__(
        self,
        *,
        build_batch_analysis_context: Callable[..., tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
        view_from_snapshot: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        snapshot_projector: Callable[..., dict[str, Any]],
    ) -> None:
        self.build_batch_analysis_context = build_batch_analysis_context
        self.view_from_snapshot = view_from_snapshot
        self.snapshot_projector = snapshot_projector

    def build_indicator_projection(
        self,
        snapshot: dict[str, Any] | None,
        *,
        summary: dict[str, Any] | None = None,
        trend_metrics: dict[str, Any] | None = None,
        quote_row: dict[str, Any] | None = None,
    ) -> StockIndicatorProjection:
        return build_indicator_projection(
            snapshot,
            snapshot_projector=self.snapshot_projector,
            summary=summary,
            trend_metrics=trend_metrics,
            quote_row=quote_row,
        )

    def build_snapshot_projection(
        self,
        frame: Any,
        code: str,
    ) -> dict[str, Any]:
        summary, snapshot, meta = self.build_batch_analysis_context(frame, code)
        view = self.view_from_snapshot(summary, snapshot)
        fields = self.snapshot_projector(
            snapshot,
            summary=summary,
            trend_metrics=dict(view.get("trend") or {}),
        )
        return {
            "summary": summary,
            "snapshot": snapshot,
            "meta": meta,
            "view": view,
            "fields": fields,
        }


__all__ = [
    "StockAnalysisProjectionService",
    "StockIndicatorProjection",
    "build_indicator_projection",
]
