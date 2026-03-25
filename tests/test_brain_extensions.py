from pathlib import Path

import json

from brain.bridge import BridgeHub, BridgeMessage, FileBridgeChannel
from brain.memory import MemoryStore
from brain.plugins import PluginLoader
from commander import CommanderConfig, CommanderRuntime


def test_memory_store_append_search(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.jsonl", max_records=10)
    store.append("user", "s1", "buy 600519", {"channel": "cli"})
    store.append("assistant", "s1", "hold", {"channel": "cli"})

    hits = store.search("buy", limit=5)
    assert len(hits) == 1
    assert "600519" in hits[0]["content"]


def test_memory_store_logs_invalid_rows_and_keeps_valid_entries(tmp_path: Path, caplog):
    store = MemoryStore(tmp_path / "memory.jsonl", max_records=10)
    store.path.write_text('{"id":"ok","content":"hello"}\n{"bad":\n', encoding="utf-8")

    with caplog.at_level("WARNING"):
        rows = store.recent(limit=5)

    assert len(rows) == 1
    assert rows[0]["id"] == "ok"
    assert "Skipped 1 invalid memory rows" in caplog.text


def test_file_bridge_channel_roundtrip(tmp_path: Path):
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    channel = FileBridgeChannel(inbox, outbox)

    req = {
        "id": "m1",
        "channel": "file",
        "chat_id": "c1",
        "session_key": "file:c1",
        "content": "status",
    }
    (inbox / "1.json").write_text(__import__("json").dumps(req), encoding="utf-8")

    batch = channel.poll_inbox()
    assert len(batch) == 1
    assert batch[0].content == "status"

    resp = BridgeMessage(
        id="m1",
        channel="file",
        chat_id="c1",
        session_key="file:c1",
        role="assistant",
        content="ok",
        ts_ms=1,
        metadata={},
    )
    out = channel.emit(resp)
    assert out.exists()


def test_plugin_loader_and_commander_reload(tmp_path: Path):
    plugin_dir = tmp_path / "plugins"
    loader = PluginLoader(plugin_dir)
    loader.ensure_templates()
    tools = loader.load_tools()
    assert tools

    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "outputs" / "state.json",
        cron_store=tmp_path / "outputs" / "cron.json",
        memory_store=tmp_path / "memory" / "memory.jsonl",
        plugin_dir=plugin_dir,
        bridge_inbox=tmp_path / "sessions" / "inbox",
        bridge_outbox=tmp_path / "sessions" / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    rt = CommanderRuntime(cfg)
    payload = rt.reload_plugins()
    assert payload["count"] >= 1
    assert any(name.startswith("plugin_") for name in payload["tools"])


def test_plugin_reload_does_not_override_builtin_tools(tmp_path: Path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "conflict.json").write_text(
        __import__("json").dumps(
            {
                "name": "invest_quick_status",
                "description": "bad conflict",
                "template": "conflict",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "outputs" / "state.json",
        cron_store=tmp_path / "outputs" / "cron.json",
        memory_store=tmp_path / "memory" / "memory.jsonl",
        plugin_dir=plugin_dir,
        bridge_inbox=tmp_path / "sessions" / "inbox",
        bridge_outbox=tmp_path / "sessions" / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    rt = CommanderRuntime(cfg)

    payload = rt.reload_plugins()

    assert payload["count"] == 0
    assert payload["skipped_conflicts"] == ["invest_quick_status"]
    assert rt.brain.tools.get("invest_quick_status") is not None


def test_file_bridge_channel_quarantines_malformed_messages(tmp_path: Path):
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    channel = FileBridgeChannel(inbox, outbox)

    (inbox / "bad.json").write_text('{"content":', encoding="utf-8")

    batch = channel.poll_inbox()
    assert batch == []
    quarantined = channel.invalid_dir / "bad.json"
    assert quarantined.exists()
    assert quarantined.with_suffix('.json.error.txt').exists()


def test_bridge_channel_derives_internal_session_key_and_ignores_external_value(tmp_path: Path):
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    channel = FileBridgeChannel(inbox, outbox)

    payload = {
        "id": "m1",
        "channel": "file",
        "chat_id": "c1",
        "session_key": "attacker:session",
        "external_conversation_id": "thread-a",
        "content": "status",
        "metadata": {"source": "external"},
    }
    (inbox / "request.json").write_text(json.dumps(payload), encoding="utf-8")

    batch = channel.poll_inbox()

    assert len(batch) == 1
    message = batch[0]
    assert message.session_key == "file:c1:thread-a"
    assert message.metadata["source"] == "external"
    assert message.metadata["ignored_external_session_key"] == "attacker:session"


def test_bridge_channel_emit_uses_atomic_json_write(tmp_path: Path) -> None:
    channel = FileBridgeChannel(tmp_path / "inbox", tmp_path / "outbox")
    path = channel.emit(
        BridgeMessage(
            id="m1",
            channel="file",
            chat_id="c1",
            session_key="file:c1",
            role="assistant",
            content="ok",
            ts_ms=1,
            metadata={},
        )
    )

    assert path.exists()
    assert list(path.parent.glob("*.tmp")) == []
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["session_key"] == "file:c1"


def test_bridge_handle_survives_error_emit_failure(tmp_path: Path):
    async def boom(_msg):
        raise RuntimeError("handler failed")

    hub = BridgeHub(tmp_path / "inbox", tmp_path / "outbox", on_message=boom, enabled=True)

    def broken_emit(_message):
        raise OSError("disk full")

    setattr(hub.file_channel, "emit", broken_emit)
    msg = BridgeMessage(
        id="m1",
        channel="file",
        chat_id="c1",
        session_key="file:c1",
        role="user",
        content="hello",
        ts_ms=1,
        metadata={},
    )

    import asyncio
    asyncio.run(hub._handle(msg))
    assert hub.failed == 1
