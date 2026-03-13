"""Application-layer training orchestrator facade."""

from __future__ import annotations

from typing import Any

from app.train import SelfLearningController


class TrainingOrchestrator(SelfLearningController):
    """Phase 6 facade for the legacy self-learning controller."""


def build_training_orchestrator(*args: Any, **kwargs: Any) -> TrainingOrchestrator:
    return TrainingOrchestrator(*args, **kwargs)
