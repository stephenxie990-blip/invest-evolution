from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from config import OUTPUT_DIR, config, normalize_date
from invest.leaderboard import write_leaderboard
from invest.router import ModelRoutingCoordinator

logger = logging.getLogger(__name__)


class TrainingRoutingService:
    """Coordinates leaderboard refresh and model-routing decisions."""

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
    ) -> dict[str, Any]:
        preview_cutoff = normalize_date(cutoff_date or controller.data_manager.random_cutoff_date())
        preview_stock_count = max(1, int(stock_count or getattr(config, "max_stocks", 50) or 50))
        preview_min_history = max(30, int(min_history_days or getattr(config, "min_history_days", 200) or 200))
        stock_data = controller.data_manager.load_stock_data(
            cutoff_date=preview_cutoff,
            stock_count=preview_stock_count,
            min_history_days=preview_min_history,
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
