from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from invest_evolution.agent_runtime.runtime import enforce_path_within_root
from invest_evolution.config import OUTPUT_DIR, PROJECT_ROOT
from invest_evolution.investment.foundation.risk import sanitize_risk_params
from invest_evolution.investment.runtimes.ops import validate_runtime_config


def _resolve_runtime_config_path(runtime_config_ref: str | Path) -> Path:
    text = str(runtime_config_ref or "").strip()
    if not text:
        raise ValueError("runtime config ref is required")

    path = Path(text).expanduser()
    looks_like_path = (
        path.is_absolute()
        or path.suffix.lower() in {".yaml", ".yml", ".json"}
        or "/" in text
        or "\\" in text
    )
    if not looks_like_path:
        configs_dir = PROJECT_ROOT / "src" / "invest_evolution" / "investment" / "runtimes" / "configs"
        for suffix in (".yaml", ".yml"):
            candidate = configs_dir / f"{text}{suffix}"
            if candidate.exists():
                return enforce_path_within_root(PROJECT_ROOT, candidate)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return enforce_path_within_root(PROJECT_ROOT, path)


def _clamp_numeric(value: Any, bounds: Dict[str, Any]) -> Any:
    try:
        numeric = float(value)
    except Exception:
        return value
    low = float(bounds.get("min", numeric))
    high = float(bounds.get("max", numeric))
    return max(low, min(high, numeric))


def _resolve_generation_output_paths(
    generations_dir: Path,
    *,
    source_path: Path,
    generation_label: str,
) -> tuple[Path, Path]:
    config_path = generations_dir / f"{source_path.stem}_{generation_label}.yaml"
    return config_path, config_path.with_suffix(".json")


def _build_mutation_meta_payload(
    *,
    source_path: Path,
    output_path: Path,
    param_adjustments: Optional[Dict[str, Any]],
    scoring_adjustments: Optional[Dict[str, Any]],
    applied_adjustments: Dict[str, Any],
    narrative_adjustments: Optional[Dict[str, Any]],
    parent_meta: Optional[Dict[str, Any]],
    generated_at: datetime,
) -> Dict[str, Any]:
    return {
        "parent_runtime_config_ref": str(source_path),
        "output_runtime_config_ref": str(output_path),
        "param_adjustments": param_adjustments or {},
        "scoring_adjustments": scoring_adjustments or {},
        "applied_adjustments": applied_adjustments,
        "narrative_adjustments": narrative_adjustments or {},
        "generated_at": generated_at.isoformat(),
        "parent_meta": parent_meta or {},
    }


def _write_generation_artifacts(
    *,
    config_path: Path,
    meta_path: Path,
    config_payload: Dict[str, Any],
    meta_payload: Dict[str, Any],
) -> None:
    config_path.write_text(
        yaml.safe_dump(config_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    meta_path.write_text(
        json.dumps(meta_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class RuntimeConfigMutator:
    """Mutate runtime config snapshots instead of mutating business code paths."""

    def __init__(self, generations_dir: Path | None = None):
        self.generations_dir = generations_dir or (OUTPUT_DIR / "runtime_generations")
        self.generations_dir.mkdir(parents=True, exist_ok=True)

    def load(self, runtime_config_ref: str | Path) -> tuple[Path, Dict[str, Any]]:
        try:
            path = _resolve_runtime_config_path(runtime_config_ref)
        except ValueError as exc:
            raise ValueError(
                f"runtime config ref {runtime_config_ref} resolves outside project root"
            ) from exc
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return path, data

    def _apply_mutation_space(
        self,
        mutated: Dict[str, Any],
        param_adjustments: Optional[Dict[str, Any]],
        scoring_adjustments: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        mutation_space = dict(mutated.get("mutation_space", {}) or {})
        applied = {"params": {}, "scoring": {}}

        if param_adjustments:
            param_space = dict(mutation_space.get("params", {}) or {})
            for key, value in param_adjustments.items():
                applied["params"][key] = _clamp_numeric(value, param_space[key]) if key in param_space else value

        if scoring_adjustments:
            scoring_space = dict(mutation_space.get("scoring", {}) or {})
            for section_name, section_patch in scoring_adjustments.items():
                if not isinstance(section_patch, dict):
                    continue
                section_space = dict(scoring_space.get(section_name, {}) or {})
                applied_section = {}
                for key, value in section_patch.items():
                    applied_section[key] = _clamp_numeric(value, section_space[key]) if key in section_space else value
                if applied_section:
                    applied["scoring"][section_name] = applied_section
        return applied

    def mutate(
        self,
        runtime_config_ref: str | Path,
        *,
        param_adjustments: Optional[Dict[str, Any]] = None,
        scoring_adjustments: Optional[Dict[str, Any]] = None,
        narrative_adjustments: Optional[Dict[str, Any]] = None,
        generation_label: Optional[str] = None,
        parent_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        path, data = self.load(runtime_config_ref)
        mutated = deepcopy(data)
        params = dict(mutated.get("params", {}))
        risk = dict(mutated.get("risk", {}))

        clean = sanitize_risk_params(param_adjustments) if param_adjustments else {}
        applied_adjustments = self._apply_mutation_space(mutated, clean or param_adjustments, scoring_adjustments)

        if applied_adjustments.get("params"):
            params.update(applied_adjustments["params"])
            for key in ("stop_loss_pct", "take_profit_pct", "trailing_pct"):
                if key in applied_adjustments["params"]:
                    risk[key] = applied_adjustments["params"][key]
        mutated["params"] = params
        mutated["risk"] = risk

        if applied_adjustments.get("scoring"):
            scoring = dict(mutated.get("scoring", {}))
            for section_name, section_patch in applied_adjustments["scoring"].items():
                section = dict(scoring.get(section_name, {}))
                section.update(section_patch)
                scoring[section_name] = section
            mutated["scoring"] = scoring

        if narrative_adjustments:
            context = dict(mutated.get("context", {}))
            context.update(narrative_adjustments)
            mutated["context"] = context

        validate_runtime_config(mutated)
        generated_at = datetime.now()
        stem = generation_label or generated_at.strftime("%Y%m%d_%H%M%S")
        out_path, meta_path = _resolve_generation_output_paths(
            self.generations_dir,
            source_path=path,
            generation_label=stem,
        )
        meta = _build_mutation_meta_payload(
            source_path=path,
            output_path=out_path,
            param_adjustments=param_adjustments,
            scoring_adjustments=scoring_adjustments,
            applied_adjustments=applied_adjustments,
            narrative_adjustments=narrative_adjustments,
            parent_meta=parent_meta,
            generated_at=generated_at,
        )
        _write_generation_artifacts(
            config_path=out_path,
            meta_path=meta_path,
            config_payload=mutated,
            meta_payload=meta,
        )
        return {
            "runtime_config_ref": str(out_path),
            "meta_path": str(meta_path),
            "config": mutated,
            "meta": meta,
            "applied_adjustments": applied_adjustments,
        }
