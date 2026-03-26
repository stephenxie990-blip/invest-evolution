from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from config import PROJECT_ROOT
from invest.foundation.risk import sanitize_risk_params
from invest.models.validation import validate_model_config


def _clamp_numeric(value: Any, bounds: Dict[str, Any]) -> Any:
    try:
        numeric = float(value)
    except Exception:
        return value
    low = float(bounds.get("min", numeric))
    high = float(bounds.get("max", numeric))
    return max(low, min(high, numeric))


class YamlConfigMutator:
    """Mutate investment-model YAML configs instead of mutating business code paths."""

    def __init__(self, generations_dir: Path | None = None):
        self.generations_dir = generations_dir or (PROJECT_ROOT / "data" / "evolution" / "generations")
        self.generations_dir.mkdir(parents=True, exist_ok=True)

    def load(self, config_path: str | Path) -> tuple[Path, Dict[str, Any]]:
        path = Path(config_path)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return path, data

    def _apply_mutation_space(
        self,
        mutated: Dict[str, Any],
        param_adjustments: Optional[Dict[str, Any]],
        scoring_adjustments: Optional[Dict[str, Any]],
        agent_weight_adjustments: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        mutation_space = dict(mutated.get("mutation_space", {}) or {})
        applied = {"params": {}, "scoring": {}, "agent_weights": {}}

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

        if agent_weight_adjustments:
            applied["agent_weights"] = dict(agent_weight_adjustments)
        return applied

    def mutate(
        self,
        config_path: str | Path,
        *,
        param_adjustments: Optional[Dict[str, Any]] = None,
        scoring_adjustments: Optional[Dict[str, Any]] = None,
        agent_weight_adjustments: Optional[Dict[str, Any]] = None,
        narrative_adjustments: Optional[Dict[str, Any]] = None,
        generation_label: Optional[str] = None,
        parent_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        path, data = self.load(config_path)
        mutated = deepcopy(data)
        params = dict(mutated.get("params", {}))
        risk = dict(mutated.get("risk", {}))

        clean = sanitize_risk_params(param_adjustments) if param_adjustments else {}
        applied_adjustments = self._apply_mutation_space(
            mutated,
            clean or param_adjustments,
            scoring_adjustments,
            agent_weight_adjustments,
        )

        if applied_adjustments.get("params"):
            params.update(applied_adjustments["params"])
            for key in ("stop_loss_pct", "take_profit_pct", "trailing_pct"):
                if key in applied_adjustments["params"]:
                    risk[key] = applied_adjustments["params"][key]
        mutated["params"] = params
        mutated["risk"] = risk

        if applied_adjustments.get("scoring"):
            scoring_section_name = "scoring" if isinstance(mutated.get("scoring"), dict) else "summary_scoring"
            scoring = dict(mutated.get(scoring_section_name, {}))
            for section_name, section_patch in applied_adjustments["scoring"].items():
                section = dict(scoring.get(section_name, {}))
                section.update(section_patch)
                scoring[section_name] = section
            mutated[scoring_section_name] = scoring

        if applied_adjustments.get("agent_weights"):
            agent_weights = dict(mutated.get("agent_weights", {}))
            agent_weights.update(dict(applied_adjustments.get("agent_weights") or {}))
            mutated["agent_weights"] = agent_weights

        if narrative_adjustments:
            context = dict(mutated.get("context", {}))
            context.update(narrative_adjustments)
            mutated["context"] = context

        validate_model_config(mutated)
        stem = generation_label or datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.generations_dir / f"{path.stem}_{stem}.yaml"
        out_path.write_text(yaml.safe_dump(mutated, allow_unicode=True, sort_keys=False), encoding="utf-8")
        meta = {
            "parent_config": str(path),
            "output_config": str(out_path),
            "param_adjustments": param_adjustments or {},
            "scoring_adjustments": scoring_adjustments or {},
            "agent_weight_adjustments": agent_weight_adjustments or {},
            "applied_adjustments": applied_adjustments,
            "narrative_adjustments": narrative_adjustments or {},
            "generated_at": datetime.now().isoformat(),
            "parent_meta": parent_meta or {},
        }
        meta_path = out_path.with_suffix(".json")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "config_path": str(out_path),
            "meta_path": str(meta_path),
            "config": mutated,
            "meta": meta,
            "applied_adjustments": applied_adjustments,
        }
