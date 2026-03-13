import json

import web_server
from commander import CommanderConfig, CommanderRuntime


def test_investment_models_api(tmp_path, monkeypatch):
    runtime = CommanderRuntime(CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state.json",
        cron_store=tmp_path / "cron.json",
        memory_store=tmp_path / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "inbox",
        bridge_outbox=tmp_path / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    ))
    monkeypatch.setattr(web_server, "_runtime", runtime)
    client = web_server.app.test_client()

    res = client.get("/api/investment-models")
    assert res.status_code == 200
    data = res.get_json()
    assert data["count"] == len(data["items"])
    assert "momentum" in data["items"]
    assert "defensive_low_vol" in data["items"]
    assert data["active_model"] == runtime.body.controller.model_name
    assert "enable_v2_pipeline" not in data


def test_leaderboard_api(tmp_path, monkeypatch):
    runtime = CommanderRuntime(CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state.json",
        cron_store=tmp_path / "cron.json",
        memory_store=tmp_path / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "inbox",
        bridge_outbox=tmp_path / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    ))
    training_root = runtime.cfg.training_output_dir.parent
    run_dir = training_root / "momentum_case"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "cycle_1.json").write_text(json.dumps({
        "cycle_id": 1,
        "cutoff_date": "20250101",
        "return_pct": 1.0,
        "is_profit": True,
        "benchmark_passed": True,
        "model_name": "momentum",
        "config_name": "momentum_v1",
        "self_assessment": {"regime": "bull", "sharpe_ratio": 1.1, "max_drawdown": 3.0, "excess_return": 0.5, "benchmark_passed": True},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(web_server, "_runtime", runtime)
    client = web_server.app.test_client()

    res = client.get("/api/leaderboard")
    assert res.status_code == 200
    data = res.get_json()
    assert data["total_models"] >= 1
    assert data["entries"][0]["model_name"] == "momentum"



def test_allocator_api(tmp_path, monkeypatch):
    runtime = CommanderRuntime(CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state.json",
        cron_store=tmp_path / "cron.json",
        memory_store=tmp_path / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "inbox",
        bridge_outbox=tmp_path / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    ))
    training_root = runtime.cfg.training_output_dir.parent
    run_dir = training_root / "defensive_case"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "cycle_1.json").write_text(json.dumps({
        "cycle_id": 1,
        "cutoff_date": "20250101",
        "return_pct": 1.0,
        "is_profit": True,
        "benchmark_passed": True,
        "model_name": "defensive_low_vol",
        "config_name": "defensive_low_vol_v1",
        "self_assessment": {"regime": "bear", "sharpe_ratio": 1.4, "max_drawdown": 2.0, "excess_return": 0.6, "benchmark_passed": True},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(web_server, "_runtime", runtime)
    client = web_server.app.test_client()

    res = client.get("/api/allocator?regime=bear&top_n=2")
    assert res.status_code == 200
    data = res.get_json()
    assert data["allocation"]["regime"] == "bear"
    assert "defensive_low_vol" in data["allocation"]["active_models"]
