from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from config import config, normalize_date


@dataclass(frozen=True)
class TrainingCycleContext:
    cycle_id: int
    cutoff_date: str
    requested_data_mode: str
    llm_mode: str
    cutoff_policy_context: dict[str, Any] = field(default_factory=dict)


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

    @staticmethod
    def _resolve_stock_count(controller: Any) -> int:
        return max(
            1,
            int(
                getattr(controller, "experiment_stock_count", None)
                or getattr(config, "max_stocks", 50)
                or 50
            ),
        )

    @staticmethod
    def _cycle_regime(item: Any) -> str:
        routing = dict(getattr(item, "routing_decision", {}) or {})
        audit_tags = dict(getattr(item, "audit_tags", {}) or {})
        return str(
            routing.get("regime")
            or audit_tags.get("routing_regime")
            or getattr(item, "regime", "")
            or "unknown"
        ).strip() or "unknown"

    def _resolve_regime_coverage(self, controller: Any, *, policy: dict[str, Any]) -> dict[str, int]:
        target_regimes = list(policy.get("target_regimes") or []) or ["bull", "bear", "oscillation"]
        coverage = {str(regime): 0 for regime in target_regimes}
        min_regime_samples = max(0, int(policy.get("min_regime_samples") or 0))
        for item in list(getattr(controller, "cycle_history", []) or []):
            regime = self._cycle_regime(item)
            if regime in coverage:
                coverage[regime] += 1
        if min_regime_samples <= 0:
            return coverage
        return {
            regime: min(int(count), min_regime_samples)
            for regime, count in coverage.items()
        }

    @staticmethod
    def _resolve_fallback_cutoff(
        controller: Any,
        *,
        cycle_id: int,
        min_date: str,
        max_date: str,
        fallback_mode: str,
        policy: dict[str, Any],
    ) -> str:
        if fallback_mode == "fixed":
            fixed = str(policy.get("date") or policy.get("cutoff_date") or max_date or min_date)
            return normalize_date(fixed)
        if fallback_mode == "sequence":
            dates = [
                normalize_date(str(item))
                for item in list(policy.get("dates") or [])
                if str(item or "").strip()
            ]
            if dates:
                return dates[min(max(cycle_id - 1, 0), len(dates) - 1)]
        if fallback_mode == "rolling":
            anchor = normalize_date(str(policy.get("anchor_date") or min_date))
            step_days = max(
                1,
                int(
                    policy.get("step_days")
                    or getattr(controller, "experiment_simulation_days", 30)
                    or 30
                ),
            )
            candidate_dt = datetime.strptime(anchor, "%Y%m%d") + timedelta(days=step_days * max(cycle_id - 1, 0))
            candidate = candidate_dt.strftime("%Y%m%d")
            if max_date:
                candidate = min(candidate, normalize_date(max_date))
            return candidate
        return normalize_date(
            controller.data_manager.random_cutoff_date(
                min_date=min_date,
                max_date=max_date or None,
            )
        )

    def _resolve_regime_balanced_cutoff(
        self,
        controller: Any,
        *,
        cycle_id: int,
        min_date: str,
        max_date: str,
        policy: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        target_regimes = list(policy.get("target_regimes") or []) or ["bull", "bear", "oscillation"]
        probe_count = max(3, int(policy.get("probe_count") or 9))
        coverage = self._resolve_regime_coverage(controller, policy=policy)
        undercovered = sorted(
            coverage.items(),
            key=lambda item: (int(item[1]), target_regimes.index(item[0]) if item[0] in target_regimes else 999),
        )
        target_regime = str(undercovered[0][0] if undercovered else target_regimes[0])
        sampled_dates: list[str] = []
        if list(policy.get("dates") or []):
            sampled_dates = [
                normalize_date(str(item))
                for item in list(policy.get("dates") or [])
                if str(item or "").strip()
            ]
        while len(sampled_dates) < probe_count:
            sampled_dates.append(
                normalize_date(
                    controller.data_manager.random_cutoff_date(
                        min_date=min_date,
                        max_date=max_date or None,
                    )
                )
            )

        cache = dict(getattr(controller, "_regime_probe_cache", {}) or {})
        probe_results: list[dict[str, Any]] = []
        for sample_date in sampled_dates[:probe_count]:
            cached = dict(cache.get(sample_date) or {})
            if not cached:
                try:
                    preview = controller.training_routing_service.preview_routing(
                        controller,
                        cutoff_date=sample_date,
                        stock_count=self._resolve_stock_count(controller),
                        min_history_days=(
                            controller.experiment_min_history_days
                            or getattr(config, "min_history_days", 200)
                        ),
                        allowed_models=getattr(controller, "experiment_allowed_models", []) or None,
                    )
                    cached = {
                        "cutoff_date": sample_date,
                        "regime": str(preview.get("regime") or "unknown"),
                        "confidence": float(preview.get("regime_confidence") or preview.get("confidence") or 0.0),
                    }
                except Exception as exc:
                    cached = {
                        "cutoff_date": sample_date,
                        "regime": "unknown",
                        "confidence": 0.0,
                        "error": str(exc),
                    }
                cache[sample_date] = dict(cached)
            probe_results.append(dict(cached))
        controller._regime_probe_cache = dict(cache)

        target_candidates = [item for item in probe_results if str(item.get("regime") or "") == target_regime]
        if target_candidates:
            selected_probe = max(target_candidates, key=lambda item: float(item.get("confidence") or 0.0))
            return normalize_date(str(selected_probe.get("cutoff_date") or "")), {
                "mode": "regime_balanced",
                "target_regime": target_regime,
                "coverage": coverage,
                "probe_count": probe_count,
                "selected_by": "target_regime_match",
                "probes": probe_results,
            }

        fallback_mode = str(policy.get("fallback_mode") or "random").strip().lower() or "random"
        fallback_cutoff = self._resolve_fallback_cutoff(
            controller,
            cycle_id=cycle_id,
            min_date=min_date,
            max_date=max_date,
            fallback_mode=fallback_mode,
            policy=policy,
        )
        return fallback_cutoff, {
            "mode": "regime_balanced",
            "target_regime": target_regime,
            "coverage": coverage,
            "probe_count": probe_count,
            "selected_by": f"fallback:{fallback_mode}",
            "probes": probe_results,
        }

    def _resolve_cutoff(self, controller: Any, *, cycle_id: int) -> tuple[str, dict[str, Any]]:
        policy = dict(getattr(controller, "experiment_cutoff_policy", {}) or {})
        mode = str(policy.get("mode") or "random").strip().lower() or "random"
        min_date = str(getattr(controller, "experiment_min_date", None) or "20180101")
        max_date = str(getattr(controller, "experiment_max_date", None) or "")

        if mode == "fixed":
            fixed = str(policy.get("date") or policy.get("cutoff_date") or max_date or min_date)
            return normalize_date(fixed), {"mode": mode}

        if mode == "sequence":
            dates = [
                normalize_date(str(item))
                for item in list(policy.get("dates") or [])
                if str(item or "").strip()
            ]
            if dates:
                index = min(max(cycle_id - 1, 0), len(dates) - 1)
                sampling_seeds = list(policy.get("sampling_seeds") or [])
                sampling_seed = None
                if index < len(sampling_seeds):
                    try:
                        sampling_seed = int(sampling_seeds[index])
                    except (TypeError, ValueError):
                        sampling_seed = None
                return dates[index], {"mode": mode, "dates": dates, "sampling_seed": sampling_seed}

        if mode == "rolling":
            anchor = normalize_date(str(policy.get("anchor_date") or min_date))
            step_days = max(
                1,
                int(
                    policy.get("step_days")
                    or getattr(controller, "experiment_simulation_days", 30)
                    or 30
                ),
            )
            candidate_dt = datetime.strptime(anchor, "%Y%m%d") + timedelta(days=step_days * max(cycle_id - 1, 0))
            candidate = candidate_dt.strftime("%Y%m%d")
            if max_date:
                candidate = min(candidate, normalize_date(max_date))
            return candidate, {"mode": mode, "anchor_date": anchor, "step_days": step_days}

        if mode == "regime_balanced":
            return self._resolve_regime_balanced_cutoff(
                controller,
                cycle_id=cycle_id,
                min_date=min_date,
                max_date=max_date,
                policy=policy,
            )

        return normalize_date(
            controller.data_manager.random_cutoff_date(
                min_date=min_date,
                max_date=max_date or None,
            )
        ), {"mode": "random"}

    def prepare_cycle_context(self, controller: Any) -> TrainingCycleContext:
        cycle_id = int(controller.current_cycle_id) + 1
        controller.current_cycle_sampling_seed = None
        if controller.experiment_seed is not None:
            seed_value = int(controller.experiment_seed) + cycle_id
            random.seed(seed_value)
            np.random.seed(seed_value % (2**32 - 1))
            controller.current_cycle_sampling_seed = seed_value

        resolved_cutoff, cutoff_policy_context = self._resolve_cutoff(controller, cycle_id=cycle_id)
        pinned_sampling_seed = cutoff_policy_context.get("sampling_seed")
        if pinned_sampling_seed is not None:
            seed_value = int(pinned_sampling_seed)
            random.seed(seed_value)
            np.random.seed(seed_value % (2**32 - 1))
            controller.current_cycle_sampling_seed = seed_value
        controller.last_cutoff_policy_context = dict(cutoff_policy_context or {})
        cutoff_date = normalize_date(
            os.getenv("INVEST_FORCE_CUTOFF_DATE", "")
            or resolved_cutoff
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
            cutoff_policy_context=dict(getattr(controller, "last_cutoff_policy_context", {}) or {}),
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
        stock_count = self._resolve_stock_count(controller)
        stock_data = controller.data_manager.load_stock_data(
            cutoff_date,
            stock_count=stock_count,
            min_history_days=min_history_days,
            include_future_days=max(
                30,
                int(
                    controller.experiment_simulation_days
                    or getattr(config, "simulation_days", 30)
                ),
            ),
            sampling_policy=dict(getattr(controller, "experiment_universe_policy", {}) or {}),
            sampling_seed=getattr(controller, "current_cycle_sampling_seed", None),
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
                stock_count=self._resolve_stock_count(controller),
                min_history_days=min_history_days,
            )
            if isinstance(raw_diagnostics, dict):
                diagnostics = dict(raw_diagnostics)
                break
        return diagnostics or {}
