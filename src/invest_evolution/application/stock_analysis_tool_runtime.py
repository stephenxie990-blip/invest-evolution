"""Shared stock-analysis query and price-window runtime helpers."""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd


class StockAnalysisToolRuntimeSupportService:
    def __init__(
        self,
        *,
        resolve_security: Callable[[str], tuple[str, dict[str, Any]]],
        get_stock_frame: Callable[[str], pd.DataFrame],
        build_tool_unavailable_response: Callable[..., dict[str, Any]],
        query_context_factory: Callable[..., Any],
        window_context_factory: Callable[..., Any],
    ) -> None:
        self.resolve_security = resolve_security
        self.get_stock_frame = get_stock_frame
        self.build_tool_unavailable_response = build_tool_unavailable_response
        self.query_context_factory = query_context_factory
        self.window_context_factory = window_context_factory

    def resolve_query_context(self, query: str) -> Any:
        code, security = self.resolve_security(query)
        return self.query_context_factory(
            query=query,
            code=code,
            security=dict(security),
            price_frame=self.get_stock_frame(code),
        )

    def resolve_price_query_context(
        self,
        query: str,
        *,
        summary: str,
        next_actions: list[str],
        status: str = "not_found",
    ) -> tuple[Any, dict[str, Any] | None]:
        context = self.resolve_query_context(query)
        if context.price_frame.empty:
            return context, self.build_tool_unavailable_response(
                status=status,
                query=context.query,
                code=context.code,
                security=context.security,
                summary=summary,
                next_actions=next_actions,
            )
        return context, None

    def resolve_window_context(
        self,
        query: str,
        *,
        days: int,
        minimum: int,
        summary: str,
        next_actions: list[str],
        status: str = "not_found",
        copy_frame: bool = False,
    ) -> tuple[Any | None, dict[str, Any] | None]:
        context, unavailable = self.resolve_price_query_context(
            query,
            summary=summary,
            next_actions=next_actions,
            status=status,
        )
        if unavailable is not None:
            return None, unavailable
        frame = self.tail_frame(
            context.price_frame,
            days=days,
            minimum=minimum,
        )
        if copy_frame:
            frame = frame.copy()
        return self.window_context_factory(query_context=context, frame=frame), None

    @staticmethod
    def tail_frame(frame: pd.DataFrame, *, days: int, minimum: int = 1) -> pd.DataFrame:
        return frame.tail(max(minimum, int(days)))

    @staticmethod
    def resolve_frame_date_window(frame: pd.DataFrame, *, days: int) -> tuple[str, str]:
        window = frame.tail(max(1, int(days)))
        return str(window["trade_date"].min()), str(frame["trade_date"].max())

    @classmethod
    def resolve_price_window(cls, frame: pd.DataFrame, *, days: int) -> dict[str, str]:
        start_date, end_date = cls.resolve_frame_date_window(frame, days=days)
        return {
            "start_date": start_date,
            "end_date": end_date,
        }
