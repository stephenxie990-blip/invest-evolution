from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from invest_evolution.agent_runtime.plugins import BridgeHub, BridgeMessage, FileBridgeChannel
from invest_evolution.agent_runtime.memory import MemoryStore
from invest_evolution.agent_runtime.plugins import DeclarativePluginTool, PluginLoader
from invest_evolution.application.commander_main import CommanderConfig, CommanderRuntime


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


def test_memory_store_append_uses_cached_count_when_below_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = MemoryStore(tmp_path / "memory.jsonl", max_records=10)
    store.append("user", "s1", "first", {})

    def fail_load_all():
        raise AssertionError("_load_all should not run while append count remains below max_records")

    monkeypatch.setattr(store, "_load_all", fail_load_all)

    store.append("assistant", "s1", "second", {})
    assert store._record_count == 2


def test_memory_store_cold_count_uses_meta_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = MemoryStore(tmp_path / "memory.jsonl", max_records=10)
    store.append("user", "s1", "first", {})
    store.append("assistant", "s1", "second", {})

    cold_store = MemoryStore(store.path, max_records=10, create=False)

    def fail_count(path, *, warning_label="memory store"):
        del path, warning_label
        raise AssertionError("_count_nonempty_lines should not run when count cache is valid")

    monkeypatch.setattr(cold_store, "_count_nonempty_lines", fail_count)

    assert cold_store._resolve_record_count() == 2


def test_memory_store_truncate_uses_streaming_tail_without_loading_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = MemoryStore(tmp_path / "memory.jsonl", max_records=100)
    rows = [
        {"id": f"r{i}", "content": f"row-{i}"}
        for i in range(106)
    ]
    store.path.write_text(
        "\n".join(__import__("json").dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    store._record_count = 106

    def fail_load_all():
        raise AssertionError("_load_all should not run during streaming truncation")

    monkeypatch.setattr(store, "_load_all", fail_load_all)

    store._truncate_if_needed()

    kept = [__import__("json").loads(line) for line in store.path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(kept) == 100
    assert kept[0]["id"] == "r6"
    assert kept[-1]["id"] == "r105"


def test_memory_store_tail_lines_reads_from_end_without_scanning_full_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = MemoryStore(tmp_path / "memory.jsonl", max_records=50)
    rows = [
        {
            "id": f"r{i}",
            "kind": "user",
            "session_key": "s1",
            "content": f"row-{i}-" + ("x" * 240),
            "metadata": {"i": i},
        }
        for i in range(2000)
    ]
    store.path.write_text(
        "\n".join(__import__("json").dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    file_size = store.path.stat().st_size
    original_open = type(store.path).open
    bytes_read = {"value": 0}

    class _CountingBinaryHandle:
        def __init__(self, handle):
            self._handle = handle

        def seek(self, *args, **kwargs):
            return self._handle.seek(*args, **kwargs)

        def tell(self):
            return self._handle.tell()

        def read(self, *args, **kwargs):
            chunk = self._handle.read(*args, **kwargs)
            bytes_read["value"] += len(chunk)
            return chunk

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            return self._handle.__exit__(exc_type, exc, tb)

    def counting_open(path_obj, *args, **kwargs):
        handle = original_open(path_obj, *args, **kwargs)
        mode = kwargs.get("mode")
        if mode is None and args:
            mode = args[0]
        if path_obj == store.path and mode == "rb":
            return _CountingBinaryHandle(handle)
        return handle

    monkeypatch.setattr(type(store.path), "open", counting_open)

    tail = store._tail_lines(10)

    assert len(tail) == 10
    assert '"id": "r1999"' in tail[-1]
    assert bytes_read["value"] < file_size // 4


def test_memory_store_read_paths_stream_without_loading_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = MemoryStore(tmp_path / "memory.jsonl", max_records=10)
    store.path.write_text(
        "\n".join(
            [
                __import__("json").dumps({"id": "r1", "kind": "user", "content": "buy 600519", "metadata": {}}),
                __import__("json").dumps({"id": "r2", "kind": "assistant", "content": "hold", "metadata": {}}),
                __import__("json").dumps({"id": "r3", "kind": "user", "content": "sell 000001", "metadata": {}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def fail_load_all():
        raise AssertionError("_load_all should not run for streaming read paths")

    monkeypatch.setattr(store, "_load_all", fail_load_all)

    assert [row["id"] for row in store.recent(limit=2)] == ["r2", "r3"]
    assert [row["id"] for row in store.search("buy", limit=2)] == ["r1"]
    record = store.get("r2")
    assert record is not None
    assert record["content"] == "hold"
    assert store.stats()["records"] == 3


def test_memory_store_recent_without_kind_uses_tail_fast_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = MemoryStore(tmp_path / "memory.jsonl", max_records=20)
    for idx in range(8):
        store.append("user", "s1", f"row-{idx}", {"i": idx})

    def fail_iter_rows():
        raise AssertionError("_iter_rows should not run for recent(limit) without kind filter")
        yield

    monkeypatch.setattr(store, "_iter_rows", fail_iter_rows)

    recent = store.recent(limit=3)
    assert [row["content"] for row in recent] == ["row-5", "row-6", "row-7"]


def test_memory_store_limit_zero_returns_empty_lists(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.jsonl", max_records=10)
    store.append("user", "s1", "first", {})
    store.append("assistant", "s1", "second", {})

    assert store.recent(limit=0) == []
    assert store.search("", limit=0) == []


def test_memory_store_append_refreshes_stale_record_count_before_truncation(tmp_path: Path):
    primary = MemoryStore(tmp_path / "memory.jsonl", max_records=3)
    primary.max_records = 3
    primary.append("user", "s1", "row-1", {})

    stale = MemoryStore(primary.path, max_records=3, create=False)
    stale.max_records = 3
    stale._record_count = 1

    primary.append("user", "s1", "row-2", {})
    primary.append("user", "s1", "row-3", {})
    primary.append("user", "s1", "row-4", {})

    stale.append("user", "s1", "row-5", {})

    rows = [__import__("json").loads(line) for line in primary.path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["content"] for row in rows] == ["row-3", "row-4", "row-5"]
    assert primary.stats()["records"] == 3


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
        playbook_dir=tmp_path / "strategies",
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
        playbook_dir=tmp_path / "strategies",
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


def test_declarative_plugin_rejects_unknown_placeholders():
    with pytest.raises(ValueError, match="unsupported placeholder"):
        DeclarativePluginTool(
            name="plugin_test",
            description="template test",
            template="hello {{input}} {{unexpected}}",
        )


def test_declarative_plugin_sanitizes_input_and_context():
    tool = DeclarativePluginTool(
        name="plugin_safe",
        description="safety test",
        template="input={{input}};context={{context}}",
    )

    result = asyncio.run(tool.execute(input='danger " {{', context="ctx\nline"))
    assert 'danger \\" { {' in result
    assert "ctx\\nline" in result


def test_plugin_loader_skips_invalid_placeholder_templates(tmp_path: Path, caplog):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "bad_template.json").write_text(
        __import__("json").dumps(
            {
                "name": "plugin_bad",
                "description": "bad template",
                "template": "hello {{unexpected}}",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        tools = PluginLoader(plugin_dir).load_tools()

    assert tools == []
    assert "Skipped declarative plugin" in caplog.text


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
