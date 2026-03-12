from pathlib import Path

from brain.schema_contract import (
    MUTATING_DEFAULT_REASON_CODES,
    READONLY_DEFAULT_REASON_CODES,
    RISK_LEVELS,
    TRAINING_DEFAULT_REASON_CODES,
    bounded_workflow_contract,
    task_bus_contract,
)
from brain.task_bus import build_bounded_entrypoint, build_bounded_orchestration, build_bounded_policy, build_bounded_workflow_protocol, build_mutating_task_bus, build_protocol_response
from brain.transcript_snapshot import build_transcript_snapshot
from commander import CommanderConfig, CommanderRuntime
from app.stock_analysis import StockAnalysisService
from market_data.repository import MarketDataRepository


def _build_stock_service(tmp_path: Path) -> StockAnalysisService:
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
    return StockAnalysisService(db_path=db_path, strategy_dir=tmp_path / "stock_strategies", enable_llm_react=False)


def test_task_bus_contract_golden_snapshot():
    expected = {
        "schema_version": "task_bus.v2",
        "top_level_keys": ["schema_version", "planner", "gate", "audit"],
        "planner": {
            "keys": ["intent", "operation", "mode", "user_goal", "available_tools", "recommended_plan", "plan_summary"],
            "step_required_keys": ["step_id", "tool", "args"],
            "summary_keys": ["schema_version", "available_tool_count", "recommended_step_count", "recommended_tool_count", "recommended_tools", "step_ids"],
            "summary_schema_version": "task_plan.v2",
        },
        "gate": {
            "keys": ["decision", "risk_level", "writes_state", "requires_confirmation", "reasons", "confirmation"],
            "confirmation_keys": ["required", "decision", "state", "reason_codes"],
            "confirmation_states": ["pending_confirmation", "confirmed_or_not_required", "not_applicable"],
            "risk_levels": ["low", "medium", "high"],
            "reason_codes": ["confirmation_required", "incomplete_parameter_coverage", "incomplete_plan_coverage", "read_only_analysis", "state_changing_request", "tool_grounded_execution", "training_changes_runtime_state"],
            "readonly_default_reason_codes": ["read_only_analysis", "tool_grounded_execution"],
            "mutating_default_reason_codes": ["state_changing_request", "tool_grounded_execution"],
            "training_default_reason_codes": ["training_changes_runtime_state", "tool_grounded_execution"],
        },
        "audit": {
            "keys": ["status", "started_at", "completed_at", "tool_count", "used_tools", "artifacts", "coverage", "artifact_taxonomy"],
            "coverage_keys": [
                "schema_version",
                "coverage_kind",
                "recommended_step_count",
                "executed_step_count",
                "available_tool_count",
                "used_tool_count",
                "recommended_tool_count",
                "covered_recommended_tools",
                "covered_recommended_step_ids",
                "missing_planned_steps",
                "missing_planned_step_ids",
                "planned_step_coverage",
                "required_tool_coverage",
                "parameterized_step_count",
                "covered_parameterized_step_ids",
                "missing_parameterized_step_ids",
                "parameter_coverage",
            ],
            "coverage_schema_version": "task_coverage.v2",
            "coverage_kinds": ["plan_vs_execution", "workflow_phase_completion"],
            "artifact_taxonomy_keys": ["schema_version", "count", "keys", "kinds", "path_keys", "object_keys", "collection_keys", "known_kinds"],
            "artifact_taxonomy_schema_version": "artifact_taxonomy.v2",
            "artifact_kinds": ["collection", "id", "object", "path", "scalar", "unknown"],
        },
        "feedback": {
            "keys": ["message", "summary", "reason_codes", "reason_texts", "requires_confirmation", "decision", "coverage"],
            "coverage_keys": ["planned_step_coverage", "parameter_coverage"],
        },
        "next_action": {
            "keys": ["kind", "label", "description", "requires_confirmation", "suggested_params"],
        },
        "response_envelope": {
            "keys": ["status", "reply", "message", "feedback", "next_action", "task_bus"],
        },
        "next_action": {
            "keys": ["kind", "label", "description", "requires_confirmation", "suggested_params"],
        },
        "response_envelope": {
            "keys": ["status", "reply", "message", "feedback", "next_action", "task_bus"],
        },
    }
    assert task_bus_contract() == expected


def test_bounded_workflow_contract_golden_snapshot():
    expected = {
        "schema_version": "bounded_workflow.v2",
        "top_level_keys": ["entrypoint", "orchestration", "protocol", "artifacts", "coverage", "artifact_taxonomy", "feedback", "next_action"],
        "protocol_keys": [
            "schema_version",
            "task_bus_schema_version",
            "plan_schema_version",
            "coverage_schema_version",
            "artifact_taxonomy_schema_version",
            "domain",
            "operation",
        ],
        "protocol_versions": {
            "task_bus": "task_bus.v2",
            "plan": "task_plan.v2",
            "coverage": "task_coverage.v2",
            "artifact_taxonomy": "artifact_taxonomy.v2",
        },
        "coverage_keys": [
            "schema_version",
            "coverage_kind",
            "workflow_step_count",
            "completed_workflow_step_count",
            "workflow_step_coverage",
            "phase_stat_key_count",
        ],
        "coverage_kind": "workflow_phase_completion",
        "artifact_taxonomy_keys": ["schema_version", "count", "keys", "kinds", "path_keys", "object_keys", "collection_keys", "known_kinds"],
        "artifact_kinds": ["collection", "id", "object", "path", "scalar", "unknown"],
        "feedback": {
            "keys": ["message", "summary", "reason_codes", "reason_texts", "requires_confirmation", "decision", "coverage"],
            "coverage_keys": ["planned_step_coverage", "parameter_coverage"],
        },
        "next_action": {
            "keys": ["kind", "label", "description", "requires_confirmation", "suggested_params"],
        },
        "response_envelope": {
            "keys": ["status", "reply", "message", "feedback", "next_action", "task_bus"],
        },
    }
    assert bounded_workflow_contract() == expected


def test_bounded_entrypoint_and_policy_helpers_omit_empty_optionals():
    entrypoint = build_bounded_entrypoint(
        kind="commander_tool_service",
        runtime_tool="invest_ask_stock",
        runtime_method="CommanderRuntime.ask_stock",
        meeting_path=False,
        agent_kind="bounded_stock_agent",
        standalone_agent=False,
    )
    policy = build_bounded_policy(
        source="yaml_strategy",
        agent_kind="bounded_stock_agent",
        tool_catalog_scope="strategy_restricted",
        workflow_mode="llm_react_with_yaml_gap_fill",
    )

    assert entrypoint == {
        "kind": "commander_tool_service",
        "meeting_path": False,
        "agent_kind": "bounded_stock_agent",
        "runtime_method": "CommanderRuntime.ask_stock",
        "runtime_tool": "invest_ask_stock",
        "standalone_agent": False,
    }
    assert policy == {
        "source": "yaml_strategy",
        "agent_kind": "bounded_stock_agent",
        "fixed_boundary": True,
        "fixed_workflow": True,
        "tool_catalog_scope": "strategy_restricted",
        "workflow_mode": "llm_react_with_yaml_gap_fill",
    }

def test_bounded_orchestration_helper_normalizes_core_fields():
    orchestration = build_bounded_orchestration(
        mode="yaml_react_like",
        available_tools=["get_daily_history", "get_realtime_quote"],
        workflow=["yaml_strategy_loaded", "finalize"],
        phase_stats={"total_steps": 2},
        policy=build_bounded_policy(
            source="yaml_strategy",
            agent_kind="bounded_stock_agent",
            tool_catalog_scope="strategy_restricted",
        ),
        extra={"step_count": 2},
    )

    assert orchestration == {
        "mode": "yaml_react_like",
        "available_tools": ["get_daily_history", "get_realtime_quote"],
        "allowed_tools": ["get_daily_history", "get_realtime_quote"],
        "workflow": ["yaml_strategy_loaded", "finalize"],
        "phase_stats": {"total_steps": 2},
        "policy": {
            "source": "yaml_strategy",
            "agent_kind": "bounded_stock_agent",
            "fixed_boundary": True,
            "fixed_workflow": True,
            "tool_catalog_scope": "strategy_restricted",
        },
        "step_count": 2,
    }



def test_transcript_snapshot_builder_normalizes_cross_domain_shape():
    payload = {
        "status": "ok",
        "detail_mode": "fast",
        "entrypoint": {
            "agent_kind": "bounded_runtime_agent",
            "domain": "runtime",
            "runtime_tool": "invest_quick_status",
            "service": None,
        },
        "orchestration": {
            "workflow": ["runtime_scope_resolve", "status_read", "finalize"],
            "mode": "bounded_readonly_workflow",
            "step_count": 1,
            "phase_stats": {"event_count": 10},
            "policy": {
                "fixed_boundary": True,
                "fixed_workflow": True,
                "writes_state": False,
                "confirmation_gate": None,
                "tool_catalog_scope": "runtime_domain",
                "workflow_mode": None,
            },
        },
        "task_bus": {
            "schema_version": "task_bus.v2",
            "planner": {
                "intent": "runtime_status",
                "operation": "status",
                "mode": "commander_runtime_method",
                "recommended_plan": [{"args": {"detail": "fast"}}],
                "plan_summary": {"recommended_tools": ["invest_quick_status"]},
            },
            "gate": {
                "requires_confirmation": False,
                "decision": "allow",
                "risk_level": "low",
                "writes_state": False,
                "confirmation": {"state": "not_applicable"},
            },
            "audit": {
                "used_tools": ["invest_quick_status"],
                "tool_count": 1,
                "coverage": {
                    "planned_step_coverage": 1.0,
                    "parameterized_step_count": 1,
                    "covered_parameterized_step_ids": ["step_01"],
                    "missing_parameterized_step_ids": [],
                    "parameter_coverage": 1.0,
                },
            },
        },
        "protocol": {"domain": "runtime", "operation": "status"},
        "feedback": {"summary": "当前任务已完成，计划与参数覆盖满足预期。"},
        "next_action": {"kind": "continue", "requires_confirmation": False},
    }

    snapshot = build_transcript_snapshot(
        payload,
        include_recommended_args=True,
        include_task_bus_coverage=True,
        include_gate_decision=True,
        include_tool_count=True,
    )

    assert snapshot["entrypoint"]["domain"] == "runtime"
    assert snapshot["orchestration"]["policy"]["tool_catalog_scope"] == "runtime_domain"
    assert snapshot["task_bus"]["recommended_args"] == [{"detail": "fast"}]
    assert snapshot["task_bus"]["planned_step_coverage"] == 1.0
    assert snapshot["feedback"]["summary"] == "当前任务已完成，计划与参数覆盖满足预期。"
    assert snapshot["next_action"]["kind"] == "continue"


def test_build_protocol_response_merges_shared_context():
    task_bus = build_mutating_task_bus(
        intent="config_management",
        operation="update_runtime_paths",
        user_goal="更新 runtime path",
        mode="builtin_intent",
        available_tools=["invest_runtime_paths_update"],
        recommended_plan=[{"tool": "invest_runtime_paths_update", "args": {"patch": {"workspace": "/tmp/new"}, "confirm": False}}],
        tool_calls=[],
        status="confirmation_required",
        requires_confirmation=True,
    )
    payload = build_protocol_response(
        payload={"status": "confirmation_required", "message": "请确认路径更新。"},
        entrypoint={"kind": "commander_builtin_intent", "intent": "config_management"},
        protocol=build_bounded_workflow_protocol(
            schema_version="bounded_workflow.v2",
            domain="config",
            operation="update_runtime_paths",
        ),
        task_bus=task_bus,
        artifacts={"workspace": "/tmp/project"},
        coverage={"workflow_step_coverage": 1.0},
        artifact_taxonomy={"schema_version": "artifact_taxonomy.v2", "count": 1},
        default_reply="请确认路径更新。",
    )

    assert payload["entrypoint"]["kind"] == "commander_builtin_intent"
    assert payload["protocol"]["domain"] == "config"
    assert payload["task_bus"]["gate"]["requires_confirmation"] is True
    assert payload["feedback"]["requires_confirmation"] is True
    assert payload["next_action"]["kind"] == "confirm"
    assert payload["artifacts"]["workspace"] == "/tmp/project"
    assert payload["coverage"]["workflow_step_coverage"] == 1.0
    assert payload["artifact_taxonomy"]["count"] == 1


def test_runtime_bounded_workflow_matches_contract_keys(tmp_path: Path):
    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state" / "state.json",
        cron_store=tmp_path / "state" / "cron.json",
        memory_store=tmp_path / "memory" / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "inbox",
        bridge_outbox=tmp_path / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    runtime = CommanderRuntime(cfg)
    payload = runtime.status(detail="fast")
    contract = bounded_workflow_contract()

    for key in contract["top_level_keys"]:
        assert key in payload
    for key in contract["protocol_keys"]:
        assert key in payload["protocol"]
    for key in contract["coverage_keys"]:
        assert key in payload["coverage"]
    for key in contract["artifact_taxonomy_keys"]:
        assert key in payload["artifact_taxonomy"]


def test_stock_payload_matches_bounded_workflow_contract_keys(tmp_path: Path):
    service = _build_stock_service(tmp_path)
    payload = service.ask_stock(question="用缠论分析 FooBank", query="FooBank")
    contract = bounded_workflow_contract()

    for key in contract["top_level_keys"]:
        assert key in payload
    for key in contract["protocol_keys"]:
        assert key in payload["protocol"]
    for key in contract["coverage_keys"]:
        assert key in payload["coverage"]
    for key in contract["artifact_taxonomy_keys"]:
        assert key in payload["artifact_taxonomy"]
    assert payload["protocol"]["domain"] == "stock"
    assert payload["protocol"]["operation"] == "ask_stock"


def test_stock_task_bus_matches_contract_keys(tmp_path: Path):
    service = _build_stock_service(tmp_path)
    payload = service.ask_stock(question="用缠论分析 FooBank", query="FooBank")
    contract = task_bus_contract()
    task_bus = payload["task_bus"]

    for key in contract["top_level_keys"]:
        assert key in task_bus
    for key in contract["planner"]["keys"]:
        assert key in task_bus["planner"]
    for key in contract["gate"]["keys"]:
        assert key in task_bus["gate"]
    for key in contract["audit"]["keys"]:
        assert key in task_bus["audit"]
    for key in contract["planner"]["step_required_keys"]:
        assert key in task_bus["planner"]["recommended_plan"][0]


def test_schema_contract_shared_enums_are_stable():
    assert RISK_LEVELS == ["low", "medium", "high"]
    assert READONLY_DEFAULT_REASON_CODES == ["read_only_analysis", "tool_grounded_execution"]
    assert MUTATING_DEFAULT_REASON_CODES == ["state_changing_request", "tool_grounded_execution"]
    assert TRAINING_DEFAULT_REASON_CODES == ["training_changes_runtime_state", "tool_grounded_execution"]


def test_task_bus_backward_compat_legacy_keys_preserved(tmp_path: Path):
    service = _build_stock_service(tmp_path)
    payload = service.ask_stock(question="用缠论分析 FooBank", query="FooBank")
    task_bus = payload["task_bus"]

    assert "planner" in task_bus
    assert "gate" in task_bus
    assert "audit" in task_bus
    assert "recommended_plan" in task_bus["planner"]
    assert "requires_confirmation" in task_bus["gate"]
    assert "artifacts" in task_bus["audit"]
    assert "used_tools" in task_bus["audit"]


def test_bounded_workflow_backward_compat_legacy_keys_preserved(tmp_path: Path):
    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state" / "state.json",
        cron_store=tmp_path / "state" / "cron.json",
        memory_store=tmp_path / "memory" / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "inbox",
        bridge_outbox=tmp_path / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    runtime = CommanderRuntime(cfg)
    payload = runtime.get_control_plane()

    assert "entrypoint" in payload
    assert "orchestration" in payload
    assert "protocol" in payload
    assert "artifacts" in payload
    assert "coverage" in payload
    assert payload["orchestration"]["policy"]["fixed_boundary"] is True
    assert payload["orchestration"]["policy"]["fixed_workflow"] is True


def test_mutating_task_bus_escalates_when_plan_or_parameter_coverage_is_incomplete():
    task_bus = build_mutating_task_bus(
        intent="config_runtime_paths_update",
        operation="update_runtime_paths",
        user_goal="config:update_runtime_paths",
        mode="commander_runtime_method",
        available_tools=["invest_runtime_paths_get", "invest_runtime_paths_update"],
        recommended_plan=[
            {"tool": "invest_runtime_paths_get", "args": {}},
            {"tool": "invest_runtime_paths_update", "args": {"confirm": True}},
        ],
        tool_calls=[{"action": {"tool": "invest_runtime_paths_update", "args": {"confirm": True}}}],
        status="ok",
        risk_level="medium",
        decision="allow",
        requires_confirmation=False,
        reasons=list(MUTATING_DEFAULT_REASON_CODES),
    )

    gate = task_bus["gate"]
    assert gate["decision"] == "confirm"
    assert gate["risk_level"] == "high"
    assert gate["requires_confirmation"] is True
    assert gate["confirmation"]["state"] == "pending_confirmation"
    assert "incomplete_plan_coverage" in gate["reasons"]
    assert "incomplete_plan_coverage" in gate["confirmation"]["reason_codes"]
    assert "incomplete_parameter_coverage" not in gate["reasons"]
