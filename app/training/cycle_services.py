from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Any

import numpy as np

from config import config, normalize_date


@dataclass(frozen=True)
class TrainingCycleContext:
    cycle_id: int
    cutoff_date: str
    requested_data_mode: str
    llm_mode: str


@dataclass(frozen=True)
class TrainingDataLoadResult:
    diagnostics: dict[str, Any]
    stock_data: dict[str, Any]
    requested_data_mode: str
    effective_data_mode: str
    data_mode: str
    degraded: bool
    degrade_reason: str
    min_history_days: int


class TrainingCycleDataService:
    """Owns the bootstrap and data-loading stages of a training cycle."""

    def prepare_cycle_context(self, controller: Any) -> TrainingCycleContext:
        cycle_id = int(controller.current_cycle_id) + 1
        if controller.experiment_seed is not None:
            seed_value = int(controller.experiment_seed) + cycle_id
            random.seed(seed_value)
            np.random.seed(seed_value % (2**32 - 1))

        cutoff_date = normalize_date(
            os.getenv("INVEST_FORCE_CUTOFF_DATE", "")
            or controller.data_manager.random_cutoff_date(
                min_date=controller.experiment_min_date or "20180101",
                max_date=controller.experiment_max_date,
            )
        )
        requested_data_mode = str(
            getattr(
                controller,
                "requested_data_mode",
                getattr(controller.data_manager, "requested_mode", "live"),
            )
            or "live"
        )
        llm_mode = str(getattr(controller, "llm_mode", "live") or "live")
        return TrainingCycleContext(
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            requested_data_mode=requested_data_mode,
            llm_mode=llm_mode,
        )

    def load_training_data(
        self,
        controller: Any,
        *,
        cutoff_date: str,
        requested_data_mode: str,
    ) -> TrainingDataLoadResult:
        min_history_days = max(
            30,
            int(
                controller.experiment_min_history_days
                or getattr(config, "min_history_days", 200)
            ),
        )
        diagnostics = self._resolve_diagnostics(
            controller,
            cutoff_date=cutoff_date,
            min_history_days=min_history_days,
        )
        stock_data = controller.data_manager.load_stock_data(
            cutoff_date,
            stock_count=config.max_stocks,
            min_history_days=min_history_days,
            include_future_days=max(
                30,
                int(
                    controller.experiment_simulation_days
                    or getattr(config, "simulation_days", 30)
                ),
            ),
        )
        resolution = dict(getattr(controller.data_manager, "last_resolution", {}) or {})
        effective_data_mode = str(
            resolution.get("effective_data_mode")
            or getattr(controller.data_manager, "last_source", "unknown")
            or "unknown"
        )
        degrade_reason = str(resolution.get("degrade_reason") or "")
        degraded = bool(resolution.get("degraded", False))
        return TrainingDataLoadResult(
            diagnostics=diagnostics,
            stock_data=stock_data,
            requested_data_mode=requested_data_mode,
            effective_data_mode=effective_data_mode,
            data_mode=effective_data_mode,
            degraded=degraded,
            degrade_reason=degrade_reason,
            min_history_days=min_history_days,
        )

    def _resolve_diagnostics(
        self,
        controller: Any,
        *,
        cutoff_date: str,
        min_history_days: int,
    ) -> dict[str, Any]:
        diagnostic_order = ["check_training_readiness", "diagnose_training_data"]
        if callable(getattr(controller.data_manager, "__dict__", {}).get("diagnose_training_data")):
            diagnostic_order = ["diagnose_training_data", "check_training_readiness"]

        diagnostics: dict[str, Any] | None = None
        for method_name in diagnostic_order:
            method = getattr(controller.data_manager, method_name, None)
            if not callable(method):
                continue
            raw_diagnostics = method(
                cutoff_date=cutoff_date,
                stock_count=config.max_stocks,
                min_history_days=min_history_days,
            )
            if isinstance(raw_diagnostics, dict):
                diagnostics = dict(raw_diagnostics)
                break
        return diagnostics or {}
