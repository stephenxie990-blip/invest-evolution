from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from config import EvolutionConfig, LOGS_DIR, OUTPUT_DIR, PROJECT_ROOT, RUNTIME_DIR, config, load_config

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
        "stop_on_freeze",
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
            "stop_on_freeze": cfg.stop_on_freeze,
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

    def apply_patch(self, patch: dict[str, Any], source: str = "unknown") -> dict[str, Any]:
        if yaml is None:
            raise RuntimeError("PyYAML 未安装，无法写入 YAML。请安装 pyyaml 后重试。")

        normalized = self.normalize_patch(patch)
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
        if "llm_api_key" in persisted and not str(persisted.get("llm_api_key") or "").strip():
            persisted.pop("llm_api_key", None)

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if self.config_path.exists():
            backup = self.config_path.with_suffix(".yaml.bak")
            shutil.copy2(self.config_path, backup)
        try:
            self.config_path.write_text(yaml.safe_dump(persisted, allow_unicode=True, sort_keys=True), encoding="utf-8")
            self._write_snapshot(persisted)
            self._append_audit_log(source=source, changed=changed)
        except Exception:
            if backup and backup.exists():
                shutil.copy2(backup, self.config_path)
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
            if hasattr(self.live_config, key):
                value = getattr(self.live_config, key)
                values[key] = list(value) if key == "index_codes" and value is not None else value
        return values

    def _snapshot_payload(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = dict(payload or self._current_editable_values())
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

