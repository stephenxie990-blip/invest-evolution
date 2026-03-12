from pathlib import Path
from types import SimpleNamespace

from app.stock_analysis import StockAnalysisService
from market_data.repository import MarketDataRepository


def _build_service(tmp_path: Path, **kwargs) -> StockAnalysisService:
    db_path = tmp_path / "market.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([
        {"code": "sh.600001", "name": "FooBank", "list_date": "20200101", "source": "test"}
    ])
    repo.upsert_daily_bars([
        {
            "code": "sh.600001",
            "trade_date": f"202401{day:02d}",
            "open": 10 + day * 0.1,
            "high": 10.5 + day * 0.1,
            "low": 9.5 + day * 0.1,
            "close": 10 + day * 0.15,
            "volume": 1000 + day * 20,
            "amount": 5000 + day * 120,
            "pct_chg": 0.8,
            "turnover": 1.2,
            "source": "test",
        }
        for day in range(1, 91)
    ])
    repo.upsert_capital_flow_daily([
        {
            "code": "sh.600001",
            "trade_date": f"202401{day:02d}",
            "close": 10 + day * 0.15,
            "pct_chg": 0.8,
            "main_net_inflow": 1000 + day * 10,
            "main_net_inflow_ratio": 0.02 + day * 0.0001,
            "super_large_net_inflow": 500 + day,
            "super_large_net_inflow_ratio": 0.01,
            "large_net_inflow": 300 + day,
            "large_net_inflow_ratio": 0.008,
            "medium_net_inflow": -100,
            "medium_net_inflow_ratio": -0.002,
            "small_net_inflow": -200,
            "small_net_inflow_ratio": -0.004,
        }
        for day in range(1, 31)
    ])
    kwargs.setdefault("enable_llm_react", False)
    return StockAnalysisService(db_path=db_path, strategy_dir=tmp_path / "stock_strategies", **kwargs)


def test_stock_analysis_returns_yaml_react_trace(tmp_path: Path):
    service = _build_service(tmp_path)

    payload = service.ask_stock(question="用缠论分析 FooBank", query="FooBank")

    assert payload["status"] == "ok"
    assert payload["strategy"]["name"] == "chan_theory"
    assert payload["orchestration"]["mode"] in {"yaml_react_like", "llm_react_yaml_hybrid"}
    assert payload["orchestration"]["step_count"] >= 5
    assert payload["orchestration"]["tool_calls"][1]["action"]["tool"] == "get_indicator_snapshot"
    assert payload["analysis"]["derived_signals"]["history_count"] >= 60
    assert payload["analysis"]["derived_signals"]["main_net_inflow_sum"] > 0
    assert payload["dashboard"]["signal"]
    assert payload["entrypoint"]["standalone_agent"] is False
    assert payload["entrypoint"]["meeting_path"] is False
    assert payload["task_bus"]["schema_version"] == "task_bus.v2"
    assert payload["task_bus"]["planner"]["plan_summary"]["schema_version"] == "task_plan.v2"
    assert payload["task_bus"]["planner"]["plan_summary"]["recommended_step_count"] == len(payload["orchestration"]["recommended_plan"])
    assert payload["task_bus"]["planner"]["recommended_plan"][0]["step_id"] == "step_01"
    assert payload["task_bus"]["gate"]["decision"] == "allow"
    assert payload["task_bus"]["gate"]["confirmation"]["state"] == "not_applicable"
    assert payload["task_bus"]["audit"]["tool_count"] == payload["orchestration"]["step_count"]
    assert payload["task_bus"]["audit"]["coverage"]["schema_version"] == "task_coverage.v2"
    assert payload["task_bus"]["audit"]["coverage"]["required_tool_coverage"] == payload["orchestration"]["coverage"]["required_tool_coverage"]
    assert payload["task_bus"]["audit"]["artifact_taxonomy"]["schema_version"] == "artifact_taxonomy.v2"
    assert payload["task_bus"]["audit"]["artifact_taxonomy"]["keys"] == ["code", "gap_fill_applied", "latest_close", "strategy", "strategy_source"]
    assert payload["orchestration"]["allowed_tools"] == payload["orchestration"]["required_tools"]
    assert payload["orchestration"]["coverage"]["required_tool_coverage"] == 1.0
    assert payload["orchestration"]["coverage"]["missing_required_tools"] == []


def test_stock_analysis_infers_strategy_from_question(tmp_path: Path):
    service = _build_service(tmp_path)

    payload = service.ask_stock(question="用趋势跟随分析 FooBank", query="FooBank")

    assert payload["status"] == "ok"
    assert payload["strategy"]["name"] == "trend_following"
    assert payload["strategy_source"] == "inferred"
    assert payload["orchestration"]["recommended_plan"][0]["tool"] == "get_daily_history"
    assert payload["orchestration"]["recommended_plan"][0]["args"]["days"] == 120
    assert payload["orchestration"]["recommended_plan"][1]["tool"] == "get_indicator_snapshot"


def test_stock_analysis_explicit_strategy_overrides_default(tmp_path: Path):
    service = _build_service(tmp_path)

    payload = service.ask_stock(question="看看 FooBank", query="FooBank", strategy="trend_following")

    assert payload["status"] == "ok"
    assert payload["strategy"]["name"] == "trend_following"
    assert payload["strategy_source"] == "explicit"
    assert payload["resolved"]["code"] == "sh.600001"
    assert "get_capital_flow" in payload["orchestration"]["available_tools"]


def test_stock_analysis_uses_llm_react_when_gateway_available(tmp_path: Path):
    first = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content="先取历史数据与趋势信号。",
            tool_calls=[
                SimpleNamespace(id="tc1", function=SimpleNamespace(name="get_daily_history", arguments='{"query":"sh.600001","days":60}')),
                SimpleNamespace(id="tc2", function=SimpleNamespace(name="get_indicator_snapshot", arguments='{"query":"sh.600001","days":120}')),
                SimpleNamespace(id="tc3", function=SimpleNamespace(name="get_realtime_quote", arguments='{"query":"sh.600001"}')),
            ],
        ))]
    )
    second = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="证据已充分，趋势偏强，可继续观察买点。", tool_calls=[]))]
    )

    class DummyGateway:
        available = True

        def __init__(self):
            self._responses = [first, second]

        def completion_raw(self, **kwargs):
            return self._responses.pop(0)

    service = _build_service(tmp_path, gateway=DummyGateway(), enable_llm_react=True)
    payload = service.ask_stock(question="用缠论分析 FooBank", query="FooBank")

    assert payload["status"] == "ok"
    assert payload["orchestration"]["mode"] == "llm_react_yaml_hybrid"
    assert payload["orchestration"]["step_count"] == 5
    assert payload["orchestration"]["llm_reasoning"]
    assert payload["orchestration"]["tool_calls"][1]["action"]["tool"] == "get_indicator_snapshot"
    assert payload["orchestration"]["tool_calls"][0]["observation"]["status"] == "ok"
    assert payload["orchestration"]["allowed_tools"] == payload["orchestration"]["required_tools"]
    assert payload["orchestration"]["coverage"]["required_tool_coverage"] == 1.0
    assert payload["orchestration"]["coverage"]["planned_step_coverage"] == 1.0
    assert payload["orchestration"]["coverage"]["missing_required_tools"] == []
    assert payload["orchestration"]["gap_fill"]["applied"] is True
    assert payload["orchestration"]["gap_fill"]["required_tool_coverage_before_fill"] == 0.6
    assert payload["orchestration"]["gap_fill"]["planned_step_coverage_before_fill"] == 0.6
    assert [step["tool"] for step in payload["orchestration"]["gap_fill"]["filled_steps"]] == ["analyze_support_resistance", "get_capital_flow"]
    assert payload["orchestration"]["phase_stats"]["llm_react_steps"] == 3
    assert payload["orchestration"]["phase_stats"]["yaml_gap_fill_steps"] == 2


def test_stock_analysis_falls_back_when_llm_react_produces_no_tool_calls(tmp_path: Path):
    only = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="直接给结论", tool_calls=[]))]
    )

    class DummyGateway:
        available = True

        def completion_raw(self, **kwargs):
            return only

    service = _build_service(tmp_path, gateway=DummyGateway(), enable_llm_react=True)
    payload = service.ask_stock(question="用缠论分析 FooBank", query="FooBank")

    assert payload["status"] == "ok"
    assert payload["orchestration"]["mode"] == "yaml_react_like"
    assert payload["orchestration"]["fallback_used"] is True


def test_stock_analysis_restricts_tool_catalog_to_strategy_yaml(tmp_path: Path):
    service = _build_service(tmp_path)
    strategy = service.load_strategy("chan_theory")

    tool_defs = service._stock_tool_definitions(allowed_tools=service._strategy_allowed_tools(strategy))
    names = [item["function"]["name"] for item in tool_defs]

    assert names == [
        "get_daily_history",
        "get_indicator_snapshot",
        "analyze_support_resistance",
        "get_capital_flow",
        "get_realtime_quote",
    ]
    assert "analyze_trend" not in names


def test_stock_analysis_gap_fill_when_llm_uses_wrong_yaml_args(tmp_path: Path):
    first = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content="先取结构与指标。",
            tool_calls=[
                SimpleNamespace(id="tc1", function=SimpleNamespace(name="get_daily_history", arguments='{"query":"sh.600001","days":60}')),
                SimpleNamespace(id="tc2", function=SimpleNamespace(name="get_indicator_snapshot", arguments='{"query":"sh.600001","days":60}')),
                SimpleNamespace(id="tc3", function=SimpleNamespace(name="get_realtime_quote", arguments='{"query":"sh.600001"}')),
            ],
        ))]
    )
    second = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="先给个初步结论。", tool_calls=[]))]
    )

    class DummyGateway:
        available = True

        def __init__(self):
            self._responses = [first, second]

        def completion_raw(self, **kwargs):
            return self._responses.pop(0)

    service = _build_service(tmp_path, gateway=DummyGateway(), enable_llm_react=True)
    payload = service.ask_stock(question="用缠论分析 FooBank", query="FooBank")

    assert payload["status"] == "ok"
    assert payload["orchestration"]["mode"] == "llm_react_yaml_hybrid"
    assert payload["orchestration"]["gap_fill"]["applied"] is True
    assert payload["orchestration"]["gap_fill"]["planned_step_coverage_before_fill"] == 0.4
    assert [step["tool"] for step in payload["orchestration"]["gap_fill"]["filled_steps"]] == [
        "get_indicator_snapshot",
        "analyze_support_resistance",
        "get_capital_flow",
    ]
    assert payload["orchestration"]["tool_calls"][3]["action"]["tool"] == "get_indicator_snapshot"
    assert payload["orchestration"]["tool_calls"][3]["action"]["args"]["days"] == 120
