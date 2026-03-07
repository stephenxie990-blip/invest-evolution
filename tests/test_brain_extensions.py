from pathlib import Path

from brain_bridge import BridgeMessage, FileBridgeChannel
from brain_memory import MemoryStore
from brain_plugins import PluginLoader
from commander import CommanderConfig, CommanderRuntime


def test_memory_store_append_search(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.jsonl", max_records=10)
    store.append("user", "s1", "buy 600519", {"channel": "cli"})
    store.append("assistant", "s1", "hold", {"channel": "cli"})

    hits = store.search("buy", limit=5)
    assert len(hits) == 1
    assert "600519" in hits[0]["content"]


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
