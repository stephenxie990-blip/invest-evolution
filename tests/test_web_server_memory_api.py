import json

import pytest

import web_server
from brain.memory import MemoryStore
from commander import CommanderConfig, CommanderRuntime


def _make_runtime(tmp_path):
    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state.json",
        cron_store=tmp_path / "cron.json",
        memory_store=tmp_path / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "inbox",
        bridge_outbox=tmp_path / "outbox",
        training_output_dir=tmp_path / "training",
        meeting_log_dir=tmp_path / "meetings",
        config_audit_log_path=tmp_path / "runtime" / "state" / "config_changes.jsonl",
        config_snapshot_dir=tmp_path / "runtime" / "state" / "config_snapshots",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    return CommanderRuntime(cfg)


def test_memory_store_search_matches_metadata(tmp_path):
    store = MemoryStore(tmp_path / "memory.jsonl")
    rec = store.append(
        kind="training_run",
        session_key="runtime:train",
        content="训练记录",
        metadata={"summary": {"status": "ok"}, "selection_mode": "meeting"},
    )

    hits = store.search("meeting", limit=10)

    assert hits
    assert hits[-1]["id"] == rec.id
    stored = store.get(rec.id)
    assert stored is not None
    assert stored["id"] == rec.id


@pytest.mark.asyncio
async def test_train_once_appends_training_memory(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)

    async def fake_run_cycles(rounds=1, force_mock=False, task_source="direct"):
        return {
            "status": "ok",
            "rounds": rounds,
            "results": [
                {
                    "status": "ok",
                    "cycle_id": 1,
                    "return_pct": 1.23,
                    "trade_count": 2,
                    "selected_count": 1,
                    "selected_stocks": ["000001.SZ"],
                    "artifacts": {"cycle_result_path": str(tmp_path / "training" / "cycle_1.json")},
                }
            ],
            "summary": {"total_cycles": 1, "success_cycles": 1},
        }

    monkeypatch.setattr(runtime.body, "run_cycles", fake_run_cycles)

    await runtime.train_once(rounds=1, mock=True)

    rows = runtime.memory.search("训练记录", limit=10)
    assert rows
    latest = rows[-1]
    assert latest["kind"] == "training_run"
    assert latest["metadata"]["training_run"] is True
    assert latest["metadata"]["summary"]["success_count"] == 1


def test_memory_api_detail_returns_training_artifacts(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)

    cycle_path = tmp_path / "training" / "cycle_1.json"
    cycle_path.parent.mkdir(parents=True, exist_ok=True)
    cycle_path.write_text(json.dumps({"analysis": "测试复盘", "selected_stocks": ["000001.SZ"]}, ensure_ascii=False), encoding="utf-8")

    selection_json = tmp_path / "meetings" / "selection" / "meeting_0001.json"
    selection_json.parent.mkdir(parents=True, exist_ok=True)
    selection_json.write_text(json.dumps({"selected": ["000001.SZ"]}, ensure_ascii=False), encoding="utf-8")

    selection_md = tmp_path / "meetings" / "selection" / "meeting_0001.md"
    selection_md.write_text("# 选股会议\n- 000001.SZ", encoding="utf-8")

    review_json = tmp_path / "meetings" / "review" / "review_0001.json"
    review_json.parent.mkdir(parents=True, exist_ok=True)
    review_json.write_text(json.dumps({"decision": {"reasoning": "保持纪律"}}, ensure_ascii=False), encoding="utf-8")

    review_md = tmp_path / "meetings" / "review" / "review_0001.md"
    review_md.write_text("# 复盘会议\n保持纪律", encoding="utf-8")

    snapshot_path = tmp_path / "runtime" / "state" / "config_snapshots" / "cycle_0001.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps({"enable_debate": False}, ensure_ascii=False), encoding="utf-8")

    runtime.memory.append(
        kind="training_run",
        session_key="runtime:train",
        content="训练记录 | status=ok | rounds=1 | baseline",
        metadata={
            "training_run": True,
            "summary": {"status": "ok", "rounds": 1, "success_count": 1, "skipped_count": 0, "error_count": 0},
            "results": [
                {
                    "status": "ok",
                    "cycle_id": 0,
                    "return_pct": 0.5,
                    "trade_count": 1,
                    "selected_count": 2,
                    "selected_stocks": ["000002.SZ", "000003.SZ"],
                    "selection_mode": "meeting",
                    "review_applied": False,
                    "llm_used": False,
                    "benchmark_passed": True,
                    "params": {"stop_loss_pct": 0.05, "max_positions": 3},
                    "optimization_events": [{"cycle_id": 0, "kind": "tune"}],
                }
            ],
            "runtime_summary": {"total_cycles": 1, "success_cycles": 1},
        },
    )

    rec = runtime.memory.append(
        kind="training_run",
        session_key="runtime:train",
        content="训练记录 | status=ok | rounds=1",
        metadata={
            "training_run": True,
            "summary": {"status": "ok", "rounds": 1, "success_count": 1, "skipped_count": 0, "error_count": 0},
            "results": [
                {
                    "status": "ok",
                    "cycle_id": 1,
                    "return_pct": 1.23,
                    "trade_count": 2,
                    "selected_count": 1,
                    "selected_stocks": ["000001.SZ"],
                    "selection_mode": "meeting",
                    "review_applied": True,
                    "llm_used": False,
                    "benchmark_passed": False,
                    "params": {"stop_loss_pct": 0.07, "position_size": 0.2},
                    "config_snapshot_path": str(snapshot_path),
                    "artifacts": {
                        "cycle_result_path": str(cycle_path),
                        "selection_meeting_json_path": str(selection_json),
                        "selection_meeting_markdown_path": str(selection_md),
                        "review_meeting_json_path": str(review_json),
                        "review_meeting_markdown_path": str(review_md),
                        "optimization_events_path": str(tmp_path / "training" / "optimization_events.jsonl"),
                    },
                }
            ],
            "runtime_summary": {"total_cycles": 1, "success_cycles": 1},
        },
    )

    monkeypatch.setattr(web_server, "_runtime", runtime)
    client = web_server.app.test_client()

    list_res = client.get("/api/memory")
    assert list_res.status_code == 200
    items = list_res.get_json()["items"]
    assert items[-1]["ts"]
    assert items[-1]["training_run"] is True

    list_human = client.get("/api/memory?view=human")
    assert list_human.status_code == 200
    assert list_human.mimetype == "text/plain"
    assert "结论：已返回" in list_human.get_data(as_text=True)

    detail_res = client.get(f"/api/memory/{rec.id}")
    assert detail_res.status_code == 200
    body = detail_res.get_json()
    assert body["item"]["id"] == rec.id
    assert body["details"]["results"][0]["cycle_result"]["analysis"] == "测试复盘"
    assert "选股会议" in body["details"]["results"][0]["selection_meeting_markdown"]
    assert body["details"]["results"][0]["config_snapshot"]["enable_debate"] is False
    assert body["details"]["compare"]["has_previous"] is True
    assert body["details"]["compare"]["metrics"]["return_pct"]["delta"] == pytest.approx(0.73)
    assert body["details"]["compare"]["selected_stocks"]["added"] == ["000001.SZ"]
    assert body["details"]["compare"]["selected_stocks"]["removed"] == ["000002.SZ", "000003.SZ"]
    assert body["details"]["compare"]["flags"]["selection_mode"]["changed"] is False
    assert body["details"]["compare"]["params"]["changed_count"] == 3

    detail_human = client.get(f"/api/memory/{rec.id}?view=human")
    assert detail_human.status_code == 200
    assert detail_human.mimetype == "text/plain"
    assert f"记录 ID：{rec.id}" in detail_human.get_data(as_text=True)
