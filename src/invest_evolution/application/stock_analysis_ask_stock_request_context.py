"""Ask-stock request-context construction helpers."""

from __future__ import annotations

from typing import Any, Callable

from invest_evolution.application.stock_analysis_ask_stock_assembly import (
    AskStockRequestContext,
)


class AskStockRequestContextService:
    def __init__(
        self,
        *,
        resolve_strategy_name: Callable[[str, str], tuple[str, str]],
        infer_days: Callable[[str, int], int],
        load_strategy: Callable[[str], Any],
        resolve_query_context: Callable[[str], Any],
        resolve_effective_as_of_date: Callable[[str, str], str],
    ) -> None:
        self.resolve_strategy_name = resolve_strategy_name
        self.infer_days = infer_days
        self.load_strategy = load_strategy
        self.resolve_query_context = resolve_query_context
        self.resolve_effective_as_of_date = resolve_effective_as_of_date

    def build_request_context(
        self,
        *,
        question: str,
        query: str,
        strategy: str,
        days: int,
        as_of_date: str,
    ) -> AskStockRequestContext:
        strategy_name, strategy_source = self.resolve_strategy_name(question, strategy)
        resolved_days = self.infer_days(question, days)
        resolved_strategy = self.load_strategy(strategy_name)
        base_context = self.resolve_query_context(query)
        code = base_context.code
        security = dict(base_context.security)
        effective_as_of_date = self.resolve_effective_as_of_date(code, as_of_date)
        return AskStockRequestContext(
            question=question,
            query=query,
            code=code,
            security=security,
            requested_as_of_date=as_of_date,
            effective_as_of_date=effective_as_of_date,
            strategy=resolved_strategy,
            strategy_source=strategy_source,
            days=resolved_days,
        )
