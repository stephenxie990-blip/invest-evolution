from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from .base import InvestmentModel
from .momentum import MomentumModel


_MODEL_REGISTRY = {
    "momentum": MomentumModel,
}


def list_models() -> list[str]:
    return sorted(_MODEL_REGISTRY)


def create_investment_model(
    model_name: str,
    config_path: str | Path | None = None,
    runtime_overrides: Optional[Dict] = None,
) -> InvestmentModel:
    key = str(model_name or "momentum").strip().lower()
    model_cls = _MODEL_REGISTRY.get(key)
    if model_cls is None:
        raise ValueError(f"Unknown investment model: {model_name}")
    return model_cls(config_path=config_path, runtime_overrides=runtime_overrides)
