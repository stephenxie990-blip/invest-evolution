import json
from pathlib import Path

from invest_evolution.agent_runtime.plugins import BridgeMessage, FileBridgeChannel


def test_bridge_channel_derives_internal_session_key_and_ignores_external_value(
    tmp_path: Path,
) -> None:
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
