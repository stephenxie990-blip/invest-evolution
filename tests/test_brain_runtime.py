import asyncio
from pathlib import Path

from brain_runtime import BrainRuntime, BrainTool


class EchoTool(BrainTool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the input text."

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }

    async def execute(self, **kwargs):
        return kwargs.get("text", "")


def test_explicit_tool_call_without_llm(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",  # no llm
    )
    runtime.tools.register(EchoTool())

    result = asyncio.run(runtime.process_direct('/tool echo {"text":"hello"}'))
    assert result == "hello"


def test_explicit_tool_call_validation_error(tmp_path: Path):
    runtime = BrainRuntime(
        workspace=tmp_path,
        model="test-model",
        api_key="",
    )
    runtime.tools.register(EchoTool())

    result = asyncio.run(runtime.process_direct('/tool echo {"wrong":"x"}'))
    assert "Invalid parameters" in result

