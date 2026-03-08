from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from config import PROJECT_ROOT
from invest.foundation.risk import sanitize_risk_params


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

    def mutate(
        self,
        config_path: str | Path,
        *,
        param_adjustments: Optional[Dict[str, Any]] = None,
        narrative_adjustments: Optional[Dict[str, Any]] = None,
        generation_label: Optional[str] = None,
        parent_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        path, data = self.load(config_path)
        mutated = deepcopy(data)
        params = dict(mutated.get("params", {}))
        risk = dict(mutated.get("risk", {}))
        if param_adjustments:
            clean = sanitize_risk_params(param_adjustments)
            params.update(clean)
            for key in ("stop_loss_pct", "take_profit_pct", "trailing_pct"):
                if key in clean:
                    risk[key] = clean[key]
        mutated["params"] = params
        mutated["risk"] = risk
        if narrative_adjustments:
            context = dict(mutated.get("context", {}))
            context.update(narrative_adjustments)
            mutated["context"] = context

        stem = generation_label or datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.generations_dir / f"{path.stem}_{stem}.yaml"
        out_path.write_text(yaml.safe_dump(mutated, allow_unicode=True, sort_keys=False), encoding="utf-8")
        meta = {
            "parent_config": str(path),
            "output_config": str(out_path),
            "param_adjustments": param_adjustments or {},
            "narrative_adjustments": narrative_adjustments or {},
            "generated_at": datetime.now().isoformat(),
            "parent_meta": parent_meta or {},
        }
        meta_path = out_path.with_suffix(".json")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"config_path": str(out_path), "meta_path": str(meta_path), "config": mutated, "meta": meta}
