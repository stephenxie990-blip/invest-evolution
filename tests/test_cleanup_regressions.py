from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from app.strategy_gene_registry import StrategyGeneRegistry
from brain.plugins import PluginLoader
from brain.runtime import BrainRuntime, BrainTool
from invest.leaderboard.engine import collect_cycle_records


class _EchoTool(BrainTool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "echo"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs):
        return "ok"


def test_plugin_loader_logs_invalid_plugin_json(tmp_path, caplog):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "bad.json").write_text('{"name":', encoding="utf-8")

    with caplog.at_level("WARNING"):
        tools = PluginLoader(plugin_dir).load_tools()

    assert tools == []
    assert "Skipped invalid plugin definition" in caplog.text


def test_collect_cycle_records_logs_invalid_cycle_payload(tmp_path, caplog):
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "cycle_1.json").write_text('{"cycle_id":', encoding="utf-8")

    with caplog.at_level("WARNING"):
        records = collect_cycle_records(tmp_path)

    assert records == []
    assert "Skipped invalid cycle record" in caplog.text


def test_python_strategy_gene_logs_invalid_meta_literal(tmp_path, caplog):
    path = tmp_path / "bad_meta.py"
    path.write_text(
        "GENE_META = some_runtime_value\n\n"
        "def helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        genes = StrategyGeneRegistry(tmp_path).reload(create_dir=False)

    assert len(genes) == 1
    assert genes[0].gene_id == "bad_meta"
    assert "Failed to parse python strategy gene metadata" in caplog.text


def test_brain_runtime_logs_progress_callback_failure(tmp_path, caplog):
    runtime = BrainRuntime(workspace=tmp_path, model="test-model", api_key="token")
    runtime.tools.register(_EchoTool())

    first = type(
        "Resp",
        (),
        {
            "choices": [
                type(
                    "Choice",
                    (),
                    {
                        "message": type(
                            "Msg",
                            (),
                            {
                                "content": "",
                                "tool_calls": [
                                    type(
                                        "ToolCall",
                                        (),
                                        {
                                            "id": "tc1",
                                            "function": type(
                                                "Fn",
                                                (),
                                                {"name": "echo", "arguments": json.dumps({})},
                                            )(),
                                        },
                                    )()
                                ],
                            },
                        )()
                    },
                )()
            ]
        },
    )()
    second = type(
        "Resp",
        (),
        {
            "choices": [
                type(
                    "Choice",
                    (),
                    {"message": type("Msg", (), {"content": "done", "tool_calls": []})()},
                )()
            ]
        },
    )()

    class DummyGateway:
        available = True

        def __init__(self):
            self._responses = [first, second]

        async def acompletion_raw(self, **kwargs):
            return self._responses.pop(0)

    cast(Any, runtime).gateway = DummyGateway()

    async def _boom(_message: str) -> None:
        raise RuntimeError("progress failed")

    with caplog.at_level("WARNING"):
        result = asyncio.run(runtime.process_direct("use tool", on_progress=_boom))

    assert result == "done"
    assert "BrainRuntime progress callback failed for tool echo" in caplog.text
