import asyncio
from types import SimpleNamespace
from pathlib import Path

from brain.runtime import BrainRuntime, BrainTool


class EchoTool(BrainTool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the input text."

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }

    async def execute(self, **kwargs):
        return kwargs.get("text", "")


def test_explicit_tool_call_without_llm(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",  # no llm
    )
    runtime.tools.register(EchoTool())

    result = asyncio.run(runtime.process_direct('/tool echo {"text":"hello"}'))
    assert result == "hello"


def test_explicit_tool_call_validation_error(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )
    runtime.tools.register(EchoTool())

    result = asyncio.run(runtime.process_direct('/tool echo {"wrong":"x"}'))
    assert "Invalid parameters" in result


def test_explicit_tool_call_invalid_json_returns_parse_error(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )
    runtime.tools.register(EchoTool())

    result = asyncio.run(runtime.process_direct('/tool echo {"text":'))
    assert "invalid tool arguments for echo" in result


def test_explicit_tool_call_non_object_json_returns_parse_error(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )
    runtime.tools.register(EchoTool())

    result = asyncio.run(runtime.process_direct('/tool echo ["hello"]'))
    assert "tool arguments must decode to a JSON object" in result


def test_parse_tool_args_invalid_json_raises(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    try:
        runtime._parse_tool_args('{"text":')
    except Exception as exc:
        assert "Expecting" in str(exc) or "delimiter" in str(exc)
    else:
        raise AssertionError("expected parse error")


def test_parse_tool_args_blank_string_returns_empty_dict(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    assert runtime._parse_tool_args("") == {}
    assert runtime._parse_tool_args("   ") == {}
    assert runtime._parse_tool_args(None) == {}


def test_parse_tool_args_non_string_non_object_raises(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    try:
        runtime._parse_tool_args(False)
    except Exception as exc:
        assert "JSON object or JSON string" in str(exc)
    else:
        raise AssertionError("expected parse error")


def test_invalid_tool_args_do_not_execute_tool(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="token",
    )
    runtime.tools.register(EchoTool())

    calls = {"count": 0}

    async def fail_if_called(name, args):
        calls["count"] += 1
        raise AssertionError("tool should not execute for malformed args")

    runtime.tools.execute = fail_if_called

    first = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content="",
            tool_calls=[SimpleNamespace(id="tc1", function=SimpleNamespace(name="echo", arguments='{"text":'))],
        ))]
    )
    second = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=[]))]
    )

    class DummyGateway:
        available = True

        def __init__(self):
            self._responses = [first, second]

        async def acompletion_raw(self, **kwargs):
            return self._responses.pop(0)

    runtime.gateway = DummyGateway()
    result = asyncio.run(runtime.process_direct("use tool"))
    assert result == "done"
    assert calls["count"] == 0


def test_default_system_prompt_mentions_grounding_and_json_args(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    prompt = runtime._system_prompt()
    assert "Ground every factual statement" in prompt
    assert "valid JSON object" in prompt
    assert "Never invent tool results" in prompt


def test_training_plan_create_tool_passes_full_lab_schema(tmp_path: Path):
    import json
    from brain.tools import InvestTrainingPlanCreateTool

    observed = {}

    class DummyRuntime:
        def create_training_plan(self, **kwargs):
            observed.update(kwargs)
            return {"ok": True, **kwargs}

    tool = InvestTrainingPlanCreateTool(DummyRuntime())
    result = asyncio.run(tool.execute(
        rounds=3,
        mock=False,
        goal="lab run",
        notes="n",
        tags=["x"],
        detail_mode="slow",
        protocol={"seed": 7},
        dataset={"simulation_days": 15},
        model_scope={"allowed_models": ["momentum"]},
        optimization={"promotion_gate": {"min_samples": 2}},
        llm={"timeout": 7, "max_retries": 1},
    ))

    payload = json.loads(result)
    assert observed["protocol"]["seed"] == 7
    assert observed["dataset"]["simulation_days"] == 15
    assert observed["model_scope"]["allowed_models"] == ["momentum"]
    assert observed["optimization"]["promotion_gate"]["min_samples"] == 2
    assert observed["llm"]["timeout"] == 7
    assert payload["llm"]["max_retries"] == 1
    assert payload["protocol"]["seed"] == 7



def test_brain_runtime_fallback_prompt_prefers_quick_status(tmp_path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = asyncio.run(runtime.process_direct("hello"))
    assert "invest_quick_status" in result


class InvestEchoTool(BrainTool):
    @property
    def name(self) -> str:
        return "invest_echo"

    @property
    def description(self) -> str:
        return "Return a JSON payload for runtime wrapping tests."

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }

    async def execute(self, **kwargs):
        import json
        return json.dumps({"status": "ok", "echo": kwargs.get("text", "")}, ensure_ascii=False)


def test_llm_tool_loop_wraps_invest_tools_with_task_bus(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="token",
    )
    runtime.tools.register(InvestEchoTool())

    first = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content="先调用工具。",
            tool_calls=[SimpleNamespace(id="tc1", function=SimpleNamespace(name="invest_echo", arguments='{"text":"hello"}'))],
        ))]
    )
    second = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="工具调用完成", tool_calls=[]))]
    )

    class DummyGateway:
        available = True

        def __init__(self):
            self._responses = [first, second]

        async def acompletion_raw(self, **kwargs):
            return self._responses.pop(0)

    runtime.gateway = DummyGateway()
    result = asyncio.run(runtime.process_direct("请帮我执行 invest echo"))
    payload = json.loads(result)
    assert payload["status"] == "ok"
    assert payload["reply"] == "工具调用完成"
    assert payload["task_bus"]["schema_version"] == "task_bus.v2"
    assert payload["task_bus"]["planner"]["mode"] == "llm_tool_loop"
    assert payload["task_bus"]["planner"]["plan_summary"]["recommended_tool_count"] == 1
    assert payload["task_bus"]["audit"]["used_tools"] == ["invest_echo"]
    assert payload["task_bus"]["audit"]["coverage"]["recommended_step_count"] == 1
    assert payload["task_bus"]["audit"]["artifact_taxonomy"]["keys"] == ["mode", "tools", "workspace"]
    assert payload["task_bus"]["gate"]["confirmation"]["state"] == "not_applicable"
    assert payload["entrypoint"]["mode"] == "llm_tool_loop"
    assert payload["entrypoint"]["intent"] == "runtime_tooling"
    assert payload["task_bus"]["planner"]["recommended_plan"][0]["step_id"] == "step_01"
    assert payload["task_bus"]["planner"]["recommended_plan"][0]["tool"] == "invest_echo"
    assert payload["task_bus"]["audit"]["coverage"]["schema_version"] == "task_coverage.v2"
    assert payload["task_bus"]["audit"]["artifact_taxonomy"]["schema_version"] == "artifact_taxonomy.v2"


def test_explicit_tool_keeps_non_invest_tools_raw(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )
    runtime.tools.register(EchoTool())

    result = asyncio.run(runtime.process_direct('/tool echo {"text":"hello"}'))
    assert result == "hello"


def test_recommended_plan_training_infers_rounds_and_real_mode(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    plan = runtime._recommended_plan_for_intent(
        intent="training_execution",
        tool_names=["invest_train"],
        writes_state=True,
        user_goal="请帮我真实训练2轮",
    )
    assert plan[1]["tool"] == "invest_training_plan_create"
    assert plan[1]["args"]["rounds"] == 2
    assert plan[1]["args"]["mock"] is False


def test_recommended_plan_data_focus_uses_capital_flow(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    plan = runtime._recommended_plan_for_intent(
        intent="data_status",
        tool_names=["invest_data_status"],
        writes_state=False,
        user_goal="帮我看下资金流数据",
    )
    assert plan[0]["tool"] == "invest_data_status"
    assert plan[1]["tool"] == "invest_data_capital_flow"


def test_recommended_plan_config_overview_keeps_control_plane_and_evolution(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    plan = runtime._recommended_plan_for_intent(
        intent="config_overview",
        tool_names=["invest_control_plane_get", "invest_evolution_config_get"],
        writes_state=False,
        user_goal="看看控制面配置",
    )
    assert plan[0]["tool"] == "invest_control_plane_get"
    assert plan[1]["tool"] == "invest_evolution_config_get"


def test_recommended_plan_stock_analysis_infers_strategy_and_days(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    plan = runtime._recommended_plan_for_intent(
        intent="stock_analysis",
        tool_names=["invest_ask_stock"],
        writes_state=False,
        user_goal="用趋势跟随分析 FooBank 120天",
    )
    assert plan[1]["tool"] == "invest_ask_stock"
    assert plan[1]["args"]["strategy"] == "trend_following"
    assert plan[1]["args"]["days"] == 120
