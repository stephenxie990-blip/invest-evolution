from __future__ import annotations

import asyncio
import importlib
import json
import sys
from typing import Any, cast

from invest_evolution.application.commander.bootstrap import PlaybookRegistry
from invest_evolution.application.training.controller import TrainingLifecycleService
from invest_evolution.application.training.policy import TrainingGovernanceService
from invest_evolution.agent_runtime.plugins import PluginLoader
from invest_evolution.agent_runtime.runtime import BrainRuntime
from invest_evolution.agent_runtime.tools import BrainTool
from invest_evolution.investment.governance.engine import collect_cycle_records


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


def test_python_playbook_logs_invalid_meta_literal(tmp_path, caplog):
    path = tmp_path / "bad_meta.py"
    path.write_text(
        "GENE_META = some_runtime_value\n\n"
        "def helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        genes = PlaybookRegistry(tmp_path).reload(create_dir=False)

    assert len(genes) == 1
    assert genes[0].playbook_id == "bad_meta"
    assert "Failed to parse python commander playbook metadata" in caplog.text


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


def test_training_execution_import_does_not_eager_load_observability():
    execution_module_name = "invest_evolution.application.training.execution"
    observability_module_name = "invest_evolution.application.training.observability"
    previous_execution_module = sys.modules.pop(execution_module_name, None)
    previous_observability_module = sys.modules.pop(observability_module_name, None)
    try:
        importlib.import_module(execution_module_name)
        assert observability_module_name not in sys.modules
    finally:
        sys.modules.pop(execution_module_name, None)
        sys.modules.pop(observability_module_name, None)
        if previous_execution_module is not None:
            sys.modules[execution_module_name] = previous_execution_module
        if previous_observability_module is not None:
            sys.modules[observability_module_name] = previous_observability_module


def test_prepare_leaderboard_safe_mode_logs_warning_on_failure(tmp_path, monkeypatch, caplog):
    service = TrainingGovernanceService()

    def _raise_write(*args, **kwargs):
        raise RuntimeError("refresh failed")

    monkeypatch.setattr("invest_evolution.application.training.policy.write_leaderboard", _raise_write)
    with caplog.at_level("WARNING"):
        cast(Any, service).prepare_leaderboard(output_dir=tmp_path / "training", safe=True)
    assert "Leaderboard refresh failed in safe mode" in caplog.text


def test_lifecycle_refresh_leaderboards_emits_warning_event_on_failure(caplog):
    events: list[tuple[str, dict[str, Any]]] = []
    controller = type(
        "Controller",
        (),
        {
            "current_cycle_id": 7,
            "training_persistence_service": type(
                "Persistence",
                (),
                {"refresh_leaderboards": staticmethod(lambda _controller: (_ for _ in ()).throw(RuntimeError("boom")))},
            )(),
            "_emit_runtime_event": staticmethod(lambda level, payload: events.append((str(level), dict(payload)))),
        },
    )()
    with caplog.at_level("WARNING"):
        TrainingLifecycleService._refresh_leaderboards(controller)
    assert "Final leaderboard refresh failed" in caplog.text
    assert events
    assert events[-1][0] == "warning"
    assert events[-1][1]["severity"] == "warning"
    assert events[-1][1]["cycle_id"] == 7
