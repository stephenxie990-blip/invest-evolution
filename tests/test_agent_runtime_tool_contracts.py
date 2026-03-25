import asyncio
import json

import pytest

import config as config_module
import web_server
from app.commander_support.services import (
    ConfigSurfaceValidationError,
    update_agent_prompt_payload,
)
from brain.runtime import BrainToolRegistry
from brain.tools import InvestAgentPromptsUpdateTool


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
    agent_dir = tmp_path / "agent_settings"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agents_config.json").write_text(
        json.dumps({"hunter": {"llm_model": "fast", "system_prompt": "old prompt"}}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigSurfaceValidationError, match="unknown agent prompt name"):
        update_agent_prompt_payload(
            agent_name="__unknown_agent__",
            system_prompt="focus on evidence",
            project_root=tmp_path,
        )


def test_agent_prompts_endpoint_rejects_llm_model_patch(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    client = web_server.app.test_client()
    res = client.post(
        "/api/agent_prompts",
        data=json.dumps(
            {
                "name": "hunter",
                "llm_model": "gpt-5-mini",
                "system_prompt": "new prompt",
            }
        ),
        content_type="application/json",
    )

    assert res.status_code == 400
    assert res.get_json()["error"] == (
        "llm_model is not editable on /api/agent_prompts; use /api/control_plane for model binding"
    )
