from types import SimpleNamespace
from typing import Any, cast

import pandas as pd

from invest_evolution.application import stock_analysis as stock_analysis_module
from invest_evolution.application.stock_analysis_response_contracts import (
    AskStockIdentifiersProjection,
    AskStockPresentationSpec,
    AskStockRequestProjection,
    AskStockResponseAssemblySpec,
    AskStockResponseHeaderFactory,
    AskStockResponseHeaderSpec,
    AskStockResponseRequestHeader,
    AskStockResponseResolutionHeader,
    AskStockResponseStrategyHeader,
    AskStockSectionsBundle,
    ToolObservationEnvelope,
)
from invest_evolution.application.stock_analysis_resolution_contracts import (
    PreDashboardAnalysisSpec,
    ResearchPersistenceAttributionProjection,
    ResearchPersistenceProjection,
    ResearchResolutionDetailSectionUpdates,
    ResearchResolutionDetailUpdatesBundle,
    ResearchResolutionDisplayContract,
    ResearchResolutionIdentifiers,
    ResearchResolutionPayloadFactory,
    ResearchResolutionPersistence,
    ResearchResolutionDashboardProjectionFactory,
    ResearchResolutionBasePayload,
)
from invest_evolution.application.stock_analysis_batch_service import (
    BatchAnalysisViewService,
    project_snapshot_fields,
)
from invest_evolution.application.stock_analysis_projection_service import (
    StockAnalysisProjectionService,
    StockIndicatorProjection,
    build_indicator_projection,
)
from invest_evolution.application.stock_analysis_observation_service import (
    StockAnalysisObservationService,
    observation_envelope,
    observation_section,
    project_tool_observation,
)
from invest_evolution.application.stock_analysis_prompt_service import (
    StockAnalysisPromptService,
    build_llm_assistant_tool_message,
    build_llm_tool_result_message,
    build_stock_user_prompt,
    default_thought,
    stock_system_prompt,
    stock_tool_definitions,
)
from invest_evolution.application.stock_analysis_parsing_service import (
    StockAnalysisParsingService,
    parse_tool_args,
    render_template_args,
)
from invest_evolution.application.stock_analysis_support_services import (
    StockAnalysisSupportServices,
    build_stock_analysis_support_services,
)
from invest_evolution.application.stock_analysis_research_bridge_service import (
    StockAnalysisResearchBridgeService as ExtractedStockAnalysisResearchBridgeService,
)
from invest_evolution.application.stock_analysis_research_services import (
    StockAnalysisResearchServices,
    build_stock_analysis_research_services,
)
from invest_evolution.application.stock_analysis_research_resolution_service import (
    ResearchResolutionService as ExtractedResearchResolutionService,
)
from invest_evolution.application.stock_analysis_tool_catalog import (
    _build_stock_tool_catalog,
    _stock_tool_parameters,
)
from invest_evolution.application.stock_analysis_tool_runtime import (
    StockAnalysisToolRuntimeSupportService,
)


def test_stock_tool_catalog_builder_keeps_alias_and_function_shapes():
    catalog = _build_stock_tool_catalog()

    assert "get_realtime_quote" in catalog.by_name
    assert catalog.aliases["get_latest_quote"] == "get_realtime_quote"
    assert catalog.definitions_by_name["get_daily_history"]["function"]["parameters"] == {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "days": {"type": "integer", "minimum": 30, "maximum": 500},
        },
        "required": ["query"],
    }
    assert catalog.definitions_by_name["get_realtime_quote"]["function"]["parameters"] == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }


def test_stock_tool_parameters_optional_days_behavior():
    with_days = _stock_tool_parameters(include_days=True, minimum=10, maximum=20)
    without_days = _stock_tool_parameters(include_days=False)

    assert with_days["properties"]["days"] == {
        "type": "integer",
        "minimum": 10,
        "maximum": 20,
    }
    assert "days" not in without_days["properties"]


def test_ask_stock_response_assembly_contract_payload_shape():
    header_factory = AskStockResponseHeaderFactory(
        spec=AskStockResponseHeaderSpec(
            request=AskStockResponseRequestHeader(
                request=AskStockRequestProjection(
                    question="请分析 FooBank",
                    query="FooBank",
                    normalized_query="foobank",
                    requested_as_of_date="20240131",
                    as_of_date="20240130",
                )
            ),
            resolution=AskStockResponseResolutionHeader(
                identifiers=AskStockIdentifiersProjection(
                    policy_id="p1",
                    research_case_id="c1",
                    attribution_id="a1",
                ),
                security={"code": "sh.600001", "name": "FooBank"},
            ),
            strategy=AskStockResponseStrategyHeader(
                entrypoint={"domain": "stock"},
                strategy_payload={"name": "chan_theory"},
                strategy_source="inferred",
                days=120,
            ),
        )
    )
    presentation = AskStockPresentationSpec(
        task_bus={"schema_version": "task_bus.v2"},
        protocol={"schema_version": "bounded_workflow.v2", "operation": "ask_stock"},
        artifacts={"code": "sh.600001"},
        coverage={"required_tool_coverage": 1.0},
        artifact_taxonomy={"schema_version": "artifact_taxonomy.v2"},
        sections=AskStockSectionsBundle(
            analysis={"summary": "analysis"},
            research={"summary": "research"},
        ),
    )

    spec = AskStockResponseAssemblySpec(
        header_factory=header_factory,
        presentation_spec=presentation,
        orchestration={"mode": "yaml_react_like"},
        dashboard={"signal": "hold"},
    )
    payload = spec.render_protocol_response()

    assert payload["status"] == "ok"
    assert payload["query"] == "FooBank"
    assert payload["policy_id"] == "p1"
    assert payload["strategy"]["name"] == "chan_theory"
    assert payload["dashboard"]["signal"] == "hold"
    assert payload["protocol"]["operation"] == "ask_stock"
    assert payload["task_bus"]["schema_version"] == "task_bus.v2"


def test_tool_observation_envelope_fallback_and_payload():
    non_mapping = ToolObservationEnvelope.from_result("bad_result")
    mapping = ToolObservationEnvelope.from_result(
        {
            "status": "warn",
            "message": "primary",
            "next_actions": ["retry"],
            "artifacts": {"hint": 1},
        },
        summary_keys=("message", "summary"),
    )

    assert non_mapping.status == "unknown"
    assert non_mapping.summary == "unknown result"
    assert mapping.to_dict(code="sh.600001") == {
        "status": "warn",
        "summary": "primary",
        "code": "sh.600001",
        "next_actions": ["retry"],
        "artifacts": {"hint": 1},
    }


def test_extracted_observation_helpers_preserve_section_and_projection_shape():
    result = {
        "status": "ok",
        "summary": "done",
        "next_actions": ["next"],
        "artifacts": {"scope": "x"},
        "metrics": {"direction": "inflow"},
    }

    envelope = observation_envelope(result)

    assert envelope.status == "ok"
    assert observation_section(result, "metrics") == {"direction": "inflow"}
    assert project_tool_observation(
        result,
        direction="inflow",
    ) == {
        "status": "ok",
        "summary": "done",
        "direction": "inflow",
        "next_actions": ["next"],
        "artifacts": {"scope": "x"},
    }


def test_stock_analysis_module_keeps_compat_exports_for_helpers():
    assert callable(stock_analysis_module._build_stock_tool_catalog)
    assert stock_analysis_module.ToolObservationEnvelope is ToolObservationEnvelope
    assert stock_analysis_module.AskStockResponseHeaderFactory is AskStockResponseHeaderFactory
    assert stock_analysis_module.StockIndicatorProjection is StockIndicatorProjection


def test_prompt_helpers_filter_tool_definitions_and_default_thought():
    definitions = {
        "get_daily_history": {"function": {"name": "get_daily_history"}},
        "get_indicator_snapshot": {"function": {"name": "get_indicator_snapshot"}},
    }

    assert stock_tool_definitions(
        definitions,
        allowed_tools=["get_indicator_snapshot"],
    ) == [{"function": {"name": "get_indicator_snapshot"}}]
    assert default_thought(
        "get_daily_history",
        normalize_tool_name=lambda name: name,
        catalog_by_name={"get_daily_history": {"thought": "先拉日线"}},
    ) == "先拉日线"


def test_prompt_helpers_build_llm_messages_and_user_prompt():
    choice = SimpleNamespace(content="先分析",)
    tool_call = SimpleNamespace(
        id="tc1",
        function=SimpleNamespace(name="get_daily_history", arguments='{"query":"Foo"}'),
    )

    assert build_llm_assistant_tool_message(
        choice=choice,
        tool_calls=[tool_call],
    ) == {
        "role": "assistant",
        "content": "先分析",
        "tool_calls": [
            {
                "id": "tc1",
                "type": "function",
                "function": {
                    "name": "get_daily_history",
                    "arguments": '{"query":"Foo"}',
                },
            }
        ],
    }
    assert build_llm_tool_result_message(
        tool_call_id="tc1",
        tool_name="get_daily_history",
        result={"status": "ok"},
    ) == {
        "role": "tool",
        "tool_call_id": "tc1",
        "name": "get_daily_history",
        "content": '{"status": "ok"}',
    }
    prompt = build_stock_user_prompt(
        question="分析 Foo",
        query="Foo",
        security={"name": "FooBank"},
        strategy=SimpleNamespace(
            display_name="缠论",
            name="chan_theory",
            description="desc",
            required_tools=["get_daily_history", "get_indicator_snapshot"],
            analysis_steps=["a", "b"],
            core_rules=["r1"],
            entry_conditions=["e1"],
            scoring={"x": 1},
            planner_prompt="先结构后指标",
        ),
        days=120,
    )
    assert "Target security: FooBank (Foo)" in prompt
    assert "Suggested planner prompt: 先结构后指标" in prompt
    assert "bounded stock-analysis planning agent" in stock_system_prompt()


def test_prompt_service_uses_runtime_catalog_providers():
    service = StockAnalysisPromptService(
        normalize_tool_name=lambda tool_name: tool_name.strip(),
        catalog_by_name_provider=lambda: {"get_daily_history": {"thought": "先拉日线"}},
        definitions_by_name_provider=lambda: {
            "get_daily_history": {"function": {"name": "get_daily_history"}},
            "get_indicator_snapshot": {"function": {"name": "get_indicator_snapshot"}},
        },
    )

    assert service.default_thought("get_daily_history") == "先拉日线"
    assert service.stock_tool_definitions(
        allowed_tools=["get_indicator_snapshot"]
    ) == [{"function": {"name": "get_indicator_snapshot"}}]


def test_parsing_helpers_render_nested_templates_and_parse_tool_args():
    rendered = render_template_args(
        {
            "query": "{{query}}",
            "days": "{{days}}",
            "nested": {"trend": "{{trend_days}}"},
            "items": ["{{history_days}}", {"q": "{{query}}"}],
        },
        query="Foo",
        days=60,
    )

    assert rendered == {
        "query": "Foo",
        "days": 60,
        "nested": {"trend": "120"},
        "items": ["60", {"q": "Foo"}],
    }
    assert parse_tool_args(None) == {}
    assert parse_tool_args("") == {}
    assert parse_tool_args("   ") == {}
    assert parse_tool_args('{"query":"Foo","days":120}') == {
        "query": "Foo",
        "days": 120,
    }


def test_parsing_helpers_reject_invalid_tool_arg_shapes():
    for raw in ('["x"]', "1", False):
        try:
            parse_tool_args(raw)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {raw!r}")


def test_parsing_service_wraps_render_and_parse_helpers():
    service = StockAnalysisParsingService()

    assert service.render_template_args(
        {"days": "{{days}}", "trend": "{{trend_days}}"},
        query="Foo",
        days=30,
    ) == {"days": 30, "trend": 120}
    assert service.parse_tool_args('{"query":"Foo"}') == {"query": "Foo"}


def test_support_services_bundle_builds_expected_service_types():
    bundle = build_stock_analysis_support_services(
        humanize_macd_cross=lambda value: f"pretty:{value}",
        resolve_strategy_name=lambda question, strategy: (strategy or question, "explicit"),
        infer_days=lambda question, default_days: default_days,
        load_strategy=lambda name: {"name": name},
        resolve_query_context=lambda query: SimpleNamespace(
            query=query,
            code="sh.600001",
            security={"name": query},
            price_frame=pd.DataFrame([{"trade_date": "20240101", "close": 10.0}]),
        ),
        resolve_effective_as_of_date=lambda code, as_of_date: as_of_date or "20240101",
        normalize_as_of_date=lambda as_of_date: str(as_of_date or ""),
        available_tools_provider=lambda: ["get_daily_history"],
        normalize_tool_name=lambda tool_name: tool_name,
        catalog_by_name_provider=lambda: {"get_daily_history": {"thought": "先拉日线"}},
        definitions_by_name_provider=lambda: {
            "get_daily_history": {"function": {"name": "get_daily_history"}}
        },
        build_indicator_projection=lambda snapshot, **_kwargs: SimpleNamespace(
            snapshot=dict(snapshot),
            indicators={},
            macd_payload={},
        ),
        build_batch_analysis_context=lambda frame, code: (
            {"close": 10.0},
            {"latest_close": 10.0, "indicators": {}},
            {"cutoff": "20240101", "code": code},
        ),
        view_from_snapshot=lambda summary, snapshot: {
            "summary": summary,
            "trend": {},
            "signal": "observe",
            "structure": "range",
        },
        snapshot_projector=lambda snapshot, **_kwargs: {
            "snapshot": dict(snapshot),
            "indicators": dict(snapshot.get("indicators") or {}),
            "macd_payload": {},
            "boll": {},
            "latest_close": snapshot.get("latest_close"),
            "ma5": None,
            "ma10": None,
            "ma20": None,
            "ma60": None,
            "volume_ratio": None,
            "rsi": 50.0,
            "ma_stack": "mixed",
            "macd_cross": "neutral",
            "atr_14": None,
        },
        resolve_security=lambda query: ("sh.600001", {"name": query}),
        get_stock_frame=lambda _code: pd.DataFrame([{"trade_date": "20240101", "close": 10.0}]),
        build_tool_unavailable_response=lambda **kwargs: kwargs,
        query_context_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        window_context_factory=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    assert isinstance(bundle, StockAnalysisSupportServices)
    assert isinstance(bundle.stock_analysis_parsing_service, StockAnalysisParsingService)
    assert bundle.stock_analysis_prompt_service.default_thought("get_daily_history") == "先拉日线"
    assert bundle.tool_runtime_support_service.resolve_price_window(
        pd.DataFrame([{"trade_date": "20240101", "close": 10.0}]),
        days=1,
    ) == {"start_date": "20240101", "end_date": "20240101"}


def test_support_services_bundle_preserves_dynamic_projection_provider():
    calls = {"count": 0}

    def _build_batch_analysis_context(frame, code):
        calls["count"] += 1
        return (
            {"close": 10.0},
            {"latest_close": 10.0, "indicators": {}},
            {"cutoff": "20240101", "code": code},
        )

    bundle = build_stock_analysis_support_services(
        humanize_macd_cross=lambda value: value,
        resolve_strategy_name=lambda question, strategy: (strategy or question, "explicit"),
        infer_days=lambda question, default_days: default_days,
        load_strategy=lambda name: {"name": name},
        resolve_query_context=lambda query: SimpleNamespace(query=query, code="sh.600001", security={}, price_frame=pd.DataFrame()),
        resolve_effective_as_of_date=lambda code, as_of_date: as_of_date,
        normalize_as_of_date=lambda as_of_date: str(as_of_date or ""),
        available_tools_provider=lambda: [],
        normalize_tool_name=lambda tool_name: tool_name,
        catalog_by_name_provider=lambda: {},
        definitions_by_name_provider=lambda: {},
        build_indicator_projection=lambda snapshot, **_kwargs: SimpleNamespace(snapshot=dict(snapshot), indicators={}, macd_payload={}),
        build_batch_analysis_context=lambda frame, code: _build_batch_analysis_context(frame, code),
        view_from_snapshot=lambda summary, snapshot: {"summary": summary, "trend": {}, "signal": "observe", "structure": "range"},
        snapshot_projector=lambda snapshot, **_kwargs: {
            "snapshot": dict(snapshot),
            "indicators": {},
            "macd_payload": {},
            "boll": {},
            "latest_close": snapshot.get("latest_close"),
            "ma5": None,
            "ma10": None,
            "ma20": None,
            "ma60": None,
            "volume_ratio": None,
            "rsi": 50.0,
            "ma_stack": "mixed",
            "macd_cross": "neutral",
            "atr_14": None,
        },
        resolve_security=lambda query: ("sh.600001", {"name": query}),
        get_stock_frame=lambda _code: pd.DataFrame(),
        build_tool_unavailable_response=lambda **kwargs: kwargs,
        query_context_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        window_context_factory=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    bundle.stock_analysis_projection_service.build_snapshot_projection(
        pd.DataFrame([{"trade_date": "20240101", "close": 10.0}]),
        "sh.600001",
    )

    assert calls["count"] == 1


def test_research_services_bundle_builds_expected_service_types():
    bundle = build_stock_analysis_research_services(
        case_store=SimpleNamespace(name="case_store"),
        scenario_engine=SimpleNamespace(name="scenario_engine"),
        attribution_engine=SimpleNamespace(name="attribution_engine"),
        repository=SimpleNamespace(name="repository"),
        controller_provider=lambda: SimpleNamespace(name="controller"),
        governance_service_factory=lambda: SimpleNamespace(name="governance"),
        normalize_as_of_date=lambda value: f"normalized:{value}",
        resolve_effective_as_of_date=lambda code, as_of_date: f"{code}:{as_of_date}",
        logger_instance=SimpleNamespace(name="logger"),
    )

    assert isinstance(bundle, StockAnalysisResearchServices)
    assert isinstance(
        bundle.research_resolution_service,
        ExtractedResearchResolutionService,
    )
    assert isinstance(
        bundle.research_bridge_service,
        ExtractedStockAnalysisResearchBridgeService,
    )
    assert (
        bundle.research_bridge_service.research_resolution_service
        is bundle.research_resolution_service
    )


def test_research_services_bundle_uses_fresh_governance_service_instances():
    calls = {"count": 0}

    def _build_governance_service():
        calls["count"] += 1
        return SimpleNamespace(name=f"governance:{calls['count']}")

    first_bundle = build_stock_analysis_research_services(
        case_store=SimpleNamespace(),
        scenario_engine=SimpleNamespace(),
        attribution_engine=SimpleNamespace(),
        repository=SimpleNamespace(),
        controller_provider=lambda: None,
        governance_service_factory=_build_governance_service,
        normalize_as_of_date=lambda value: str(value or ""),
        resolve_effective_as_of_date=lambda code, as_of_date: str(as_of_date or code),
        logger_instance=SimpleNamespace(),
    )
    second_bundle = build_stock_analysis_research_services(
        case_store=SimpleNamespace(),
        scenario_engine=SimpleNamespace(),
        attribution_engine=SimpleNamespace(),
        repository=SimpleNamespace(),
        controller_provider=lambda: None,
        governance_service_factory=_build_governance_service,
        normalize_as_of_date=lambda value: str(value or ""),
        resolve_effective_as_of_date=lambda code, as_of_date: str(as_of_date or code),
        logger_instance=SimpleNamespace(),
    )

    assert calls["count"] == 2
    assert (
        first_bundle.research_bridge_service.governance_service
        is not second_bundle.research_bridge_service.governance_service
    )


def test_tool_runtime_support_service_builds_unavailable_price_context_response():
    frame = pd.DataFrame(columns=["trade_date", "close"])
    recorded_calls: list[dict[str, Any]] = []

    service = StockAnalysisToolRuntimeSupportService(
        resolve_security=lambda query: ("sh.600001", {"name": query}),
        get_stock_frame=lambda _code: frame,
        build_tool_unavailable_response=lambda **kwargs: (
            recorded_calls.append(dict(kwargs)) or {"status": kwargs["status"]}
        ),
        query_context_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        window_context_factory=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    context, unavailable = service.resolve_price_query_context(
        "FooBank",
        summary="未找到最新报价",
        next_actions=["先同步本地历史数据"],
    )

    assert context.code == "sh.600001"
    assert context.security == {"name": "FooBank"}
    assert unavailable == {"status": "not_found"}
    assert recorded_calls == [
        {
            "status": "not_found",
            "query": "FooBank",
            "code": "sh.600001",
            "security": {"name": "FooBank"},
            "summary": "未找到最新报价",
            "next_actions": ["先同步本地历史数据"],
        }
    ]


def test_tool_runtime_support_service_resolves_window_and_price_window():
    frame = pd.DataFrame(
        [
            {"trade_date": "20240101", "close": 10.0},
            {"trade_date": "20240102", "close": 11.0},
            {"trade_date": "20240103", "close": 12.0},
        ]
    )

    service = StockAnalysisToolRuntimeSupportService(
        resolve_security=lambda query: ("sh.600001", {"name": query}),
        get_stock_frame=lambda _code: frame,
        build_tool_unavailable_response=lambda **kwargs: kwargs,
        query_context_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        window_context_factory=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    window_context, unavailable = service.resolve_window_context(
        "FooBank",
        days=2,
        minimum=2,
        summary="unused",
        next_actions=[],
    )

    assert unavailable is None
    assert window_context is not None
    assert list(window_context.frame["trade_date"]) == ["20240102", "20240103"]
    assert service.resolve_price_window(frame, days=2) == {
        "start_date": "20240102",
        "end_date": "20240103",
    }


def test_build_indicator_projection_preserves_payload_shape():
    projection = build_indicator_projection(
        {
            "latest_close": 11.2,
            "indicators": {
                "sma_5": 11.3,
                "sma_10": 11.0,
                "sma_20": 10.7,
                "sma_60": 10.2,
                "rsi_14": 58.0,
                "volume_ratio_5_20": 1.1,
                "ma_stack": "bullish",
                "atr_14": 0.8,
                "macd_12_26_9": {"cross": "golden_cross"},
                "bollinger_20": {"position": 0.6},
            },
        },
        snapshot_projector=project_snapshot_fields,
        summary={"close": 11.0},
    )

    assert projection.snapshot["latest_close"] == 11.2
    assert projection.indicators["ma_stack"] == "bullish"
    assert projection.macd_payload["cross"] == "golden_cross"
    assert projection.boll["position"] == 0.6
    assert projection.projected_fields["atr_14"] == 0.8


def test_projection_service_builds_snapshot_projection_shape():
    service = StockAnalysisProjectionService(
        build_batch_analysis_context=lambda frame, code: (
            {"close": 11.0},
            {"latest_close": 11.2, "indicators": {"sma_20": 10.7}},
            {"cutoff": "20240131", "frame_rows": len(frame), "code": code},
        ),
        view_from_snapshot=lambda summary, snapshot: {
            "summary": summary,
            "trend": {"ma20": snapshot["indicators"].get("sma_20")},
            "signal": "observe",
            "structure": "range",
        },
        snapshot_projector=lambda snapshot, **kwargs: {
            "snapshot": dict(snapshot),
            "indicators": dict(snapshot.get("indicators") or {}),
            "macd_payload": {},
            "boll": {},
            "latest_close": snapshot.get("latest_close"),
            "ma5": None,
            "ma10": None,
            "ma20": (dict(kwargs.get("trend_metrics") or {})).get("ma20"),
            "ma60": None,
            "volume_ratio": None,
            "rsi": 50.0,
            "ma_stack": "mixed",
            "macd_cross": "neutral",
            "atr_14": None,
        },
    )
    frame = pd.DataFrame([{"trade_date": "20240131", "close": 11.2}])

    payload = service.build_snapshot_projection(frame, "sh.600001")

    assert payload["summary"] == {"close": 11.0}
    assert payload["snapshot"]["latest_close"] == 11.2
    assert payload["meta"]["code"] == "sh.600001"
    assert payload["view"]["signal"] == "observe"
    assert payload["fields"]["ma20"] == 10.7


def test_observation_service_summarizes_indicator_snapshot_shape():
    service = StockAnalysisObservationService(
        build_indicator_projection=lambda snapshot, **_kwargs: SimpleNamespace(
            snapshot=dict(snapshot),
            indicators=dict((snapshot.get("indicators") or {})),
            macd_payload=dict(
                ((snapshot.get("indicators") or {}).get("macd_12_26_9") or {})
            ),
        )
    )

    payload = service.summarize_observation(
        "get_indicator_snapshot",
        {
            "status": "ok",
            "summary": "已生成指标快照",
            "observation_summary": "RSI=58.0",
            "snapshot": {
                "latest_close": 11.2,
                "indicators": {
                    "rsi_14": 58.0,
                    "ma_stack": "bullish",
                    "macd_12_26_9": {"cross": "golden_cross"},
                },
            },
            "next_actions": ["continue"],
            "artifacts": {"latest_trade_date": "20240131"},
        },
    )

    assert payload["status"] == "ok"
    assert payload["summary"] == "RSI=58.0"
    assert payload["latest_close"] == 11.2
    assert payload["rsi_14"] == 58.0
    assert payload["ma_stack"] == "bullish"
    assert payload["macd_cross"] == "golden_cross"


def test_research_resolution_display_contract_builds_payload_with_shared_updates():
    factory = ResearchResolutionPayloadFactory(
        base_payload=ResearchResolutionBasePayload(
            status="ok",
            requested_as_of_date="20240131",
            as_of_date="20240130",
        )
    )
    projection_factory = ResearchResolutionDashboardProjectionFactory(
        strategy=SimpleNamespace(core_rules=["rule-a"], entry_conditions=["entry-a"]),
        dashboard_projection_builder=lambda **kwargs: {
            "hypothesis": kwargs["hypothesis"],
            "core_rules": kwargs["core_rules"],
            "entry_conditions": kwargs["entry_conditions"],
            "supplemental_reason": kwargs["supplemental_reason"],
        },
    )
    display_contract = ResearchResolutionDisplayContract(
        dashboard_projection_factory=projection_factory,
        payload_factory=factory,
    )
    detail_updates = ResearchResolutionDetailUpdatesBundle(
        shared_updates={"shared": "yes"},
        research=ResearchResolutionDetailSectionUpdates(
            payload_updates={"research_only": "r1"}
        ),
        research_bridge=ResearchResolutionDetailSectionUpdates(
            payload_updates={"bridge_only": "b1"}
        ),
    )
    spec = display_contract.build_spec(
        analysis=PreDashboardAnalysisSpec(
            hypothesis=cast(Any, {"stance": "watch"}),
            matched_signals=["volume_up"],
            supplemental_reason="calm",
        ),
        identifiers=ResearchResolutionIdentifiers(
            policy_id="p1",
            research_case_id="c1",
            attribution_id="a1",
        ),
        detail_updates=detail_updates,
    )

    payload = spec.to_payload()
    assert payload["policy_id"] == "p1"
    assert payload["research_case_id"] == "c1"
    assert payload["attribution_id"] == "a1"
    assert payload["research"]["status"] == "ok"
    assert payload["research"]["shared"] == "yes"
    assert payload["research"]["research_only"] == "r1"
    assert payload["research_bridge"]["bridge_only"] == "b1"
    assert payload["dashboard"]["core_rules"] == ["rule-a"]
    assert payload["dashboard"]["entry_conditions"] == ["entry-a"]


def test_research_resolution_persistence_projection_helpers():
    projection = ResearchPersistenceProjection(
        case={"id": "case-1"},
        attribution=ResearchPersistenceAttributionProjection(
            saved=True,
            record={"id": "att-1"},
            preview={"score": 0.8},
        ),
        calibration_report={"gap": "none"},
        identifiers=ResearchResolutionIdentifiers(
            policy_id="p2",
            research_case_id="case-1",
            attribution_id="att-1",
        ),
    )
    persistence = ResearchResolutionPersistence(policy_id="p2", projection=projection)

    assert persistence.display_identifiers().to_dict() == {
        "policy_id": "p2",
        "research_case_id": "case-1",
        "attribution_id": "att-1",
    }
    assert projection.to_detail_payload() == {
        "case": {"id": "case-1"},
        "attribution": {
            "saved": True,
            "record": {"id": "att-1"},
            "preview": {"score": 0.8},
        },
        "calibration_report": {"gap": "none"},
    }


def test_stock_analysis_module_keeps_compat_exports_for_resolution_contracts():
    assert stock_analysis_module.ResearchResolutionPayloadFactory is ResearchResolutionPayloadFactory
    assert stock_analysis_module.ResearchResolutionDisplayContract is ResearchResolutionDisplayContract
    assert stock_analysis_module.ResearchResolutionIdentifiers is ResearchResolutionIdentifiers


def test_stock_analysis_module_compat_exports_research_resolution_from_extracted_module():
    assert (
        stock_analysis_module.ResearchResolutionService
        is ExtractedResearchResolutionService
    )
    assert (
        stock_analysis_module.ResearchResolutionService.__module__
        == "invest_evolution.application.stock_analysis_research_resolution_service"
    )


def test_extracted_research_resolution_service_builds_fallback_projection():
    service = ExtractedResearchResolutionService(
        case_store=SimpleNamespace(),
        scenario_engine=SimpleNamespace(),
        attribution_engine=SimpleNamespace(),
        logger=SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    )
    strategy = SimpleNamespace(
        name="chan_theory",
        display_name="缠论",
        scoring={"多头排列": 10},
        core_rules=["结构优先"],
        entry_conditions=["结构未破坏"],
    )

    projection = service.build_canonical_fallback_projection(
        strategy=strategy,
        derived={
            "flags": {"多头排列": True},
            "algo_score": 5.0,
            "latest_close": 12.0,
            "matched_signals": ["多头排列"],
        },
        execution={"final_reasoning": "趋势保持，动能尚可。"},
        dashboard_projection_builder=lambda **kwargs: {
            "stance": kwargs["hypothesis"].stance,
            "matched_signals": kwargs["matched_signals"],
            "supplemental_reason": kwargs["supplemental_reason"],
            "core_rules": kwargs["core_rules"],
            "entry_conditions": kwargs["entry_conditions"],
        },
    )

    assert projection["stance"] == "偏强关注"
    assert projection["matched_signals"] == ["多头排列"]
    assert projection["core_rules"] == ["结构优先"]
    assert projection["entry_conditions"] == ["结构未破坏"]


def test_batch_analysis_service_uses_injected_snapshot_projector():
    service = BatchAnalysisViewService(
        humanize_macd_cross=lambda value: f"pretty:{value}",
        snapshot_projector=lambda *_args, **_kwargs: {
            "indicators": {"atr_14": 1.5},
            "macd_payload": {"cross": "golden_cross"},
            "boll": {"position": 0.7},
            "latest_close": 10.0,
            "ma5": 10.5,
            "ma10": 10.2,
            "ma20": 9.8,
            "ma60": 9.2,
            "volume_ratio": 1.3,
            "rsi": 61.2,
            "ma_stack": "bullish",
            "macd_cross": "golden_cross",
            "atr_14": 1.5,
        },
    )

    payload = service.view_from_snapshot({"close": 9.9}, {"latest_close": 10.0})

    assert payload["signal"] == "bullish"
    assert payload["structure"] == "uptrend"
    assert payload["summary"]["macd"] == "pretty:golden_cross"
    assert payload["trend"]["bollinger_position"] == 0.7
    assert payload["trend"]["atr_14"] == 1.5


def test_project_snapshot_fields_preserves_indicator_projection_shape():
    payload = project_snapshot_fields(
        {
            "latest_close": 11.2,
            "indicators": {
                "sma_5": 11.3,
                "sma_10": 11.0,
                "sma_20": 10.7,
                "sma_60": 10.2,
                "rsi_14": 58.0,
                "volume_ratio_5_20": 1.1,
                "ma_stack": "bullish",
                "atr_14": 0.8,
                "macd_12_26_9": {"cross": "golden_cross"},
                "bollinger_20": {"position": 0.6},
            },
        },
        summary={"close": 11.0},
    )

    assert payload["latest_close"] == 11.2
    assert payload["ma_stack"] == "bullish"
    assert payload["macd_cross"] == "golden_cross"
    assert payload["boll"]["position"] == 0.6
    assert payload["atr_14"] == 0.8


def test_stock_analysis_module_keeps_compat_exports_for_batch_service_boundary():
    assert stock_analysis_module.BatchAnalysisViewService is BatchAnalysisViewService
    assert callable(stock_analysis_module._project_snapshot_fields)
