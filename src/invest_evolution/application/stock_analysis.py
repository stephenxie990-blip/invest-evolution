"""Canonical stock analysis facade, strategies, and research bridge."""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

import pandas as pd

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from invest_evolution.application.stock_analysis_response_contracts import (
    AskStockResponseHeaderFactory as ExtractedAskStockResponseHeaderFactory,
    ToolObservationEnvelope,
)
from invest_evolution.application.stock_analysis_ask_stock_assembly import (
    AskStockExecutionRunBundle,
    AskStockRequestContext,
    AskStockStageContract,
)
from invest_evolution.application.stock_analysis_ask_stock_execution import (
    AskStockExecutionOrchestrationService,
)
from invest_evolution.application.stock_analysis_resolution_contracts import (
    ResearchResolutionDisplayContract as ExtractedResearchResolutionDisplayContract,
    ResearchResolutionIdentifiers as ExtractedResearchResolutionIdentifiers,
    ResearchResolutionPayloadFactory as ExtractedResearchResolutionPayloadFactory,
)
from invest_evolution.application.stock_analysis_batch_service import (
    BatchAnalysisViewService,
    project_snapshot_fields as _project_snapshot_fields,
)
from invest_evolution.application.stock_analysis_research_bridge_service import (
    StockAnalysisResearchBridgeService as ExtractedStockAnalysisResearchBridgeService,
)
from invest_evolution.application.stock_analysis_research_resolution_service import (
    ResearchResolutionService as ExtractedResearchResolutionService,
)
from invest_evolution.application.stock_analysis_tool_catalog import (
    StockToolCatalog,
    _build_stock_tool_catalog,
)
from invest_evolution.application.stock_analysis_tool_response_builders import (
    build_tool_analysis_response as extracted_build_tool_analysis_response,
    build_tool_common_payload as extracted_build_tool_common_payload,
    build_tool_records_response as extracted_build_tool_records_response,
    build_tool_response as extracted_build_tool_response,
    build_tool_unavailable_response as extracted_build_tool_unavailable_response,
)
from invest_evolution.application.stock_analysis_projection_service import (
    StockIndicatorProjection as ExtractedStockIndicatorProjection,
    build_indicator_projection as extracted_build_indicator_projection,
)
from invest_evolution.application.stock_analysis_observation_service import (
    observation_envelope as extracted_observation_envelope,
    observation_section as extracted_observation_section,
    project_tool_observation as extracted_project_tool_observation,
)
from invest_evolution.application.stock_analysis_prompt_service import (
    build_llm_assistant_tool_message as extracted_build_llm_assistant_tool_message,
    build_llm_tool_result_message as extracted_build_llm_tool_result_message,
    build_stock_user_prompt as extracted_build_stock_user_prompt,
    stock_system_prompt as extracted_stock_system_prompt,
)
from invest_evolution.application.stock_analysis_parsing_service import (
    parse_tool_args as extracted_parse_tool_args,
    render_template_args as extracted_render_template_args,
)
from invest_evolution.application.stock_analysis_support_services import (
    build_stock_analysis_support_services,
)
from invest_evolution.application.stock_analysis_research_services import (
    build_stock_analysis_research_services,
)
from invest_evolution.application.stock_analysis_tool_runtime import (
    StockAnalysisToolRuntimeSupportService,
)
from invest_evolution.common.utils import (
    LLMGateway,
    LLMGatewayError,
    LLMUnavailableError,
)
from invest_evolution.application.training.policy import TrainingGovernanceService
from invest_evolution.config import PROJECT_ROOT, normalize_date
from invest_evolution.config.control_plane import resolve_default_llm
from invest_evolution.investment.research import (
    ResearchAttributionEngine,
    ResearchCaseStore,
    ResearchScenarioEngine,
    build_dashboard_projection as extracted_build_dashboard_projection,
)
from invest_evolution.market_data.repository import MarketDataRepository

logger = logging.getLogger("invest_evolution.application.stock_analysis")

_STOCK_LLM_REACT_FALLBACK_EXCEPTIONS = (
    LLMUnavailableError,
    LLMGatewayError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
    json.JSONDecodeError,
)

_STOCK_TOOL_EXECUTION_EXCEPTIONS = (
    RuntimeError,
    ValueError,
    TypeError,
    LookupError,
    OSError,
)

# Compatibility export: runtime now resolves to extracted service module.
ResearchResolutionPayloadFactory = ExtractedResearchResolutionPayloadFactory
ResearchResolutionDisplayContract = ExtractedResearchResolutionDisplayContract
ResearchResolutionIdentifiers = ExtractedResearchResolutionIdentifiers
AskStockResponseHeaderFactory = ExtractedAskStockResponseHeaderFactory
ResearchResolutionService = ExtractedResearchResolutionService
StockAnalysisResearchBridgeService = ExtractedStockAnalysisResearchBridgeService
build_dashboard_projection = extracted_build_dashboard_projection
build_tool_response = extracted_build_tool_response
build_tool_common_payload = extracted_build_tool_common_payload
build_tool_unavailable_response = extracted_build_tool_unavailable_response
build_tool_analysis_response = extracted_build_tool_analysis_response
build_tool_records_response = extracted_build_tool_records_response


@dataclass(frozen=True)
class StockExecutionPlanningBundle:
    plan: list["StockToolPlanStep"]
    recommended_plan: list[dict[str, Any]]
    allowed_tools: list[str]

@dataclass(frozen=True)
class StockExecutionTraceStep:
    step: int
    source: str
    thought: str
    tool: str
    args: dict[str, Any]
    result: dict[str, Any]
    observation: dict[str, Any]
    raw_status: str

    def as_tool_call_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "source": self.source,
            "thought": self.thought,
            "action": {"tool": self.tool, "args": dict(self.args)},
            "observation": dict(self.observation),
            "raw_status": self.raw_status,
        }

    def as_result_sequence_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "result": dict(self.result),
            "source": self.source,
        }

    def as_plan_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": dict(self.args),
            "thought": self.thought,
        }

    @classmethod
    def from_execution_payload(
        cls,
        *,
        step_number: int,
        tool_call: dict[str, Any],
        result_entry: dict[str, Any] | None,
    ) -> StockExecutionTraceStep:
        action = dict(tool_call.get("action") or {})
        return cls(
            step=int(tool_call.get("step") or step_number),
            source=str(
                tool_call.get("source") or dict(result_entry or {}).get("source") or ""
            ),
            thought=str(tool_call.get("thought") or ""),
            tool=str(action.get("tool") or dict(result_entry or {}).get("tool") or ""),
            args=dict(action.get("args") or {}),
            result=dict(dict(result_entry or {}).get("result") or {}),
            observation=dict(tool_call.get("observation") or {}),
            raw_status=str(tool_call.get("raw_status") or "ok"),
        )


@dataclass(frozen=True)
class StockExecutionResponseBundle:
    mode: str
    trace_steps: list[StockExecutionTraceStep]
    results: dict[str, Any]
    final_reasoning: str
    fallback_used: bool
    workflow: list[str]
    gap_fill: dict[str, Any]
    yaml_planned_steps: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "plan": [step.as_plan_dict() for step in self.trace_steps],
            "tool_calls": [step.as_tool_call_dict() for step in self.trace_steps],
            "results": dict(self.results),
            "result_sequence": [
                step.as_result_sequence_dict() for step in self.trace_steps
            ],
            "final_reasoning": self.final_reasoning,
            "fallback_used": self.fallback_used,
            "workflow": list(self.workflow),
            "phase_stats": self.phase_stats(),
            "gap_fill": dict(self.gap_fill),
        }

    def phase_stats(self) -> dict[str, int]:
        return {
            "llm_react_steps": len(
                [item for item in self.trace_steps if item.source == "llm_react"]
            ),
            "yaml_gap_fill_steps": len(
                [item for item in self.trace_steps if item.source == "yaml_gap_fill"]
            ),
            "yaml_planned_steps": int(self.yaml_planned_steps),
            "total_steps": len(self.trace_steps),
        }

    @classmethod
    def from_payload(cls, execution: dict[str, Any]) -> "StockExecutionResponseBundle":
        tool_calls = [
            dict(item or {}) for item in list(execution.get("tool_calls") or [])
        ]
        result_sequence = [
            dict(item or {}) for item in list(execution.get("result_sequence") or [])
        ]
        trace_steps = [
            StockExecutionTraceStep.from_execution_payload(
                step_number=index,
                tool_call=tool_call,
                result_entry=(
                    result_sequence[index - 1]
                    if index - 1 < len(result_sequence)
                    else None
                ),
            )
            for index, tool_call in enumerate(tool_calls, start=1)
        ]
        phase_stats = dict(execution.get("phase_stats") or {})
        return cls(
            mode=str(execution.get("mode") or ""),
            trace_steps=trace_steps,
            results=dict(execution.get("results") or {}),
            final_reasoning=str(execution.get("final_reasoning") or ""),
            fallback_used=bool(execution.get("fallback_used")),
            workflow=[str(item) for item in list(execution.get("workflow") or [])],
            gap_fill=dict(execution.get("gap_fill") or {}),
            yaml_planned_steps=int(phase_stats.get("yaml_planned_steps") or 0),
        )


@dataclass(frozen=True)
class StockExecutionCoverageAudit:
    required_tools: list[str]
    allowed_tools: list[str]
    planned_tools: list[str]
    executed_tools: list[str]
    missing_required_tools: list[str]
    missing_planned_steps: list[dict[str, Any]]
    out_of_policy_tools: list[str]
    planned_step_count: int
    executed_step_count: int

    def required_tool_coverage(self) -> float:
        if not self.required_tools:
            return 1.0
        return round(
            (len(self.required_tools) - len(self.missing_required_tools))
            / len(self.required_tools),
            3,
        )

    def planned_step_coverage(self) -> float:
        if not self.planned_step_count:
            return 1.0
        return round(
            (self.planned_step_count - len(self.missing_planned_steps))
            / self.planned_step_count,
            3,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "required_tools": list(self.required_tools),
            "allowed_tools": list(self.allowed_tools),
            "planned_tools": list(self.planned_tools),
            "executed_tools": list(self.executed_tools),
            "missing_required_tools": list(self.missing_required_tools),
            "missing_planned_steps": list(self.missing_planned_steps),
            "out_of_policy_tools": list(self.out_of_policy_tools),
            "required_tool_coverage": self.required_tool_coverage(),
            "planned_step_count": self.planned_step_count,
            "executed_step_count": self.executed_step_count,
            "planned_step_coverage": self.planned_step_coverage(),
        }


@dataclass(frozen=True)
class StockQueryContext:
    query: str
    code: str
    security: dict[str, Any]
    price_frame: pd.DataFrame


StockIndicatorProjection = ExtractedStockIndicatorProjection


@dataclass(frozen=True)
class StockWindowContext:
    query_context: StockQueryContext
    frame: pd.DataFrame

    @property
    def query(self) -> str:
        return self.query_context.query

    @property
    def code(self) -> str:
        return self.query_context.code

    @property
    def security(self) -> dict[str, Any]:
        return dict(self.query_context.security)


def _build_indicator_projection(
    snapshot: dict[str, Any] | None,
    *,
    summary: dict[str, Any] | None = None,
    trend_metrics: dict[str, Any] | None = None,
    quote_row: dict[str, Any] | None = None,
) -> StockIndicatorProjection:
    return extracted_build_indicator_projection(
        snapshot,
        snapshot_projector=_project_snapshot_fields,
        summary=summary,
        trend_metrics=trend_metrics,
        quote_row=quote_row,
    )


# Execution planning helpers
class StockAnalysisExecutionMixin:
    # Declared here so the extracted execution helpers stay type-safe when mixed
    # back into StockAnalysisService.
    _tool_catalog: StockToolCatalog
    _tool_registry: dict[str, Callable[..., dict[str, Any]]]
    gateway: Any
    stock_analysis_observation_service: Any
    stock_analysis_prompt_service: Any
    stock_analysis_parsing_service: Any

    def _stock_tool_aliases(self) -> dict[str, str]:
        return dict(self._tool_catalog.aliases)

    def _normalize_tool_name(self, tool_name: str) -> str:
        raw = str(tool_name or "").strip()
        return self._stock_tool_aliases().get(raw, raw)

    def _action_signature(self, tool_name: str, args: dict[str, Any]) -> str:
        payload = {
            "tool": self._normalize_tool_name(tool_name),
            "args": dict(args or {}),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _build_plan(
        self, *, strategy: StockAnalysisStrategy, query: str, days: int
    ) -> list[StockToolPlanStep]:
        plan = strategy.tool_call_plan or []
        if plan:
            rendered: list[StockToolPlanStep] = []
            for step in plan:
                rendered.append(
                    StockToolPlanStep(
                        tool=step.tool,
                        thought=step.thought,
                        args=self._render_template_args(
                            step.args, query=query, days=days
                        ),
                    )
                )
            return rendered

        derived: list[StockToolPlanStep] = []
        for tool in strategy.required_tools:
            if tool == "get_daily_history":
                derived.append(
                    StockToolPlanStep(
                        tool=tool,
                        thought="先拉取历史K线，建立价格结构基础。",
                        args={"query": query, "days": days},
                    )
                )
            elif tool == "analyze_trend":
                derived.append(
                    StockToolPlanStep(
                        tool=tool,
                        thought="继续计算趋势、均线与动量信号。",
                        args={"query": query, "days": max(120, days)},
                    )
                )
            elif tool in {"get_realtime_quote", "get_latest_quote"}:
                derived.append(
                    StockToolPlanStep(
                        tool="get_realtime_quote",
                        thought="最后确认最新收盘/报价。",
                        args={"query": query},
                    )
                )
        return derived

    def _build_trace_step(
        self,
        *,
        step_number: int,
        source: str,
        tool_name: str,
        args: dict[str, Any],
        thought: str,
        result: dict[str, Any],
    ) -> StockExecutionTraceStep:
        return StockExecutionTraceStep(
            step=step_number,
            source=source,
            thought=thought or self._default_thought(tool_name),
            tool=tool_name,
            args=dict(args),
            result=dict(result),
            observation=self._summarize_observation(tool_name, result),
            raw_status=str(result.get("status", "ok"))
            if isinstance(result, dict)
            else "ok",
        )

    def _record_execution_step(
        self,
        *,
        trace_steps: list[StockExecutionTraceStep],
        results: dict[str, Any],
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        source: str,
        thought: str,
    ) -> None:
        results[tool_name] = result
        trace_steps.append(
            self._build_trace_step(
                step_number=len(trace_steps) + 1,
                source=source,
                tool_name=tool_name,
                args=args,
                thought=thought,
                result=result,
            ),
        )

    def _execute_tool_step(
        self,
        *,
        trace_steps: list[StockExecutionTraceStep],
        results: dict[str, Any],
        tool_name: str,
        args: dict[str, Any],
        source: str,
        thought: str,
    ) -> dict[str, Any]:
        result = self._run_stock_tool(tool_name, args)
        self._record_execution_step(
            trace_steps=trace_steps,
            results=results,
            tool_name=tool_name,
            args=args,
            result=result,
            source=source,
            thought=thought,
        )
        return result

    @staticmethod
    def _build_execution_bundle(
        *,
        mode: str,
        trace_steps: list[StockExecutionTraceStep],
        results: dict[str, Any],
        final_reasoning: str,
        fallback_used: bool,
        workflow: list[str],
        gap_fill: dict[str, Any],
        yaml_planned_steps: int,
    ) -> StockExecutionResponseBundle:
        return StockExecutionResponseBundle(
            mode=mode,
            trace_steps=list(trace_steps),
            results=dict(results),
            final_reasoning=final_reasoning,
            fallback_used=fallback_used,
            workflow=list(workflow),
            gap_fill=dict(gap_fill),
            yaml_planned_steps=yaml_planned_steps,
        )

    @staticmethod
    def _execution_bundle_from_payload(
        execution: dict[str, Any],
    ) -> StockExecutionResponseBundle:
        return StockExecutionResponseBundle.from_payload(execution)

    def _build_execution_planning_bundle(
        self,
        *,
        strategy: StockAnalysisStrategy,
        query: str,
        days: int,
    ) -> StockExecutionPlanningBundle:
        plan = self._build_plan(strategy=strategy, query=query, days=days)
        return StockExecutionPlanningBundle(
            plan=plan,
            recommended_plan=[step.to_dict() for step in plan],
            allowed_tools=self._strategy_allowed_tools(strategy),
        )

    @classmethod
    def _trace_steps_from_execution(
        cls,
        execution: dict[str, Any],
    ) -> list[StockExecutionTraceStep]:
        return list(cls._execution_bundle_from_payload(execution).trace_steps)

    @staticmethod
    def _build_gap_fill_state(
        *,
        enabled: bool,
        applied: bool,
        reason: str,
        missing_plan_steps_before_fill: list[dict[str, Any]] | None = None,
        filled_steps: list[dict[str, Any]] | None = None,
        missing_plan_steps_after_fill: list[dict[str, Any]] | None = None,
        required_tool_coverage_before_fill: float | None = None,
        required_tool_coverage_after_fill: float | None = None,
        planned_step_coverage_before_fill: float | None = None,
        planned_step_coverage_after_fill: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "enabled": enabled,
            "applied": applied,
            "reason": reason,
            "missing_plan_steps_before_fill": list(
                missing_plan_steps_before_fill or []
            ),
            "filled_steps": list(filled_steps or []),
            "missing_plan_steps_after_fill": list(missing_plan_steps_after_fill or []),
        }
        if required_tool_coverage_before_fill is not None:
            payload["required_tool_coverage_before_fill"] = (
                required_tool_coverage_before_fill
            )
        if required_tool_coverage_after_fill is not None:
            payload["required_tool_coverage_after_fill"] = (
                required_tool_coverage_after_fill
            )
        if planned_step_coverage_before_fill is not None:
            payload["planned_step_coverage_before_fill"] = (
                planned_step_coverage_before_fill
            )
        if planned_step_coverage_after_fill is not None:
            payload["planned_step_coverage_after_fill"] = (
                planned_step_coverage_after_fill
            )
        return payload

    def _build_execution_response(
        self,
        *,
        mode: str,
        trace_steps: list[StockExecutionTraceStep],
        results: dict[str, Any],
        final_reasoning: str,
        fallback_used: bool,
        workflow: list[str],
        gap_fill: dict[str, Any],
        yaml_planned_steps: int,
    ) -> dict[str, Any]:
        return self._build_execution_bundle(
            mode=mode,
            trace_steps=trace_steps,
            results=results,
            final_reasoning=final_reasoning,
            fallback_used=fallback_used,
            workflow=workflow,
            gap_fill=gap_fill,
            yaml_planned_steps=yaml_planned_steps,
        ).to_payload()

    def _strategy_allowed_tools(self, strategy: StockAnalysisStrategy) -> list[str]:
        allowed: list[str] = []
        seen: set[str] = set()
        for step in list(strategy.tool_call_plan or []):
            tool_name = str(step.tool or "").strip()
            if tool_name and tool_name in self._tool_registry and tool_name not in seen:
                allowed.append(tool_name)
                seen.add(tool_name)
        for tool_name in list(strategy.required_tools or []):
            normalized = (
                "get_realtime_quote"
                if tool_name == "get_latest_quote"
                else str(tool_name or "").strip()
            )
            if (
                normalized
                and normalized in self._tool_registry
                and normalized not in seen
            ):
                allowed.append(normalized)
                seen.add(normalized)
        return allowed

    def _normalize_plan_step(self, item: dict[str, Any]) -> dict[str, Any]:
        step = dict(item or {})
        tool_name = self._normalize_tool_name(str(step.get("tool") or ""))
        return {
            "tool": tool_name,
            "args": dict(step.get("args") or {}),
            "thought": str(step.get("thought") or self._default_thought(tool_name)),
        }

    def _normalized_plan_steps(
        self, recommended_plan: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [self._normalize_plan_step(item) for item in list(recommended_plan or [])]

    def _executed_tool_state(
        self, execution: dict[str, Any]
    ) -> tuple[list[str], set[str]]:
        executed_tools: list[str] = []
        executed_signatures: set[str] = set()
        for item in list(execution.get("tool_calls") or []):
            action = dict(item.get("action") or {})
            tool_name = self._normalize_tool_name(str(action.get("tool") or ""))
            args = dict(action.get("args") or {})
            if tool_name:
                executed_tools.append(tool_name)
                executed_signatures.add(self._action_signature(tool_name, args))
        return list(dict.fromkeys(executed_tools)), executed_signatures

    def _build_execution_coverage_audit(
        self,
        *,
        required_tools: list[str],
        allowed_tools: list[str],
        execution: dict[str, Any],
        recommended_plan: list[dict[str, Any]],
    ) -> StockExecutionCoverageAudit:
        normalized_plan = self._normalized_plan_steps(recommended_plan)
        planned_tools = list(
            dict.fromkeys(
                [str(step.get("tool") or "") for step in normalized_plan if step.get("tool")]
            )
        )
        executed_tools, executed_signatures = self._executed_tool_state(execution)
        missing_planned_steps = [
            step
            for step in normalized_plan
            if self._action_signature(
                str(step.get("tool") or ""), dict(step.get("args") or {})
            )
            not in executed_signatures
        ]
        return StockExecutionCoverageAudit(
            required_tools=list(required_tools),
            allowed_tools=list(allowed_tools),
            planned_tools=planned_tools,
            executed_tools=executed_tools,
            missing_required_tools=[
                tool for tool in required_tools if tool not in executed_tools
            ],
            missing_planned_steps=missing_planned_steps,
            out_of_policy_tools=[
                tool for tool in executed_tools if tool not in allowed_tools
            ],
            planned_step_count=len(normalized_plan),
            executed_step_count=len(list(execution.get("tool_calls") or [])),
        )

    def _missing_plan_steps(
        self,
        *,
        execution: dict[str, Any],
        recommended_plan: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self._build_execution_coverage_audit(
            required_tools=[],
            allowed_tools=[],
            execution=execution,
            recommended_plan=recommended_plan,
        ).missing_planned_steps

    def _build_execution_coverage(
        self,
        *,
        strategy: StockAnalysisStrategy,
        execution: dict[str, Any],
        recommended_plan: list[dict[str, Any]],
        allowed_tools: list[str],
    ) -> dict[str, Any]:
        return self._build_execution_coverage_audit(
            required_tools=self._strategy_allowed_tools(strategy),
            allowed_tools=allowed_tools,
            execution=execution,
            recommended_plan=recommended_plan,
        ).to_payload()

    def _execute_plan_deterministic(
        self, *, plan: list[StockToolPlanStep], fallback_reason: str = ""
    ) -> dict[str, Any]:
        trace_steps: list[StockExecutionTraceStep] = []
        results: dict[str, Any] = {}
        for step in plan:
            self._execute_tool_step(
                trace_steps=trace_steps,
                results=results,
                tool_name=step.tool,
                args=step.args,
                source="yaml_plan",
                thought=step.thought,
            )
        return self._build_execution_response(
            mode="yaml_react_like",
            trace_steps=trace_steps,
            results=results,
            final_reasoning="",
            fallback_used=True,
            workflow=["yaml_strategy_loaded", "yaml_plan_execute", "finalize"],
            gap_fill=self._build_gap_fill_state(
                enabled=False,
                applied=False,
                reason=fallback_reason or "yaml_only_mode",
            ),
            yaml_planned_steps=len(plan),
        )

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
        trace_steps: list[StockExecutionTraceStep] = []
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
                    self._build_llm_assistant_tool_message(
                        choice=choice, tool_calls=llm_tool_calls
                    )
                )
                for tc in llm_tool_calls:
                    args = self._parse_tool_args(tc.function.arguments)
                    result = self._execute_tool_step(
                        trace_steps=trace_steps,
                        results=results,
                        tool_name=tc.function.name,
                        args=args,
                        source="llm_react",
                        thought=(getattr(choice, "content", "") or "").strip(),
                    )
                    messages.append(
                        self._build_llm_tool_result_message(
                            tool_call_id=tc.id,
                            tool_name=tc.function.name,
                            result=result,
                        )
                    )
                continue
            final_reasoning = (getattr(choice, "content", "") or "").strip()
            break

        if not trace_steps:
            raise RuntimeError("llm react produced no tool calls")
        return self._build_execution_response(
            mode="llm_react",
            trace_steps=trace_steps,
            results=results,
            final_reasoning=final_reasoning,
            fallback_used=False,
            workflow=["yaml_strategy_loaded", "llm_react", "gap_audit_pending"],
            gap_fill=self._build_gap_fill_state(
                enabled=True,
                applied=False,
                reason="awaiting_gap_audit",
            ),
            yaml_planned_steps=0,
        )

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
        missing_steps = self._missing_plan_steps(
            execution=execution, recommended_plan=recommended_plan
        )
        execution_bundle = self._execution_bundle_from_payload(execution)
        trace_steps = list(execution_bundle.trace_steps)
        results = dict(execution_bundle.results)
        filled_steps: list[dict[str, Any]] = []
        for step in missing_steps:
            tool_name = self._normalize_tool_name(str(step.get("tool") or ""))
            args = dict(step.get("args") or {})
            filled_step = {
                "tool": tool_name,
                "args": args,
                "thought": str(step.get("thought") or self._default_thought(tool_name)),
            }
            filled_steps.append(filled_step)
            self._execute_tool_step(
                trace_steps=trace_steps,
                results=results,
                tool_name=tool_name,
                args=args,
                source="yaml_gap_fill",
                thought=filled_step["thought"],
            )
        merged = self._build_execution_response(
            mode="llm_react_yaml_hybrid",
            trace_steps=trace_steps,
            results=results,
            final_reasoning=execution_bundle.final_reasoning,
            fallback_used=False,
            workflow=[
                "yaml_strategy_loaded",
                "llm_react",
                "gap_audit",
                "yaml_gap_fill" if filled_steps else "yaml_gap_fill_skipped",
                "finalize",
            ],
            gap_fill={},
            yaml_planned_steps=len(recommended_plan),
        )
        post_coverage = self._build_execution_coverage(
            strategy=strategy,
            execution=merged,
            recommended_plan=recommended_plan,
            allowed_tools=allowed_tools,
        )
        merged["gap_fill"] = self._build_gap_fill_state(
            enabled=True,
            applied=bool(filled_steps),
            reason="missing_yaml_steps_detected"
            if filled_steps
            else "llm_already_satisfied_yaml_plan",
            missing_plan_steps_before_fill=pre_coverage.get(
                "missing_planned_steps", []
            ),
            filled_steps=filled_steps,
            missing_plan_steps_after_fill=post_coverage.get(
                "missing_planned_steps", []
            ),
            required_tool_coverage_before_fill=pre_coverage.get(
                "required_tool_coverage", 0.0
            ),
            required_tool_coverage_after_fill=post_coverage.get(
                "required_tool_coverage", 0.0
            ),
            planned_step_coverage_before_fill=pre_coverage.get(
                "planned_step_coverage", 0.0
            ),
            planned_step_coverage_after_fill=post_coverage.get(
                "planned_step_coverage", 0.0
            ),
        )
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
        except _STOCK_TOOL_EXECUTION_EXCEPTIONS as exc:
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
        quote = dict(
            execution["results"].get("get_realtime_quote")
            or execution["results"].get("get_latest_quote")
            or {}
        )
        indicator_result = dict(
            execution["results"].get("get_indicator_snapshot") or {}
        )
        support_result = dict(
            execution["results"].get("analyze_support_resistance") or {}
        )
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
        snapshot_payload: dict[str, Any] = (
            dict(indicator_result.get("snapshot") or {})
            if isinstance(indicator_result.get("snapshot"), dict)
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
        indicator_projection = _build_indicator_projection(
            snapshot_payload,
            summary=summary,
            trend_metrics=trend_metrics,
            quote_row=quote_row,
        )
        boll = dict(indicator_projection.boll)
        projected = indicator_projection.projected_fields
        latest_close = float(projected["latest_close"] or 0.0)
        ma20 = float(projected["ma20"] or 0.0)
        volume_ratio = projected["volume_ratio"]
        macd_cross = str(projected["macd_cross"] or "neutral")
        rsi = float(projected["rsi"] or 50.0)
        algo_score = float(summary.get("algo_score") or 0.0)
        ma_stack = str(projected["ma_stack"] or "mixed")
        main_net_inflow_sum = float(flow_metrics.get("main_net_inflow_sum") or 0.0)
        distance_to_support_pct = float(
            support_levels.get("distance_to_support_pct") or 0.0
        )
        distance_to_resistance_pct = float(
            support_levels.get("distance_to_resistance_pct") or 0.0
        )
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
            "volume_ratio": round(float(volume_ratio), 3)
            if volume_ratio is not None
            else None,
            "macd": self._humanize_macd_cross(macd_cross),
            "macd_cross": macd_cross,
            "rsi": round(rsi, 2),
            "algo_score": round(algo_score, 3),
            "ma_trend": "多头"
            if ma_stack == "bullish"
            else "空头"
            if ma_stack == "bearish"
            else "交叉",
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
    def _render_template_args(
        payload: dict[str, Any], *, query: str, days: int
    ) -> dict[str, Any]:
        return extracted_render_template_args(payload, query=query, days=days)

    @staticmethod
    def _parse_tool_args(raw: Any) -> dict[str, Any]:
        return extracted_parse_tool_args(raw)

    def _stock_tool_catalog_entries(self) -> tuple[dict[str, Any], ...]:
        return self._tool_catalog.entries

    def _stock_tool_catalog_by_name(self) -> dict[str, dict[str, Any]]:
        return dict(self._tool_catalog.by_name)

    def _stock_tool_definitions(
        self, allowed_tools: list[str] | None = None
    ) -> list[dict[str, Any]]:
        return self.stock_analysis_prompt_service.stock_tool_definitions(
            allowed_tools=allowed_tools
        )

    def _default_thought(self, tool_name: str) -> str:
        return self.stock_analysis_prompt_service.default_thought(tool_name)

    @staticmethod
    def _build_llm_assistant_tool_message(
        *, choice: Any, tool_calls: list[Any]
    ) -> dict[str, Any]:
        return extracted_build_llm_assistant_tool_message(
            choice=choice,
            tool_calls=tool_calls,
        )

    @staticmethod
    def _build_llm_tool_result_message(
        *,
        tool_call_id: str,
        tool_name: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return extracted_build_llm_tool_result_message(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result=result,
        )

    @staticmethod
    def _observation_envelope(
        result: Any,
        *,
        summary_keys: tuple[str, ...] = ("summary",),
    ) -> ToolObservationEnvelope:
        return extracted_observation_envelope(result, summary_keys=summary_keys)

    @staticmethod
    def _observation_section(result: dict[str, Any], key: str) -> dict[str, Any]:
        return extracted_observation_section(result, key)

    @classmethod
    def _project_tool_observation(
        cls,
        result: dict[str, Any],
        *,
        summary_keys: tuple[str, ...] = ("summary",),
        **payload: Any,
    ) -> dict[str, Any]:
        return extracted_project_tool_observation(
            result,
            summary_keys=summary_keys,
            **payload,
        )

    def _summarize_observation(
        self, tool_name: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        return self.stock_analysis_observation_service.summarize_observation(
            tool_name,
            result,
        )

    def _stock_system_prompt(self, strategy: StockAnalysisStrategy) -> str:
        return extracted_stock_system_prompt()

    def _build_stock_user_prompt(
        self,
        *,
        question: str,
        query: str,
        security: dict[str, Any],
        strategy: StockAnalysisStrategy,
        days: int,
    ) -> str:
        return extracted_build_stock_user_prompt(
            question=question,
            query=query,
            security=security,
            strategy=strategy,
            days=days,
        )

# Strategy contracts and store
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


DEFAULT_STOCK_STRATEGIES: dict[str, dict[str, Any]] = {
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
            {
                "tool": "get_daily_history",
                "args": {"query": "{{query}}", "days": 60},
                "thought": "先看近60日结构，确认价格区间和走势背景。",
            },
            {
                "tool": "get_indicator_snapshot",
                "args": {"query": "{{query}}", "days": 120},
                "thought": "把均线、MACD、RSI、ATR、布林带先跑全。",
            },
            {
                "tool": "analyze_support_resistance",
                "args": {"query": "{{query}}", "days": 120},
                "thought": "确认关键支撑阻力与当前价格所处位置。",
            },
            {
                "tool": "get_capital_flow",
                "args": {"query": "{{query}}", "days": 20},
                "thought": "看看资金是否支持当前结构。",
            },
            {
                "tool": "get_realtime_quote",
                "args": {"query": "{{query}}"},
                "thought": "最后确认最新价格，用于入场位和止损位参考。",
            },
        ],
        "core_rules": ["结构优先", "趋势级别", "背离/动能", "关键价位", "风险控制"],
        "analysis_steps": [
            "获取近60日日线",
            "识别指标状态",
            "判断支撑阻力",
            "观察资金确认",
            "结合最新价格输出结论",
        ],
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
            {
                "tool": "get_daily_history",
                "args": {"query": "{{query}}", "days": 120},
                "thought": "先拉长窗口确认中期趋势。",
            },
            {
                "tool": "get_indicator_snapshot",
                "args": {"query": "{{query}}", "days": 180},
                "thought": "用统一指标快照确认 MA 栈、MACD、RSI 和 ATR。",
            },
            {
                "tool": "analyze_trend",
                "args": {"query": "{{query}}", "days": 180},
                "thought": "进一步判断趋势方向与结构是否完整。",
            },
            {
                "tool": "get_capital_flow",
                "args": {"query": "{{query}}", "days": 20},
                "thought": "观察资金是否顺着趋势流入。",
            },
            {
                "tool": "get_realtime_quote",
                "args": {"query": "{{query}}"},
                "thought": "确认最新价格是否仍站在关键均线之上。",
            },
        ],
        "core_rules": ["均线顺势", "量能确认", "资金确认", "止损先行"],
        "analysis_steps": [
            "获取近120日日线",
            "计算指标快照",
            "验证趋势结构",
            "观察资金确认",
            "输出趋势建议",
        ],
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


class StockAnalysisStrategyStore:
    def __init__(self, strategy_dir: Path):
        self.strategy_dir = Path(strategy_dir)
        self.strategy_dir.mkdir(parents=True, exist_ok=True)

    def ensure_default_strategies(self) -> None:
        if yaml is None:
            return
        for filename, payload in DEFAULT_STOCK_STRATEGIES.items():
            path = self.strategy_dir / filename
            if path.exists():
                continue
            path.write_text(
                yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

    def list_strategies(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.strategy_dir.glob("*.yaml")):
            strategy = self.load_path(path)
            if strategy is not None:
                items.append(strategy.to_dict())
        return items

    def load_strategy(self, name: str) -> StockAnalysisStrategy:
        path = self._strategy_path(name)
        strategy = self.load_path(path)
        if strategy is None:
            raise FileNotFoundError(f"stock strategy not found: {name}")
        return strategy

    def _strategy_path(self, name: str) -> Path:
        return self.strategy_dir / f"{str(name or 'chan_theory').strip()}.yaml"

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        return [str(item) for item in (value or []) if str(item).strip()]

    @staticmethod
    def _coerce_scoring(value: Any) -> dict[str, float]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, float] = {}
        for key, score in value.items():
            name = str(key or "").strip()
            if not name:
                continue
            try:
                normalized[name] = float(score)
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid strategy scoring weight: key=%s value=%r type=%s",
                    name,
                    score,
                    type(score).__name__,
                )
        return normalized

    @staticmethod
    def _build_tool_plan(value: Any) -> list[StockToolPlanStep]:
        tool_plan: list[StockToolPlanStep] = []
        for item in list(value or []):
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
        return tool_plan

    @staticmethod
    def _load_yaml_payload(path: Path) -> dict[str, Any] | None:
        if yaml is None or not path.exists():
            return None
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return payload if isinstance(payload, dict) else None

    def _build_strategy(
        self, *, path: Path, payload: dict[str, Any]
    ) -> StockAnalysisStrategy:
        return StockAnalysisStrategy(
            name=str(payload.get("name") or path.stem),
            display_name=str(
                payload.get("display_name") or payload.get("name") or path.stem
            ),
            description=str(payload.get("description") or ""),
            required_tools=self._string_list(payload.get("required_tools")),
            analysis_steps=self._string_list(payload.get("analysis_steps")),
            entry_conditions=self._string_list(payload.get("entry_conditions")),
            scoring=self._coerce_scoring(payload.get("scoring")),
            core_rules=self._string_list(payload.get("core_rules")),
            tool_call_plan=self._build_tool_plan(payload.get("tool_call_plan")),
            aliases=self._string_list(payload.get("aliases")),
            planner_prompt=str(
                payload.get("planner_prompt")
                or "优先按策略规则逐步调用工具，再给出简短结论。"
            ),
            react_enabled=bool(payload.get("react_enabled", True)),
            max_steps=max(1, min(8, int(payload.get("max_steps", 4) or 4))),
        )

    def load_path(self, path: Path) -> StockAnalysisStrategy | None:
        payload = self._load_yaml_payload(path)
        if payload is None:
            return None
        return self._build_strategy(path=path, payload=payload)


# Canonical facade
_STOCK_CODE_RE = re.compile(r"\b(?:sh|sz)\.\d{6}\b|\b\d{6}(?:\.(?:SH|SZ|sh|sz))?\b")
_DAY_COUNT_RE = re.compile(r"(\d{2,4})\s*(?:个)?(?:交易)?(?:日|天)")


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    values = (
        frame[column]
        if column in frame.columns
        else pd.Series(index=frame.index, dtype="float64")
    )
    return cast(pd.Series, pd.to_numeric(values, errors="coerce"))


class StockAnalysisService(StockAnalysisExecutionMixin):
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
        self.strategy_store = StockAnalysisStrategyStore(self.strategy_dir)
        self._project_root = self._resolve_project_root(
            project_root=project_root,
            strategy_dir=strategy_dir,
            db_path=db_path,
        )
        self._runtime_state_dir = self._init_runtime_state_dir(self._project_root)
        self._analysis_as_of_date: str = ""
        self._controller_provider = controller_provider
        self.strategy_store.ensure_default_strategies()
        self._tool_catalog = _build_stock_tool_catalog()
        self._tool_registry = self._build_tool_registry()
        self.gateway = gateway or self._build_gateway(
            model=model, api_key=api_key, api_base=api_base
        )
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
        registry: dict[str, Callable[..., dict[str, Any]]] = {}
        for item in self._stock_tool_catalog_entries():
            tool_name = str(item.get("name") or "").strip()
            executor_attr = str(item.get("executor") or "").strip()
            if tool_name and executor_attr:
                registry[tool_name] = cast(
                    Callable[..., dict[str, Any]], getattr(self, executor_attr)
                )
        for alias, canonical_name in self._stock_tool_aliases().items():
            if alias and canonical_name in registry:
                registry[alias] = registry[canonical_name]
        return registry

    def _init_research_services(self) -> None:
        self.case_store = ResearchCaseStore(self._runtime_state_dir)
        self.scenario_engine = ResearchScenarioEngine(self.case_store)
        self.attribution_engine = ResearchAttributionEngine(self.repository)
        support_services = build_stock_analysis_support_services(
            humanize_macd_cross=self._humanize_macd_cross,
            resolve_strategy_name=lambda question, strategy: self._resolve_strategy_name(
                question=question,
                strategy=strategy,
            ),
            infer_days=lambda question, default_days: self._infer_days(
                question=question,
                default_days=default_days,
            ),
            load_strategy=self.load_strategy,
            resolve_query_context=self._resolve_query_context,
            resolve_effective_as_of_date=self._resolve_effective_as_of_date,
            normalize_as_of_date=self._normalize_as_of_date,
            available_tools_provider=lambda: sorted(self._tool_registry.keys()),
            normalize_tool_name=lambda tool_name: self._normalize_tool_name(tool_name),
            catalog_by_name_provider=lambda: self._stock_tool_catalog_by_name(),
            definitions_by_name_provider=lambda: dict(
                self._tool_catalog.definitions_by_name
            ),
            build_indicator_projection=lambda snapshot, **kwargs: _build_indicator_projection(
                snapshot,
                **kwargs,
            ),
            build_batch_analysis_context=lambda frame, code: self._build_batch_analysis_context(
                frame, code
            ),
            view_from_snapshot=lambda summary, snapshot: self._view_from_snapshot(
                summary, snapshot
            ),
            snapshot_projector=lambda snapshot, **kwargs: _project_snapshot_fields(
                snapshot, **kwargs
            ),
            resolve_security=lambda query: self.resolve_security(query),
            get_stock_frame=lambda code: self._get_stock_frame(code),
            build_tool_unavailable_response=lambda **kwargs: self._build_tool_unavailable_response(
                **kwargs
            ),
            query_context_factory=StockQueryContext,
            window_context_factory=StockWindowContext,
        )
        self.batch_analysis_service = support_services.batch_analysis_service
        self.ask_stock_request_context_service = (
            support_services.ask_stock_request_context_service
        )
        self.ask_stock_response_assembly_service = (
            support_services.ask_stock_response_assembly_service
        )
        self.stock_analysis_prompt_service = (
            support_services.stock_analysis_prompt_service
        )
        self.stock_analysis_parsing_service = (
            support_services.stock_analysis_parsing_service
        )
        self.stock_analysis_observation_service = (
            support_services.stock_analysis_observation_service
        )
        self.stock_analysis_projection_service = (
            support_services.stock_analysis_projection_service
        )
        self.tool_runtime_support_service = (
            support_services.tool_runtime_support_service
        )
        research_services = build_stock_analysis_research_services(
            case_store=self.case_store,
            scenario_engine=self.scenario_engine,
            attribution_engine=self.attribution_engine,
            repository=self.repository,
            controller_provider=self._controller_provider,
            governance_service_factory=TrainingGovernanceService,
            normalize_as_of_date=self._normalize_as_of_date,
            resolve_effective_as_of_date=self._resolve_effective_as_of_date,
            logger_instance=logger,
        )
        self.research_resolution_service = (
            research_services.research_resolution_service
        )
        self.research_bridge_service = research_services.research_bridge_service
        self.ask_stock_execution_orchestration_service = (
            AskStockExecutionOrchestrationService(
                build_execution_planning_bundle=lambda **kwargs: self._build_execution_planning_bundle(
                    **kwargs
                ),
                run_react_executor=lambda **kwargs: self._run_react_executor(
                    **kwargs
                ),
                build_execution_coverage=lambda **kwargs: self._build_execution_coverage(
                    **kwargs
                ),
                derive_signals=lambda execution: self._derive_signals(execution),
                build_research_bridge=lambda **kwargs: self._build_research_bridge(
                    **kwargs
                ),
                research_resolution_service=self.research_resolution_service,
                dashboard_projection_builder=lambda **kwargs: build_dashboard_projection(
                    **kwargs
                ),
            )
        )

    def list_strategies(self) -> list[dict[str, Any]]:
        return self.strategy_store.list_strategies()

    def get_daily_history(self, query: str, *, days: int = 60) -> dict[str, Any]:
        window_context, unavailable = self._resolve_window_context(
            query,
            days=days,
            minimum=10,
            summary="未找到历史K线数据",
            next_actions=["确认股票代码或先同步本地历史数据"],
        )
        if unavailable is not None:
            unavailable["items"] = []
            return unavailable
        assert window_context is not None
        code = window_context.code
        security = window_context.security
        frame = window_context.frame
        items = frame.to_dict(orient="records")
        return self._build_tool_records_response(
            query=query,
            code=code,
            security=security,
            records_key="items",
            records=items,
            summary=f"已获取 {len(items)} 条历史K线",
            next_actions=["可继续做趋势和结构分析"],
            artifacts={
                "last_trade_date": items[-1].get("trade_date") if items else None
            },
        )

    def get_realtime_quote(self, query: str) -> dict[str, Any]:
        context, unavailable = self._resolve_price_query_context(
            query,
            summary="未找到最新报价",
            next_actions=["确认股票代码或先同步本地日线数据"],
        )
        if unavailable is not None:
            return unavailable
        code = context.code
        security = dict(context.security)
        frame = cast(pd.DataFrame, context.price_frame)
        row = dict(frame.tail(1).to_dict(orient="records")[0])
        return build_tool_response(
            status="ok",
            query=query,
            code=code,
            security=security,
            quote=row,
            summary=f"最新参考价格 {row.get('close')}",
            next_actions=["可据此计算入场位和止损位"],
            artifacts={"trade_date": row.get("trade_date")},
        )

    @staticmethod
    def _empty_snapshot() -> dict[str, Any]:
        return BatchAnalysisViewService.empty_snapshot()

    def _resolve_query_context(self, query: str) -> StockQueryContext:
        return cast(
            StockQueryContext,
            self.tool_runtime_support_service.resolve_query_context(query),
        )

    def _resolve_price_query_context(
        self,
        query: str,
        *,
        summary: str,
        next_actions: list[str],
        status: str = "not_found",
    ) -> tuple[StockQueryContext, dict[str, Any] | None]:
        context, unavailable = (
            self.tool_runtime_support_service.resolve_price_query_context(
                query,
                summary=summary,
                next_actions=next_actions,
                status=status,
            )
        )
        return cast(StockQueryContext, context), unavailable

    def _resolve_window_context(
        self,
        query: str,
        *,
        days: int,
        minimum: int,
        summary: str,
        next_actions: list[str],
        status: str = "not_found",
        copy_frame: bool = False,
    ) -> tuple[StockWindowContext | None, dict[str, Any] | None]:
        context, unavailable = self.tool_runtime_support_service.resolve_window_context(
            query,
            days=days,
            minimum=minimum,
            summary=summary,
            next_actions=next_actions,
            status=status,
            copy_frame=copy_frame,
        )
        return cast(StockWindowContext | None, context), unavailable

    def _build_batch_analysis_context(
        self, frame: pd.DataFrame, code: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return self.batch_analysis_service.build_batch_analysis_context(frame, code)

    def _view_from_snapshot(
        self, summary: dict[str, Any], snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        return self.batch_analysis_service.view_from_snapshot(summary, snapshot)

    @staticmethod
    def _tail_frame(
        frame: pd.DataFrame, *, days: int, minimum: int = 1
    ) -> pd.DataFrame:
        return StockAnalysisToolRuntimeSupportService.tail_frame(
            frame,
            days=days,
            minimum=minimum,
        )

    @staticmethod
    def _resolve_frame_date_window(
        frame: pd.DataFrame, *, days: int
    ) -> tuple[str, str]:
        return StockAnalysisToolRuntimeSupportService.resolve_frame_date_window(
            frame,
            days=days,
        )

    def _resolve_price_window(
        self, frame: pd.DataFrame, *, days: int
    ) -> dict[str, str]:
        return StockAnalysisToolRuntimeSupportService.resolve_price_window(
            frame,
            days=days,
        )

    def _build_snapshot_projection(
        self, frame: pd.DataFrame, code: str
    ) -> dict[str, Any]:
        return self.stock_analysis_projection_service.build_snapshot_projection(
            frame,
            code,
        )

    def _build_tool_unavailable_response(
        self,
        *,
        status: str,
        query: str,
        code: str,
        summary: str,
        next_actions: list[str],
        artifacts: dict[str, Any] | None = None,
        security: dict[str, Any] | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        return build_tool_unavailable_response(
            status=status,
            query=query,
            code=code,
            security=security,
            summary=summary,
            next_actions=next_actions,
            artifacts=artifacts,
            **payload,
        )

    def _build_tool_analysis_response(
        self,
        *,
        query: str,
        code: str,
        security: dict[str, Any] | None,
        summary: str,
        next_actions: list[str],
        artifacts: dict[str, Any] | None = None,
        observation_summary: str = "",
        **payload: Any,
    ) -> dict[str, Any]:
        return build_tool_analysis_response(
            query=query,
            code=code,
            security=security,
            summary=summary,
            next_actions=next_actions,
            artifacts=artifacts,
            observation_summary=observation_summary,
            **payload,
        )

    def _build_tool_records_response(
        self,
        *,
        query: str,
        code: str,
        security: dict[str, Any] | None,
        records_key: str,
        records: list[dict[str, Any]],
        summary: str,
        next_actions: list[str],
        artifacts: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        return build_tool_records_response(
            query=query,
            code=code,
            security=security,
            records_key=records_key,
            records=records,
            summary=summary,
            next_actions=next_actions,
            artifacts=artifacts,
            metrics=metrics,
            **payload,
        )

    def analyze_trend(self, query: str, *, days: int = 120) -> dict[str, Any]:
        window_context, unavailable = self._resolve_window_context(
            query,
            days=days,
            minimum=30,
            summary="未找到趋势分析所需数据",
            next_actions=["先检查数据或改用更长时间范围"],
        )
        if unavailable is not None:
            return unavailable
        assert window_context is not None
        code = window_context.code
        security = window_context.security
        frame = window_context.frame
        projection = self._build_snapshot_projection(frame, code)
        snapshot = dict(projection["snapshot"])
        meta = dict(projection["meta"])
        view = dict(projection["view"])
        trend = view["trend"]
        return self._build_tool_analysis_response(
            query=query,
            code=code,
            security=security,
            signal=view["signal"],
            structure=view["structure"],
            summary=view["summary"],
            indicator_snapshot=snapshot,
            trend=trend,
            observation_summary=(
                f"趋势={view['signal']}, 结构={view['structure']}, "
                f"MA20={trend.get('ma20', 0.0):.2f}, RSI={trend.get('rsi_14', 50.0):.1f}"
            ),
            next_actions=["结合支撑阻力、资金流和最新价格生成结论"],
            artifacts={
                "cutoff_date": meta["cutoff"],
                "latest_trade_date": snapshot.get("latest_trade_date"),
            },
        )

    def get_indicator_snapshot(self, query: str, *, days: int = 180) -> dict[str, Any]:
        window_context, unavailable = self._resolve_window_context(
            query,
            days=days,
            minimum=30,
            summary="未找到指标快照所需数据",
            next_actions=["确认股票代码或同步本地行情数据"],
        )
        if unavailable is not None:
            return unavailable
        assert window_context is not None
        code = window_context.code
        security = window_context.security
        frame = window_context.frame
        projection = self._build_snapshot_projection(frame, code)
        snapshot = dict(projection["snapshot"])
        fields = dict(projection["fields"])
        indicators = dict(fields["indicators"])
        macd_payload = dict(fields["macd_payload"])
        return self._build_tool_analysis_response(
            query=query,
            code=code,
            security=security,
            days=int(days),
            snapshot=snapshot,
            summary="已生成指标快照",
            observation_summary=(
                f"RSI={indicators.get('rsi_14')}, MA栈={indicators.get('ma_stack')}, "
                f"MACD={macd_payload.get('cross', 'neutral')}"
            ),
            next_actions=["可继续分析支撑阻力、资金流或最新价格位置"],
            artifacts={"latest_trade_date": snapshot.get("latest_trade_date")},
        )

    def analyze_support_resistance(
        self, query: str, *, days: int = 120
    ) -> dict[str, Any]:
        window_context, unavailable = self._resolve_window_context(
            query,
            days=days,
            minimum=30,
            summary="未找到支撑阻力分析所需数据",
            next_actions=["确认股票代码或同步本地行情数据"],
            copy_frame=True,
        )
        if unavailable is not None:
            return unavailable
        assert window_context is not None
        code = window_context.code
        security = window_context.security
        frame = window_context.frame
        highs = _numeric_series(frame, "high").dropna()
        lows = _numeric_series(frame, "low").dropna()
        closes = _numeric_series(frame, "close").dropna()
        if highs.empty or lows.empty or closes.empty:
            return self._build_tool_unavailable_response(
                status="not_found",
                query=query,
                code=code,
                summary="高低收盘序列不可用",
                next_actions=["检查行情数据完整性"],
            )
        projection = self._build_snapshot_projection(frame, code)
        snapshot = dict(projection["snapshot"])
        fields = dict(projection["fields"])
        latest_close = float(fields["latest_close"] or closes.iloc[-1] or 0.0)
        support_20 = (
            float(lows.tail(20).min()) if len(lows) >= 20 else float(lows.min())
        )
        resistance_20 = (
            float(highs.tail(20).max()) if len(highs) >= 20 else float(highs.max())
        )
        support_60 = float(lows.tail(60).min()) if len(lows) >= 60 else support_20
        resistance_60 = (
            float(highs.tail(60).max()) if len(highs) >= 60 else resistance_20
        )
        atr = float(fields.get("atr_14") or 0.0)
        distance_to_support = (
            0.0 if latest_close <= 0 else (latest_close - support_20) / latest_close
        )
        distance_to_resistance = (
            0.0 if latest_close <= 0 else (resistance_20 - latest_close) / latest_close
        )
        bias = "neutral"
        if latest_close >= resistance_20 * 0.985:
            bias = "breakout_test"
        elif latest_close <= support_20 * 1.015:
            bias = "support_test"
        return self._build_tool_analysis_response(
            query=query,
            code=code,
            security=security,
            levels={
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
            summary="已识别关键支撑阻力位",
            observation_summary=(
                f"支撑{support_20:.2f}/阻力{resistance_20:.2f}, "
                f"距支撑{distance_to_support * 100:.1f}%, 距阻力{distance_to_resistance * 100:.1f}%"
            ),
            next_actions=["结合趋势、资金流和价格确认入场/风控位置"],
            artifacts={"latest_trade_date": snapshot.get("latest_trade_date")},
        )

    def get_capital_flow(self, query: str, *, days: int = 20) -> dict[str, Any]:
        context, unavailable = self._resolve_price_query_context(
            query,
            summary="未找到资金流查询所需行情基线",
            next_actions=["确认股票代码或同步本地行情数据"],
        )
        if unavailable is not None:
            return unavailable
        code = context.code
        security = dict(context.security)
        price_frame = cast(pd.DataFrame, context.price_frame)
        price_window = self._resolve_price_window(price_frame, days=days)
        start_date = str(price_window["start_date"])
        end_date = str(price_window["end_date"])
        frame = self.repository.query_capital_flow_daily(
            codes=[code], start_date=start_date, end_date=end_date
        )
        if frame.empty:
            frame = (
                self.repository.query_capital_flow_daily(
                    codes=[code],
                    end_date=self._current_analysis_cutoff(),
                )
                .sort_values("trade_date")
                .tail(max(1, int(days)))
            )
        if frame.empty:
            return self._build_tool_unavailable_response(
                status="no_data",
                query=query,
                code=code,
                security=security,
                summary="本地暂无资金流数据",
                next_actions=["可继续依赖价格与指标工具分析，或补充资金流数据"],
                artifacts={"start_date": start_date, "end_date": end_date},
            )
        frame = frame.sort_values("trade_date").tail(max(1, int(days)))
        latest = dict(frame.tail(1).to_dict(orient="records")[0])
        main_sum = float(_numeric_series(frame, "main_net_inflow").fillna(0).sum())
        ratio_mean = float(
            _numeric_series(frame, "main_net_inflow_ratio").fillna(0).mean()
        )
        direction = "inflow" if main_sum > 0 else "outflow" if main_sum < 0 else "flat"
        return self._build_tool_records_response(
            query=query,
            code=code,
            security=security,
            records_key="items",
            records=frame.to_dict(orient="records"),
            metrics={
                "main_net_inflow_sum": round(main_sum, 2),
                "main_net_inflow_ratio_avg": round(ratio_mean, 4),
                "latest_trade_date": latest.get("trade_date"),
                "direction": direction,
            },
            summary=f"近{len(frame)}日主力资金{direction}",
            next_actions=["结合趋势与支撑阻力判断资金是否确认当前结构"],
            artifacts={"start_date": start_date, "end_date": end_date},
            observation_summary=f"主力净流入合计={main_sum:.2f}, 平均占比={ratio_mean:.2f}",
        )

    def get_intraday_context(self, query: str, *, days: int = 5) -> dict[str, Any]:
        context, unavailable = self._resolve_price_query_context(
            query,
            summary="未找到分时上下文查询所需行情基线",
            next_actions=["确认股票代码或同步本地行情数据"],
        )
        if unavailable is not None:
            return unavailable
        code = context.code
        security = dict(context.security)
        daily = cast(pd.DataFrame, context.price_frame)
        price_window = self._resolve_price_window(daily, days=days)
        start_date = str(price_window["start_date"])
        end_date = str(price_window["end_date"])
        frame = self.repository.query_intraday_bars_60m(
            codes=[code], start_date=start_date, end_date=end_date
        )
        if frame.empty:
            return self._build_tool_unavailable_response(
                status="no_data",
                query=query,
                code=code,
                security=security,
                summary="本地暂无60分钟数据",
                next_actions=["仍可使用日线与指标工具完成分析"],
                artifacts={"start_date": start_date, "end_date": end_date},
            )
        latest_day = str(frame["trade_date"].max())
        latest = cast(pd.DataFrame, frame[frame["trade_date"] == latest_day].copy())
        latest = cast(pd.DataFrame, latest.sort_values(by=["bar_time"]))
        close_series = _numeric_series(latest, "close")
        high_series = _numeric_series(latest, "high")
        low_series = _numeric_series(latest, "low")
        first_close = float(close_series.iloc[0])
        last_close = float(close_series.iloc[-1])
        day_range = float(high_series.max() - low_series.min())
        intraday_bias = (
            "up"
            if last_close > first_close
            else "down"
            if last_close < first_close
            else "flat"
        )
        return self._build_tool_records_response(
            query=query,
            code=code,
            security=security,
            records_key="bars",
            records=latest.to_dict(orient="records"),
            metrics={
                "latest_trade_date": latest_day,
                "first_close": round(first_close, 2),
                "last_close": round(last_close, 2),
                "day_range": round(day_range, 2),
                "intraday_bias": intraday_bias,
            },
            summary=f"最新60分钟结构偏{intraday_bias}",
            next_actions=["必要时用来确认日线结论是否得到短周期配合"],
            artifacts={"start_date": start_date, "end_date": end_date},
            observation_summary=f"首收={first_close:.2f}, 末收={last_close:.2f}, 振幅={day_range:.2f}",
        )

    def ask_stock(
        self,
        *,
        question: str,
        query: str,
        strategy: str = "chan_theory",
        days: int = 60,
        as_of_date: str = "",
    ) -> dict[str, Any]:
        request_context = self._build_ask_stock_request_context(
            question=question,
            query=query,
            strategy=strategy,
            days=days,
            as_of_date=as_of_date,
        )
        with self._analysis_scope(request_context.effective_as_of_date):
            execution_bundle = self._run_ask_stock_execution_stage(
                request_context=request_context,
            )
        research_resolution = self._resolve_ask_stock_research_outputs(
            request_context=request_context,
            execution_bundle=execution_bundle,
        )
        stage_contract = self._build_ask_stock_stage_contract(
            request_context=request_context,
            execution_bundle=execution_bundle,
            research_resolution=research_resolution,
        )
        assembly_stage = self.ask_stock_response_assembly_service.build_assembly_stage_bundle(
            stage_contract=stage_contract,
        )
        response_assembly_spec = self.ask_stock_response_assembly_service.build_response_assembly_spec(
            response_inputs=assembly_stage.response_inputs,
        )
        return response_assembly_spec.render_protocol_response()

    def _build_ask_stock_request_context(
        self,
        *,
        question: str,
        query: str,
        strategy: str,
        days: int,
        as_of_date: str,
    ) -> AskStockRequestContext:
        return self.ask_stock_request_context_service.build_request_context(
            question=question,
            query=query,
            strategy=strategy,
            days=days,
            as_of_date=as_of_date,
        )

    def _run_ask_stock_execution_stage(
        self,
        *,
        request_context: AskStockRequestContext,
    ) -> AskStockExecutionRunBundle:
        return self.ask_stock_execution_orchestration_service.run_execution_stage(
            request_context=request_context,
        )

    def _build_ask_stock_stage_contract(
        self,
        *,
        request_context: AskStockRequestContext,
        execution_bundle: AskStockExecutionRunBundle,
        research_resolution: dict[str, Any],
    ) -> AskStockStageContract:
        return self.ask_stock_response_assembly_service.build_stage_contract(
            request_context=request_context,
            execution_bundle=execution_bundle,
            research_resolution=research_resolution,
        )

    def _resolve_ask_stock_research_outputs(
        self,
        *,
        request_context: AskStockRequestContext,
        execution_bundle: AskStockExecutionRunBundle,
    ) -> dict[str, Any]:
        return self.ask_stock_execution_orchestration_service.resolve_research_outputs(
            request_context=request_context,
            execution_bundle=execution_bundle,
        )

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
        return self.research_bridge_service.build_research_bridge(
            code=code,
            security=security,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            days=days,
            derived=derived,
        )

    @staticmethod
    def _normalize_as_of_date(value: str | None = None) -> str:
        raw = str(value or "").strip()
        return normalize_date(raw) if raw else ""

    def _current_analysis_cutoff(self) -> str | None:
        return self._normalize_as_of_date(self._analysis_as_of_date) or None

    def _resolve_effective_as_of_date(
        self, code: str, requested_as_of_date: str = ""
    ) -> str:
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
        frame = self.repository.get_stock(
            code, cutoff_date=self._current_analysis_cutoff()
        )
        if frame.empty:
            return frame
        result = frame.copy()
        if "trade_date" in result.columns:
            result["trade_date"] = result["trade_date"].astype(str)
            result = result.sort_values("trade_date").reset_index(drop=True)
        return result

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
        return self.strategy_store.load_strategy(name)

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

    def _resolve_strategy_name(
        self, *, question: str, strategy: str
    ) -> tuple[str, str]:
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
                    return str(
                        item.get("name") or explicit or "chan_theory"
                    ), "inferred"
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
        planning_bundle: StockExecutionPlanningBundle | None = None,
    ) -> dict[str, Any]:
        resolved_planning_bundle = planning_bundle or self._build_execution_planning_bundle(
            strategy=strategy,
            query=query,
            days=days,
        )
        if self.enable_llm_react and strategy.react_enabled and self.gateway.available:
            try:
                llm_execution = self._execute_plan_with_llm(
                    question=question,
                    query=query,
                    security=security,
                    strategy=strategy,
                    days=days,
                    allowed_tools=resolved_planning_bundle.allowed_tools,
                )
                if llm_execution.get("tool_calls"):
                    return self._apply_yaml_gap_fill(
                        execution=llm_execution,
                        strategy=strategy,
                        plan=resolved_planning_bundle.plan,
                        allowed_tools=resolved_planning_bundle.allowed_tools,
                    )
            except _STOCK_LLM_REACT_FALLBACK_EXCEPTIONS as exc:  # pragma: no cover
                logger.warning(
                    "stock llm react failed for %s via %s, fallback to yaml plan: %s",
                    query,
                    strategy.name,
                    exc,
                )
        return self._execute_plan_deterministic(
            plan=resolved_planning_bundle.plan,
            fallback_reason="llm_react_unavailable_or_empty",
        )

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


__all__ = [
    "BatchAnalysisViewService",
    "ResearchResolutionService",
    "StockAnalysisService",
    "StockAnalysisStrategy",
    "StockAnalysisStrategyStore",
    "StockToolPlanStep",
]
