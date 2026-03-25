import pytest

from invest_evolution.agent_runtime.tools import (
    BrainToolRegistry,
    InvestAgentPromptsUpdateTool,
)
from invest_evolution.application.config_surface import (
    ConfigSurfaceValidationError,
    update_agent_prompt_payload,
)


class _NoopRuntime:
    def update_agent_prompt(self, *, agent_name: str, system_prompt: str) -> dict:
        return {"status": "ok", "agent_name": agent_name, "prompt": system_prompt}


def test_agent_prompt_tool_schema_rejects_unknown_fields() -> None:
    tool = InvestAgentPromptsUpdateTool(_NoopRuntime())
    errors = tool.validate_params(
        {"name": "researcher", "system_prompt": "focus", "extra": "not-allowed"}
    )
    assert any("unexpected field extra" in item for item in errors)


def test_tool_registry_rejects_unknown_fields_for_agent_prompt_tool() -> None:
    import asyncio

    registry = BrainToolRegistry()
    registry.register(InvestAgentPromptsUpdateTool(_NoopRuntime()))
    result = asyncio.run(
        registry.execute(
            "invest_agent_prompts_update",
            {"name": "researcher", "system_prompt": "focus", "llm_model": "fast"},
        )
    )
    assert "Invalid parameters for tool 'invest_agent_prompts_update'" in result
    assert "unexpected field llm_model" in result


def test_update_agent_prompt_payload_rejects_unknown_agent(tmp_path) -> None:
    with pytest.raises(ConfigSurfaceValidationError, match="unknown agent prompt name"):
        update_agent_prompt_payload(
            agent_name="__unknown_agent__",
            system_prompt="focus on evidence",
            project_root=tmp_path,
        )
