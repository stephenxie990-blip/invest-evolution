from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from config import EvolutionConfig, LOGS_DIR, OUTPUT_DIR, PROJECT_ROOT, RUNTIME_DIR, config, get_config_layer_paths, load_config

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


class EvolutionConfigService:
    """Unified service for reading, validating, persisting and auditing evolution config."""

    EDITABLE_KEYS = {
        "llm_fast_model",
        "llm_deep_model",
        "llm_api_base",
        "llm_api_key",
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
        "investment_model",
        "investment_model_config",
        "allocator_enabled",
        "allocator_top_n",
        "model_routing_enabled",
        "model_routing_mode",
        "model_routing_allowed_models",
        "model_switch_cooldown_cycles",
        "model_switch_min_confidence",
        "model_switch_hysteresis_margin",
        "model_routing_agent_override_enabled",
        "model_routing_agent_override_max_gap",
        "model_routing_policy",
        "stop_on_freeze",
        "web_ui_shell_mode",
        "frontend_canary_enabled",
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
        return self._config_path or (self.project_root / "config" / "evolution.yaml")

    @property
    def audit_log_path(self) -> Path:
        return self._audit_log_path or (self.project_root / "runtime" / "state" / "config_changes.jsonl")

    @property
    def snapshot_dir(self) -> Path:
        return self._snapshot_dir or (self.project_root / "runtime" / "state" / "config_snapshots")

    @property
    def local_override_path(self) -> Path:
        return self.config_path.parent / "evolution.local.yaml"

    def _read_yaml_dict(self, path: Path) -> dict[str, Any]:
        if yaml is None or not path.exists():
            return {}
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return payload if isinstance(payload, dict) else {}

    def _secret_source(self) -> str:
        if str(os.environ.get("LLM_API_KEY", "")).strip():
            return "env"
        local_payload = self._read_yaml_dict(self.local_override_path)
        if str(local_payload.get("llm_api_key", "")).strip():
            return "local_yaml"
        primary_payload = self._read_yaml_dict(self.config_path)
        if str(primary_payload.get("llm_api_key", "")).strip():
            return "yaml"
        if str(getattr(self.live_config, "llm_api_key", "")).strip():
            return "runtime"
        return "unset"

    def get_masked_payload(self) -> dict[str, Any]:
        cfg = self.live_config
        llm_key_masked = ""
        if getattr(cfg, "llm_api_key", ""):
            v = str(getattr(cfg, "llm_api_key"))
            llm_key_masked = ("*" * max(0, len(v) - 4)) + v[-4:]
        return {
            "config_path": str(self.config_path),
            "config_file_exists": self.config_path.exists(),
            "llm_fast_model": cfg.llm_fast_model,
            "llm_deep_model": cfg.llm_deep_model,
            "llm_api_base": cfg.llm_api_base,
            "llm_api_key_masked": llm_key_masked,
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
            "investment_model": cfg.investment_model,
            "investment_model_config": cfg.investment_model_config,
            "allocator_enabled": cfg.allocator_enabled,
            "allocator_top_n": cfg.allocator_top_n,
            "model_routing_enabled": cfg.model_routing_enabled,
            "model_routing_mode": cfg.model_routing_mode,
            "model_routing_allowed_models": list(cfg.model_routing_allowed_models or []),
            "model_switch_cooldown_cycles": cfg.model_switch_cooldown_cycles,
            "model_switch_min_confidence": cfg.model_switch_min_confidence,
            "model_switch_hysteresis_margin": cfg.model_switch_hysteresis_margin,
            "model_routing_agent_override_enabled": cfg.model_routing_agent_override_enabled,
            "model_routing_agent_override_max_gap": cfg.model_routing_agent_override_max_gap,
            "model_routing_policy": dict(cfg.model_routing_policy or {}),
            "stop_on_freeze": cfg.stop_on_freeze,
            "web_ui_shell_mode": getattr(cfg, "web_ui_shell_mode", "legacy"),
            "frontend_canary_enabled": bool(getattr(cfg, "frontend_canary_enabled", False)),
            "frontend_canary_query_param": str(getattr(cfg, "frontend_canary_query_param", "__frontend") or "__frontend"),
            "config_layers": [str(path) for path in get_config_layer_paths(self.config_path)],
            "local_override_path": str(self.local_override_path),
            "llm_api_key_source": self._secret_source(),
            "audit_log_path": str(self.audit_log_path),
            "snapshot_dir": str(self.snapshot_dir),
        }

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
        if "investment_model" in out:
            out["investment_model"] = str(out["investment_model"]).strip() or "momentum"
        if "investment_model_config" in out:
            out["investment_model_config"] = str(out["investment_model_config"]).strip()
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
        if "model_routing_mode" in out:
            out["model_routing_mode"] = str(out["model_routing_mode"] or "rule").strip().lower() or "rule"
        if "model_switch_cooldown_cycles" in out:
            out["model_switch_cooldown_cycles"] = int(out["model_switch_cooldown_cycles"])
        if "model_switch_min_confidence" in out:
            out["model_switch_min_confidence"] = float(out["model_switch_min_confidence"])
        if "model_switch_hysteresis_margin" in out:
            out["model_switch_hysteresis_margin"] = float(out["model_switch_hysteresis_margin"])
        if "model_routing_agent_override_max_gap" in out:
            out["model_routing_agent_override_max_gap"] = float(out["model_routing_agent_override_max_gap"])
        if "web_ui_shell_mode" in out:
            out["web_ui_shell_mode"] = str(out["web_ui_shell_mode"] or "legacy").strip().lower() or "legacy"
        for bool_key in ("model_routing_enabled", "model_routing_agent_override_enabled", "frontend_canary_enabled"):
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
        if "model_routing_allowed_models" in out:
            value = out["model_routing_allowed_models"]
            if value is None:
                out["model_routing_allowed_models"] = []
            elif isinstance(value, str):
                out["model_routing_allowed_models"] = [part.strip() for part in value.split(",") if part.strip()]
            elif isinstance(value, list):
                out["model_routing_allowed_models"] = [str(part).strip() for part in value if str(part).strip()]
            else:
                raise ValueError("model_routing_allowed_models must be a list or comma-separated string")
        if "model_routing_policy" in out:
            if out["model_routing_policy"] is None:
                out["model_routing_policy"] = {}
            if not isinstance(out["model_routing_policy"], dict):
                raise ValueError("model_routing_policy must be an object")
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
        if "model_routing_mode" in patch and patch["model_routing_mode"] not in {"off", "rule", "hybrid", "agent"}:
            raise ValueError("model_routing_mode must be one of: off, rule, hybrid, agent")
        if "model_switch_cooldown_cycles" in patch and patch["model_switch_cooldown_cycles"] < 0:
            raise ValueError("model_switch_cooldown_cycles must be >= 0")
        if "model_switch_min_confidence" in patch and not (0.0 <= patch["model_switch_min_confidence"] <= 1.0):
            raise ValueError("model_switch_min_confidence must be within [0, 1]")
        if "model_switch_hysteresis_margin" in patch and patch["model_switch_hysteresis_margin"] < 0:
            raise ValueError("model_switch_hysteresis_margin must be >= 0")
        if "model_routing_agent_override_max_gap" in patch and patch["model_routing_agent_override_max_gap"] < 0:
            raise ValueError("model_routing_agent_override_max_gap must be >= 0")
        if "web_ui_shell_mode" in patch and patch["web_ui_shell_mode"] not in {"legacy", "app"}:
            raise ValueError("web_ui_shell_mode must be one of: legacy, app")

    def apply_patch(self, patch: dict[str, Any], source: str = "unknown") -> dict[str, Any]:
        if yaml is None:
            raise RuntimeError("PyYAML 未安装，无法写入 YAML。请安装 pyyaml 后重试。")

        normalized = self.normalize_patch(patch)
        explicit_secret_update = "llm_api_key" in normalized
        secret_value = str(normalized.get("llm_api_key", "") or "").strip() if explicit_secret_update else ""
        before = self._current_editable_values()
        changed = {}
        for key, value in normalized.items():
            if key == "llm_api_key":
                value = str(value).strip()
                if not value:
                    continue
            old = before.get(key)
            if old != value:
                changed[key] = {"before": self._redact(key, old), "after": self._redact(key, value)}
                setattr(self.live_config, key, value)

        persisted = self._current_editable_values()
        persisted.pop("llm_api_key", None)

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        local_backup = None
        existing_local = self._read_yaml_dict(self.local_override_path)
        updated_local = dict(existing_local)
        if explicit_secret_update:
            if secret_value:
                updated_local["llm_api_key"] = secret_value
            else:
                updated_local.pop("llm_api_key", None)

        if self.config_path.exists():
            backup = self.config_path.with_suffix(".yaml.bak")
            shutil.copy2(self.config_path, backup)
        if self.local_override_path.exists():
            local_backup = self.local_override_path.with_suffix(".yaml.bak")
            shutil.copy2(self.local_override_path, local_backup)
        try:
            self.config_path.write_text(yaml.safe_dump(persisted, allow_unicode=True, sort_keys=True), encoding="utf-8")
            if explicit_secret_update:
                if updated_local:
                    self.local_override_path.write_text(yaml.safe_dump(updated_local, allow_unicode=True, sort_keys=True), encoding="utf-8")
                else:
                    self.local_override_path.unlink(missing_ok=True)
            self._write_snapshot(persisted)
            self._append_audit_log(source=source, changed=changed)
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
            if key in {"index_codes", "model_routing_allowed_models"} and value is not None:
                values[key] = list(value)
            elif key == "model_routing_policy" and value is not None:
                values[key] = dict(value)
            else:
                values[key] = value
        return values

    def _snapshot_payload(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = dict(payload or self._current_editable_values())
        if "llm_api_key" not in raw and str(getattr(self.live_config, "llm_api_key", "") or "").strip():
            raw["llm_api_key"] = getattr(self.live_config, "llm_api_key")
        return {key: self._redact(key, value) for key, value in raw.items()}

    @staticmethod
    def _redact(key: str, value: Any) -> Any:
        if key != "llm_api_key":
            return value
        if not value:
            return ""
        value = str(value)
        return ("*" * max(0, len(value) - 4)) + value[-4:]

class RuntimePathConfigService:
    """Persist and expose injectable runtime artifact paths for Web/Commander/Train."""

    EDITABLE_KEYS = {
        "training_output_dir",
        "meeting_log_dir",
        "config_audit_log_path",
        "config_snapshot_dir",
    }

    def __init__(self, project_root: Path | None = None, config_path: Path | None = None):
        self.project_root = Path(project_root or PROJECT_ROOT)
        self._config_path = Path(config_path) if config_path else None

    @property
    def config_path(self) -> Path:
        return self._config_path or (self.project_root / "runtime" / "state" / "runtime_paths.json")

    def get_payload(self) -> dict[str, Any]:
        payload = self.default_payload()
        payload.update(self._load_raw())
        payload["config_path"] = str(self.config_path)
        payload["config_file_exists"] = self.config_path.exists()
        return payload

    def default_payload(self) -> dict[str, str]:
        runtime_dir = self.project_root / "runtime"
        return {
            "training_output_dir": str(runtime_dir / "outputs" / "training"),
            "meeting_log_dir": str(runtime_dir / "logs" / "meetings"),
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
            path = self.project_root / path
        return path.resolve()

