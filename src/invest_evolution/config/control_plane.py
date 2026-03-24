"""Control-plane resolution and runtime config services."""

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

from invest_evolution.config import (
    DEPRECATED_MANAGER_RUNTIME_FLAGS,
    EFFECTIVE_RUNTIME_MODE,
    EvolutionConfig,
    PROJECT_ROOT,
    RUNTIME_CONTRACT_VERSION,
    config,
    get_config_layer_paths,
    get_runtime_override_path,
    normalize_manager_active_ids,
    normalize_manager_budget_weights,
)

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


logger = logging.getLogger(__name__)

class EvolutionConfigService:
    """Unified service for reading, validating, persisting and auditing evolution config."""

    SECRET_KEYS = {"web_api_token"}

    EDITABLE_KEYS = {
        "llm_timeout",
        "llm_max_retries",
        "enable_debate",
        "max_debate_rounds",
        "max_risk_discuss_rounds",
        "data_source",
        "max_stocks",
        "simulation_days",
        "min_history_days",
        "initial_capital",
        "max_positions",
        "position_size_pct",
        "index_codes",
        "default_manager_id",
        "default_manager_config_ref",
        "allocator_enabled",
        "allocator_top_n",
        "manager_arch_enabled",
        "manager_shadow_mode",
        "manager_allocator_enabled",
        "portfolio_assembly_enabled",
        "dual_review_enabled",
        "manager_persistence_enabled",
        "manager_active_ids",
        "manager_budget_weights",
        "governance_enabled",
        "governance_mode",
        "governance_allowed_manager_ids",
        "governance_cooldown_cycles",
        "governance_min_confidence",
        "governance_hysteresis_margin",
        "governance_agent_override_enabled",
        "governance_agent_override_max_gap",
        "governance_policy",
        "stop_on_freeze",
        "web_rate_limit_enabled",
        "web_rate_limit_window_sec",
        "web_rate_limit_read_max",
        "web_rate_limit_write_max",
        "web_rate_limit_heavy_max",
    }

    def __init__(
        self,
        project_root: Path | None = None,
        live_config: EvolutionConfig | None = None,
        config_path: Path | None = None,
        audit_log_path: Path | None = None,
        snapshot_dir: Path | None = None,
    ):
        self.project_root = Path(project_root or PROJECT_ROOT)
        self.live_config = live_config or config
        self._config_path = Path(config_path) if config_path else None
        self._audit_log_path = Path(audit_log_path) if audit_log_path else None
        self._snapshot_dir = Path(snapshot_dir) if snapshot_dir else None

    @property
    def config_path(self) -> Path:
        if self._config_path is not None:
            return self._config_path
        config_dir = self.project_root / "config"
        canonical = config_dir / "evolution.yaml.example"
        materialized = config_dir / "evolution.yaml"
        if canonical.exists():
            return canonical
        if materialized.exists():
            return materialized
        return canonical

    @property
    def audit_log_path(self) -> Path:
        return self._audit_log_path or (self.project_root / "runtime" / "state" / "config_changes.jsonl")

    @property
    def snapshot_dir(self) -> Path:
        return self._snapshot_dir or (self.project_root / "runtime" / "state" / "config_snapshots")

    @property
    def local_override_path(self) -> Path:
        return self.config_path.parent / "evolution.local.yaml"

    @property
    def runtime_override_path(self) -> Path:
        return get_runtime_override_path(self.config_path)

    def _read_yaml_dict(self, path: Path) -> dict[str, Any]:
        if yaml is None or not path.exists():
            return {}
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return payload if isinstance(payload, dict) else {}


    def _web_auth_secret_source(self) -> str:
        if str(os.environ.get("WEB_API_TOKEN", "")).strip():
            return "env"
        local_payload = self._read_yaml_dict(self.local_override_path)
        if str(local_payload.get("web_api_token", "")).strip():
            return "local_yaml"
        primary_payload = self._read_yaml_dict(self.config_path)
        if str(primary_payload.get("web_api_token", "")).strip():
            return "yaml"
        if str(getattr(self.live_config, "web_api_token", "")).strip():
            return "runtime"
        return "unset"

    def get_masked_payload(self) -> dict[str, Any]:
        cfg = self.live_config
        web_api_token_masked = ""
        if getattr(cfg, "web_api_token", ""):
            v = str(getattr(cfg, "web_api_token"))
            web_api_token_masked = ("*" * max(0, len(v) - 4)) + v[-4:]
        return {
            "config_path": str(self.config_path),
            "config_file_exists": self.config_path.exists(),
            "runtime_override_path": str(self.runtime_override_path),
            "runtime_override_exists": self.runtime_override_path.exists(),
            "web_api_token_masked": web_api_token_masked,
            "web_api_require_auth": bool(getattr(cfg, "web_api_require_auth", False)),
            "web_api_public_read_enabled": bool(getattr(cfg, "web_api_public_read_enabled", False)),
            "web_rate_limit_enabled": bool(getattr(cfg, "web_rate_limit_enabled", True)),
            "web_rate_limit_window_sec": int(getattr(cfg, "web_rate_limit_window_sec", 60)),
            "web_rate_limit_read_max": int(getattr(cfg, "web_rate_limit_read_max", 120)),
            "web_rate_limit_write_max": int(getattr(cfg, "web_rate_limit_write_max", 20)),
            "web_rate_limit_heavy_max": int(getattr(cfg, "web_rate_limit_heavy_max", 5)),
            "web_status_training_lab_limit": int(getattr(cfg, "web_status_training_lab_limit", 3)),
            "web_status_events_summary_limit": int(getattr(cfg, "web_status_events_summary_limit", 20)),
            "web_runtime_async_timeout_sec": int(getattr(cfg, "web_runtime_async_timeout_sec", 600)),
            "llm_timeout": cfg.llm_timeout,
            "llm_max_retries": cfg.llm_max_retries,
            "enable_debate": cfg.enable_debate,
            "max_debate_rounds": cfg.max_debate_rounds,
            "max_risk_discuss_rounds": cfg.max_risk_discuss_rounds,
            "data_source": cfg.data_source,
            "max_stocks": cfg.max_stocks,
            "simulation_days": cfg.simulation_days,
            "min_history_days": cfg.min_history_days,
            "initial_capital": cfg.initial_capital,
            "max_positions": cfg.max_positions,
            "position_size_pct": cfg.position_size_pct,
            "index_codes": list(cfg.index_codes or []),
            "default_manager_id": cfg.default_manager_id,
            "default_manager_config_ref": cfg.default_manager_config_ref,
            "allocator_enabled": cfg.allocator_enabled,
            "allocator_top_n": cfg.allocator_top_n,
            "manager_arch_enabled": cfg.manager_arch_enabled,
            "manager_shadow_mode": cfg.manager_shadow_mode,
            "manager_allocator_enabled": cfg.manager_allocator_enabled,
            "portfolio_assembly_enabled": cfg.portfolio_assembly_enabled,
            "dual_review_enabled": cfg.dual_review_enabled,
            "manager_persistence_enabled": cfg.manager_persistence_enabled,
            "manager_active_ids": list(cfg.manager_active_ids or []),
            "manager_budget_weights": dict(cfg.manager_budget_weights or {}),
            "governance_enabled": cfg.governance_enabled,
            "governance_mode": cfg.governance_mode,
            "governance_allowed_manager_ids": list(cfg.governance_allowed_manager_ids or []),
            "governance_cooldown_cycles": cfg.governance_cooldown_cycles,
            "governance_min_confidence": cfg.governance_min_confidence,
            "governance_hysteresis_margin": cfg.governance_hysteresis_margin,
            "governance_agent_override_enabled": cfg.governance_agent_override_enabled,
            "governance_agent_override_max_gap": cfg.governance_agent_override_max_gap,
            "governance_policy": dict(cfg.governance_policy or {}),
            "stop_on_freeze": cfg.stop_on_freeze,
            "config_layers": [str(path) for path in get_config_layer_paths(self.config_path)],
            "local_override_path": str(self.local_override_path),
            "web_api_token_source": self._web_auth_secret_source(),
            "audit_log_path": str(self.audit_log_path),
            "snapshot_dir": str(self.snapshot_dir),
            "effective_runtime_mode": EFFECTIVE_RUNTIME_MODE,
            "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
            "deprecated_flags": list(DEPRECATED_MANAGER_RUNTIME_FLAGS),
        }

    def _load_runtime_override(self) -> dict[str, Any]:
        payload = self._read_yaml_dict(self.runtime_override_path)
        return {k: v for k, v in payload.items() if k in self.EDITABLE_KEYS}

    def normalize_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in patch.items() if k in self.EDITABLE_KEYS}
        if "llm_timeout" in out:
            out["llm_timeout"] = int(out["llm_timeout"])
        if "llm_max_retries" in out:
            out["llm_max_retries"] = int(out["llm_max_retries"])
        if "max_debate_rounds" in out:
            out["max_debate_rounds"] = int(out["max_debate_rounds"])
        if "max_risk_discuss_rounds" in out:
            out["max_risk_discuss_rounds"] = int(out["max_risk_discuss_rounds"])
        if "max_stocks" in out:
            out["max_stocks"] = int(out["max_stocks"])
        if "simulation_days" in out:
            out["simulation_days"] = int(out["simulation_days"])
        if "min_history_days" in out:
            out["min_history_days"] = int(out["min_history_days"])
        if "initial_capital" in out:
            out["initial_capital"] = float(out["initial_capital"])
        if "max_positions" in out:
            out["max_positions"] = int(out["max_positions"])
        if "position_size_pct" in out:
            out["position_size_pct"] = float(out["position_size_pct"])
        if "default_manager_id" in out:
            out["default_manager_id"] = str(out["default_manager_id"]).strip() or "momentum"
        if "default_manager_config_ref" in out:
            out["default_manager_config_ref"] = str(out["default_manager_config_ref"]).strip()
        if "allocator_enabled" in out:
            val = out["allocator_enabled"]
            if isinstance(val, bool):
                pass
            elif isinstance(val, str):
                low = val.strip().lower()
                if low in {"1", "true", "yes", "y", "on"}:
                    out["allocator_enabled"] = True
                elif low in {"0", "false", "no", "n", "off"}:
                    out["allocator_enabled"] = False
                else:
                    raise ValueError("allocator_enabled must be a boolean")
            else:
                raise ValueError("allocator_enabled must be a boolean")
        if "allocator_top_n" in out:
            out["allocator_top_n"] = int(out["allocator_top_n"])
        for bool_key in (
            "manager_arch_enabled",
            "manager_shadow_mode",
            "manager_allocator_enabled",
            "portfolio_assembly_enabled",
            "dual_review_enabled",
            "manager_persistence_enabled",
        ):
            if bool_key not in out:
                continue
            val = out[bool_key]
            if isinstance(val, bool):
                continue
            if isinstance(val, str):
                low = val.strip().lower()
                if low in {"1", "true", "yes", "y", "on"}:
                    out[bool_key] = True
                elif low in {"0", "false", "no", "n", "off"}:
                    out[bool_key] = False
                else:
                    raise ValueError(f"{bool_key} must be a boolean")
            else:
                raise ValueError(f"{bool_key} must be a boolean")
        if "governance_mode" in out:
            out["governance_mode"] = str(out["governance_mode"] or "rule").strip().lower() or "rule"
        if "governance_cooldown_cycles" in out:
            out["governance_cooldown_cycles"] = int(out["governance_cooldown_cycles"])
        if "governance_min_confidence" in out:
            out["governance_min_confidence"] = float(out["governance_min_confidence"])
        if "governance_hysteresis_margin" in out:
            out["governance_hysteresis_margin"] = float(out["governance_hysteresis_margin"])
        if "governance_agent_override_max_gap" in out:
            out["governance_agent_override_max_gap"] = float(out["governance_agent_override_max_gap"])
        for int_key in ("web_rate_limit_window_sec", "web_rate_limit_read_max", "web_rate_limit_write_max", "web_rate_limit_heavy_max"):
            if int_key in out:
                out[int_key] = int(out[int_key])
        for bool_key in ("governance_enabled", "governance_agent_override_enabled", "web_rate_limit_enabled"):
            if bool_key not in out:
                continue
            val = out[bool_key]
            if isinstance(val, bool):
                continue
            if isinstance(val, str):
                low = val.strip().lower()
                if low in {"1", "true", "yes", "y", "on"}:
                    out[bool_key] = True
                elif low in {"0", "false", "no", "n", "off"}:
                    out[bool_key] = False
                else:
                    raise ValueError(f"{bool_key} must be a boolean")
            else:
                raise ValueError(f"{bool_key} must be a boolean")
        if "governance_allowed_manager_ids" in out:
            value = out["governance_allowed_manager_ids"]
            if value is None:
                out["governance_allowed_manager_ids"] = []
            elif isinstance(value, str):
                out["governance_allowed_manager_ids"] = [part.strip() for part in value.split(",") if part.strip()]
            elif isinstance(value, list):
                out["governance_allowed_manager_ids"] = [str(part).strip() for part in value if str(part).strip()]
            else:
                raise ValueError("governance_allowed_manager_ids must be a list or comma-separated string")
        if "manager_active_ids" in out:
            value = out["manager_active_ids"]
            if value is not None and not isinstance(value, (str, list)):
                raise ValueError("manager_active_ids must be a list or comma-separated string")
            out["manager_active_ids"] = normalize_manager_active_ids(value)
        if "manager_budget_weights" in out:
            value = out["manager_budget_weights"]
            if value is None:
                out["manager_budget_weights"] = {}
            elif not isinstance(value, dict):
                raise ValueError("manager_budget_weights must be an object")
            else:
                out["manager_budget_weights"] = normalize_manager_budget_weights(value)
        if "governance_policy" in out:
            if out["governance_policy"] is None:
                out["governance_policy"] = {}
            if not isinstance(out["governance_policy"], dict):
                raise ValueError("governance_policy must be an object")
        if "stop_on_freeze" in out:
            val = out["stop_on_freeze"]
            if isinstance(val, bool):
                pass
            elif isinstance(val, str):
                low = val.strip().lower()
                if low in {"1", "true", "yes", "y", "on"}:
                    out["stop_on_freeze"] = True
                elif low in {"0", "false", "no", "n", "off"}:
                    out["stop_on_freeze"] = False
                else:
                    raise ValueError("stop_on_freeze must be a boolean")
            else:
                raise ValueError("stop_on_freeze must be a boolean")

        if "enable_debate" in out:
            val = out["enable_debate"]
            if isinstance(val, bool):
                pass
            elif isinstance(val, str):
                low = val.strip().lower()
                if low in {"1", "true", "yes", "y", "on"}:
                    out["enable_debate"] = True
                elif low in {"0", "false", "no", "n", "off"}:
                    out["enable_debate"] = False
                else:
                    raise ValueError("enable_debate must be a boolean")
            else:
                raise ValueError("enable_debate must be a boolean")
        if "index_codes" in out:
            if out["index_codes"] is None:
                out["index_codes"] = []
            if not isinstance(out["index_codes"], list):
                raise ValueError("index_codes must be a list")
            out["index_codes"] = [str(x) for x in out["index_codes"]]
        self._validate_patch(out)
        return out

    def _validate_patch(self, patch: dict[str, Any]) -> None:
        if "max_stocks" in patch and patch["max_stocks"] <= 0:
            raise ValueError("max_stocks must be > 0")
        if "simulation_days" in patch and patch["simulation_days"] <= 0:
            raise ValueError("simulation_days must be > 0")
        if "min_history_days" in patch and patch["min_history_days"] < 30:
            raise ValueError("min_history_days must be >= 30")
        if "initial_capital" in patch and patch["initial_capital"] <= 0:
            raise ValueError("initial_capital must be > 0")
        if "max_positions" in patch and patch["max_positions"] <= 0:
            raise ValueError("max_positions must be > 0")
        if "position_size_pct" in patch and not (0 < patch["position_size_pct"] <= 1.0):
            raise ValueError("position_size_pct must be within (0, 1]")
        if "allocator_top_n" in patch and patch["allocator_top_n"] <= 0:
            raise ValueError("allocator_top_n must be > 0")
        if "manager_budget_weights" in patch:
            for manager_id, weight in dict(patch["manager_budget_weights"] or {}).items():
                if float(weight) < 0:
                    raise ValueError(f"manager_budget_weights[{manager_id}] must be >= 0")
        if "governance_mode" in patch and patch["governance_mode"] not in {"off", "rule", "hybrid", "agent"}:
            raise ValueError("governance_mode must be one of: off, rule, hybrid, agent")
        if "governance_cooldown_cycles" in patch and patch["governance_cooldown_cycles"] < 0:
            raise ValueError("governance_cooldown_cycles must be >= 0")
        if "governance_min_confidence" in patch and not (0.0 <= patch["governance_min_confidence"] <= 1.0):
            raise ValueError("governance_min_confidence must be within [0, 1]")
        if "governance_hysteresis_margin" in patch and patch["governance_hysteresis_margin"] < 0:
            raise ValueError("governance_hysteresis_margin must be >= 0")
        if "governance_agent_override_max_gap" in patch and patch["governance_agent_override_max_gap"] < 0:
            raise ValueError("governance_agent_override_max_gap must be >= 0")
        for limit_key in ("web_rate_limit_window_sec", "web_rate_limit_read_max", "web_rate_limit_write_max", "web_rate_limit_heavy_max"):
            if limit_key in patch and int(patch[limit_key]) <= 0:
                raise ValueError(f"{limit_key} must be > 0")

    def apply_patch(self, patch: dict[str, Any], source: str = "unknown") -> dict[str, Any]:
        if yaml is None:
            raise RuntimeError("PyYAML 未安装，无法写入 YAML。请安装 pyyaml 后重试。")

        normalized = self.normalize_patch(patch)
        before = self._current_editable_values()
        changed = {}
        for key, value in normalized.items():
            old = before.get(key)
            if old != value:
                changed[key] = {"before": self._redact(key, old), "after": self._redact(key, value)}
                setattr(self.live_config, key, value)

        persisted = self._load_runtime_override()
        persisted.update(normalized)

        self.runtime_override_path.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if self.runtime_override_path.exists():
            backup = self.runtime_override_path.with_suffix(".yaml.bak")
            shutil.copy2(self.runtime_override_path, backup)
        try:
            self.runtime_override_path.write_text(
                yaml.safe_dump(persisted, allow_unicode=True, sort_keys=True),
                encoding="utf-8",
            )
            self._write_snapshot(self._current_editable_values())
            self._append_audit_log(source=source, changed=changed)
        except Exception:
            if backup and backup.exists():
                shutil.copy2(backup, self.runtime_override_path)
            raise
        finally:
            if backup and backup.exists():
                backup.unlink(missing_ok=True)

        return {
            "updated": sorted(changed.keys()),
            "config": self.get_masked_payload(),
        }

    def write_runtime_snapshot(self, *, cycle_id: int, output_dir: str | Path | None = None) -> Path:
        payload = self._snapshot_payload()
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.snapshot_dir / f"cycle_{int(cycle_id):04d}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if output_dir:
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            copy_path = out_dir / f"cycle_{int(cycle_id):04d}_config_snapshot.json"
            copy_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _write_snapshot(self, payload: dict[str, Any]) -> None:
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.snapshot_dir / f"config_{ts}.json"
        path.write_text(
            json.dumps(self._snapshot_payload(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _append_audit_log(self, *, source: str, changed: dict[str, Any]) -> None:
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(),
            "source": source,
            "changed": changed,
        }
        with self.audit_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _current_editable_values(self) -> dict[str, Any]:
        values = {}
        for key in self.EDITABLE_KEYS:
            if not hasattr(self.live_config, key):
                continue
            value = getattr(self.live_config, key)
            if key in {"index_codes", "governance_allowed_manager_ids"} and value is not None:
                values[key] = list(value)
            elif key == "governance_policy" and value is not None:
                values[key] = dict(value)
            else:
                values[key] = value
        return values

    def _snapshot_payload(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = dict(payload or self._current_editable_values())
        return {key: self._redact(key, value) for key, value in raw.items()}

    @staticmethod
    def _redact(key: str, value: Any) -> Any:
        if key not in EvolutionConfigService.SECRET_KEYS:
            return value
        if not value:
            return ""
        value = str(value)
        return ("*" * max(0, len(value) - 4)) + value[-4:]

class RuntimePathConfigService:
    """Persist and expose injectable runtime artifact paths for Web/Commander/Train."""

    EDITABLE_KEYS = {
        "training_output_dir",
        "artifact_log_dir",
        "config_audit_log_path",
        "config_snapshot_dir",
    }

    def __init__(self, project_root: Path | None = None, config_path: Path | None = None):
        self.project_root = Path(project_root or PROJECT_ROOT)
        self._config_path = Path(config_path) if config_path else None

    @property
    def runtime_root(self) -> Path:
        return (self.project_root / "runtime").resolve()

    @property
    def config_path(self) -> Path:
        return self._config_path or (self.project_root / "runtime" / "state" / "runtime_paths.json")

    def get_payload(self) -> dict[str, Any]:
        payload = self.default_payload()
        payload.update(self._load_raw())
        payload["config_path"] = str(self.config_path)
        payload["config_file_exists"] = self.config_path.exists()
        return payload

    def default_payload(self) -> dict[str, Any]:
        runtime_dir = self.project_root / "runtime"
        return {
            "training_output_dir": str(runtime_dir / "outputs" / "training"),
            "artifact_log_dir": str(runtime_dir / "logs" / "artifacts"),
            "config_audit_log_path": str(runtime_dir / "state" / "config_changes.jsonl"),
            "config_snapshot_dir": str(runtime_dir / "state" / "config_snapshots"),
        }

    def load_overrides(self) -> dict[str, str]:
        return self._load_raw()

    def normalize_patch(self, patch: dict[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in self.EDITABLE_KEYS:
            if key not in patch or patch[key] is None:
                continue
            value = str(patch[key]).strip()
            if not value:
                raise ValueError(f"{key} must be a non-empty path")
            out[key] = str(self._normalize_path(value))
        return out

    def apply_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        normalized = self.normalize_patch(patch)
        current = self._load_raw()
        current.update(normalized)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "updated": sorted(normalized.keys()),
            "config": self.get_payload(),
        }

    def _load_raw(self) -> dict[str, str]:
        if not self.config_path.exists():
            return {}
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        normalized = {}
        for key, value in data.items():
            if key in self.EDITABLE_KEYS and value:
                normalized[key] = str(self._normalize_path(value))
        return normalized

    def _normalize_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.runtime_root / path
        resolved = path.resolve()
        try:
            resolved.relative_to(self.runtime_root)
        except ValueError as exc:
            raise ValueError(f"path must stay within runtime directory: {self.runtime_root}") from exc
        return resolved


PUBLIC_RUNTIME_PATH_KEYS: tuple[str, ...] = (
    "training_output_dir",
    "artifact_log_dir",
)


def build_public_runtime_paths_payload(payload: dict[str, Any] | None) -> dict[str, str]:
    public_payload: dict[str, str] = {}
    source = dict(payload or {})
    for key in PUBLIC_RUNTIME_PATH_KEYS:
        if key in source and source[key] is not None:
            public_payload[key] = str(source[key])
    return public_payload

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
    from invest_evolution.config import PROJECT_ROOT

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


def _as_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _sanitize_name(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower())
    return lowered.strip("_") or "component"


def _default_control_plane_payload() -> dict[str, Any]:
    from invest_evolution.config import agent_config_registry, config

    payload: dict[str, Any] = {
        "llm": {
            "providers": {
                "default_provider": {
                    "api_base": str(getattr(config, "llm_api_base", "") or ""),
                    "api_key": "",
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
    paths = get_control_plane_paths(project_root)
    payload = {} if paths else _default_control_plane_payload()
    for path in paths:
        payload = _deep_merge(payload, _read_yaml_dict(path))
    return payload


def clear_control_plane_cache() -> None:
    load_control_plane.cache_clear()


class ControlPlaneResolver:
    def __init__(self, payload: dict[str, Any]):
        self.payload = copy.deepcopy(payload)
        self.llm = _as_dict(self.payload.get("llm"))
        self.providers = _as_dict(self.llm.get("providers"))
        self.models = _as_dict(self.llm.get("models"))
        self.bindings = _as_dict(self.llm.get("bindings"))
        self.data = _as_dict(self.payload.get("data"))

    @classmethod
    def load(cls, project_root: str | Path | None = None) -> "ControlPlaneResolver":
        return cls(load_control_plane(project_root))

    def runtime_data_policy(self) -> dict[str, Any]:
        policy = _as_dict(self.data.get("runtime_policy"))
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
            profile = _as_dict(self.models.get(binding_name))
            provider_name = str(profile.get("provider", "") or "").strip()
            provider = _as_dict(self.providers.get(provider_name))
            model = str(profile.get("model", "") or "").strip()
            api_key = str(provider.get("api_key", "") or "").strip()
            api_base = str(provider.get("api_base", "") or "").strip()
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
        control_plane_present = bool(self.providers or self.models or self.bindings)
        return ResolvedLLMConfig(
            component_key=component_key,
            model=str(fallback_model or ""),
            api_key=str(fallback_api_key or ""),
            api_base=str(fallback_api_base or ""),
            source="fallback",
            issue=_build_fallback_resolution_issue(
                component_key=component_key,
                model=str(fallback_model or ""),
                api_key=str(fallback_api_key or ""),
                binding_name="",
                provider_name="",
                control_plane_present=control_plane_present,
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
    if not get_control_plane_paths(project_root):
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
    from invest_evolution.investment.shared import LLMCaller

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
    from invest_evolution.config import config

    normalized = str(kind or "fast").strip().lower()
    if normalized not in {"fast", "deep"}:
        raise ValueError("kind must be fast or deep")
    component_key = f"defaults.{normalized}"
    fallback_model = str(getattr(config, "llm_fast_model" if normalized == "fast" else "llm_deep_model", "") or "")
    return resolve_component_llm(
        component_key,
        fallback_model=fallback_model,
        fallback_api_key="",
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
    from invest_evolution.investment.shared import LLMCaller

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


def _build_fallback_resolution_issue(
    *,
    component_key: str,
    model: str,
    api_key: str,
    binding_name: str,
    provider_name: str,
    control_plane_present: bool,
) -> str:
    component_label = str(component_key or "").strip() or "defaults.fast"
    if control_plane_present:
        fallback_scope = "fallback values" if any(str(value or "").strip() for value in (model, api_key)) else "empty fallback values"
        return (
            f"control_plane is present but llm.bindings.{component_label} is not configured; "
            f"runtime is using {fallback_scope} for this component. "
            "Add an explicit binding in config/control_plane.yaml before the next release cut."
        )
    return _build_llm_resolution_issue(
        component_key=component_key,
        model=model,
        api_key=api_key,
        binding_name=binding_name,
        provider_name=provider_name,
        source="fallback",
    )


def llm_resolution_status(resolved: ResolvedLLMConfig) -> dict[str, Any]:
    fallback_active = str(resolved.source or "") == "fallback"
    return {
        "component_key": str(resolved.component_key or ""),
        "binding_name": str(resolved.binding_name or ""),
        "profile_name": str(resolved.profile_name or ""),
        "provider_name": str(resolved.provider_name or ""),
        "source": str(resolved.source or ""),
        "ownership_mode": "fallback" if fallback_active else "control_plane",
        "fallback_active": fallback_active,
        "model": str(resolved.model or ""),
        "api_base": str(resolved.api_base or ""),
        "api_key_configured": bool(str(resolved.api_key or "").strip()),
        "issue": str(resolved.issue or ""),
        "governance_summary": (
            "control_plane ownership active"
            if not fallback_active
            else "fallback values active; define bindings in config/control_plane.yaml to complete governance cutover"
        ),
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
    llm = _as_dict(payload.get("llm"))
    bindings = _as_dict(llm.get("bindings"))
    return str(bindings.get(component_key, "") or "").strip()



def _profile_dict(payload: dict[str, Any], profile_name: str) -> dict[str, Any]:
    llm = _as_dict(payload.get("llm"))
    models = _as_dict(llm.get("models"))
    return _as_dict(models.get(profile_name))



def _provider_dict(payload: dict[str, Any], provider_name: str) -> dict[str, Any]:
    llm = _as_dict(payload.get("llm"))
    providers = _as_dict(llm.get("providers"))
    return _as_dict(providers.get(provider_name))
