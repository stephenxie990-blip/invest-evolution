from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

import pandas as pd

from app.llm_gateway import LLMGateway, LLMGatewayError, LLMUnavailableError
from app.training.routing_services import TrainingRoutingService
from config import OUTPUT_DIR, PROJECT_ROOT, config, normalize_date
from config.control_plane import resolve_default_llm
from brain.schema_contract import BOUNDED_WORKFLOW_SCHEMA_VERSION
from brain.task_bus import build_bounded_entrypoint, build_bounded_orchestration, build_bounded_policy, build_bounded_response_context, build_readonly_task_bus, build_protocol_response
from invest.models import create_investment_model, resolve_model_config_path
from invest.research import (
    ResearchAttributionEngine,
    ResearchCaseStore,
    ResearchScenarioEngine,
    build_dashboard_projection,
    build_research_snapshot,
    resolve_policy_snapshot,
)
from app.stock_analysis_batch_service import BatchAnalysisViewService
from app.stock_analysis_research_services import (
    build_stock_analysis_research_services,
)
from app.stock_analysis_support_services import (
    build_stock_analysis_support_services,
)
from market_data import DataManager
from market_data.repository import MarketDataRepository

logger = logging.getLogger(__name__)
_TRAINING_ROUTING_SERVICE = TrainingRoutingService()

_STOCK_CODE_RE = re.compile(r"\b(?:sh|sz)\.\d{6}\b|\b\d{6}(?:\.(?:SH|SZ|sh|sz))?\b")
_DAY_COUNT_RE = re.compile(r"(\d{2,4})\s*(?:个)?(?:交易)?(?:日|天)")


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    values = frame[column] if column in frame.columns else pd.Series(index=frame.index, dtype="float64")
    return cast(pd.Series, pd.to_numeric(values, errors="coerce"))


@dataclass
class StockToolPlanStep:
    tool: str
    args: dict[str, Any]
    thought: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "thought": self.thought,
        }


@dataclass
class StockAnalysisStrategy:
    name: str
    display_name: str
    description: str
    required_tools: list[str]
    analysis_steps: list[str]
    entry_conditions: list[str]
    scoring: dict[str, float]
    core_rules: list[str]
    tool_call_plan: list[StockToolPlanStep]
    aliases: list[str]
    planner_prompt: str
    react_enabled: bool
    max_steps: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "required_tools": self.required_tools,
            "analysis_steps": self.analysis_steps,
            "entry_conditions": self.entry_conditions,
            "scoring": self.scoring,
            "core_rules": self.core_rules,
            "tool_call_plan": [step.to_dict() for step in self.tool_call_plan],
            "aliases": self.aliases,
            "planner_prompt": self.planner_prompt,
            "react_enabled": self.react_enabled,
            "max_steps": self.max_steps,
        }


class StockAnalysisService:
    def __init__(
        self,
        db_path: str | Path | None = None,
        strategy_dir: str | Path | None = None,
        *,
        gateway: LLMGateway | None = None,
        model: str = "",
        api_key: str = "",
        api_base: str = "",
        project_root: str | Path | None = None,
        enable_llm_react: bool = True,
        controller_provider: Callable[[], Any] | None = None,
    ):
        self.repository = MarketDataRepository(str(db_path) if db_path else None)
        self.repository.initialize_schema()
        self.strategy_dir = self._init_strategy_dir(strategy_dir)
        self._project_root = self._resolve_project_root(
            project_root=project_root,
            strategy_dir=strategy_dir,
            db_path=db_path,
        )
        self._runtime_state_dir = self._init_runtime_state_dir(self._project_root)
        self._analysis_as_of_date: str = ""
        self._controller_provider = controller_provider
        self._ensure_default_strategies()
        self._tool_registry = self._build_tool_registry()
        self.gateway = gateway or self._build_gateway(model=model, api_key=api_key, api_base=api_base)
        self.enable_llm_react = bool(enable_llm_react)
        self._init_research_services()

    def _init_strategy_dir(self, strategy_dir: str | Path | None) -> Path:
        resolved = Path(strategy_dir or (PROJECT_ROOT / "stock_strategies"))
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _resolve_project_root(
        self,
        *,
        project_root: str | Path | None,
        strategy_dir: str | Path | None,
        db_path: str | Path | None,
    ) -> Path:
        if project_root is not None:
            return Path(project_root)
        if strategy_dir is not None:
            return Path(strategy_dir).expanduser().resolve().parent
        if db_path is not None:
            return Path(db_path).expanduser().resolve().parent
        return PROJECT_ROOT

    def _init_runtime_state_dir(self, project_root: Path) -> Path:
        runtime_state_dir = project_root / "runtime" / "state"
        runtime_state_dir.mkdir(parents=True, exist_ok=True)
        return runtime_state_dir

    def _build_tool_registry(self) -> dict[str, Callable[..., dict[str, Any]]]:
        return {
            "get_daily_history": self.get_daily_history,
            "get_indicator_snapshot": self.get_indicator_snapshot,
            "analyze_trend": self.analyze_trend,
            "analyze_support_resistance": self.analyze_support_resistance,
            "get_capital_flow": self.get_capital_flow,
            "get_intraday_context": self.get_intraday_context,
            "get_realtime_quote": self.get_realtime_quote,
            "get_latest_quote": self.get_realtime_quote,
        }

    def _init_research_services(self) -> None:
        self.case_store = ResearchCaseStore(self._runtime_state_dir)
        self.scenario_engine = ResearchScenarioEngine(self.case_store)
        self.attribution_engine = ResearchAttributionEngine(self.repository)
        support_services = build_stock_analysis_support_services(
            humanize_macd_cross=self._humanize_macd_cross,
        )
        self.batch_analysis_service = support_services.batch_analysis_service
        research_services = build_stock_analysis_research_services(
            case_store=self.case_store,
            scenario_engine=self.scenario_engine,
            attribution_engine=self.attribution_engine,
            logger=logger,
        )
        self.research_resolution_service = (
            research_services.research_resolution_service
        )

    def list_strategies(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.strategy_dir.glob("*.yaml")):
            strategy = self._load_strategy(path)
            if strategy is not None:
                items.append(strategy.to_dict())
        return items

    def get_daily_history(self, query: str, *, days: int = 60) -> dict[str, Any]:
        code, security = self.resolve_security(query)
        frame = self._get_stock_frame(code)
        if frame.empty:
            return {
                "status": "not_found",
                "query": query,
                "code": code,
                "items": [],
                "summary": "未找到历史K线数据",
                "next_actions": ["确认股票代码或先同步本地历史数据"],
                "artifacts": {},
            }
        frame = frame.tail(max(10, int(days)))
        items = frame.to_dict(orient="records")
        return {
            "status": "ok",
            "query": query,
            "security": security,
            "code": code,
            "count": int(len(frame)),
            "items": items,
            "summary": f"已获取 {len(items)} 条历史K线",
            "next_actions": ["可继续做趋势和结构分析"],
            "artifacts": {"last_trade_date": items[-1].get("trade_date") if items else None},
        }

    def get_realtime_quote(self, query: str) -> dict[str, Any]:
        code, security = self.resolve_security(query)
        frame = self._get_stock_frame(code)
        if frame.empty:
            return {
                "status": "not_found",
                "query": query,
                "code": code,
                "summary": "未找到最新报价",
                "next_actions": ["确认股票代码或先同步本地日线数据"],
                "artifacts": {},
            }
        row = dict(frame.tail(1).to_dict(orient="records")[0])
        return {
            "status": "ok",
            "query": query,
            "security": security,
            "code": code,
            "quote": row,
            "summary": f"最新参考价格 {row.get('close')}",
            "next_actions": ["可据此计算入场位和止损位"],
            "artifacts": {"trade_date": row.get("trade_date")},
        }

    @staticmethod
    def _empty_snapshot() -> dict[str, Any]:
        return BatchAnalysisViewService.empty_snapshot()

    def _build_batch_analysis_context(self, frame: pd.DataFrame, code: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return self.batch_analysis_service.build_batch_analysis_context(frame, code)

    def _view_from_snapshot(self, summary: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        return self.batch_analysis_service.view_from_snapshot(summary, snapshot)

    def analyze_trend(self, query: str, *, days: int = 120) -> dict[str, Any]:
        code, security = self.resolve_security(query)
        frame = self._get_stock_frame(code)
        if frame.empty:
            return {
                "status": "not_found",
                "query": query,
                "code": code,
                "summary": "未找到趋势分析所需数据",
                "next_actions": ["先检查数据或改用更长时间范围"],
                "artifacts": {},
            }
        frame = frame.tail(max(30, int(days)))
        summary, snapshot, meta = self._build_batch_analysis_context(frame, code)
        view = self._view_from_snapshot(summary, snapshot)
        trend = view["trend"]
        return {
            "status": "ok",
            "query": query,
            "security": security,
            "code": code,
            "signal": view["signal"],
            "structure": view["structure"],
            "summary": view["summary"],
            "indicator_snapshot": snapshot,
            "trend": trend,
            "observation_summary": f"趋势={view['signal']}, 结构={view['structure']}, MA20={trend.get('ma20', 0.0):.2f}, RSI={trend.get('rsi_14', 50.0):.1f}",
            "next_actions": ["结合支撑阻力、资金流和最新价格生成结论"],
            "artifacts": {"cutoff_date": meta["cutoff"], "latest_trade_date": snapshot.get("latest_trade_date")},
        }

    def get_indicator_snapshot(self, query: str, *, days: int = 180) -> dict[str, Any]:
        code, security = self.resolve_security(query)
        frame = self._get_stock_frame(code)
        if frame.empty:
            return {
                "status": "not_found",
                "query": query,
                "code": code,
                "summary": "未找到指标快照所需数据",
                "next_actions": ["确认股票代码或同步本地行情数据"],
                "artifacts": {},
            }
        frame = frame.tail(max(30, int(days)))
        _, snapshot, _ = self._build_batch_analysis_context(frame, code)
        indicators = dict(snapshot.get("indicators") or {})
        return {
            "status": "ok",
            "query": query,
            "security": security,
            "code": code,
            "days": int(days),
            "snapshot": snapshot,
            "summary": "已生成指标快照",
            "observation_summary": (
                f"RSI={indicators.get('rsi_14')}, MA栈={indicators.get('ma_stack')}, "
                f"MACD={dict(indicators.get('macd_12_26_9') or {}).get('cross', 'neutral')}"
            ),
            "next_actions": ["可继续分析支撑阻力、资金流或最新价格位置"],
            "artifacts": {"latest_trade_date": snapshot.get("latest_trade_date")},
        }

    def analyze_support_resistance(self, query: str, *, days: int = 120) -> dict[str, Any]:
        code, security = self.resolve_security(query)
        frame = self._get_stock_frame(code)
        if frame.empty:
            return {
                "status": "not_found",
                "query": query,
                "code": code,
                "summary": "未找到支撑阻力分析所需数据",
                "next_actions": ["确认股票代码或同步本地行情数据"],
                "artifacts": {},
            }
        frame = frame.tail(max(30, int(days))).copy()
        highs = _numeric_series(frame, "high").dropna()
        lows = _numeric_series(frame, "low").dropna()
        closes = _numeric_series(frame, "close").dropna()
        if highs.empty or lows.empty or closes.empty:
            return {
                "status": "not_found",
                "query": query,
                "code": code,
                "summary": "高低收盘序列不可用",
                "next_actions": ["检查行情数据完整性"],
                "artifacts": {},
            }
        _, snapshot, _ = self._build_batch_analysis_context(frame, code)
        indicators = dict(snapshot.get("indicators") or {})
        latest_close = float(snapshot.get("latest_close") or closes.iloc[-1] or 0.0)
        support_20 = float(lows.tail(20).min()) if len(lows) >= 20 else float(lows.min())
        resistance_20 = float(highs.tail(20).max()) if len(highs) >= 20 else float(highs.max())
        support_60 = float(lows.tail(60).min()) if len(lows) >= 60 else support_20
        resistance_60 = float(highs.tail(60).max()) if len(highs) >= 60 else resistance_20
        atr = float(indicators.get("atr_14") or 0.0)
        distance_to_support = 0.0 if latest_close <= 0 else (latest_close - support_20) / latest_close
        distance_to_resistance = 0.0 if latest_close <= 0 else (resistance_20 - latest_close) / latest_close
        bias = "neutral"
        if latest_close >= resistance_20 * 0.985:
            bias = "breakout_test"
        elif latest_close <= support_20 * 1.015:
            bias = "support_test"
        return {
            "status": "ok",
            "query": query,
            "security": security,
            "code": code,
            "levels": {
                "support_20": round(support_20, 2),
                "resistance_20": round(resistance_20, 2),
                "support_60": round(support_60, 2),
                "resistance_60": round(resistance_60, 2),
                "latest_close": round(latest_close, 2),
                "atr_14": round(atr, 4),
                "distance_to_support_pct": round(distance_to_support * 100.0, 2),
                "distance_to_resistance_pct": round(distance_to_resistance * 100.0, 2),
                "bias": bias,
            },
            "summary": "已识别关键支撑阻力位",
            "observation_summary": (
                f"支撑{support_20:.2f}/阻力{resistance_20:.2f}, "
                f"距支撑{distance_to_support * 100:.1f}%, 距阻力{distance_to_resistance * 100:.1f}%"
            ),
            "next_actions": ["结合趋势、资金流和价格确认入场/风控位置"],
            "artifacts": {"latest_trade_date": snapshot.get("latest_trade_date")},
        }

    def get_capital_flow(self, query: str, *, days: int = 20) -> dict[str, Any]:
        code, security = self.resolve_security(query)
        price_frame = self._get_stock_frame(code)
        if price_frame.empty:
            return {
                "status": "not_found",
                "query": query,
                "code": code,
                "summary": "未找到资金流查询所需行情基线",
                "next_actions": ["确认股票代码或同步本地行情数据"],
                "artifacts": {},
            }
        end_date = str(price_frame["trade_date"].max())
        start_date = str(price_frame.tail(max(1, int(days)))["trade_date"].min())
        frame = self.repository.query_capital_flow_daily(codes=[code], start_date=start_date, end_date=end_date)
        if frame.empty:
            frame = self.repository.query_capital_flow_daily(
                codes=[code],
                end_date=self._current_analysis_cutoff(),
            ).sort_values("trade_date").tail(max(1, int(days)))
        if frame.empty:
            return {
                "status": "no_data",
                "query": query,
                "security": security,
                "code": code,
                "summary": "本地暂无资金流数据",
                "next_actions": ["可继续依赖价格与指标工具分析，或补充资金流数据"],
                "artifacts": {"start_date": start_date, "end_date": end_date},
            }
        frame = frame.sort_values("trade_date").tail(max(1, int(days)))
        latest = dict(frame.tail(1).to_dict(orient="records")[0])
        main_sum = float(_numeric_series(frame, "main_net_inflow").fillna(0).sum())
        ratio_mean = float(_numeric_series(frame, "main_net_inflow_ratio").fillna(0).mean())
        direction = "inflow" if main_sum > 0 else "outflow" if main_sum < 0 else "flat"
        return {
            "status": "ok",
            "query": query,
            "security": security,
            "code": code,
            "count": int(len(frame)),
            "items": frame.to_dict(orient="records"),
            "metrics": {
                "main_net_inflow_sum": round(main_sum, 2),
                "main_net_inflow_ratio_avg": round(ratio_mean, 4),
                "latest_trade_date": latest.get("trade_date"),
                "direction": direction,
            },
            "summary": f"近{len(frame)}日主力资金{direction}",
            "observation_summary": f"主力净流入合计={main_sum:.2f}, 平均占比={ratio_mean:.2f}",
            "next_actions": ["结合趋势与支撑阻力判断资金是否确认当前结构"],
            "artifacts": {"start_date": start_date, "end_date": end_date},
        }

    def get_intraday_context(self, query: str, *, days: int = 5) -> dict[str, Any]:
        code, security = self.resolve_security(query)
        daily = self._get_stock_frame(code)
        if daily.empty:
            return {
                "status": "not_found",
                "query": query,
                "code": code,
                "summary": "未找到分时上下文查询所需行情基线",
                "next_actions": ["确认股票代码或同步本地行情数据"],
                "artifacts": {},
            }
        end_date = str(daily["trade_date"].max())
        start_date = str(daily.tail(max(1, int(days)))["trade_date"].min())
        frame = self.repository.query_intraday_bars_60m(codes=[code], start_date=start_date, end_date=end_date)
        if frame.empty:
            return {
                "status": "no_data",
                "query": query,
                "security": security,
                "code": code,
                "summary": "本地暂无60分钟数据",
                "next_actions": ["仍可使用日线与指标工具完成分析"],
                "artifacts": {"start_date": start_date, "end_date": end_date},
            }
        latest_day = str(frame["trade_date"].max())
        latest = cast(pd.DataFrame, frame[frame["trade_date"] == latest_day].copy())
        latest = cast(pd.DataFrame, latest.sort_values(by=["bar_time"]))
        close_series = _numeric_series(latest, "close")
        high_series = _numeric_series(latest, "high")
        low_series = _numeric_series(latest, "low")
        first_close = float(close_series.iloc[0])
        last_close = float(close_series.iloc[-1])
        day_range = float(high_series.max() - low_series.min())
        intraday_bias = "up" if last_close > first_close else "down" if last_close < first_close else "flat"
        return {
            "status": "ok",
            "query": query,
            "security": security,
            "code": code,
            "count": int(len(latest)),
            "bars": latest.to_dict(orient="records"),
            "metrics": {
                "latest_trade_date": latest_day,
                "first_close": round(first_close, 2),
                "last_close": round(last_close, 2),
                "day_range": round(day_range, 2),
                "intraday_bias": intraday_bias,
            },
            "summary": f"最新60分钟结构偏{intraday_bias}",
            "observation_summary": f"首收={first_close:.2f}, 末收={last_close:.2f}, 振幅={day_range:.2f}",
            "next_actions": ["必要时用来确认日线结论是否得到短周期配合"],
            "artifacts": {"start_date": start_date, "end_date": end_date},
        }

    def ask_stock(
        self,
        *,
        question: str,
        query: str,
        strategy: str = "chan_theory",
        days: int = 60,
        as_of_date: str = "",
    ) -> dict[str, Any]:
        strategy_name, strategy_source = self._resolve_strategy_name(question=question, strategy=strategy)
        resolved_days = self._infer_days(question=question, default_days=days)
        strat = self.load_strategy(strategy_name)
        code, security = self.resolve_security(query)
        effective_as_of_date = self._resolve_effective_as_of_date(code, as_of_date)
        with self._analysis_scope(effective_as_of_date):
            recommended_plan = [step.to_dict() for step in self._build_plan(strategy=strat, query=code, days=resolved_days)]
            allowed_tools = self._strategy_allowed_tools(strat)
            execution = self._run_react_executor(question=question, query=code, security=security, strategy=strat, days=resolved_days)
            coverage = self._build_execution_coverage(strategy=strat, execution=execution, recommended_plan=recommended_plan, allowed_tools=allowed_tools)
            derived = self._derive_signals(execution)
        research_resolution = self._resolve_ask_stock_research_outputs(
            question=question,
            query=query,
            strategy=strat,
            strategy_source=strategy_source,
            code=code,
            security=security,
            requested_as_of_date=as_of_date,
            effective_as_of_date=effective_as_of_date,
            days=resolved_days,
            execution=execution,
            derived=derived,
        )
        dashboard = research_resolution["dashboard"]
        research_payload = research_resolution["research"]
        model_bridge_payload = research_resolution["model_bridge"]
        policy_id = str(research_resolution.get("policy_id") or "")
        research_case_id = str(research_resolution.get("research_case_id") or "")
        attribution_id = str(research_resolution.get("attribution_id") or "")
        task_bus_artifacts = self._build_ask_stock_task_artifacts(
            code=code,
            strategy_name=strat.name,
            strategy_source=strategy_source,
            derived=derived,
            execution=execution,
        )
        task_bus = self._build_ask_stock_task_bus(
            question=question,
            query=query,
            execution=execution,
            recommended_plan=recommended_plan,
            coverage=coverage,
            task_bus_artifacts=task_bus_artifacts,
        )
        bounded_context = self._build_ask_stock_bounded_context(
            execution=execution,
            coverage=coverage,
            task_bus_artifacts=task_bus_artifacts,
            policy_id=policy_id,
            research_case_id=research_case_id,
            attribution_id=attribution_id,
        )
        payload = self._build_ask_stock_payload(
            question=question,
            query=query,
            code=code,
            as_of_date=as_of_date,
            effective_as_of_date=effective_as_of_date,
            security=security,
            strategy=strat,
            strategy_source=strategy_source,
            days=resolved_days,
            execution=execution,
            allowed_tools=allowed_tools,
            recommended_plan=recommended_plan,
            coverage=coverage,
            derived=derived,
            dashboard=dashboard,
            research_payload=research_payload,
            model_bridge_payload=model_bridge_payload,
            task_bus=task_bus,
            policy_id=policy_id,
            research_case_id=research_case_id,
            attribution_id=attribution_id,
        )
        return build_protocol_response(
            payload=payload,
            protocol=bounded_context["protocol"],
            task_bus=task_bus,
            artifacts=bounded_context["artifacts"],
            coverage=bounded_context["coverage"],
            artifact_taxonomy=bounded_context["artifact_taxonomy"],
            default_reply="已完成问股分析。",
        )

    def _build_ask_stock_task_artifacts(
        self,
        *,
        code: str,
        strategy_name: str,
        strategy_source: str,
        derived: dict[str, Any],
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "code": code,
            "strategy": strategy_name,
            "strategy_source": strategy_source,
            "latest_close": derived.get("latest_close"),
            "gap_fill_applied": bool(dict(execution.get("gap_fill") or {}).get("applied")),
        }

    def _build_ask_stock_task_bus(
        self,
        *,
        question: str,
        query: str,
        execution: dict[str, Any],
        recommended_plan: list[dict[str, Any]],
        coverage: dict[str, Any],
        task_bus_artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        return build_readonly_task_bus(
            intent="stock_analysis",
            operation="ask_stock",
            user_goal=question or query,
            mode=execution["mode"],
            available_tools=sorted(self._tool_registry.keys()),
            recommended_plan=recommended_plan,
            tool_calls=execution["tool_calls"],
            artifacts=task_bus_artifacts,
            coverage=coverage,
            status="ok",
        )

    def _build_ask_stock_bounded_context(
        self,
        *,
        execution: dict[str, Any],
        coverage: dict[str, Any],
        task_bus_artifacts: dict[str, Any],
        policy_id: str,
        research_case_id: str,
        attribution_id: str,
    ) -> dict[str, Any]:
        return build_bounded_response_context(
            schema_version=BOUNDED_WORKFLOW_SCHEMA_VERSION,
            domain="stock",
            operation="ask_stock",
            artifacts={
                **task_bus_artifacts,
                "policy_id": policy_id,
                "research_case_id": research_case_id,
                "attribution_id": attribution_id,
            },
            workflow=list(execution.get("workflow") or []),
            phase_stats=dict(execution.get("phase_stats") or {}),
            coverage=coverage,
        )

    def _build_ask_stock_payload(
        self,
        *,
        question: str,
        query: str,
        code: str,
        as_of_date: str,
        effective_as_of_date: str,
        security: dict[str, Any],
        strategy: StockAnalysisStrategy,
        strategy_source: str,
        days: int,
        execution: dict[str, Any],
        allowed_tools: list[str],
        recommended_plan: list[dict[str, Any]],
        coverage: dict[str, Any],
        derived: dict[str, Any],
        dashboard: dict[str, Any],
        research_payload: dict[str, Any],
        model_bridge_payload: dict[str, Any],
        task_bus: dict[str, Any],
        policy_id: str,
        research_case_id: str,
        attribution_id: str,
    ) -> dict[str, Any]:
        requested_as_of_date = self._normalize_as_of_date(as_of_date)
        identifiers = {
            "policy_id": policy_id,
            "research_case_id": research_case_id,
            "attribution_id": attribution_id,
        }
        request = {
            "question": question,
            "query": query,
            "normalized_query": code,
            "requested_as_of_date": requested_as_of_date,
            "as_of_date": effective_as_of_date,
        }
        return {
            "status": "ok",
            "question": question,
            "query": query,
            "normalized_query": code,
            "as_of_date": effective_as_of_date,
            "requested_as_of_date": requested_as_of_date,
            "policy_id": policy_id,
            "research_case_id": research_case_id,
            "attribution_id": attribution_id,
            "request": request,
            "identifiers": dict(identifiers),
            "resolved": security,
            "resolved_security": security,
            "resolved_entities": {"security": security},
            "entrypoint": build_bounded_entrypoint(
                kind="commander_tool_service",
                runtime_tool="invest_ask_stock",
                runtime_method="CommanderRuntime.ask_stock",
                service="StockAnalysisService",
                domain="stock",
                agent_kind="bounded_stock_agent",
                standalone_agent=False,
                meeting_path=False,
                agent_system="commander_brain_tooling",
            ),
            "strategy": strategy.to_dict(),
            "strategy_source": strategy_source,
            "days": days,
            "task_bus": task_bus,
            "orchestration": build_bounded_orchestration(
                mode=execution["mode"],
                available_tools=sorted(self._tool_registry.keys()),
                allowed_tools=allowed_tools,
                workflow=list(execution.get("workflow") or []),
                phase_stats=dict(execution.get("phase_stats") or {}),
                policy=build_bounded_policy(
                    source="yaml_strategy",
                    agent_kind="bounded_stock_agent",
                    workflow_mode="llm_react_with_yaml_gap_fill",
                    react_enabled=bool(strategy.react_enabled),
                    tool_catalog_scope="strategy_restricted",
                    fixed_boundary=True,
                    fixed_workflow=True,
                ),
                extra={
                    "required_tools": list(strategy.required_tools),
                    "recommended_plan": recommended_plan,
                    "tool_plan": execution["plan"],
                    "tool_calls": execution["tool_calls"],
                    "step_count": len(execution["tool_calls"]),
                    "llm_reasoning": execution.get("final_reasoning", ""),
                    "fallback_used": bool(execution.get("fallback_used", execution["mode"] == "yaml_react_like")),
                    "gap_fill": dict(execution.get("gap_fill") or {}),
                    "coverage": coverage,
                },
            ),
            "analysis": {
                "tool_results": execution["results"],
                "result_sequence": execution["result_sequence"],
                "derived_signals": derived,
                "model_bridge": {**dict(model_bridge_payload), "identifiers": dict(identifiers)},
            },
            "research": {**dict(research_payload), "identifiers": dict(identifiers)},
            "dashboard": dashboard,
        }

    def _resolve_ask_stock_research_outputs(
        self,
        *,
        question: str,
        query: str,
        strategy: StockAnalysisStrategy,
        strategy_source: str,
        code: str,
        security: dict[str, Any],
        requested_as_of_date: str,
        effective_as_of_date: str,
        days: int,
        execution: dict[str, Any],
        derived: dict[str, Any],
    ) -> dict[str, Any]:
        research_bridge = self._build_research_bridge(
            code=code,
            security=security,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            days=days,
            derived=derived,
        )
        return self.research_resolution_service.resolve_outputs(
            research_bridge=research_bridge,
            question=question,
            query=query,
            strategy=strategy,
            strategy_source=strategy_source,
            code=code,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            execution=execution,
            derived=derived,
            dashboard_projection_builder=build_dashboard_projection,
        )

    @staticmethod
    def _normalize_as_of_date(value: str | None = None) -> str:
        raw = str(value or "").strip()
        return normalize_date(raw) if raw else ""

    def _current_analysis_cutoff(self) -> str | None:
        return self._normalize_as_of_date(self._analysis_as_of_date) or None

    def _resolve_effective_as_of_date(self, code: str, requested_as_of_date: str = "") -> str:
        requested = self._normalize_as_of_date(requested_as_of_date)
        frame = self.repository.get_stock(code, cutoff_date=requested or None)
        if frame.empty:
            return requested
        trade_dates = frame.get("trade_date")
        if trade_dates is None or len(trade_dates) == 0:
            return requested
        return normalize_date(str(pd.Series(trade_dates).astype(str).max()))

    @contextmanager
    def _analysis_scope(self, as_of_date: str | None = None):
        previous = self._analysis_as_of_date
        self._analysis_as_of_date = self._normalize_as_of_date(as_of_date)
        try:
            yield self._analysis_as_of_date
        finally:
            self._analysis_as_of_date = previous

    def _get_stock_frame(self, code: str) -> pd.DataFrame:
        frame = self.repository.get_stock(code, cutoff_date=self._current_analysis_cutoff())
        if frame.empty:
            return frame
        result = frame.copy()
        if "trade_date" in result.columns:
            result["trade_date"] = result["trade_date"].astype(str)
            result = result.sort_values("trade_date").reset_index(drop=True)
        return result

    def _resolve_live_controller(self) -> Any | None:
        if self._controller_provider is None:
            return None
        try:
            return self._controller_provider()
        except Exception:
            logger.warning("Failed to resolve live controller for ask_stock research bridge", exc_info=True)
            return None

    def _ensure_query_in_stock_data(
        self,
        *,
        stock_data: dict[str, pd.DataFrame],
        code: str,
        cutoff_date: str,
    ) -> dict[str, pd.DataFrame]:
        enriched = dict(stock_data or {})
        if code in enriched:
            return enriched
        query_frame = self.repository.get_stock(code, cutoff_date=cutoff_date)
        if query_frame.empty:
            return enriched
        query_frame = query_frame.copy()
        if "trade_date" in query_frame.columns:
            query_frame["trade_date"] = query_frame["trade_date"].astype(str)
            query_frame = query_frame.sort_values("trade_date").reset_index(drop=True)
        enriched[code] = query_frame
        return enriched

    def _build_research_bridge(
        self,
        *,
        code: str,
        security: dict[str, Any],
        requested_as_of_date: str,
        effective_as_of_date: str,
        days: int,
        derived: dict[str, Any],
    ) -> dict[str, Any]:
        controller = self._resolve_live_controller()
        latest_live_date = self._resolve_effective_as_of_date(code, "")
        replay_mode = bool(self._normalize_as_of_date(requested_as_of_date)) and bool(latest_live_date) and str(effective_as_of_date) < str(latest_live_date)
        current_model = str(getattr(controller, "model_name", getattr(config, "investment_model", "momentum")) or "momentum")
        fallback_config_path = str(resolve_model_config_path(current_model))
        base_config_path = str(getattr(controller, "model_config_path", getattr(config, "investment_model_config", fallback_config_path)) or fallback_config_path)
        try:
            base_config_path = str(Path(base_config_path).expanduser().resolve())
        except Exception:
            base_config_path = fallback_config_path
        current_params = dict(getattr(controller, "current_params", {}) or {})
        if replay_mode:
            current_params = {}
        stock_count = max(10, int(getattr(config, "max_stocks", 50) or 50))
        query_history_frame = self.repository.get_stock(code, cutoff_date=effective_as_of_date)
        query_history_days = int(len(query_history_frame))
        min_history_days = max(30, min(60, query_history_days if query_history_days > 0 else int(days or 60)))
        lookback_days = max(60, int(days or 60))
        parameter_source = "config_default_replay_safe" if replay_mode else "live_controller" if controller is not None else "config_default"
        data_manager = DataManager(
            db_path=str(self.repository.db_path),
            prefer_offline=True,
            allow_mock_fallback=False,
        )
        try:
            stock_data = data_manager.load_stock_data(
                cutoff_date=effective_as_of_date,
                stock_count=stock_count,
                min_history_days=min_history_days,
                include_capital_flow=False,
            )
        except Exception as exc:
            logger.warning("Research bridge data load failed for %s", code, exc_info=True)
            return {
                "status": "unavailable",
                "error": str(exc),
                "details": {
                    "stage": "load_stock_data",
                    "parameter_source": parameter_source,
                    "as_of_date": effective_as_of_date,
                },
            }
        stock_data = self._ensure_query_in_stock_data(
            stock_data=stock_data,
            code=code,
            cutoff_date=effective_as_of_date,
        )
        if not stock_data:
            return {
                "status": "unavailable",
                "error": "research bridge returned empty stock universe",
                "details": {
                    "stage": "empty_universe",
                    "parameter_source": parameter_source,
                    "as_of_date": effective_as_of_date,
                },
            }
        allowed_models = [
            str(item).strip()
            for item in (
                getattr(controller, "experiment_allowed_models", None)
                or getattr(controller, "model_routing_allowed_models", None)
                or getattr(config, "model_routing_allowed_models", None)
                or []
            )
            if str(item).strip()
        ]
        routing_enabled = bool(getattr(controller, "model_routing_enabled", getattr(config, "model_routing_enabled", True)))
        routing_mode = str(getattr(controller, "model_routing_mode", getattr(config, "model_routing_mode", "rule")) or "rule").strip().lower()
        try:
            decision = _TRAINING_ROUTING_SERVICE.route_model(
                controller,
                stock_data=stock_data,
                cutoff_date=effective_as_of_date,
                current_model=current_model,
                data_manager=data_manager,
                output_dir=getattr(controller, "output_dir", OUTPUT_DIR),
                allowed_models=allowed_models or None,
                current_cycle_id=getattr(controller, "current_cycle_id", None),
                safe_leaderboard_refresh=True,
            )
        except Exception as exc:
            logger.warning("Research bridge routing failed for %s", code, exc_info=True)
            return {
                "status": "unavailable",
                "error": str(exc),
                "details": {
                    "stage": "routing",
                    "parameter_source": parameter_source,
                    "as_of_date": effective_as_of_date,
                },
            }
        selected_model = str(getattr(decision, "selected_model", "") or current_model or "momentum")
        selected_config = str(getattr(decision, "selected_config", "") or resolve_model_config_path(selected_model))
        try:
            selected_config = str(Path(selected_config).expanduser().resolve())
        except Exception:
            selected_config = str(selected_config)
        runtime_overrides = current_params if (not replay_mode and selected_model == current_model and selected_config == base_config_path) else {}
        try:
            investment_model = create_investment_model(
                selected_model,
                config_path=selected_config,
                runtime_overrides=runtime_overrides,
            )
            model_output = investment_model.process(stock_data, effective_as_of_date)
        except Exception as exc:
            logger.warning("Research bridge model execution failed for %s", code, exc_info=True)
            return {
                "status": "unavailable",
                "error": str(exc),
                "details": {
                    "stage": "model_process",
                    "selected_model": selected_model,
                    "selected_config": selected_config,
                },
            }
        routing_context = {
            "as_of_date": effective_as_of_date,
            "requested_as_of_date": self._normalize_as_of_date(requested_as_of_date),
            "routing_mode": routing_mode if routing_enabled else "off",
            "current_model": current_model,
            "selected_model": selected_model,
            "selected_config": selected_config,
            "decision_source": str(getattr(decision, "decision_source", "") or ""),
            "regime": str(getattr(decision, "regime", "") or "unknown"),
            "regime_confidence": float(getattr(decision, "regime_confidence", 0.0) or 0.0),
            "decision_confidence": float(getattr(decision, "decision_confidence", 0.0) or 0.0),
            "allowed_models": allowed_models or [selected_model],
            "hold_current": bool(getattr(decision, "hold_current", False)),
            "hold_reason": str(getattr(decision, "hold_reason", "") or ""),
        }
        data_lineage = {
            "db_path": str(self.repository.db_path),
            "requested_as_of_date": self._normalize_as_of_date(requested_as_of_date),
            "effective_as_of_date": effective_as_of_date,
            "data_source": str(getattr(data_manager, "last_source", "unknown") or "unknown"),
            "data_resolution": dict(getattr(data_manager, "last_resolution", {}) or {}),
            "stock_count": len(stock_data),
            "min_history_days": min_history_days,
            "lookback_days": lookback_days,
        }
        snapshot = build_research_snapshot(
            model_output=model_output,
            security=security,
            query_code=code,
            stock_data=stock_data,
            routing_context=routing_context,
            data_lineage=data_lineage,
            derived_signals=derived,
        )
        policy = resolve_policy_snapshot(
            investment_model=investment_model,
            routing_context=routing_context,
            data_window={
                "as_of_date": effective_as_of_date,
                "lookback_days": lookback_days,
                "simulation_days": int(getattr(config, "simulation_days", 30) or 30),
                "universe_definition": f"stock_count={stock_count}|min_history_days={min_history_days}",
                "stock_universe_size": len(stock_data),
            },
            metadata={
                "parameter_source": parameter_source,
                "controller_bound": bool(controller is not None),
                "replay_mode": replay_mode,
                "requested_as_of_date": self._normalize_as_of_date(requested_as_of_date),
                "effective_as_of_date": effective_as_of_date,
            },
        )
        return {
            "status": "ok",
            "controller_bound": bool(controller is not None),
            "replay_mode": replay_mode,
            "parameter_source": parameter_source,
            "routing_decision": decision.to_dict(),
            "model_output": model_output,
            "snapshot": snapshot,
            "policy": policy,
        }

    def resolve_security(self, query: str) -> tuple[str, dict[str, Any]]:
        raw = str(query or "").strip()
        if not raw:
            raise ValueError("query is required")
        match = _STOCK_CODE_RE.search(raw)
        if match:
            token = match.group(0)
            code = self._normalize_code(token)
            sec = self.repository.query_securities([code])
            return code, (sec[0] if sec else {"code": code, "name": "", "industry": ""})
        candidates = self.repository.query_securities()
        for row in candidates:
            name = str(row.get("name") or "")
            code = str(row.get("code") or "")
            if raw == name or raw == code or raw in name or name in raw or code in raw:
                return code, row
        raise ValueError(f"无法识别股票: {raw}")

    def load_strategy(self, name: str) -> StockAnalysisStrategy:
        path = self.strategy_dir / f"{str(name or 'chan_theory').strip()}.yaml"
        strategy = self._load_strategy(path)
        if strategy is None:
            raise FileNotFoundError(f"stock strategy not found: {name}")
        return strategy

    def _build_gateway(self, *, model: str, api_key: str, api_base: str) -> LLMGateway:
        resolved = resolve_default_llm("fast", project_root=self._project_root)
        return LLMGateway(
            model=model or resolved.model or "",
            api_key=api_key or resolved.api_key or "",
            api_base=api_base or resolved.api_base or "",
            timeout=60,
            max_retries=2,
            unavailable_message=str(resolved.issue or ""),
        )

    def _resolve_strategy_name(self, *, question: str, strategy: str) -> tuple[str, str]:
        explicit = str(strategy or "").strip()
        if explicit and explicit not in {"auto"}:
            if explicit != "chan_theory":
                return explicit, "explicit"
        low = str(question or "").lower()
        for item in self.list_strategies():
            names = [str(item.get("name") or ""), str(item.get("display_name") or "")]
            names.extend([str(x) for x in item.get("aliases") or []])
            for alias in names:
                token = alias.strip().lower()
                if token and token in low:
                    return str(item.get("name") or explicit or "chan_theory"), "inferred"
        return explicit or "chan_theory", "default"

    @staticmethod
    def _infer_days(*, question: str, default_days: int) -> int:
        text = str(question or "")
        match = _DAY_COUNT_RE.search(text)
        if not match:
            return max(30, int(default_days or 60))
        return max(30, min(500, int(match.group(1))))

    def _run_react_executor(
        self,
        *,
        question: str,
        query: str,
        security: dict[str, Any],
        strategy: StockAnalysisStrategy,
        days: int,
    ) -> dict[str, Any]:
        plan = self._build_plan(strategy=strategy, query=query, days=days)
        allowed_tools = self._strategy_allowed_tools(strategy)
        if self.enable_llm_react and strategy.react_enabled and self.gateway.available:
            try:
                llm_execution = self._execute_plan_with_llm(
                    question=question,
                    query=query,
                    security=security,
                    strategy=strategy,
                    days=days,
                    allowed_tools=allowed_tools,
                )
                if llm_execution.get("tool_calls"):
                    return self._apply_yaml_gap_fill(
                        execution=llm_execution,
                        strategy=strategy,
                        plan=plan,
                        allowed_tools=allowed_tools,
                    )
            except Exception as exc:  # pragma: no cover
                logger.warning("stock llm react failed, fallback to yaml plan: %s", exc)
        return self._execute_plan_deterministic(plan=plan, fallback_reason="llm_react_unavailable_or_empty")

    def _build_plan(self, *, strategy: StockAnalysisStrategy, query: str, days: int) -> list[StockToolPlanStep]:
        plan = strategy.tool_call_plan or []
        if plan:
            rendered: list[StockToolPlanStep] = []
            for step in plan:
                rendered.append(
                    StockToolPlanStep(
                        tool=step.tool,
                        thought=step.thought,
                        args=self._render_template_args(step.args, query=query, days=days),
                    )
                )
            return rendered

        derived: list[StockToolPlanStep] = []
        for tool in strategy.required_tools:
            if tool == "get_daily_history":
                derived.append(StockToolPlanStep(tool=tool, thought="先拉取历史K线，建立价格结构基础。", args={"query": query, "days": days}))
            elif tool == "analyze_trend":
                derived.append(StockToolPlanStep(tool=tool, thought="继续计算趋势、均线与动量信号。", args={"query": query, "days": max(120, days)}))
            elif tool in {"get_realtime_quote", "get_latest_quote"}:
                derived.append(StockToolPlanStep(tool="get_realtime_quote", thought="最后确认最新收盘/报价。", args={"query": query}))
        return derived

    def _strategy_allowed_tools(self, strategy: StockAnalysisStrategy) -> list[str]:
        allowed: list[str] = []
        seen: set[str] = set()
        for step in list(strategy.tool_call_plan or []):
            tool_name = str(step.tool or "").strip()
            if tool_name and tool_name in self._tool_registry and tool_name not in seen:
                allowed.append(tool_name)
                seen.add(tool_name)
        for tool_name in list(strategy.required_tools or []):
            normalized = "get_realtime_quote" if tool_name == "get_latest_quote" else str(tool_name or "").strip()
            if normalized and normalized in self._tool_registry and normalized not in seen:
                allowed.append(normalized)
                seen.add(normalized)
        return allowed

    @staticmethod
    def _normalize_tool_name(tool_name: str) -> str:
        return "get_realtime_quote" if str(tool_name or "").strip() == "get_latest_quote" else str(tool_name or "").strip()

    def _action_signature(self, tool_name: str, args: dict[str, Any]) -> str:
        payload = {
            "tool": self._normalize_tool_name(tool_name),
            "args": dict(args or {}),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _missing_plan_steps(
        self,
        *,
        execution: dict[str, Any],
        recommended_plan: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        executed_signatures = {
            self._action_signature(str(dict(item.get("action") or {}).get("tool") or ""), dict(dict(item.get("action") or {}).get("args") or {}))
            for item in list(execution.get("tool_calls") or [])
        }
        missing: list[dict[str, Any]] = []
        for item in list(recommended_plan or []):
            step = dict(item or {})
            tool_name = self._normalize_tool_name(str(step.get("tool") or ""))
            args = dict(step.get("args") or {})
            if self._action_signature(tool_name, args) not in executed_signatures:
                missing.append({
                    "tool": tool_name,
                    "args": args,
                    "thought": str(step.get("thought") or self._default_thought(tool_name)),
                })
        return missing

    def _build_execution_coverage(
        self,
        *,
        strategy: StockAnalysisStrategy,
        execution: dict[str, Any],
        recommended_plan: list[dict[str, Any]],
        allowed_tools: list[str],
    ) -> dict[str, Any]:
        required_tools = self._strategy_allowed_tools(strategy)
        planned_tools: list[str] = []
        for item in list(recommended_plan or []):
            tool_name = self._normalize_tool_name(str(dict(item).get("tool") or ""))
            if tool_name and tool_name not in planned_tools:
                planned_tools.append(tool_name)
        executed_tools: list[str] = []
        executed_signatures: set[str] = set()
        for item in list(execution.get("tool_calls") or []):
            action = dict(item.get("action") or {})
            tool_name = self._normalize_tool_name(str(action.get("tool") or ""))
            args = dict(action.get("args") or {})
            if tool_name:
                executed_tools.append(tool_name)
                executed_signatures.add(self._action_signature(tool_name, args))
        unique_executed_tools = list(dict.fromkeys(executed_tools))
        missing_required = [tool for tool in required_tools if tool not in unique_executed_tools]
        out_of_policy = [tool for tool in unique_executed_tools if tool not in allowed_tools]
        missing_planned_steps: list[dict[str, Any]] = []
        for item in list(recommended_plan or []):
            step = dict(item or {})
            tool_name = self._normalize_tool_name(str(step.get("tool") or ""))
            args = dict(step.get("args") or {})
            signature = self._action_signature(tool_name, args)
            if signature not in executed_signatures:
                missing_planned_steps.append({
                    "tool": tool_name,
                    "args": args,
                    "thought": str(step.get("thought") or self._default_thought(tool_name)),
                })
        required_coverage = 1.0 if not required_tools else round((len(required_tools) - len(missing_required)) / len(required_tools), 3)
        planned_coverage = 1.0 if not recommended_plan else round((len(recommended_plan) - len(missing_planned_steps)) / len(recommended_plan), 3)
        return {
            "required_tools": required_tools,
            "allowed_tools": list(allowed_tools),
            "planned_tools": planned_tools,
            "executed_tools": unique_executed_tools,
            "missing_required_tools": missing_required,
            "missing_planned_steps": missing_planned_steps,
            "out_of_policy_tools": out_of_policy,
            "required_tool_coverage": required_coverage,
            "planned_step_count": len(list(recommended_plan or [])),
            "executed_step_count": len(list(execution.get("tool_calls") or [])),
            "planned_step_coverage": planned_coverage,
        }

    def _execute_plan_deterministic(self, *, plan: list[StockToolPlanStep], fallback_reason: str = "") -> dict[str, Any]:
        tool_calls: list[dict[str, Any]] = []
        result_sequence: list[dict[str, Any]] = []
        results: dict[str, Any] = {}
        for idx, step in enumerate(plan, start=1):
            result = self._run_stock_tool(step.tool, step.args)
            result_sequence.append({"tool": step.tool, "result": result, "source": "yaml_plan"})
            results[step.tool] = result
            tool_calls.append({
                "step": idx,
                "source": "yaml_plan",
                "thought": step.thought or self._default_thought(step.tool),
                "action": {"tool": step.tool, "args": step.args},
                "observation": self._summarize_observation(step.tool, result),
                "raw_status": str(result.get("status", "ok")) if isinstance(result, dict) else "ok",
            })
        return {
            "mode": "yaml_react_like",
            "plan": [step.to_dict() for step in plan],
            "tool_calls": tool_calls,
            "results": results,
            "result_sequence": result_sequence,
            "final_reasoning": "",
            "fallback_used": True,
            "workflow": ["yaml_strategy_loaded", "yaml_plan_execute", "finalize"],
            "phase_stats": {
                "llm_react_steps": 0,
                "yaml_gap_fill_steps": 0,
                "yaml_planned_steps": len(tool_calls),
                "total_steps": len(tool_calls),
            },
            "gap_fill": {
                "enabled": False,
                "applied": False,
                "reason": fallback_reason or "yaml_only_mode",
                "missing_plan_steps_before_fill": [],
                "filled_steps": [],
                "missing_plan_steps_after_fill": [],
            },
        }

    def _execute_plan_with_llm(
        self,
        *,
        question: str,
        query: str,
        security: dict[str, Any],
        strategy: StockAnalysisStrategy,
        days: int,
        allowed_tools: list[str],
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._stock_system_prompt(strategy)},
            {
                "role": "user",
                "content": self._build_stock_user_prompt(
                    question=question,
                    query=query,
                    security=security,
                    strategy=strategy,
                    days=days,
                ),
            },
        ]
        tool_defs = self._stock_tool_definitions(allowed_tools=allowed_tools)
        tool_calls: list[dict[str, Any]] = []
        result_sequence: list[dict[str, Any]] = []
        results: dict[str, Any] = {}
        final_reasoning = ""
        max_steps = max(1, min(8, int(strategy.max_steps or 4)))
        for _ in range(max_steps):
            try:
                response = self.gateway.completion_raw(
                    messages=messages,
                    temperature=0.2,
                    max_tokens=1400,
                    tools=tool_defs,
                    tool_choice="auto",
                )
            except (LLMUnavailableError, LLMGatewayError) as exc:
                logger.warning("stock llm planner unavailable: %s", exc)
                raise
            choice = response.choices[0].message
            llm_tool_calls = getattr(choice, "tool_calls", None) or []
            if llm_tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": getattr(choice, "content", "") or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                            }
                            for tc in llm_tool_calls
                        ],
                    }
                )
                for tc in llm_tool_calls:
                    args = self._parse_tool_args(tc.function.arguments)
                    result = self._run_stock_tool(tc.function.name, args)
                    results[tc.function.name] = result
                    result_sequence.append({"tool": tc.function.name, "result": result, "source": "llm_react"})
                    tool_calls.append({
                        "step": len(tool_calls) + 1,
                        "source": "llm_react",
                        "thought": (getattr(choice, "content", "") or "").strip() or self._default_thought(tc.function.name),
                        "action": {"tool": tc.function.name, "args": args},
                        "observation": self._summarize_observation(tc.function.name, result),
                        "raw_status": str(result.get("status", "ok")),
                    })
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.function.name,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                continue
            final_reasoning = (getattr(choice, "content", "") or "").strip()
            break

        if not tool_calls:
            raise RuntimeError("llm react produced no tool calls")
        return {
            "mode": "llm_react",
            "plan": [
                {
                    "tool": call["action"]["tool"],
                    "args": call["action"]["args"],
                    "thought": call["thought"],
                }
                for call in tool_calls
            ],
            "tool_calls": tool_calls,
            "results": results,
            "result_sequence": result_sequence,
            "final_reasoning": final_reasoning,
            "fallback_used": False,
            "workflow": ["yaml_strategy_loaded", "llm_react", "gap_audit_pending"],
            "phase_stats": {
                "llm_react_steps": len(tool_calls),
                "yaml_gap_fill_steps": 0,
                "yaml_planned_steps": 0,
                "total_steps": len(tool_calls),
            },
            "gap_fill": {
                "enabled": True,
                "applied": False,
                "reason": "awaiting_gap_audit",
                "missing_plan_steps_before_fill": [],
                "filled_steps": [],
                "missing_plan_steps_after_fill": [],
            },
        }

    def _apply_yaml_gap_fill(
        self,
        *,
        execution: dict[str, Any],
        strategy: StockAnalysisStrategy,
        plan: list[StockToolPlanStep],
        allowed_tools: list[str],
    ) -> dict[str, Any]:
        recommended_plan = [step.to_dict() for step in plan]
        pre_coverage = self._build_execution_coverage(
            strategy=strategy,
            execution=execution,
            recommended_plan=recommended_plan,
            allowed_tools=allowed_tools,
        )
        missing_steps = self._missing_plan_steps(execution=execution, recommended_plan=recommended_plan)
        tool_calls = list(execution.get("tool_calls") or [])
        result_sequence = list(execution.get("result_sequence") or [])
        results = dict(execution.get("results") or {})
        filled_steps: list[dict[str, Any]] = []
        for step in missing_steps:
            tool_name = self._normalize_tool_name(str(step.get("tool") or ""))
            args = dict(step.get("args") or {})
            result = self._run_stock_tool(tool_name, args)
            results[tool_name] = result
            filled_step = {
                "tool": tool_name,
                "args": args,
                "thought": str(step.get("thought") or self._default_thought(tool_name)),
            }
            filled_steps.append(filled_step)
            result_sequence.append({"tool": tool_name, "result": result, "source": "yaml_gap_fill"})
            tool_calls.append({
                "step": len(tool_calls) + 1,
                "source": "yaml_gap_fill",
                "thought": filled_step["thought"],
                "action": {"tool": tool_name, "args": args},
                "observation": self._summarize_observation(tool_name, result),
                "raw_status": str(result.get("status", "ok")) if isinstance(result, dict) else "ok",
            })
        merged = {
            **execution,
            "mode": "llm_react_yaml_hybrid",
            "plan": [
                {
                    "tool": call["action"]["tool"],
                    "args": call["action"]["args"],
                    "thought": call["thought"],
                }
                for call in tool_calls
            ],
            "tool_calls": tool_calls,
            "results": results,
            "result_sequence": result_sequence,
            "fallback_used": False,
            "workflow": [
                "yaml_strategy_loaded",
                "llm_react",
                "gap_audit",
                "yaml_gap_fill" if filled_steps else "yaml_gap_fill_skipped",
                "finalize",
            ],
            "phase_stats": {
                "llm_react_steps": len([item for item in tool_calls if item.get("source") == "llm_react"]),
                "yaml_gap_fill_steps": len([item for item in tool_calls if item.get("source") == "yaml_gap_fill"]),
                "yaml_planned_steps": len(recommended_plan),
                "total_steps": len(tool_calls),
            },
        }
        post_coverage = self._build_execution_coverage(
            strategy=strategy,
            execution=merged,
            recommended_plan=recommended_plan,
            allowed_tools=allowed_tools,
        )
        merged["gap_fill"] = {
            "enabled": True,
            "applied": bool(filled_steps),
            "reason": "missing_yaml_steps_detected" if filled_steps else "llm_already_satisfied_yaml_plan",
            "missing_plan_steps_before_fill": pre_coverage.get("missing_planned_steps", []),
            "filled_steps": filled_steps,
            "missing_plan_steps_after_fill": post_coverage.get("missing_planned_steps", []),
            "required_tool_coverage_before_fill": pre_coverage.get("required_tool_coverage", 0.0),
            "required_tool_coverage_after_fill": post_coverage.get("required_tool_coverage", 0.0),
            "planned_step_coverage_before_fill": pre_coverage.get("planned_step_coverage", 0.0),
            "planned_step_coverage_after_fill": post_coverage.get("planned_step_coverage", 0.0),
        }
        return merged

    def _run_stock_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        executor = self._tool_registry.get(tool_name)
        if executor is None:
            return {
                "status": "error",
                "summary": f"未知工具 {tool_name}",
                "next_actions": ["只使用可用的 stock tools"],
                "artifacts": {},
            }
        try:
            return executor(**args)
        except Exception as exc:
            return {
                "status": "error",
                "summary": f"{tool_name} 执行失败: {exc}",
                "next_actions": ["检查参数或换用其它工具"],
                "artifacts": {},
            }

    @staticmethod
    def _humanize_macd_cross(cross: str) -> str:
        mapping = {
            "golden_cross": "金叉",
            "dead_cross": "死叉",
            "bullish": "看多",
            "bearish": "看空",
            "neutral": "中性",
        }
        return mapping.get(str(cross or "neutral"), "中性")

    def _derive_signals(self, execution: dict[str, Any]) -> dict[str, Any]:
        history = dict(execution["results"].get("get_daily_history") or {})
        trend = dict(execution["results"].get("analyze_trend") or {})
        quote = dict(execution["results"].get("get_realtime_quote") or execution["results"].get("get_latest_quote") or {})
        indicator_result = dict(execution["results"].get("get_indicator_snapshot") or {})
        support_result = dict(execution["results"].get("analyze_support_resistance") or {})
        capital_flow = dict(execution["results"].get("get_capital_flow") or {})
        summary: dict[str, Any] = (
            dict(trend.get("summary") or {})
            if isinstance(trend.get("summary"), dict)
            else {}
        )
        trend_metrics: dict[str, Any] = (
            dict(trend.get("trend") or {})
            if isinstance(trend.get("trend"), dict)
            else {}
        )
        quote_row: dict[str, Any] = (
            dict(quote.get("quote") or {})
            if isinstance(quote.get("quote"), dict)
            else {}
        )
        snapshot: dict[str, Any] = (
            dict(indicator_result.get("snapshot") or {})
            if isinstance(indicator_result.get("snapshot"), dict)
            else {}
        )
        indicators: dict[str, Any] = (
            dict(snapshot.get("indicators") or {})
            if isinstance(snapshot.get("indicators"), dict)
            else {}
        )
        macd_payload: dict[str, Any] = (
            dict(indicators.get("macd_12_26_9") or {})
            if isinstance(indicators.get("macd_12_26_9"), dict)
            else {}
        )
        boll: dict[str, Any] = (
            dict(indicators.get("bollinger_20") or {})
            if isinstance(indicators.get("bollinger_20"), dict)
            else {}
        )
        support_levels: dict[str, Any] = (
            dict(support_result.get("levels") or {})
            if isinstance(support_result.get("levels"), dict)
            else {}
        )
        flow_metrics: dict[str, Any] = (
            dict(capital_flow.get("metrics") or {})
            if isinstance(capital_flow.get("metrics"), dict)
            else {}
        )
        latest_close = float(
            snapshot.get("latest_close")
            or trend_metrics.get("latest_close")
            or quote_row.get("close")
            or summary.get("close")
            or 0.0
        )
        ma20 = float(indicators.get("sma_20") or trend_metrics.get("ma20") or 0.0)
        volume_ratio = indicators.get("volume_ratio_5_20") or trend_metrics.get("volume_ratio")
        macd_cross = str(macd_payload.get("cross") or trend_metrics.get("macd_cross") or "neutral")
        rsi = float(
            indicators.get("rsi_14")
            or trend_metrics.get("rsi_14")
            or summary.get("rsi")
            or 50.0
        )
        algo_score = float(summary.get("algo_score") or 0.0)
        ma_stack = str(indicators.get("ma_stack") or "mixed")
        main_net_inflow_sum = float(flow_metrics.get("main_net_inflow_sum") or 0.0)
        distance_to_support_pct = float(support_levels.get("distance_to_support_pct") or 0.0)
        distance_to_resistance_pct = float(support_levels.get("distance_to_resistance_pct") or 0.0)
        flags = {
            "多头排列": ma_stack == "bullish",
            "空头排列": ma_stack == "bearish",
            "MACD金叉": macd_cross == "golden_cross",
            "MACD死叉": macd_cross == "dead_cross",
            "RSI超卖": rsi < 30,
            "RSI超买": rsi > 70,
            "价格站上MA20": latest_close > ma20 if ma20 else False,
            "跌破MA20": latest_close < ma20 if ma20 else False,
            "量比放大": volume_ratio is not None and float(volume_ratio) >= 1.1,
            "趋势向上": trend.get("signal") == "bullish",
            "趋势向下": trend.get("signal") == "bearish",
            "结构未破坏": trend.get("structure") in {"uptrend", "range"},
            "结构走弱": trend.get("structure") == "downtrend",
            "资金净流入": main_net_inflow_sum > 0,
            "接近支撑": 0.0 <= distance_to_support_pct <= 3.0,
            "逼近阻力": 0.0 <= distance_to_resistance_pct <= 3.0,
            "布林下轨附近": float(boll.get("position") or 0.5) <= 0.2,
            "布林上轨附近": float(boll.get("position") or 0.5) >= 0.8,
        }
        matched_signals = [name for name, ok in flags.items() if ok]
        return {
            "history_count": int(history.get("count") or 0),
            "latest_close": round(latest_close, 2) if latest_close else None,
            "ma20": round(ma20, 2) if ma20 else None,
            "volume_ratio": round(float(volume_ratio), 3) if volume_ratio is not None else None,
            "macd": self._humanize_macd_cross(macd_cross),
            "macd_cross": macd_cross,
            "rsi": round(rsi, 2),
            "algo_score": round(algo_score, 3),
            "ma_trend": "多头" if ma_stack == "bullish" else "空头" if ma_stack == "bearish" else "交叉",
            "ma_stack": ma_stack,
            "trend_signal": trend.get("signal") or "observe",
            "structure": trend.get("structure") or "range",
            "support_20": support_levels.get("support_20"),
            "resistance_20": support_levels.get("resistance_20"),
            "distance_to_support_pct": round(distance_to_support_pct, 2),
            "distance_to_resistance_pct": round(distance_to_resistance_pct, 2),
            "main_net_inflow_sum": round(main_net_inflow_sum, 2),
            "bollinger_position": boll.get("position"),
            "flags": flags,
            "matched_signals": matched_signals,
        }

    @staticmethod
    def _render_template_args(payload: dict[str, Any], *, query: str, days: int) -> dict[str, Any]:
        def render(value: Any) -> Any:
            if isinstance(value, str):
                return (
                    value.replace("{{query}}", query)
                    .replace("{{days}}", str(days))
                    .replace("{{history_days}}", str(days))
                    .replace("{{trend_days}}", str(max(120, days)))
                )
            if isinstance(value, dict):
                return {k: render(v) for k, v in value.items()}
            if isinstance(value, list):
                return [render(v) for v in value]
            return value

        rendered = render(payload)
        for key, value in list(rendered.items()):
            if isinstance(value, str) and value.isdigit():
                rendered[key] = int(value)
        return rendered

    @staticmethod
    def _parse_tool_args(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if raw is None:
            return {}
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return {}
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("tool arguments must decode to a JSON object")
            return parsed
        raise ValueError("tool arguments must be a JSON object or JSON string")

    def _stock_tool_definitions(self, allowed_tools: list[str] | None = None) -> list[dict[str, Any]]:
        catalog = [
            {
                "type": "function",
                "function": {
                    "name": "get_daily_history",
                    "description": "获取历史日线 OHLCV 数据，用于结构和趋势分析。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "days": {"type": "integer", "minimum": 30, "maximum": 500},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_indicator_snapshot",
                    "description": "计算均线、MACD、RSI、ATR、布林带和量比等指标快照。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "days": {"type": "integer", "minimum": 30, "maximum": 500},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "analyze_trend",
                    "description": "分析均线、MACD、RSI、结构与趋势方向。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "days": {"type": "integer", "minimum": 30, "maximum": 500},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "analyze_support_resistance",
                    "description": "识别支撑阻力、距关键位距离与突破/回踩偏向。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "days": {"type": "integer", "minimum": 30, "maximum": 500},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_capital_flow",
                    "description": "读取本地主力资金流数据，判断净流入/流出方向。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "days": {"type": "integer", "minimum": 1, "maximum": 120},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_intraday_context",
                    "description": "读取本地60分钟数据，确认短周期顺逆风。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "days": {"type": "integer", "minimum": 1, "maximum": 20},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_realtime_quote",
                    "description": "获取最新价格/最近收盘数据，用于风险位和结论确认。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                        },
                        "required": ["query"],
                    },
                },
            },
        ]
        if not allowed_tools:
            return catalog
        allowed = {str(name or "").strip() for name in allowed_tools if str(name or "").strip()}
        return [item for item in catalog if str(dict(item.get("function") or {}).get("name") or "") in allowed]

    @staticmethod
    def _default_thought(tool_name: str) -> str:
        mapping = {
            "get_daily_history": "先读取日线历史，建立价格结构上下文。",
            "get_indicator_snapshot": "先把关键指标跑全，避免只看单一信号。",
            "analyze_trend": "结合均线、MACD 和 RSI 判断当前趋势级别。",
            "analyze_support_resistance": "再确认支撑阻力和关键价格区间。",
            "get_capital_flow": "查看资金是否顺着当前趋势流入或流出。",
            "get_intraday_context": "必要时用60分钟结构确认短周期是否配合。",
            "get_realtime_quote": "最后用最新价格校准入场位和止损位。",
        }
        return mapping.get(tool_name, f"调用 {tool_name} 获取下一步分析所需信息。")

    @staticmethod
    def _summarize_observation(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {"status": "unknown", "summary": "unknown result", "next_actions": [], "artifacts": {}}
        status = str(result.get("status", "ok"))
        if tool_name == "get_daily_history":
            items = list(result.get("items") or [])
            last_trade_date = items[-1].get("trade_date") if items else None
            return {
                "status": status,
                "summary": str(result.get("summary") or ""),
                "count": int(result.get("count") or len(items)),
                "last_trade_date": last_trade_date,
                "next_actions": list(result.get("next_actions") or []),
                "artifacts": dict(result.get("artifacts") or {}),
            }
        if tool_name == "get_indicator_snapshot":
            snapshot: dict[str, Any] = (
                dict(result.get("snapshot") or {})
                if isinstance(result.get("snapshot"), dict)
                else {}
            )
            indicators: dict[str, Any] = (
                dict(snapshot.get("indicators") or {})
                if isinstance(snapshot.get("indicators"), dict)
                else {}
            )
            macd: dict[str, Any] = (
                dict(indicators.get("macd_12_26_9") or {})
                if isinstance(indicators.get("macd_12_26_9"), dict)
                else {}
            )
            return {
                "status": status,
                "summary": str(result.get("observation_summary") or result.get("summary") or ""),
                "latest_close": snapshot.get("latest_close"),
                "rsi_14": indicators.get("rsi_14"),
                "ma_stack": indicators.get("ma_stack"),
                "macd_cross": macd.get("cross"),
                "next_actions": list(result.get("next_actions") or []),
                "artifacts": dict(result.get("artifacts") or {}),
            }
        if tool_name == "analyze_trend":
            trend: dict[str, Any] = (
                dict(result.get("trend") or {})
                if isinstance(result.get("trend"), dict)
                else {}
            )
            return {
                "status": status,
                "summary": str(result.get("observation_summary") or result.get("summary") or ""),
                "signal": result.get("signal"),
                "structure": result.get("structure"),
                "latest_close": trend.get("latest_close"),
                "ma20": trend.get("ma20"),
                "volume_ratio": trend.get("volume_ratio"),
                "macd_cross": trend.get("macd_cross"),
                "next_actions": list(result.get("next_actions") or []),
                "artifacts": dict(result.get("artifacts") or {}),
            }
        if tool_name == "analyze_support_resistance":
            levels: dict[str, Any] = (
                dict(result.get("levels") or {})
                if isinstance(result.get("levels"), dict)
                else {}
            )
            return {
                "status": status,
                "summary": str(result.get("observation_summary") or result.get("summary") or ""),
                "support_20": levels.get("support_20"),
                "resistance_20": levels.get("resistance_20"),
                "bias": levels.get("bias"),
                "next_actions": list(result.get("next_actions") or []),
                "artifacts": dict(result.get("artifacts") or {}),
            }
        if tool_name == "get_capital_flow":
            metrics: dict[str, Any] = (
                dict(result.get("metrics") or {})
                if isinstance(result.get("metrics"), dict)
                else {}
            )
            return {
                "status": status,
                "summary": str(result.get("observation_summary") or result.get("summary") or ""),
                "direction": metrics.get("direction"),
                "main_net_inflow_sum": metrics.get("main_net_inflow_sum"),
                "next_actions": list(result.get("next_actions") or []),
                "artifacts": dict(result.get("artifacts") or {}),
            }
        if tool_name == "get_intraday_context":
            metrics: dict[str, Any] = (
                dict(result.get("metrics") or {})
                if isinstance(result.get("metrics"), dict)
                else {}
            )
            return {
                "status": status,
                "summary": str(result.get("observation_summary") or result.get("summary") or ""),
                "intraday_bias": metrics.get("intraday_bias"),
                "latest_trade_date": metrics.get("latest_trade_date"),
                "next_actions": list(result.get("next_actions") or []),
                "artifacts": dict(result.get("artifacts") or {}),
            }
        if tool_name in {"get_realtime_quote", "get_latest_quote"}:
            quote: dict[str, Any] = (
                dict(result.get("quote") or {})
                if isinstance(result.get("quote"), dict)
                else {}
            )
            return {
                "status": status,
                "summary": str(result.get("summary") or ""),
                "close": quote.get("close"),
                "trade_date": quote.get("trade_date"),
                "next_actions": list(result.get("next_actions") or []),
                "artifacts": dict(result.get("artifacts") or {}),
            }
        return {
            "status": status,
            "summary": str(result.get("summary") or ""),
            "next_actions": list(result.get("next_actions") or []),
            "artifacts": dict(result.get("artifacts") or {}),
        }

    def _stock_system_prompt(self, strategy: StockAnalysisStrategy) -> str:
        return (
            "You are a bounded stock-analysis planning agent. "
            "The tool catalog is restricted by the strategy YAML and defines your hard boundary. "
            "Use the provided tools to gather evidence before concluding. "
            "Prefer the strategy's required tools, stay close to the YAML workflow, and stop once the evidence is sufficient. "
            "If you already have enough evidence, return a short final reasoning summary. "
            "Do not invent market data or tool results."
        )

    def _build_stock_user_prompt(
        self,
        *,
        question: str,
        query: str,
        security: dict[str, Any],
        strategy: StockAnalysisStrategy,
        days: int,
    ) -> str:
        return (
            f"User question: {question}\n"
            f"Target security: {security.get('name') or ''} ({query})\n"
            f"Strategy: {strategy.display_name} / {strategy.name}\n"
            f"Description: {strategy.description}\n"
            f"Required tools: {', '.join(strategy.required_tools)}\n"
            f"Analysis steps: {' -> '.join(strategy.analysis_steps)}\n"
            f"Core rules: {'; '.join(strategy.core_rules)}\n"
            f"Entry conditions: {'; '.join(strategy.entry_conditions)}\n"
            f"Scoring rules: {json.dumps(strategy.scoring, ensure_ascii=False)}\n"
            f"Recommended lookback days: {days}\n"
            f"Suggested planner prompt: {strategy.planner_prompt}\n"
            "First decide the next most useful tool call."
        )

    def _load_strategy(self, path: Path) -> StockAnalysisStrategy | None:
        if yaml is None or not path.exists():
            return None
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            return None
        tool_plan: list[StockToolPlanStep] = []
        for item in list(payload.get("tool_call_plan") or []):
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "").strip()
            if not tool:
                continue
            tool_plan.append(
                StockToolPlanStep(
                    tool=tool,
                    args=dict(item.get("args") or {}),
                    thought=str(item.get("thought") or ""),
                )
            )
        aliases = [str(x) for x in (payload.get("aliases") or []) if str(x).strip()]
        return StockAnalysisStrategy(
            name=str(payload.get("name") or path.stem),
            display_name=str(payload.get("display_name") or payload.get("name") or path.stem),
            description=str(payload.get("description") or ""),
            required_tools=[str(x) for x in (payload.get("required_tools") or [])],
            analysis_steps=[str(x) for x in (payload.get("analysis_steps") or [])],
            entry_conditions=[str(x) for x in (payload.get("entry_conditions") or [])],
            scoring={str(k): float(v) for k, v in dict(payload.get("scoring") or {}).items()},
            core_rules=[str(x) for x in (payload.get("core_rules") or [])],
            tool_call_plan=tool_plan,
            aliases=aliases,
            planner_prompt=str(payload.get("planner_prompt") or "优先按策略规则逐步调用工具，再给出简短结论。"),
            react_enabled=bool(payload.get("react_enabled", True)),
            max_steps=max(1, min(8, int(payload.get("max_steps", 4) or 4))),
        )

    def _ensure_default_strategies(self) -> None:
        if yaml is None:
            return
        defaults = {
            "chan_theory.yaml": {
                "name": "chan_theory",
                "display_name": "缠论",
                "aliases": ["缠论", "chan", "chan theory"],
                "description": "基于结构、趋势、背离、支撑阻力与资金确认的离线问股模板。",
                "required_tools": [
                    "get_daily_history",
                    "get_indicator_snapshot",
                    "analyze_support_resistance",
                    "get_capital_flow",
                    "get_realtime_quote",
                ],
                "planner_prompt": "先确认结构，再确认指标与关键价位，最后看资金和最新价格是否共振。",
                "react_enabled": True,
                "max_steps": 5,
                "tool_call_plan": [
                    {"tool": "get_daily_history", "args": {"query": "{{query}}", "days": 60}, "thought": "先看近60日结构，确认价格区间和走势背景。"},
                    {"tool": "get_indicator_snapshot", "args": {"query": "{{query}}", "days": 120}, "thought": "把均线、MACD、RSI、ATR、布林带先跑全。"},
                    {"tool": "analyze_support_resistance", "args": {"query": "{{query}}", "days": 120}, "thought": "确认关键支撑阻力与当前价格所处位置。"},
                    {"tool": "get_capital_flow", "args": {"query": "{{query}}", "days": 20}, "thought": "看看资金是否支持当前结构。"},
                    {"tool": "get_realtime_quote", "args": {"query": "{{query}}"}, "thought": "最后确认最新价格，用于入场位和止损位参考。"},
                ],
                "core_rules": ["结构优先", "趋势级别", "背离/动能", "关键价位", "风险控制"],
                "analysis_steps": ["获取近60日日线", "识别指标状态", "判断支撑阻力", "观察资金确认", "结合最新价格输出结论"],
                "entry_conditions": ["结构未破坏", "MACD/均线改善", "靠近支撑或突破有效"],
                "scoring": {
                    "多头排列": 10,
                    "MACD金叉": 8,
                    "RSI超卖": 5,
                    "资金净流入": 6,
                    "接近支撑": 4,
                    "MACD死叉": -8,
                    "空头排列": -10,
                    "逼近阻力": -4,
                },
            },
            "trend_following.yaml": {
                "name": "trend_following",
                "display_name": "趋势跟随",
                "aliases": ["趋势跟随", "trend following", "趋势策略"],
                "description": "基于均线、量价、资金和关键价位的趋势问股模板。",
                "required_tools": [
                    "get_daily_history",
                    "get_indicator_snapshot",
                    "analyze_trend",
                    "get_capital_flow",
                    "get_realtime_quote",
                ],
                "planner_prompt": "优先确认中期趋势，再验证量价与资金是否共振，最后检查当前价格位置。",
                "react_enabled": True,
                "max_steps": 5,
                "tool_call_plan": [
                    {"tool": "get_daily_history", "args": {"query": "{{query}}", "days": 120}, "thought": "先拉长窗口确认中期趋势。"},
                    {"tool": "get_indicator_snapshot", "args": {"query": "{{query}}", "days": 180}, "thought": "用统一指标快照确认 MA 栈、MACD、RSI 和 ATR。"},
                    {"tool": "analyze_trend", "args": {"query": "{{query}}", "days": 180}, "thought": "进一步判断趋势方向与结构是否完整。"},
                    {"tool": "get_capital_flow", "args": {"query": "{{query}}", "days": 20}, "thought": "观察资金是否顺着趋势流入。"},
                    {"tool": "get_realtime_quote", "args": {"query": "{{query}}"}, "thought": "确认最新价格是否仍站在关键均线之上。"},
                ],
                "core_rules": ["均线顺势", "量能确认", "资金确认", "止损先行"],
                "analysis_steps": ["获取近120日日线", "计算指标快照", "验证趋势结构", "观察资金确认", "输出趋势建议"],
                "entry_conditions": ["价格站上MA20", "多头排列", "资金净流入"],
                "scoring": {
                    "价格站上MA20": 10,
                    "量比放大": 6,
                    "趋势向上": 8,
                    "资金净流入": 5,
                    "接近支撑": 3,
                    "跌破MA20": -12,
                    "趋势向下": -8,
                    "空头排列": -10,
                },
            },
        }
        for filename, payload in defaults.items():
            path = self.strategy_dir / filename
            if path.exists():
                continue
            path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    @staticmethod
    def _normalize_code(token: str) -> str:
        raw = str(token or "").strip()
        if raw.lower().startswith(("sh.", "sz.")):
            return raw.lower()
        if "." in raw:
            code, market = raw.split(".", 1)
            return f"{market.lower()}.{code}"
        if raw.startswith(("6", "9")):
            return f"sh.{raw}"
        return f"sz.{raw}"
