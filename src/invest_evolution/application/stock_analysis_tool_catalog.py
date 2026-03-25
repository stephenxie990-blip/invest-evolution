"""Stock analysis tool catalog contracts and builders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StockToolCatalog:
    entries: tuple[dict[str, Any], ...]
    by_name: dict[str, dict[str, Any]]
    aliases: dict[str, str]
    definitions_by_name: dict[str, dict[str, Any]]


def _stock_tool_parameters(
    *,
    include_days: bool = True,
    minimum: int = 30,
    maximum: int = 500,
) -> dict[str, Any]:
    properties: dict[str, Any] = {"query": {"type": "string"}}
    if include_days:
        properties["days"] = {
            "type": "integer",
            "minimum": int(minimum),
            "maximum": int(maximum),
        }
    return {
        "type": "object",
        "properties": properties,
        "required": ["query"],
    }


def _stock_tool_catalog_entry(
    *,
    name: str,
    executor: str,
    description: str,
    thought: str,
    include_days: bool = True,
    minimum: int = 30,
    maximum: int = 500,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "executor": executor,
        "description": description,
        "thought": thought,
        "parameters": _stock_tool_parameters(
            include_days=include_days,
            minimum=minimum,
            maximum=maximum,
        ),
    }
    if aliases:
        payload["aliases"] = list(aliases)
    return payload


def _build_stock_tool_catalog_entries() -> tuple[dict[str, Any], ...]:
    return (
        _stock_tool_catalog_entry(
            name="get_daily_history",
            executor="get_daily_history",
            description="获取历史日线 OHLCV 数据，用于结构和趋势分析。",
            thought="先读取日线历史，建立价格结构上下文。",
        ),
        _stock_tool_catalog_entry(
            name="get_indicator_snapshot",
            executor="get_indicator_snapshot",
            description="计算均线、MACD、RSI、ATR、布林带和量比等指标快照。",
            thought="先把关键指标跑全，避免只看单一信号。",
        ),
        _stock_tool_catalog_entry(
            name="analyze_trend",
            executor="analyze_trend",
            description="分析均线、MACD、RSI、结构与趋势方向。",
            thought="结合均线、MACD 和 RSI 判断当前趋势级别。",
        ),
        _stock_tool_catalog_entry(
            name="analyze_support_resistance",
            executor="analyze_support_resistance",
            description="识别支撑阻力、距关键位距离与突破/回踩偏向。",
            thought="再确认支撑阻力和关键价格区间。",
        ),
        _stock_tool_catalog_entry(
            name="get_capital_flow",
            executor="get_capital_flow",
            description="读取本地主力资金流数据，判断净流入/流出方向。",
            thought="查看资金是否顺着当前趋势流入或流出。",
            minimum=1,
            maximum=120,
        ),
        _stock_tool_catalog_entry(
            name="get_intraday_context",
            executor="get_intraday_context",
            description="读取本地60分钟数据，确认短周期顺逆风。",
            thought="必要时用60分钟结构确认短周期是否配合。",
            minimum=1,
            maximum=20,
        ),
        _stock_tool_catalog_entry(
            name="get_realtime_quote",
            executor="get_realtime_quote",
            description="获取最新价格/最近收盘数据，用于风险位和结论确认。",
            thought="最后用最新价格校准入场位和止损位。",
            include_days=False,
            aliases=["get_latest_quote"],
        ),
    )


def _build_stock_tool_catalog() -> StockToolCatalog:
    entries = _build_stock_tool_catalog_entries()
    by_name = {str(item["name"]): dict(item) for item in entries}
    aliases: dict[str, str] = {}
    for item in entries:
        canonical_name = str(item.get("name") or "").strip()
        for alias in list(item.get("aliases") or []):
            alias_name = str(alias or "").strip()
            if alias_name:
                aliases[alias_name] = canonical_name
    definitions_by_name = {
        str(item["name"]): {
            "type": "function",
            "function": {
                "name": str(item["name"]),
                "description": str(item["description"]),
                "parameters": dict(item["parameters"]),
            },
        }
        for item in entries
    }
    return StockToolCatalog(
        entries=entries,
        by_name=by_name,
        aliases=aliases,
        definitions_by_name=definitions_by_name,
    )

