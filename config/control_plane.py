from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

logger = logging.getLogger(__name__)

_ENV_PLACEHOLDER = re.compile(r"\$\{ENV:([A-Z0-9_]+)(?::-(.*?))?\}")
_SECRET_KEYS = {"api_key"}


@dataclass(frozen=True)
class ResolvedLLMConfig:
    component_key: str
    model: str
    api_key: str
    api_base: str
    binding_name: str = ""
    profile_name: str = ""
    provider_name: str = ""
    source: str = "fallback"
    issue: str = ""


def _project_root(project_root: str | Path | None = None) -> Path:
    if project_root is not None:
        return Path(project_root)
    from config import PROJECT_ROOT

    return Path(PROJECT_ROOT)


def _expand_env_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _expand_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_placeholders(item) for item in value]
    if isinstance(value, str):
        def _replace(match: re.Match[str]) -> str:
            env_name = match.group(1)
            fallback = match.group(2) if match.group(2) is not None else ""
            return os.environ.get(env_name, fallback)

        return _ENV_PLACEHOLDER.sub(_replace, value)
    return value


def _read_yaml_dict(path: Path) -> dict[str, Any]:
    if yaml is None or not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return {}
    return _expand_env_placeholders(loaded)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(dict(base[key]), value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _sanitize_name(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower())
    return lowered.strip("_") or "component"


def _default_control_plane_payload() -> dict[str, Any]:
    from config import agent_config_registry, config

    payload: dict[str, Any] = {
        "llm": {
            "providers": {
                "default_provider": {
                    "api_base": str(getattr(config, "llm_api_base", "") or ""),
                    "api_key": str(getattr(config, "llm_api_key", "") or ""),
                }
            },
            "models": {
                "default_fast": {
                    "provider": "default_provider",
                    "model": str(getattr(config, "llm_fast_model", "") or ""),
                },
                "default_deep": {
                    "provider": "default_provider",
                    "model": str(getattr(config, "llm_deep_model", "") or ""),
                },
            },
            "bindings": {
                "defaults.fast": "default_fast",
                "defaults.deep": "default_deep",
                "controller.main": "default_fast",
                "meeting.selection.fast": "default_fast",
                "meeting.selection.deep": "default_deep",
                "meeting.selection.debate.bull": "default_fast",
                "meeting.selection.debate.bear": "default_fast",
                "meeting.selection.debate.judge": "default_deep",
                "meeting.review.fast": "default_fast",
                "meeting.review.deep": "default_deep",
                "meeting.review.risk.aggressive": "default_fast",
                "meeting.review.risk.conservative": "default_fast",
                "meeting.review.risk.neutral": "default_fast",
                "meeting.review.risk.judge": "default_deep",
                "optimizer.loss_analysis": "default_deep",
                "commander.brain": "default_fast",
            },
        },
        "data": {
            "runtime_policy": {
                "allow_online_fallback": False,
                "allow_capital_flow_sync": False,
            }
        },
    }

    bindings = payload["llm"]["bindings"]
    models = payload["llm"]["models"]
    for agent_name, cfg in agent_config_registry.all().items():
        raw_model = str(cfg.get("llm_model", "") or "").strip()
        binding_key = f"agent.{agent_name}"
        lowered = raw_model.lower()
        if lowered in {"", "fast"}:
            bindings[binding_key] = "default_fast"
            continue
        if lowered == "deep":
            bindings[binding_key] = "default_deep"
            continue
        profile_name = f"agent_{_sanitize_name(agent_name)}"
        models[profile_name] = {
            "provider": "default_provider",
            "model": raw_model,
        }
        bindings[binding_key] = profile_name

    return payload


def get_control_plane_paths(project_root: str | Path | None = None) -> list[Path]:
    root = _project_root(project_root)
    config_dir = root / "config"
    primary = config_dir / "control_plane.yaml"
    local_override = config_dir / "control_plane.local.yaml"
    paths: list[Path] = []
    if primary.exists():
        paths.append(primary)
    if local_override.exists():
        paths.append(local_override)
    extra_path = os.environ.get("INVEST_CONTROL_PLANE_PATH", "").strip()
    if extra_path:
        candidate = Path(extra_path)
        if candidate.exists() and candidate not in paths:
            paths.append(candidate)
    return paths


@lru_cache(maxsize=8)
def load_control_plane(project_root: str | Path | None = None) -> dict[str, Any]:
    payload = _default_control_plane_payload()
    for path in get_control_plane_paths(project_root):
        payload = _deep_merge(payload, _read_yaml_dict(path))
    return payload


def clear_control_plane_cache() -> None:
    load_control_plane.cache_clear()


class ControlPlaneResolver:
    def __init__(self, payload: dict[str, Any]):
        self.payload = copy.deepcopy(payload)
        self.llm = dict(self.payload.get("llm") or {})
        self.providers = dict(self.llm.get("providers") or {})
        self.models = dict(self.llm.get("models") or {})
        self.bindings = dict(self.llm.get("bindings") or {})
        self.data = dict(self.payload.get("data") or {})

    @classmethod
    def load(cls, project_root: str | Path | None = None) -> "ControlPlaneResolver":
        return cls(load_control_plane(project_root))

    def runtime_data_policy(self) -> dict[str, Any]:
        policy = dict((self.data.get("runtime_policy") or {}))
        policy.setdefault("allow_online_fallback", False)
        policy.setdefault("allow_capital_flow_sync", False)
        return policy

    def resolve_llm(
        self,
        component_key: str,
        *,
        fallback_model: str = "",
        fallback_api_key: str = "",
        fallback_api_base: str = "",
    ) -> ResolvedLLMConfig:
        binding_name = str(self.bindings.get(component_key, "") or "").strip()
        if binding_name:
            profile = dict(self.models.get(binding_name) or {})
            provider_name = str(profile.get("provider", "") or "").strip()
            provider = dict(self.providers.get(provider_name) or {})
            model = str(profile.get("model", "") or "").strip() or str(fallback_model or "")
            api_key = str(provider.get("api_key", "") or "").strip() or str(fallback_api_key or "")
            api_base = str(provider.get("api_base", "") or "").strip() or str(fallback_api_base or "")
            if model:
                return ResolvedLLMConfig(
                    component_key=component_key,
                    model=model,
                    api_key=api_key,
                    api_base=api_base,
                    binding_name=binding_name,
                    profile_name=binding_name,
                    provider_name=provider_name,
                    source="control_plane",
                    issue=_build_llm_resolution_issue(
                        component_key=component_key,
                        model=model,
                        api_key=api_key,
                        binding_name=binding_name,
                        provider_name=provider_name,
                        source="control_plane",
                    ),
                )
        return ResolvedLLMConfig(
            component_key=component_key,
            model=str(fallback_model or ""),
            api_key=str(fallback_api_key or ""),
            api_base=str(fallback_api_base or ""),
            source="fallback",
            issue=_build_llm_resolution_issue(
                component_key=component_key,
                model=str(fallback_model or ""),
                api_key=str(fallback_api_key or ""),
                binding_name="",
                provider_name="",
                source="fallback",
            ),
        )


def resolve_component_llm(
    component_key: str,
    *,
    fallback_model: str = "",
    fallback_api_key: str = "",
    fallback_api_base: str = "",
    project_root: str | Path | None = None,
) -> ResolvedLLMConfig:
    resolver = ControlPlaneResolver.load(project_root)
    return resolver.resolve_llm(
        component_key,
        fallback_model=fallback_model,
        fallback_api_key=fallback_api_key,
        fallback_api_base=fallback_api_base,
    )


def build_component_llm_caller(
    component_key: str,
    *,
    fallback_model: str = "",
    fallback_api_key: str = "",
    fallback_api_base: str = "",
    timeout: int | None = None,
    max_retries: int | None = None,
    dry_run: bool = False,
    project_root: str | Path | None = None,
):
    from invest.shared import LLMCaller

    resolved = resolve_component_llm(
        component_key,
        fallback_model=fallback_model,
        fallback_api_key=fallback_api_key,
        fallback_api_base=fallback_api_base,
        project_root=project_root,
    )
    return LLMCaller(
        model=str(resolved.model or ""),
        api_key=str(resolved.api_key or ""),
        api_base=str(resolved.api_base or ""),
        timeout=int(timeout or 60),
        max_retries=int(max_retries or 2),
        dry_run=dry_run,
        unavailable_message=str(resolved.issue or ""),
    )


def get_runtime_data_policy(project_root: str | Path | None = None) -> dict[str, Any]:
    return ControlPlaneResolver.load(project_root).runtime_data_policy()


def resolve_default_llm(kind: str = "fast", *, project_root: str | Path | None = None) -> ResolvedLLMConfig:
    from config import config

    normalized = str(kind or "fast").strip().lower()
    if normalized not in {"fast", "deep"}:
        raise ValueError("kind must be fast or deep")
    component_key = f"defaults.{normalized}"
    fallback_model = str(getattr(config, "llm_fast_model" if normalized == "fast" else "llm_deep_model", "") or "")
    return resolve_component_llm(
        component_key,
        fallback_model=fallback_model,
        fallback_api_key=str(getattr(config, "llm_api_key", "") or ""),
        fallback_api_base=str(getattr(config, "llm_api_base", "") or ""),
        project_root=project_root,
    )


def build_default_llm_caller(
    kind: str = "fast",
    *,
    timeout: int | None = None,
    max_retries: int | None = None,
    dry_run: bool = False,
    project_root: str | Path | None = None,
):
    resolved = resolve_default_llm(kind, project_root=project_root)
    from invest.shared import LLMCaller

    return LLMCaller(
        model=str(resolved.model or ""),
        api_key=str(resolved.api_key or ""),
        api_base=str(resolved.api_base or ""),
        timeout=int(timeout or 60),
        max_retries=int(max_retries or 2),
        dry_run=dry_run,
        unavailable_message=str(resolved.issue or ""),
    )


def _build_llm_resolution_issue(
    *,
    component_key: str,
    model: str,
    api_key: str,
    binding_name: str,
    provider_name: str,
    source: str,
) -> str:
    if str(api_key or "").strip():
        return ""

    component_label = str(component_key or "").strip() or "defaults.fast"
    model_label = str(model or "").strip()
    binding_label = str(binding_name or "").strip() or component_label
    provider_label = str(provider_name or "").strip()

    if source == "control_plane" and provider_label:
        return (
            f"control_plane binding {binding_label} resolved model {model_label or '<empty-model>'} "
            f"via provider {provider_label}, but llm.providers.{provider_label}.api_key is empty; "
            "set it in config/control_plane.local.yaml or POST /api/control_plane"
        )
    if model_label:
        return (
            f"LLM binding {binding_label} resolved model {model_label}, but provider api_key is empty; "
            "configure the system provider api_key in config/control_plane.local.yaml or POST /api/control_plane"
        )
    return (
        f"LLM binding {binding_label} is not fully configured; "
        "configure the model binding and provider api_key in config/control_plane.yaml and config/control_plane.local.yaml"
    )


def llm_resolution_status(resolved: ResolvedLLMConfig) -> dict[str, Any]:
    return {
        "component_key": str(resolved.component_key or ""),
        "binding_name": str(resolved.binding_name or ""),
        "profile_name": str(resolved.profile_name or ""),
        "provider_name": str(resolved.provider_name or ""),
        "source": str(resolved.source or ""),
        "model": str(resolved.model or ""),
        "api_base": str(resolved.api_base or ""),
        "api_key_configured": bool(str(resolved.api_key or "").strip()),
        "issue": str(resolved.issue or ""),
    }


def get_default_llm_status(kind: str = "fast", *, project_root: str | Path | None = None) -> dict[str, Any]:
    normalized = str(kind or "fast").strip().lower()
    if normalized not in {"fast", "deep"}:
        raise ValueError("kind must be fast or deep")
    resolved = resolve_default_llm(normalized, project_root=project_root)
    payload = llm_resolution_status(resolved)
    payload["kind"] = normalized
    return payload


def _mask_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            if key in _SECRET_KEYS:
                raw = str(item or "")
                masked[key] = (("*" * max(0, len(raw) - 4)) + raw[-4:]) if raw else ""
            else:
                masked[key] = _mask_secrets(item)
        return masked
    if isinstance(value, list):
        return [_mask_secrets(item) for item in value]
    return value


def _split_secret_tree(value: Any) -> tuple[Any, Any]:
    if isinstance(value, dict):
        public: dict[str, Any] = {}
        local: dict[str, Any] = {}
        for key, item in value.items():
            if key in _SECRET_KEYS:
                local[key] = item
                continue
            public_value, local_value = _split_secret_tree(item)
            if public_value not in ({}, [], None, ""):
                public[key] = public_value
            elif isinstance(item, dict):
                public[key] = {}
            if local_value not in ({}, [], None, ""):
                local[key] = local_value
        return public, local
    if isinstance(value, list):
        public_items = []
        local_items = []
        for item in value:
            public_value, local_value = _split_secret_tree(item)
            public_items.append(public_value)
            local_items.append(local_value)
        has_local = any(item not in ({}, [], None, "") for item in local_items)
        return public_items, (local_items if has_local else [])
    return value, None


def _collect_changed_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    changed: list[str] = []
    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(set(before) | set(after))
        for key in keys:
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            changed.extend(_collect_changed_paths(before.get(key), after.get(key), child_prefix))
        return changed
    if before != after:
        return [prefix or "<root>"]
    return []


class ControlPlaneConfigService:
    def __init__(
        self,
        project_root: str | Path | None = None,
        audit_log_path: str | Path | None = None,
        snapshot_dir: str | Path | None = None,
    ):
        self.project_root = _project_root(project_root)
        self._audit_log_path = Path(audit_log_path) if audit_log_path else None
        self._snapshot_dir = Path(snapshot_dir) if snapshot_dir else None

    @property
    def config_path(self) -> Path:
        return self.project_root / "config" / "control_plane.yaml"

    @property
    def local_override_path(self) -> Path:
        return self.project_root / "config" / "control_plane.local.yaml"

    @property
    def audit_log_path(self) -> Path:
        return self._audit_log_path or (self.project_root / "runtime" / "state" / "control_plane_changes.jsonl")

    @property
    def snapshot_dir(self) -> Path:
        return self._snapshot_dir or (self.project_root / "runtime" / "state" / "control_plane_snapshots")

    def get_payload(self) -> dict[str, Any]:
        clear_control_plane_cache()
        return load_control_plane(self.project_root)

    def get_masked_payload(self) -> dict[str, Any]:
        return _mask_secrets(self.get_payload())

    def _validate(self, payload: dict[str, Any]) -> None:
        llm = dict(payload.get("llm") or {})
        providers = dict(llm.get("providers") or {})
        models = dict(llm.get("models") or {})
        bindings = dict(llm.get("bindings") or {})
        for name, profile in models.items():
            if not isinstance(profile, dict):
                raise ValueError(f"llm.models.{name} must be an object")
            provider = str(profile.get("provider", "") or "").strip()
            if provider and provider not in providers:
                raise ValueError(f"llm.models.{name}.provider references unknown provider {provider}")
            if not str(profile.get("model", "") or "").strip():
                raise ValueError(f"llm.models.{name}.model is required")
        for binding_key, binding_value in bindings.items():
            if str(binding_value or "").strip() and str(binding_value) not in models:
                raise ValueError(f"llm.bindings.{binding_key} references unknown model profile {binding_value}")
        runtime_policy = dict((payload.get("data") or {}).get("runtime_policy") or {})
        for key in ("allow_online_fallback", "allow_capital_flow_sync"):
            value = runtime_policy.get(key)
            if value not in (None, True, False):
                raise ValueError(f"data.runtime_policy.{key} must be boolean")

    def apply_patch(self, patch: dict[str, Any], source: str = "unknown") -> dict[str, Any]:
        if yaml is None:
            raise RuntimeError("PyYAML 未安装，无法写入 control plane 配置")
        current = self.get_payload()
        merged = _deep_merge(copy.deepcopy(current), copy.deepcopy(patch or {}))
        self._validate(merged)
        public_payload, local_payload = _split_secret_tree(merged)
        changed = _collect_changed_paths(current, merged)

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        local_backup = None
        if self.config_path.exists():
            backup = self.config_path.with_suffix('.yaml.bak')
            shutil.copy2(self.config_path, backup)
        if self.local_override_path.exists():
            local_backup = self.local_override_path.with_suffix('.yaml.bak')
            shutil.copy2(self.local_override_path, local_backup)
        try:
            self.config_path.write_text(yaml.safe_dump(public_payload, allow_unicode=True, sort_keys=True), encoding='utf-8')
            local_dict = local_payload if isinstance(local_payload, dict) else {}
            if local_dict:
                self.local_override_path.write_text(yaml.safe_dump(local_dict, allow_unicode=True, sort_keys=True), encoding='utf-8')
            else:
                self.local_override_path.unlink(missing_ok=True)
            self._write_snapshot(merged)
            self._append_audit_log(source=source, updated=changed)
            clear_control_plane_cache()
        except Exception:
            if backup and backup.exists():
                shutil.copy2(backup, self.config_path)
            if local_backup and local_backup.exists():
                shutil.copy2(local_backup, self.local_override_path)
            raise
        finally:
            if backup and backup.exists():
                backup.unlink(missing_ok=True)
            if local_backup and local_backup.exists():
                local_backup.unlink(missing_ok=True)

        return {
            "updated": changed,
            "config": self.get_masked_payload(),
            "restart_required": True,
        }

    def _write_snapshot(self, payload: dict[str, Any]) -> None:
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = self.snapshot_dir / f'control_plane_{ts}.json'
        path.write_text(
            json.dumps(_mask_secrets(payload), ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def _append_audit_log(self, *, source: str, updated: list[str]) -> None:
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(),
            "source": source,
            "updated": list(updated),
        }
        with self.audit_log_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def _binding_name(payload: dict[str, Any], component_key: str) -> str:
    llm = dict(payload.get("llm") or {})
    bindings = dict(llm.get("bindings") or {})
    return str(bindings.get(component_key, "") or "").strip()



def _profile_dict(payload: dict[str, Any], profile_name: str) -> dict[str, Any]:
    llm = dict(payload.get("llm") or {})
    models = dict(llm.get("models") or {})
    return dict(models.get(profile_name) or {})



def _provider_dict(payload: dict[str, Any], provider_name: str) -> dict[str, Any]:
    llm = dict(payload.get("llm") or {})
    providers = dict(llm.get("providers") or {})
    return dict(providers.get(provider_name) or {})
