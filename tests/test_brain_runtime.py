import asyncio
from types import SimpleNamespace
from pathlib import Path

from invest_evolution.agent_runtime.runtime import BrainRuntime
from invest_evolution.agent_runtime.tools import BrainTool


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


class BrokenTool(BrainTool):
    @property
    def name(self) -> str:
        return "broken"

    @property
    def description(self) -> str:
        return "Always raise runtime error."

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs):
        raise RuntimeError("boom")


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


def test_explicit_tool_call_logs_execution_exception(tmp_path: Path, caplog):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )
    runtime.tools.register(BrokenTool())

    with caplog.at_level("ERROR"):
        result = asyncio.run(runtime.process_direct("/tool broken {}"))

    assert "Error executing broken: RuntimeError: boom" in result
    assert "Tool execution failed: broken" in caplog.text


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

    setattr(runtime.tools, "execute", fail_if_called)

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

    setattr(runtime, "gateway", DummyGateway())
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
    from invest_evolution.agent_runtime.tools import InvestTrainingPlanCreateTool

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
        manager_scope={"allowed_manager_ids": ["momentum"]},
        optimization={"promotion_gate": {"min_samples": 2}},
        llm={"timeout": 7, "max_retries": 1},
    ))

    payload = json.loads(result)
    assert observed["protocol"]["seed"] == 7
    assert observed["dataset"]["simulation_days"] == 15
    assert observed["manager_scope"]["allowed_manager_ids"] == ["momentum"]
    assert observed["optimization"]["promotion_gate"]["min_samples"] == 2
    assert observed["llm"]["timeout"] == 7
    assert payload["llm"]["max_retries"] == 1
    assert payload["protocol"]["seed"] == 7



def test_governance_preview_tool_uses_allowed_manager_ids(tmp_path: Path):
    import json
    from invest_evolution.agent_runtime.tools import InvestGovernancePreviewTool

    observed = {}

    class DummyRuntime:
        def get_governance_preview(self, **kwargs):
            observed.update(kwargs)
            return {"status": "ok", "governance": kwargs}

    tool = InvestGovernancePreviewTool(DummyRuntime())
    result = asyncio.run(tool.execute(
        cutoff_date="20260319",
        stock_count=25,
        min_history_days=120,
        allowed_manager_ids=["momentum", "value_quality"],
    ))

    payload = json.loads(result)
    assert observed["allowed_manager_ids"] == ["momentum", "value_quality"]
    assert "allowed_models" not in tool.parameters["properties"]
    assert payload["governance"]["allowed_manager_ids"] == ["momentum", "value_quality"]


def test_model_analytics_plan_only_special_cases_governance_preview():
    from invest_evolution.agent_runtime.planner import build_model_analytics_plan

    governance_plan = build_model_analytics_plan("get_governance_preview")
    legacy_plan = build_model_analytics_plan("get_model_routing_preview")

    assert governance_plan == [
        {"tool": "invest_governance_preview", "args": {}},
        {"tool": "invest_managers", "args": {}},
    ]
    assert legacy_plan != governance_plan
    assert legacy_plan == [
        {"tool": "invest_managers", "args": {}},
        {"tool": "invest_leaderboard", "args": {}},
        {"tool": "invest_governance_preview", "args": {}},
    ]


def test_brain_runtime_fallback_prompt_prefers_quick_status(tmp_path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = asyncio.run(runtime.process_direct("hello"))
    assert "control-plane" in result
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

    setattr(runtime, "gateway", DummyGateway())
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


def test_wrap_tool_response_normalizes_stock_analysis_contract_sections(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = runtime._wrap_tool_response(
        json.dumps(
            {
                "status": "ok",
                "question": "请分析 Alpha",
                "query": "Alpha",
                "normalized_query": "sh.600001",
                "as_of_date": "20240130",
                "requested_as_of_date": "20240130",
                "policy_id": "policy_1",
                "research_case_id": "case_1",
                "attribution_id": "attr_1",
                "resolved_security": {"code": "sh.600001"},
            },
            ensure_ascii=False,
        ),
        user_goal="分析 Alpha",
        tool_names=["invest_ask_stock"],
        mode="explicit_tool",
    )

    payload = json.loads(result)
    assert payload["request"]["query"] == "Alpha"
    assert payload["identifiers"]["policy_id"] == "policy_1"
    assert payload["resolved_entities"]["security"]["code"] == "sh.600001"
    assert isinstance(payload["analysis"], dict)
    assert isinstance(payload["research"], dict)
    assert isinstance(payload["dashboard"], dict)


def test_wrap_tool_response_normalizes_training_plan_create_contract_sections(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = runtime._wrap_tool_response(
        json.dumps({"plan_id": "plan_1", "status": "planned", "spec": {"rounds": 3}}, ensure_ascii=False),
        user_goal="创建训练计划",
        tool_names=["invest_training_plan_create"],
        mode="explicit_tool",
    )

    payload = json.loads(result)
    assert payload["plan_id"] == "plan_1"
    assert payload["spec"]["rounds"] == 3
    assert isinstance(payload["guardrails"], dict)
    assert isinstance(payload["objective"], dict)
    assert isinstance(payload["artifacts"], dict)


def test_wrap_tool_response_training_execution_receipt_includes_promotion_and_lineage(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = runtime._wrap_tool_response(
        json.dumps(
            {
                "status": "completed",
                "plan_id": "plan_1",
                "results": [
                    {
                        "status": "ok",
                        "cycle_id": 7,
                        "return_pct": 0.8,
                        "promotion_record": {"status": "candidate_generated", "gate_status": "awaiting_gate"},
                        "lineage_record": {"lineage_status": "candidate_pending"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        user_goal="执行训练计划",
        tool_names=["invest_training_plan_execute"],
        mode="explicit_tool",
    )

    payload = json.loads(result)
    human = payload["human_readable"]
    assert any("晋升状态：candidate_generated / awaiting_gate" in item for item in human["facts"])
    assert any("lineage：candidate_pending" in item for item in human["facts"])


class InvestMutationTool(BrainTool):
    def __init__(self):
        self.calls = 0

    @property
    def name(self) -> str:
        return "invest_runtime_paths_update"

    @property
    def description(self) -> str:
        return "Mutation tool for guardrail tests."

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "patch": {"type": "object"},
                "confirm": {"type": "boolean"},
            },
            "required": ["patch"],
        }

    async def execute(self, **kwargs):
        import json

        self.calls += 1
        return json.dumps({"status": "ok", "updated": list((kwargs.get("patch") or {}).keys())}, ensure_ascii=False)


def test_explicit_mutating_tool_guardrail_blocks_empty_patch(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )
    tool = InvestMutationTool()
    runtime.tools.register(tool)

    result = asyncio.run(runtime.process_direct('/tool invest_runtime_paths_update {"patch":{}}'))
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["empty_patch"]
    assert tool.calls == 0


def test_explicit_mutating_tool_guardrail_blocks_placeholder_patch(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )
    tool = InvestMutationTool()
    runtime.tools.register(tool)

    result = asyncio.run(
        runtime.process_direct('/tool invest_runtime_paths_update {"patch":{"training_output_dir":"<new_path>"}}')
    )
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["placeholder_arguments"]
    assert tool.calls == 0


def test_explicit_mutating_tool_guardrail_blocks_cross_scope_patch(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )
    tool = InvestMutationTool()
    runtime.tools.register(tool)

    result = asyncio.run(
        runtime.process_direct('/tool invest_evolution_config_update {"patch":{"llm":{"provider":"foo"}}}')
    )
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["cross_scope_patch"]
    assert tool.calls == 0


def test_training_plan_create_guardrail_blocks_history_window_shorter_than_simulation(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = asyncio.run(
        runtime.process_direct(
            '/tool invest_training_plan_create {"rounds":2,"dataset":{"min_history_days":10,"simulation_days":15}}'
        )
    )
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["history_window_too_short"]


def test_training_plan_create_guardrail_blocks_invalid_single_cycle_window_size(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = asyncio.run(
        runtime.process_direct(
            '/tool invest_training_plan_create {"rounds":2,"protocol":{"review_window":{"mode":"single_cycle","size":3}}}'
        )
    )
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["single_cycle_window_size_conflict"]


def test_training_plan_create_guardrail_blocks_invalid_cutoff_policy_mode(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = asyncio.run(
        runtime.process_direct(
            '/tool invest_training_plan_create {"rounds":2,"protocol":{"cutoff_policy":{"mode":"surprise"}}}'
        )
    )
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["invalid_cutoff_policy_mode"]


def test_explicit_mutating_tool_guardrail_blocks_blank_runtime_path(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )
    tool = InvestMutationTool()
    runtime.tools.register(tool)

    result = asyncio.run(
        runtime.process_direct('/tool invest_runtime_paths_update {"patch":{"training_output_dir":"   "}}')
    )
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["blank_runtime_path"]
    assert tool.calls == 0


def test_explicit_mutating_tool_guardrail_blocks_relative_runtime_path(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )
    tool = InvestMutationTool()
    runtime.tools.register(tool)

    result = asyncio.run(
        runtime.process_direct('/tool invest_runtime_paths_update {"patch":{"training_output_dir":"relative/output"}}')
    )
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["relative_runtime_path"]
    assert tool.calls == 0


def test_training_plan_create_guardrail_blocks_fixed_cutoff_without_date(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = asyncio.run(
        runtime.process_direct(
            '/tool invest_training_plan_create {"rounds":2,"protocol":{"cutoff_policy":{"mode":"fixed"}}}'
        )
    )
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["fixed_cutoff_missing_date"]


def test_training_plan_create_guardrail_blocks_sequence_cutoff_without_dates(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = asyncio.run(
        runtime.process_direct(
            '/tool invest_training_plan_create {"rounds":2,"protocol":{"cutoff_policy":{"mode":"sequence"}}}'
        )
    )
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["sequence_cutoff_missing_dates"]


def test_training_plan_create_guardrail_blocks_regime_balanced_cutoff_without_targets(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = asyncio.run(
        runtime.process_direct(
            '/tool invest_training_plan_create {"rounds":2,"protocol":{"cutoff_policy":{"mode":"regime_balanced"}}}'
        )
    )
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["regime_balanced_missing_targets"]


def test_training_plan_create_guardrail_blocks_invalid_llm_mode(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = asyncio.run(
        runtime.process_direct('/tool invest_training_plan_create {"rounds":2,"llm":{"mode":"turbo"}}')
    )
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["invalid_llm_mode"]


def test_agent_prompt_update_guardrail_blocks_missing_agent_name(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = asyncio.run(
        runtime.process_direct('/tool invest_agent_prompts_update {"system_prompt":"focus on evidence"}')
    )
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["missing_agent_name"]


def test_agent_prompt_update_guardrail_blocks_empty_system_prompt(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    result = asyncio.run(
        runtime.process_direct('/tool invest_agent_prompts_update {"name":"researcher","system_prompt":"   "}')
    )
    payload = json.loads(result)

    assert payload["status"] == "guardrail_blocked"
    assert payload["guardrails"]["reason_codes"] == ["empty_system_prompt"]


def test_wrap_tool_response_attaches_runtime_governance_metrics(tmp_path: Path):
    import json

    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )

    wrapped = runtime._wrap_tool_response(
        json.dumps({"status": "planned", "plan_id": "plan_1"}, ensure_ascii=False),
        user_goal="create training plan",
        tool_names=["invest_training_plan_create"],
        mode="explicit_tool",
    )
    payload = json.loads(wrapped)

    assert payload["structured_output"]["status"] == "validated"
    metrics = payload["governance_metrics"]["runtime"]
    assert metrics["structured_output"]["validated_count"] >= 1
    assert metrics["guardrails"]["block_count"] == 0
