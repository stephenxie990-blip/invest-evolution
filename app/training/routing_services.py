from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from config import OUTPUT_DIR, config, normalize_date
from invest.leaderboard import write_leaderboard
from invest.models import create_investment_model
from invest.router import ModelRoutingCoordinator

logger = logging.getLogger(__name__)


class TrainingRoutingService:
    """Coordinates leaderboard refresh and model-routing decisions."""

    @staticmethod
    def _validation_mode_enabled(owner: Any | None) -> bool:
        return (
            str(getattr(owner, "experiment_mode", "standard") or "standard").strip().lower()
            == "validation"
        )

    def _apply_validation_mode_constraints(self, controller: Any) -> None:
        controller.allocator_enabled = False
        controller.model_routing_enabled = False
        controller.model_routing_mode = "off"
        controller.model_routing_agent_override_enabled = False
        allowed = [
            str(item).strip()
            for item in list(getattr(controller, "experiment_allowed_models", []) or [])
            if str(item).strip()
        ]
        if not allowed and str(getattr(controller, "model_name", "") or "").strip():
            allowed = [str(controller.model_name)]
        if allowed:
            controller.model_routing_allowed_models = allowed

    @staticmethod
    def _build_validation_hold_decision(controller: Any) -> dict[str, Any]:
        allowed_models = [
            str(item).strip()
            for item in list(getattr(controller, "experiment_allowed_models", []) or [])
            if str(item).strip()
        ]
        if not allowed_models and str(getattr(controller, "model_name", "") or "").strip():
            allowed_models = [str(controller.model_name)]
        current_model = str(getattr(controller, "model_name", "") or "")
        return {
            "current_model": current_model,
            "selected_model": current_model,
            "selected_config": str(getattr(controller, "model_config_path", "") or ""),
            "candidate_models": list(allowed_models),
            "candidate_weights": {name: 1.0 if name == current_model else 0.0 for name in allowed_models},
            "regime": str(dict(getattr(controller, "last_routing_decision", {}) or {}).get("regime") or ""),
            "regime_confidence": 0.0,
            "regime_source": "validation_mode",
            "decision_confidence": 1.0,
            "decision_source": "validation_mode",
            "switch_applied": False,
            "hold_current": True,
            "hold_reason": "validation_mode_pinned",
            "reasoning": "validation mode pins the active model and disables routing/allocator",
            "guardrail_checks": [],
            "allocation_plan": {},
            "cash_reserve_hint": None,
            "routing_mode": "off",
            "allowed_models": list(allowed_models),
        }

    def sync_runtime_from_config(self, controller: Any) -> None:
        previous_model = controller.model_name
        previous_config_path = controller.model_config_path
        controller.model_name = str(
            getattr(config, "investment_model", controller.model_name) or controller.model_name
        )
        controller.model_config_path = str(
            getattr(config, "investment_model_config", controller.model_config_path)
            or controller.model_config_path
        )
        controller.allocator_enabled = bool(
            getattr(config, "allocator_enabled", controller.allocator_enabled)
        )
        controller.allocator_top_n = int(
            getattr(config, "allocator_top_n", controller.allocator_top_n)
            or controller.allocator_top_n
        )
        controller.model_routing_enabled = bool(
            getattr(config, "model_routing_enabled", controller.model_routing_enabled)
            or controller.allocator_enabled
        )
        controller.model_routing_mode = str(
            getattr(config, "model_routing_mode", controller.model_routing_mode)
            or controller.model_routing_mode
        ).strip().lower()
        controller.model_routing_allowed_models = [
            str(item).strip()
            for item in (
                getattr(
                    config,
                    "model_routing_allowed_models",
                    controller.model_routing_allowed_models,
                )
                or []
            )
            if str(item).strip()
        ]
        controller.model_switch_cooldown_cycles = int(
            getattr(
                config,
                "model_switch_cooldown_cycles",
                controller.model_switch_cooldown_cycles,
            )
            or controller.model_switch_cooldown_cycles
        )
        controller.model_switch_min_confidence = float(
            getattr(
                config,
                "model_switch_min_confidence",
                controller.model_switch_min_confidence,
            )
            or controller.model_switch_min_confidence
        )
        controller.model_switch_hysteresis_margin = float(
            getattr(
                config,
                "model_switch_hysteresis_margin",
                controller.model_switch_hysteresis_margin,
            )
            or controller.model_switch_hysteresis_margin
        )
        controller.model_routing_agent_override_enabled = bool(
            getattr(
                config,
                "model_routing_agent_override_enabled",
                controller.model_routing_agent_override_enabled,
            )
        )
        controller.model_routing_agent_override_max_gap = float(
            getattr(
                config,
                "model_routing_agent_override_max_gap",
                controller.model_routing_agent_override_max_gap,
            )
            or controller.model_routing_agent_override_max_gap
        )
        controller.model_routing_policy = dict(
            getattr(config, "model_routing_policy", controller.model_routing_policy) or {}
        )
        if self._validation_mode_enabled(controller):
            self._apply_validation_mode_constraints(controller)
        self.refresh_routing_coordinator(controller)
        if previous_model != controller.model_name or previous_config_path != controller.model_config_path:
            controller.current_params = {}
            self.reload_investment_model(controller, controller.model_config_path)

    def reload_investment_model(self, controller: Any, config_path: str | None = None) -> None:
        if config_path:
            controller.model_config_path = str(config_path)
        controller.investment_model = create_investment_model(
            controller.model_name,
            config_path=controller.model_config_path,
            runtime_overrides=controller.current_params,
        )
        controller._sync_runtime_policy_from_model()

    def build_routing_coordinator(self, owner: Any) -> ModelRoutingCoordinator:
        return ModelRoutingCoordinator(
            routing_policy=dict(getattr(owner, "model_routing_policy", {}) or {}),
            min_confidence=float(getattr(owner, "model_switch_min_confidence", 0.60) or 0.60),
            cooldown_cycles=int(getattr(owner, "model_switch_cooldown_cycles", 2) or 2),
            hysteresis_margin=float(getattr(owner, "model_switch_hysteresis_margin", 0.08) or 0.08),
            agent_override_max_gap=float(getattr(owner, "model_routing_agent_override_max_gap", 0.18) or 0.18),
        )

    def refresh_routing_coordinator(self, owner: Any) -> ModelRoutingCoordinator:
        coordinator = self.build_routing_coordinator(owner)
        owner.routing_coordinator = coordinator
        return coordinator

    def prepare_leaderboard(self, *, output_dir: str | Path | None, safe: bool = False) -> Path:
        leaderboard_root = Path(output_dir or OUTPUT_DIR)
        if leaderboard_root.name == "training":
            leaderboard_root = leaderboard_root.parent
        leaderboard_root.mkdir(parents=True, exist_ok=True)
        leaderboard_path = leaderboard_root / "leaderboard.json"
        if safe:
            try:
                write_leaderboard(leaderboard_root, leaderboard_path)
            except Exception:
                logger.debug("Leaderboard refresh failed", exc_info=True)
        else:
            write_leaderboard(leaderboard_root, leaderboard_path)
        return leaderboard_path

    def preview_routing(
        self,
        controller: Any,
        *,
        cutoff_date: str | None = None,
        stock_count: int | None = None,
        min_history_days: int | None = None,
        allowed_models: list[str] | None = None,
        sampling_policy: dict[str, Any] | None = None,
        sampling_seed: int | None = None,
        regime_only: bool = False,
    ) -> dict[str, Any]:
        preview_cutoff = normalize_date(cutoff_date or controller.data_manager.random_cutoff_date())
        preview_stock_count = max(1, int(stock_count or getattr(config, "max_stocks", 50) or 50))
        preview_min_history = max(30, int(min_history_days or getattr(config, "min_history_days", 200) or 200))
        stock_data = self._load_preview_stock_data(
            controller,
            cutoff_date=preview_cutoff,
            stock_count=preview_stock_count,
            min_history_days=preview_min_history,
            sampling_policy=sampling_policy,
            sampling_seed=sampling_seed,
        )
        if regime_only:
            return self.preview_market_regime(
                controller,
                cutoff_date=preview_cutoff,
                stock_data=stock_data,
            )
        decision = self.route_model(
            controller,
            stock_data=stock_data,
            cutoff_date=preview_cutoff,
            current_model=str(getattr(controller, "model_name", "momentum") or "momentum"),
            data_manager=controller.data_manager,
            output_dir=getattr(controller, "output_dir", OUTPUT_DIR),
            allowed_models=allowed_models,
            current_cycle_id=int(getattr(controller, "current_cycle_id", 0) or 0) + 1,
        )
        return decision.to_dict()

    def preview_market_regime(
        self,
        controller: Any,
        *,
        cutoff_date: str,
        stock_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        coordinator = getattr(controller, "routing_coordinator", None)
        if coordinator is None:
            coordinator = self.build_routing_coordinator(controller or config)
            controller.routing_coordinator = coordinator
        resolved_stock_data = dict(stock_data or {})
        observation = coordinator.observer.observe(
            resolved_stock_data,
            cutoff_date,
            data_manager=getattr(controller, "data_manager", None),
        )
        routing_mode = str(
            getattr(controller, "model_routing_mode", getattr(config, "model_routing_mode", "rule"))
            or "rule"
        ).strip().lower()
        agents = dict(getattr(controller, "agents", {}) or {})
        regime_payload = coordinator.classifier.classify(
            observation,
            agent=agents.get("market_regime") if routing_mode in {"hybrid", "agent"} else None,
            mode="hybrid" if routing_mode in {"hybrid", "agent"} else "rule",
        )
        return {
            "cutoff_date": cutoff_date,
            "regime": str(regime_payload.get("regime") or "unknown"),
            "regime_confidence": float(regime_payload.get("confidence", 0.0) or 0.0),
            "confidence": float(regime_payload.get("confidence", 0.0) or 0.0),
            "reasoning": str(regime_payload.get("reasoning") or ""),
            "regime_source": str(regime_payload.get("source") or "rule"),
            "decision_source": "regime_only_preview",
            "market_observation": observation.to_dict(),
        }

    def _load_preview_stock_data(
        self,
        controller: Any,
        *,
        cutoff_date: str,
        stock_count: int,
        min_history_days: int,
        sampling_policy: dict[str, Any] | None = None,
        sampling_seed: int | None = None,
    ) -> dict[str, Any]:
        normalized_policy = dict(sampling_policy or {})
        cache_key = (
            str(cutoff_date),
            int(stock_count),
            int(min_history_days),
            tuple(sorted((str(key), str(value)) for key, value in normalized_policy.items())),
            int(sampling_seed) if sampling_seed is not None else None,
        )
        cache = dict(getattr(controller, "_routing_preview_stock_data_cache", {}) or {})
        if cache_key in cache:
            return dict(cache[cache_key] or {})
        stock_data = controller.data_manager.load_stock_data(
            cutoff_date=cutoff_date,
            stock_count=stock_count,
            min_history_days=min_history_days,
            sampling_policy=normalized_policy or None,
            sampling_seed=sampling_seed,
        )
        cache[cache_key] = dict(stock_data or {})
        controller._routing_preview_stock_data_cache = cache
        return dict(stock_data or {})

    def apply_model_routing(
        self,
        controller: Any,
        *,
        stock_data: dict[str, Any],
        cutoff_date: str,
        cycle_id: int,
        event_emitter: Any,
    ) -> None:
        if self._validation_mode_enabled(controller):
            self._apply_validation_mode_constraints(controller)
            controller.last_routing_decision = self._build_validation_hold_decision(controller)
            controller.routing_history.append(dict(controller.last_routing_decision))
            controller.last_allocation_plan = {}
            return
        if not controller.model_routing_enabled or controller.model_routing_mode == "off":
            return
        controller._emit_agent_status(
            "ModelRouter",
            "running",
            "正在评估市场状态并为本轮训练选择投资模型...",
            cycle_id=cycle_id,
            stage="model_routing",
            progress_pct=22,
            step=2,
            total_steps=6,
        )
        event_emitter(
            "routing_started",
            {
                **controller._event_context(cycle_id),
                "current_model": controller.model_name,
                "routing_mode": controller.model_routing_mode,
            },
        )
        decision = self.route_model(
            controller,
            stock_data=stock_data,
            cutoff_date=cutoff_date,
            current_model=controller.model_name,
            data_manager=controller.data_manager,
            output_dir=controller.output_dir,
            allowed_models=controller.experiment_allowed_models or controller.model_routing_allowed_models,
            current_cycle_id=cycle_id,
        )
        controller.last_routing_decision = decision.to_dict()
        controller.routing_history.append(dict(controller.last_routing_decision))
        controller.last_allocation_plan = dict(decision.allocation_plan or {})
        event_emitter(
            "regime_classified",
            {
                **controller._event_context(cycle_id),
                "regime": decision.regime,
                "confidence": decision.regime_confidence,
                "source": decision.regime_source,
                "reasoning": (
                    decision.evidence.get("rule_result") or {}
                ).get("reasoning")
                or decision.reasoning,
            },
        )
        event_emitter(
            "routing_decided",
            {
                **controller._event_context(cycle_id),
                "current_model": decision.current_model,
                "selected_model": decision.selected_model,
                "selected_config": decision.selected_config,
                "candidate_models": decision.candidate_models,
                "candidate_weights": decision.candidate_weights,
                "regime": decision.regime,
                "regime_confidence": decision.regime_confidence,
                "decision_confidence": decision.decision_confidence,
                "decision_source": decision.decision_source,
                "switch_applied": decision.switch_applied,
                "hold_current": decision.hold_current,
                "hold_reason": decision.hold_reason,
                "reasoning": decision.reasoning,
                "guardrail_checks": decision.guardrail_checks,
            },
        )
        previous_model = controller.model_name
        if decision.switch_applied and decision.selected_model != controller.model_name:
            controller.current_params = {}
            controller.model_name = decision.selected_model
            controller.model_config_path = decision.selected_config
            self.reload_investment_model(controller, controller.model_config_path)
            controller.last_model_switch_cycle_id = cycle_id
            event_emitter(
                "model_switch_applied",
                {
                    **controller._event_context(cycle_id),
                    "from_model": previous_model,
                    "to_model": controller.model_name,
                    "reasoning": decision.reasoning,
                },
            )
        elif decision.hold_current and decision.selected_model == previous_model:
            event_emitter(
                "model_switch_blocked",
                {
                    **controller._event_context(cycle_id),
                    "current_model": previous_model,
                    "candidate_models": decision.candidate_models,
                    "hold_reason": decision.hold_reason,
                    "reasoning": decision.reasoning,
                },
            )
        controller._emit_agent_status(
            "ModelRouter",
            "completed",
            f"router 识别 {decision.regime} 市场，当前主模型 {controller.model_name}",
            cycle_id=cycle_id,
            stage="model_routing",
            progress_pct=24,
            step=2,
            total_steps=6,
            details=controller.last_routing_decision,
            thinking=controller._thinking_excerpt(decision.reasoning),
        )
        controller._emit_module_log(
            "model_routing",
            "模型路由完成",
            decision.reasoning,
            cycle_id=cycle_id,
            kind="routing_decision",
            details=controller.last_routing_decision,
            metrics={
                "switch_applied": decision.switch_applied,
                "hold_current": decision.hold_current,
                "cash_reserve_hint": decision.cash_reserve_hint,
                "decision_confidence": decision.decision_confidence,
            },
        )

    def route_model(
        self,
        owner: Any | None,
        *,
        stock_data: dict[str, Any],
        cutoff_date: str,
        current_model: str,
        data_manager: Any,
        output_dir: str | Path | None,
        allowed_models: list[str] | None = None,
        current_cycle_id: int | None = None,
        safe_leaderboard_refresh: bool = False,
    ) -> Any:
        if self._validation_mode_enabled(owner):
            self._apply_validation_mode_constraints(owner)
            coordinator = self.build_routing_coordinator(owner)
            owner.routing_coordinator = coordinator
        routing_enabled = bool(
            getattr(owner, "model_routing_enabled", getattr(config, "model_routing_enabled", True))
        )
        routing_mode = str(
            getattr(owner, "model_routing_mode", getattr(config, "model_routing_mode", "rule")) or "rule"
        ).strip().lower()
        allocator_top_n = int(getattr(owner, "allocator_top_n", getattr(config, "allocator_top_n", 3)) or 3)
        coordinator = getattr(owner, "routing_coordinator", None) if owner is not None else None
        if coordinator is None:
            coordinator = self.build_routing_coordinator(owner or config)
            if owner is not None:
                owner.routing_coordinator = coordinator
        agents = dict(getattr(owner, "agents", {}) or {}) if owner is not None else {}
        selector_enabled = bool(
            getattr(
                owner,
                "model_routing_agent_override_enabled",
                getattr(config, "model_routing_agent_override_enabled", False),
            )
        )
        decision = coordinator.route(
            stock_data=stock_data,
            cutoff_date=cutoff_date,
            current_model=current_model,
            leaderboard_path=self.prepare_leaderboard(
                output_dir=output_dir,
                safe=(safe_leaderboard_refresh or owner is None),
            ),
            allocator_top_n=allocator_top_n,
            allowed_models=self._resolve_allowed_models(owner, allowed_models) or None,
            routing_mode=routing_mode if routing_enabled else "off",
            regime_agent=agents.get("market_regime") if routing_mode in {"hybrid", "agent"} else None,
            selector_agent=(
                agents.get("model_selector")
                if selector_enabled and routing_mode in {"hybrid", "agent"}
                else None
            ),
            previous_decision=dict(getattr(owner, "last_routing_decision", {}) or {}),
            current_cycle_id=current_cycle_id,
            last_switch_cycle_id=getattr(owner, "last_model_switch_cycle_id", None),
            data_manager=data_manager,
        )
        return decision

    def _resolve_allowed_models(self, owner: Any | None, override: list[str] | None) -> list[str]:
        candidates = (
            override
            or getattr(owner, "experiment_allowed_models", None)
            or getattr(owner, "model_routing_allowed_models", None)
            or getattr(config, "model_routing_allowed_models", None)
            or []
        )
        return [str(item).strip() for item in candidates if str(item).strip()]
