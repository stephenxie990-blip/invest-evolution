"""Config and runtime-path support helpers for commander."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


def apply_runtime_path_overrides(
    cfg: Any,
    overrides: dict[str, Any],
    *,
    editable_keys: Iterable[str],
) -> Any:
    for key in editable_keys:
        if value := overrides.get(key):
            setattr(cfg, key, Path(value).expanduser().resolve())
    cfg.__post_init__()
    return cfg


def sync_runtime_path_config(
    runtime: Any,
    payload: dict[str, Any],
    *,
    editable_keys: Iterable[str],
    meeting_recorder_cls: Any,
    evolution_config_service_cls: Any,
) -> None:
    import config as config_module

    apply_runtime_path_overrides(runtime.cfg, payload, editable_keys=editable_keys)
    controller = runtime.body.controller
    controller.output_dir = Path(runtime.cfg.training_output_dir)
    controller.output_dir.mkdir(parents=True, exist_ok=True)
    controller.meeting_recorder = meeting_recorder_cls(base_dir=str(runtime.cfg.meeting_log_dir))
    controller.config_service = evolution_config_service_cls(
        project_root=config_module.PROJECT_ROOT,
        live_config=config_module.config,
        audit_log_path=Path(runtime.cfg.config_audit_log_path),
        snapshot_dir=Path(runtime.cfg.config_snapshot_dir),
    )


def relocate_commander_state_paths(
    cfg: Any,
    *,
    default_state_dir: Path,
    default_state_parent: Path,
    default_training_output_dir: Path,
    default_meeting_log_dir: Path,
    state_dir_relocations: dict[str, str],
) -> None:
    state_parent_changed = cfg.state_file.parent != default_state_parent
    if cfg.runtime_state_dir == default_state_dir and state_parent_changed:
        cfg.runtime_state_dir = cfg.state_file.parent
    if cfg.training_output_dir == default_training_output_dir and state_parent_changed:
        cfg.training_output_dir = cfg.state_file.parent / "training"
    if cfg.meeting_log_dir == default_meeting_log_dir and state_parent_changed:
        cfg.meeting_log_dir = cfg.state_file.parent / "meetings"
    for attr, filename in state_dir_relocations.items():
        if getattr(cfg, attr) == default_state_dir / filename:
            setattr(cfg, attr, cfg.runtime_state_dir / filename)


def build_commander_config_from_args(
    config_cls: Any,
    args: Any,
    *,
    path_config_service_cls: Any,
    project_root: Path,
    apply_runtime_overrides: Any,
) -> Any:
    cfg = config_cls()
    runtime_paths = path_config_service_cls(project_root=project_root).load_overrides()
    apply_runtime_overrides(cfg, runtime_paths)

    if workspace := getattr(args, "workspace", None):
        cfg.workspace = Path(workspace).expanduser().resolve()
    if strategy_dir := getattr(args, "strategy_dir", None):
        cfg.strategy_dir = Path(strategy_dir).expanduser().resolve()

    if model := getattr(args, "model", None):
        cfg.model = model
    if api_key := getattr(args, "api_key", None):
        cfg.api_key = api_key
    if api_base := getattr(args, "api_base", None):
        cfg.api_base = api_base

    if getattr(args, "mock", False):
        cfg.mock_mode = True
    if getattr(args, "no_autopilot", False):
        cfg.autopilot_enabled = False
    if getattr(args, "no_heartbeat", False):
        cfg.heartbeat_enabled = False

    if train_interval_sec := getattr(args, "train_interval_sec", None):
        cfg.training_interval_sec = max(60, int(train_interval_sec))
    if heartbeat_interval_sec := getattr(args, "heartbeat_interval_sec", None):
        cfg.heartbeat_interval_sec = max(60, int(heartbeat_interval_sec))

    return cfg
