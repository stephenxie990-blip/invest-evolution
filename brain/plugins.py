"""Plugin tool loader for commander runtime.

Supported now: declarative JSON tools under plugins/*.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .runtime import BrainTool

logger = logging.getLogger(__name__)


class DeclarativePluginTool(BrainTool):
    def __init__(self, name: str, description: str, template: str):
        self._name = name
        self._description = description
        self._template = template

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "free text input"},
                "context": {"type": "string", "description": "optional context"},
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        payload = self._template
        payload = payload.replace("{{input}}", str(kwargs.get("input", "")))
        payload = payload.replace("{{context}}", str(kwargs.get("context", "")))
        return payload


class PluginLoader:
    def __init__(self, plugin_dir: Path, create_dir: bool = True):
        self.plugin_dir = Path(plugin_dir)
        if create_dir:
            self.plugin_dir.mkdir(parents=True, exist_ok=True)

    def ensure_templates(self) -> None:
        sample = self.plugin_dir / "risk_note.json"
        if sample.exists():
            return
        sample.write_text(
            json.dumps(
                {
                    "name": "plugin_risk_note",
                    "description": "Generate a compact risk note for latest context.",
                    "template": "[RiskNote] context={{context}} input={{input}}",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def load_tools(self) -> list[BrainTool]:
        tools: list[BrainTool] = []
        for path in sorted(self.plugin_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Skipped invalid plugin definition %s: %s", path, exc)
                continue
            name = str(data.get("name") or "").strip()
            description = str(data.get("description") or "").strip()
            template = str(data.get("template") or "").strip()
            if not name or not template:
                continue
            tools.append(DeclarativePluginTool(name=name, description=description or name, template=template))
        return tools
