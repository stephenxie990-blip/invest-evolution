import json

import pytest

from commander import CommanderConfig, CommanderRuntime
from market_data.repository import MarketDataRepository


@pytest.fixture()
def runtime_with_db(tmp_path, monkeypatch):
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
            "close": 10 + day * 0.12,
            "volume": 1000 + day * 10,
            "amount": 5000 + day * 100,
            "pct_chg": 0.5,
            "turnover": 1.2,
            "source": "test",
        }
        for day in range(1, 31)
    ])
    monkeypatch.setenv("INVEST_DB_PATH", str(db_path))

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
    runtime._ensure_runtime_storage()
    return runtime


def test_build_commander_tools_exposes_unified_entry_tools(runtime_with_db):
    from brain.tools import build_commander_tools

    names = [tool.name for tool in build_commander_tools(runtime_with_db)]
    assert "invest_training_runs_list" in names
    assert "invest_control_plane_get" in names
    assert "invest_data_status" in names
    assert "invest_events_summary" in names
    assert "invest_ask_stock" in names


def test_runtime_exposes_analysis_config_data_and_observability(runtime_with_db):
    runtime = runtime_with_db

    models = runtime.get_investment_models()
    assert "items" in models

    control_plane = runtime.get_control_plane()
    assert control_plane["status"] == "ok"
    assert control_plane["entrypoint"]["agent_kind"] == "bounded_config_agent"
    assert control_plane["orchestration"]["policy"]["fixed_boundary"] is True

    data_status = runtime.get_data_status(refresh=False)
    assert "quality" in data_status
    assert data_status["entrypoint"]["agent_kind"] == "bounded_data_agent"
    assert data_status["orchestration"]["phase_stats"]["requested_refresh"] is False

    events = runtime.get_events_summary(limit=20)
    assert events["status"] == "ok"
    assert "summary" in events


def test_runtime_high_risk_write_requires_confirmation(runtime_with_db):
    payload = runtime_with_db.update_control_plane({"llm": {"bindings": {"controller.main": "x"}}}, confirm=False)
    assert payload["status"] == "confirmation_required"
    assert payload["restart_required"] is True
    assert payload["entrypoint"]["agent_kind"] == "bounded_config_agent"
    assert payload["orchestration"]["workflow"] == ["config_scope_resolve", "gate_confirmation", "finalize"]
    assert payload["orchestration"]["policy"]["confirmation_gate"] is True
    assert payload["pending"]["patch"]["llm"]["bindings"]["controller.main"] == "x"


@pytest.mark.asyncio
async def test_runtime_ask_routes_natural_language_without_llm(runtime_with_db):
    result = await runtime_with_db.ask("请看看系统状态", session_key="test:nl")
    payload = json.loads(result)
    assert payload["detail_mode"] == "fast"
    assert payload["entrypoint"]["agent_kind"] == "bounded_runtime_agent"
    assert payload["orchestration"]["workflow"] == ["runtime_scope_resolve", "status_read", "finalize"]
    assert payload["task_bus"]["planner"]["intent"] == "runtime_status"
    assert payload["task_bus"]["gate"]["writes_state"] is False


@pytest.mark.asyncio
async def test_runtime_ask_stock_works_via_natural_language_fallback(runtime_with_db):
    result = await runtime_with_db.ask("用缠论分析 FooBank", session_key="test:stock")
    payload = json.loads(result)
    assert payload["status"] == "ok"
    assert payload["resolved"]["code"] == "sh.600001"
    assert payload["dashboard"]["signal"]
    assert payload["orchestration"]["mode"] == "yaml_react_like"
    assert payload["orchestration"]["step_count"] >= 3
    assert payload["task_bus"]["planner"]["intent"] == "stock_analysis"


def test_runtime_records_runtime_events(runtime_with_db):
    runtime_with_db._append_runtime_event("custom_event", {"ok": True})
    payload = runtime_with_db.get_events_tail(limit=10)
    assert payload["count"] >= 1
    assert payload["items"][-1]["event"] == "custom_event"


@pytest.mark.asyncio
async def test_runtime_ask_prefers_data_status_over_generic_status(runtime_with_db):
    result = await runtime_with_db.ask("请帮我刷新数据状态", session_key="test:data-status")
    payload = json.loads(result)
    assert "quality" in payload
    assert payload["quality"]["health_status"] in {"healthy", "warning", "error"}
    assert payload["task_bus"]["planner"]["intent"] == "data_status"
    assert payload["task_bus"]["audit"]["used_tools"] == ["invest_data_status"]
    assert payload["task_bus"]["planner"]["recommended_plan"][0]["tool"] == "invest_data_status"
    assert payload["task_bus"]["planner"]["recommended_plan"][1]["tool"] == "invest_data_download"


@pytest.mark.asyncio
async def test_runtime_ask_combines_status_and_recent_training(runtime_with_db):
    result = await runtime_with_db.ask("分析一下系统状态和最近训练", session_key="test:combo")
    payload = json.loads(result)
    assert payload["status"] == "ok"
    assert payload["intent"] == "status_and_recent_training"
    assert "quick_status" in payload
    assert "training_lab" in payload
    assert payload["human_readable"]["title"] == "系统运行摘要"
    assert payload["human_readable"]["bullets"]
    assert payload["task_bus"]["audit"]["used_tools"] == ["invest_quick_status", "invest_training_lab_summary"]


@pytest.mark.asyncio
async def test_runtime_ask_config_query_does_not_misroute_to_stock(runtime_with_db):
    result = await runtime_with_db.ask("我想看看配置有没有问题", session_key="test:config-risk")
    payload = json.loads(result)
    assert payload["status"] == "ok"
    assert "runtime" in payload
    assert "event_summary" in payload
    assert payload["human_readable"]["title"] == "系统运行摘要"


@pytest.mark.asyncio
async def test_runtime_ask_control_plane_overview_via_natural_language(runtime_with_db):
    result = await runtime_with_db.ask("看看控制面配置", session_key="test:config-overview")
    payload = json.loads(result)
    assert payload["status"] == "ok"
    assert payload["intent"] == "config_overview"
    assert "control_plane" in payload
    assert "evolution_config" in payload
    assert payload["entrypoint"]["agent_kind"] == "bounded_config_agent"
    assert payload["orchestration"]["workflow"] == ["config_scope_resolve", "control_plane_read", "evolution_config_read", "finalize"]
    assert payload["task_bus"]["planner"]["intent"] == "config_overview"
    assert payload["task_bus"]["gate"]["writes_state"] is False
    assert payload["task_bus"]["planner"]["recommended_plan"][0]["tool"] == "invest_control_plane_get"
    assert payload["task_bus"]["planner"]["recommended_plan"][1]["tool"] == "invest_evolution_config_get"


@pytest.mark.asyncio
async def test_runtime_ask_multi_round_real_training_requires_explicit_confirmation(runtime_with_db):
    result = await runtime_with_db.ask("请帮我真实训练2轮", session_key="test:train-confirm")
    payload = json.loads(result)
    assert payload["status"] == "confirmation_required"
    assert payload["pending"]["rounds"] == 2
    assert payload["pending"]["mock"] is False
    assert payload["task_bus"]["schema_version"] == "task_bus.v2"
    assert payload["task_bus"]["planner"]["intent"] == "training_execution"
    assert payload["task_bus"]["planner"]["plan_summary"]["recommended_step_count"] >= 2
    assert payload["task_bus"]["gate"]["writes_state"] is True
    assert payload["task_bus"]["gate"]["requires_confirmation"] is True
    assert payload["task_bus"]["gate"]["confirmation"]["required"] is True
    assert payload["task_bus"]["gate"]["confirmation"]["state"] == "pending_confirmation"
    assert "tool_grounded_execution" in payload["task_bus"]["gate"]["confirmation"]["reason_codes"]
    assert payload["task_bus"]["gate"]["confirmation"]["reason_codes"]
    assert payload["task_bus"]["planner"]["recommended_plan"][0]["step_id"] == "step_01"
    assert payload["task_bus"]["planner"]["recommended_plan"][0]["tool"] == "invest_quick_test"
    assert payload["task_bus"]["planner"]["recommended_plan"][1]["tool"] == "invest_training_plan_create"


@pytest.mark.asyncio
async def test_runtime_ask_data_status_preserves_bounded_workflow(runtime_with_db):
    result = await runtime_with_db.ask("请帮我刷新数据状态", session_key="test:data-bounded")
    payload = json.loads(result)
    assert payload["entrypoint"]["agent_kind"] == "bounded_data_agent"
    assert payload["orchestration"]["policy"]["fixed_workflow"] is True
    assert payload["orchestration"]["phase_stats"]["requested_refresh"] is True


@pytest.mark.asyncio
async def test_runtime_ask_multi_round_training_confirmation_has_bounded_workflow(runtime_with_db):
    result = await runtime_with_db.ask("请帮我真实训练2轮", session_key="test:train-bounded")
    payload = json.loads(result)
    assert payload["status"] == "confirmation_required"
    assert payload["entrypoint"]["agent_kind"] == "bounded_training_agent"
    assert payload["orchestration"]["policy"]["confirmation_gate"] is True
    assert payload["orchestration"]["phase_stats"]["rounds"] == 2



def test_update_evolution_config_bounded_workflow_on_confirm(runtime_with_db, monkeypatch):
    import app.commander as commander_module

    monkeypatch.setattr(
        commander_module,
        "update_evolution_config_payload",
        lambda **kwargs: {"status": "ok", "updated": ["data_source"], "config": {"data_source": "mock"}},
    )

    payload = runtime_with_db.update_evolution_config({"data_source": "mock"}, confirm=True)

    assert payload["status"] == "ok"
    assert payload["entrypoint"]["agent_kind"] == "bounded_config_agent"
    assert payload["entrypoint"]["runtime_tool"] == "invest_evolution_config_update"
    assert payload["orchestration"]["workflow"] == ["config_scope_resolve", "evolution_config_write", "finalize"]
    assert payload["orchestration"]["policy"]["writes_state"] is True
    assert payload["orchestration"]["phase_stats"]["updated_count"] == 1


def test_trigger_data_download_confirmation_is_bounded(runtime_with_db):
    payload = runtime_with_db.trigger_data_download(confirm=False)

    assert payload["status"] == "confirmation_required"
    assert payload["entrypoint"]["agent_kind"] == "bounded_data_agent"
    assert payload["orchestration"]["workflow"] == ["data_scope_resolve", "gate_confirmation", "finalize"]
    assert payload["orchestration"]["policy"]["confirmation_gate"] is True
    assert "job" in payload
