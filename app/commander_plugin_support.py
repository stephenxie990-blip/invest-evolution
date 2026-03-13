"""Plugin and tool registration helpers for commander."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def register_fusion_tools(
    runtime: Any,
    *,
    build_tools: Callable[[Any], list[Any]],
    load_plugins: Callable[..., dict[str, Any]],
) -> None:
    for tool in build_tools(runtime):
        runtime.brain.tools.register(tool)
    load_plugins(persist=False)


def load_plugin_tools(
    *,
    brain_tools: Any,
    plugin_loader: Any,
    plugin_tool_names: set[str],
    plugin_dir: Path,
    persist: bool,
    persist_state: Callable[[], None],
) -> dict[str, Any]:
    for name in list(plugin_tool_names):
        brain_tools.unregister(name)
    plugin_tool_names.clear()

    loaded: list[str] = []
    for tool in plugin_loader.load_tools():
        brain_tools.register(tool)
        plugin_tool_names.add(tool.name)
        loaded.append(tool.name)

    if persist:
        persist_state()
    return {"count": len(loaded), "tools": loaded, "plugin_dir": str(plugin_dir)}
