"""Canonical config/public-surface helpers shared by commander and web."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from invest_evolution.config import (
    PROJECT_ROOT,
    AgentConfigRegistry,
    agent_config_registry,
    config,
)
from invest_evolution.config.control_plane import (
    PUBLIC_RUNTIME_PATH_KEYS,
    ControlPlaneConfigService,
    EvolutionConfigService,
    RuntimePathConfigService,
    build_public_runtime_paths_payload,
    get_default_llm_status,
)

_CONTROL_PLANE_PUBLIC_METADATA_KEYS = frozenset(
    {
        "config_path",
        "local_override_path",
        "local_override_exists",
        "audit_log_path",
        "audit_log_exists",
        "snapshot_dir",
    }
)

_EVOLUTION_CONFIG_INTERNAL_METADATA_KEYS = frozenset(
    {
        "config_path",
        "config_file_exists",
        "runtime_override_path",
        "runtime_override_exists",
        "config_layers",
        "local_override_path",
        "web_api_token_masked",
        "web_api_token_source",
        "audit_log_path",
        "snapshot_dir",
        "effective_runtime_mode",
        "runtime_contract_version",
        "deprecated_flags",
    }
)


@dataclass(frozen=True)
class ConfigSurfaceReadSpec:
    runtime_fetch: Callable[[Any], Any]
    fallback_fetch: Callable[[], Any]


@dataclass(frozen=True)
class ConfigSurfaceUpdateSpec:
    runtime_update: Callable[[Any, dict[str, Any]], Any]
    fallback_update: Callable[[dict[str, Any]], Any]
    error_label: str
    request_payload: Callable[[dict[str, Any]], Any]
    validate_payload: Callable[[dict[str, Any]], None] | None = None


@dataclass(frozen=True)
class ConfigSurfaceRouteSpec:
    surface: str
    path: str
    update_request_kind: str


class ConfigSurfaceValidationError(ValueError):
    def __init__(self, message: str, *, invalid_keys: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.invalid_keys = invalid_keys


def _resolve_agent_registry(project_root: Path | None = None):
    root = Path(project_root or PROJECT_ROOT)
    target = (root / "agent_settings" / "agents_config.json").resolve()
    current = Path(agent_config_registry.json_path).resolve()
    if current == target:
        return agent_config_registry
    if not target.exists():
        return agent_config_registry
    return AgentConfigRegistry(target)


def list_agent_prompts_payload(*, project_root: Path | None = None) -> dict[str, Any]:
    registry = _resolve_agent_registry(project_root or PROJECT_ROOT)
    items = []
    for cfg in registry.list_configs():
        name = str(cfg.get("name", "") or "").strip()
        if not name:
            continue
        items.append(
            {
                "name": name,
                "role": str(cfg.get("role", name) or name),
                "system_prompt": str(cfg.get("system_prompt", "") or ""),
            }
        )
    return {"status": "ok", "configs": items}


def _known_agent_names(registry: AgentConfigRegistry) -> set[str]:
    return {
        str(item.get("name") or "").strip()
        for item in list(registry.list_configs() or [])
        if str(item.get("name") or "").strip()
    }


def update_agent_prompt_payload(
    *, agent_name: str, system_prompt: str, project_root: Path | None = None
) -> dict[str, Any]:
    registry = _resolve_agent_registry(project_root)
    normalized_agent_name = str(agent_name or "").strip()
    known_names = _known_agent_names(registry)
    if normalized_agent_name not in known_names:
        raise ConfigSurfaceValidationError(
            f"unknown agent prompt name: {normalized_agent_name or '<empty>'}",
            invalid_keys=("name",),
        )
    current_cfg = dict(registry.get_config(normalized_agent_name) or {})
    current_cfg["system_prompt"] = str(system_prompt or "")
    ok = registry.save_config(normalized_agent_name, current_cfg)
    if not ok:
        raise RuntimeError("failed to persist agent config")
    if registry is not agent_config_registry:
        agent_config_registry.reload()
    return {
        "status": "ok",
        "updated": [f"agent_prompts.{normalized_agent_name}.system_prompt"],
        "restart_required": False,
    }


def public_runtime_path_keys() -> tuple[str, ...]:
    return PUBLIC_RUNTIME_PATH_KEYS


def invalid_runtime_paths_patch_keys(patch: dict[str, Any] | None) -> list[str]:
    payload = dict(patch or {})
    return sorted(set(payload.keys()) - set(public_runtime_path_keys()))


def validate_runtime_paths_patch(patch: dict[str, Any]) -> None:
    invalid_keys = invalid_runtime_paths_patch_keys(patch)
    if invalid_keys:
        raise ConfigSurfaceValidationError(
            "runtime_paths public API only accepts training_output_dir and artifact_log_dir; config audit/snapshot paths are internal runtime details",
            invalid_keys=tuple(invalid_keys),
        )


def build_public_control_plane_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(payload or {})
    return {
        str(key): value
        for key, value in source.items()
        if str(key) not in _CONTROL_PLANE_PUBLIC_METADATA_KEYS
    }


def build_public_evolution_config_payload(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    source = dict(payload or {})
    return {
        str(key): value
        for key, value in source.items()
        if str(key) not in _EVOLUTION_CONFIG_INTERNAL_METADATA_KEYS
    }


def _public_runtime_paths_response_config(payload: dict[str, Any]) -> dict[str, Any]:
    return build_public_runtime_paths_payload(payload)


def get_runtime_paths_payload(
    runtime: Any | None = None, *, project_root: Path | None = None
) -> dict[str, Any]:
    service = RuntimePathConfigService(project_root=project_root or PROJECT_ROOT)
    payload = service.get_payload()
    if runtime is not None:
        payload.update(
            {
                "training_output_dir": str(runtime.cfg.training_output_dir),
                "artifact_log_dir": str(runtime.cfg.artifact_log_dir),
                "config_audit_log_path": str(runtime.cfg.config_audit_log_path),
                "config_snapshot_dir": str(runtime.cfg.config_snapshot_dir),
            }
        )
    return {
        "status": "ok",
        "config": _public_runtime_paths_response_config(payload),
    }


def update_runtime_paths_payload(
    *,
    patch: dict[str, Any],
    runtime: Any | None = None,
    project_root: Path | None = None,
    sync_runtime: Any | None = None,
) -> dict[str, Any]:
    service = RuntimePathConfigService(project_root=project_root or PROJECT_ROOT)
    payload = service.apply_patch(patch)
    if runtime is not None and sync_runtime is not None:
        sync_runtime(runtime, payload["config"])
        payload["config"].update(
            {
                "training_output_dir": str(runtime.cfg.training_output_dir),
                "artifact_log_dir": str(runtime.cfg.artifact_log_dir),
                "config_audit_log_path": str(runtime.cfg.config_audit_log_path),
                "config_snapshot_dir": str(runtime.cfg.config_snapshot_dir),
            }
        )
    return {
        "status": "ok",
        "updated": payload["updated"],
        "config": _public_runtime_paths_response_config(payload["config"]),
    }


def get_evolution_config_payload(
    *, project_root: Path | None = None, live_config: Any = None
) -> dict[str, Any]:
    service = EvolutionConfigService(
        project_root=project_root or PROJECT_ROOT, live_config=live_config or config
    )
    return {
        "status": "ok",
        "config": build_public_evolution_config_payload(service.get_masked_payload()),
    }


def invalid_evolution_config_patch_keys(patch: dict[str, Any] | None) -> list[str]:
    payload = dict(patch or {})
    invalid_keys: list[str] = []
    for key in ("llm_fast_model", "llm_deep_model", "llm_api_base", "llm_api_key"):
        if key in payload:
            invalid_keys.append(key)
    if "llm" in payload:
        invalid_keys.append("llm")
    return invalid_keys


def validate_evolution_config_patch(patch: dict[str, Any]) -> None:
    invalid_keys = invalid_evolution_config_patch_keys(patch)
    if invalid_keys:
        raise ConfigSurfaceValidationError(
            "evolution_config 不接受 llm 相关 patch；请改用 /api/control_plane 管理 provider / model / api_key 绑定",
            invalid_keys=tuple(invalid_keys),
        )


def _as_mapping(value: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigSurfaceValidationError(
            f"{path} must be an object",
            invalid_keys=(path,),
        )
    return {str(key): item for key, item in value.items()}


def _raise_invalid_public_control_plane_keys(
    *,
    path: str,
    invalid_keys: list[str],
    message: str,
) -> None:
    if invalid_keys:
        raise ConfigSurfaceValidationError(
            message,
            invalid_keys=tuple(invalid_keys),
        )


def validate_control_plane_patch(patch: dict[str, Any]) -> None:
    payload = dict(patch or {})
    invalid_root_keys = sorted(set(payload.keys()) - {"llm", "data"})
    _raise_invalid_public_control_plane_keys(
        path="control_plane",
        invalid_keys=invalid_root_keys,
        message="control_plane public API only accepts llm and data roots; config metadata and runtime-only fields are internal details",
    )

    llm_payload = payload.get("llm")
    if llm_payload is not None:
        llm = _as_mapping(llm_payload, path="llm")
        invalid_llm_keys = [f"llm.{key}" for key in sorted(set(llm.keys()) - {"providers", "models", "bindings"})]
        _raise_invalid_public_control_plane_keys(
            path="llm",
            invalid_keys=invalid_llm_keys,
            message="control_plane.llm only accepts providers, models, and bindings",
        )

        providers_payload = llm.get("providers")
        if providers_payload is not None:
            providers = _as_mapping(providers_payload, path="llm.providers")
            for provider_name, provider_payload in providers.items():
                provider_path = f"llm.providers.{provider_name}"
                provider = _as_mapping(provider_payload, path=provider_path)
                invalid_provider_keys = [
                    f"{provider_path}.{key}"
                    for key in sorted(set(provider.keys()) - {"api_base", "api_key"})
                ]
                _raise_invalid_public_control_plane_keys(
                    path=provider_path,
                    invalid_keys=invalid_provider_keys,
                    message="control_plane.llm.providers entries only accept api_base and api_key",
                )
                for key in ("api_base", "api_key"):
                    if key in provider and not isinstance(provider[key], str):
                        raise ConfigSurfaceValidationError(
                            f"{provider_path}.{key} must be a string",
                            invalid_keys=(f"{provider_path}.{key}",),
                        )

        models_payload = llm.get("models")
        if models_payload is not None:
            models = _as_mapping(models_payload, path="llm.models")
            for profile_name, profile_payload in models.items():
                profile_path = f"llm.models.{profile_name}"
                profile = _as_mapping(profile_payload, path=profile_path)
                invalid_profile_keys = [
                    f"{profile_path}.{key}"
                    for key in sorted(set(profile.keys()) - {"provider", "model"})
                ]
                _raise_invalid_public_control_plane_keys(
                    path=profile_path,
                    invalid_keys=invalid_profile_keys,
                    message="control_plane.llm.models entries only accept provider and model",
                )
                for key in ("provider", "model"):
                    if key in profile and not isinstance(profile[key], str):
                        raise ConfigSurfaceValidationError(
                            f"{profile_path}.{key} must be a string",
                            invalid_keys=(f"{profile_path}.{key}",),
                        )

        bindings_payload = llm.get("bindings")
        if bindings_payload is not None:
            bindings = _as_mapping(bindings_payload, path="llm.bindings")
            for binding_name, binding_value in bindings.items():
                if not isinstance(binding_value, str):
                    raise ConfigSurfaceValidationError(
                        f"llm.bindings.{binding_name} must be a string",
                        invalid_keys=(f"llm.bindings.{binding_name}",),
                    )

    data_payload = payload.get("data")
    if data_payload is not None:
        data = _as_mapping(data_payload, path="data")
        invalid_data_keys = [f"data.{key}" for key in sorted(set(data.keys()) - {"runtime_policy"})]
        _raise_invalid_public_control_plane_keys(
            path="data",
            invalid_keys=invalid_data_keys,
            message="control_plane.data only accepts runtime_policy",
        )

        runtime_policy_payload = data.get("runtime_policy")
        if runtime_policy_payload is not None:
            runtime_policy = _as_mapping(
                runtime_policy_payload,
                path="data.runtime_policy",
            )
            invalid_policy_keys = [
                f"data.runtime_policy.{key}"
                for key in sorted(
                    set(runtime_policy.keys())
                    - {"allow_online_fallback", "allow_capital_flow_sync"}
                )
            ]
            _raise_invalid_public_control_plane_keys(
                path="data.runtime_policy",
                invalid_keys=invalid_policy_keys,
                message="control_plane.data.runtime_policy only accepts allow_online_fallback and allow_capital_flow_sync",
            )
            for key, value in runtime_policy.items():
                if not isinstance(value, bool):
                    raise ConfigSurfaceValidationError(
                        f"data.runtime_policy.{key} must be a boolean",
                        invalid_keys=(f"data.runtime_policy.{key}",),
                    )


def update_evolution_config_payload(
    *,
    patch: dict[str, Any],
    project_root: Path | None = None,
    live_config: Any = None,
    source: str = "commander",
) -> dict[str, Any]:
    invalid_keys = invalid_evolution_config_patch_keys(patch)
    if invalid_keys:
        raise ValueError(
            "evolution_config 不接受 llm 相关 patch；请改用 /api/control_plane 管理 provider / model / api_key 绑定"
        )
    service = EvolutionConfigService(
        project_root=project_root or PROJECT_ROOT, live_config=live_config or config
    )
    payload = service.apply_patch(patch, source=source)
    return {
        "status": "ok",
        "updated": payload["updated"],
        "config": build_public_evolution_config_payload(payload["config"]),
        "restart_required": False,
    }


def get_control_plane_payload(*, project_root: Path | None = None) -> dict[str, Any]:
    service = ControlPlaneConfigService(project_root=project_root or PROJECT_ROOT)
    return {
        "status": "ok",
        "config": service.get_masked_payload(),
        "restart_required": False,
        "config_path": str(service.config_path),
        "local_override_path": str(service.local_override_path),
        "local_override_exists": service.local_override_path.exists(),
        "audit_log_path": str(service.audit_log_path),
        "audit_log_exists": service.audit_log_path.exists(),
        "snapshot_dir": str(service.snapshot_dir),
        "llm_resolution": {
            "fast": get_default_llm_status(
                "fast", project_root=project_root or PROJECT_ROOT
            ),
            "deep": get_default_llm_status(
                "deep", project_root=project_root or PROJECT_ROOT
            ),
        },
    }


def update_control_plane_payload(
    *,
    patch: dict[str, Any],
    project_root: Path | None = None,
    source: str = "commander",
) -> dict[str, Any]:
    service = ControlPlaneConfigService(project_root=project_root or PROJECT_ROOT)
    payload = service.apply_patch(patch, source=source)
    return {
        "status": "ok",
        "updated": payload["updated"],
        "config": payload["config"],
        "restart_required": True,
    }


def build_config_surface_read_specs(
    *,
    project_root: Path | None = None,
    live_config: Any = None,
) -> dict[str, ConfigSurfaceReadSpec]:
    resolved_project_root = Path(project_root or PROJECT_ROOT)
    resolved_live_config = live_config or config
    return {
        "agent_prompts": ConfigSurfaceReadSpec(
            runtime_fetch=lambda runtime: runtime.list_agent_prompts(),
            fallback_fetch=lambda: list_agent_prompts_payload(
                project_root=resolved_project_root,
            ),
        ),
        "runtime_paths": ConfigSurfaceReadSpec(
            runtime_fetch=lambda runtime: runtime.get_runtime_paths(),
            fallback_fetch=lambda: get_runtime_paths_payload(
                None,
                project_root=resolved_project_root,
            ),
        ),
        "evolution_config": ConfigSurfaceReadSpec(
            runtime_fetch=lambda runtime: runtime.get_evolution_config(),
            fallback_fetch=lambda: get_evolution_config_payload(
                project_root=resolved_project_root,
                live_config=resolved_live_config,
            ),
        ),
        "control_plane": ConfigSurfaceReadSpec(
            runtime_fetch=lambda runtime: build_public_control_plane_payload(
                runtime.get_control_plane()
            ),
            fallback_fetch=lambda: build_public_control_plane_payload(
                get_control_plane_payload(project_root=resolved_project_root)
            ),
        ),
    }


def build_config_surface_update_specs(
    *,
    project_root: Path | None = None,
    live_config: Any = None,
) -> dict[str, ConfigSurfaceUpdateSpec]:
    resolved_project_root = Path(project_root or PROJECT_ROOT)
    resolved_live_config = live_config or config
    return {
        "agent_prompts": ConfigSurfaceUpdateSpec(
            runtime_update=lambda runtime, payload: runtime.update_agent_prompt(
                agent_name=payload["name"],
                system_prompt=payload["system_prompt"],
            ),
            fallback_update=lambda payload: update_agent_prompt_payload(
                agent_name=payload["name"],
                system_prompt=payload["system_prompt"],
                project_root=resolved_project_root,
            ),
            error_label="Role/agent prompt update",
            request_payload=lambda payload: payload["data"],
        ),
        "runtime_paths": ConfigSurfaceUpdateSpec(
            runtime_update=lambda runtime, payload: runtime.update_runtime_paths(
                payload, confirm=True
            ),
            fallback_update=lambda payload: update_runtime_paths_payload(
                patch=payload,
                runtime=None,
                project_root=resolved_project_root,
                sync_runtime=None,
            ),
            error_label="Runtime paths update",
            request_payload=lambda payload: payload,
            validate_payload=validate_runtime_paths_patch,
        ),
        "evolution_config": ConfigSurfaceUpdateSpec(
            runtime_update=lambda runtime, payload: runtime.update_evolution_config(
                payload,
                confirm=True,
            ),
            fallback_update=lambda payload: update_evolution_config_payload(
                patch=payload,
                project_root=resolved_project_root,
                live_config=resolved_live_config,
            ),
            error_label="Evolution config update",
            request_payload=lambda payload: payload,
            validate_payload=validate_evolution_config_patch,
        ),
        "control_plane": ConfigSurfaceUpdateSpec(
            runtime_update=lambda runtime, payload: runtime.update_control_plane(
                payload,
                confirm=True,
            ),
            fallback_update=lambda payload: update_control_plane_payload(
                patch=payload,
                project_root=resolved_project_root,
            ),
            error_label="Control plane update",
            request_payload=lambda payload: payload,
            validate_payload=validate_control_plane_patch,
        ),
    }


def build_config_surface_route_specs() -> tuple[ConfigSurfaceRouteSpec, ...]:
    return (
        ConfigSurfaceRouteSpec(
            surface="agent_prompts",
            path="/api/agent_prompts",
            update_request_kind="agent_prompt",
        ),
        ConfigSurfaceRouteSpec(
            surface="runtime_paths",
            path="/api/runtime_paths",
            update_request_kind="patch_object",
        ),
        ConfigSurfaceRouteSpec(
            surface="evolution_config",
            path="/api/evolution_config",
            update_request_kind="patch_object",
        ),
        ConfigSurfaceRouteSpec(
            surface="control_plane",
            path="/api/control_plane",
            update_request_kind="patch_object",
        ),
    )
