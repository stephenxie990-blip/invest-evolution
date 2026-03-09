from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from .base import InvestmentModel
from .defensive_low_vol import DefensiveLowVolModel
from .mean_reversion import MeanReversionModel
from .momentum import MomentumModel
from .value_quality import ValueQualityModel


_MODEL_REGISTRY = {
    "momentum": MomentumModel,
    "mean_reversion": MeanReversionModel,
    "value_quality": ValueQualityModel,
    "defensive_low_vol": DefensiveLowVolModel,
}


def list_models() -> list[str]:
    return sorted(_MODEL_REGISTRY)


def resolve_model_config_path(model_name: str) -> Path:
    key = str(model_name or "momentum").strip().lower()
    model_cls = _MODEL_REGISTRY.get(key)
    if model_cls is None:
        raise ValueError(f"Unknown investment model: {model_name}")
    return model_cls.resolve_config_path(None)


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
