from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List

from invest_evolution.investment.contracts import (
    ManagerOutput,
    ManagerPlan,
    ManagerPlanPosition,
    ManagerRunContext,
    ManagerSpec,
    PortfolioPlan,
    PortfolioPlanPosition,
    resolve_agent_context_confidence,
)
from invest_evolution.investment.foundation.risk import (
    clamp_position_size,
    clamp_stop_loss_pct,
    clamp_take_profit_pct,
)
from invest_evolution.investment.runtimes.ops import ScoringService, ScreeningService


@dataclass
class PortfolioAssemblyConfig:
    max_positions: int = 8
    max_single_position: float = 0.25


class RiskCheckService:
    """Applies reusable single-position and plan-level clamps."""

    def sanitize_manager_plan(
        self,
        manager_plan: ManagerPlan,
        *,
        max_single_position: float | None = None,
    ) -> ManagerPlan:
        exposure_target = max(0.0, 1.0 - float(manager_plan.cash_reserve or 0.0))
        positions = [
            self._sanitize_manager_position(position, max_single_position=max_single_position)
            for position in list(manager_plan.positions or [])
        ]
        positions = self._normalize_manager_positions(
            positions,
            exposure_target=exposure_target,
            max_positions=manager_plan.max_positions or len(positions),
        )
        return replace(
            manager_plan,
            positions=positions,
            max_positions=manager_plan.max_positions or len(positions),
        )

    def sanitize_portfolio_positions(
        self,
        positions: List[PortfolioPlanPosition],
        *,
        exposure_target: float,
        max_single_position: float | None = None,
        max_positions: int | None = None,
    ) -> List[PortfolioPlanPosition]:
        clean = [
            self._sanitize_portfolio_position(position, max_single_position=max_single_position)
            for position in list(positions or [])
        ]
        ordered = sorted(clean, key=lambda item: item.target_weight, reverse=True)
        if max_positions and max_positions > 0:
            ordered = ordered[:max_positions]
        total = sum(item.target_weight for item in ordered)
        scale = exposure_target / total if total > 0 and exposure_target >= 0 else 0.0
        normalized: List[PortfolioPlanPosition] = []
        for index, position in enumerate(ordered, start=1):
            normalized.append(
                replace(
                    position,
                    rank=index,
                    target_weight=round(max(0.0, position.target_weight * scale), 8),
                )
            )
        return normalized

    def _sanitize_manager_position(
        self,
        position: ManagerPlanPosition,
        *,
        max_single_position: float | None = None,
    ) -> ManagerPlanPosition:
        upper = max_single_position if max_single_position is not None else 0.30
        return replace(
            position,
            target_weight=clamp_position_size(position.target_weight, upper=upper),
            stop_loss_pct=clamp_stop_loss_pct(position.stop_loss_pct),
            take_profit_pct=clamp_take_profit_pct(position.take_profit_pct),
        )

    def _sanitize_portfolio_position(
        self,
        position: PortfolioPlanPosition,
        *,
        max_single_position: float | None = None,
    ) -> PortfolioPlanPosition:
        upper = max_single_position if max_single_position is not None else 0.30
        return replace(
            position,
            target_weight=clamp_position_size(position.target_weight, upper=upper),
            stop_loss_pct=clamp_stop_loss_pct(position.stop_loss_pct),
            take_profit_pct=clamp_take_profit_pct(position.take_profit_pct),
        )

    @staticmethod
    def _normalize_manager_positions(
        positions: List[ManagerPlanPosition],
        *,
        exposure_target: float,
        max_positions: int,
    ) -> List[ManagerPlanPosition]:
        ordered = sorted(positions, key=lambda item: item.target_weight, reverse=True)
        if max_positions > 0:
            ordered = ordered[:max_positions]
        total = sum(item.target_weight for item in ordered)
        scale = exposure_target / total if total > 0 and exposure_target >= 0 else 0.0
        normalized: List[ManagerPlanPosition] = []
        for index, position in enumerate(ordered, start=1):
            normalized.append(
                replace(
                    position,
                    rank=index,
                    target_weight=round(max(0.0, position.target_weight * scale), 8),
                )
            )
        return normalized


class PlanAssemblyService:
    """Transforms manager output into a manager-owned plan contract."""

    def __init__(
        self,
        *,
        screening_service: ScreeningService | None = None,
        scoring_service: ScoringService | None = None,
        risk_check_service: RiskCheckService | None = None,
    ) -> None:
        self.screening_service = screening_service or ScreeningService()
        self.scoring_service = scoring_service or ScoringService()
        self.risk_check_service = risk_check_service or RiskCheckService()

    def build_manager_plan(
        self,
        *,
        manager_spec: ManagerSpec,
        manager_output: ManagerOutput,
        run_context: ManagerRunContext,
    ) -> ManagerPlan:
        signal_packet = manager_output.signal_packet
        params = dict(signal_packet.params or {})
        exposure_target = max(0.0, 1.0 - float(signal_packet.cash_reserve or 0.0))
        default_max_positions = int(signal_packet.max_positions or params.get("max_positions") or 0)
        selected_signals = self.screening_service.select_signals(
            signal_packet,
            top_n=default_max_positions or None,
        )
        normalized_weights = self.scoring_service.normalize_signal_weights(
            selected_signals,
            total_exposure=exposure_target,
        )
        max_hold_days = int(params.get("max_hold_days") or 30)
        positions: List[ManagerPlanPosition] = []
        for index, signal in enumerate(selected_signals, start=1):
            thesis = "; ".join(signal.evidence[:2]).strip()
            if not thesis:
                thesis = f"{manager_spec.display_name} score={float(signal.score):.2f}"
            positions.append(
                ManagerPlanPosition(
                    code=signal.code,
                    rank=index,
                    target_weight=normalized_weights.get(signal.code, 0.0),
                    score=float(signal.score or 0.0),
                    entry_method="market",
                    entry_price=None,
                    stop_loss_pct=float(signal.stop_loss_pct or params.get("stop_loss_pct") or 0.05),
                    take_profit_pct=float(signal.take_profit_pct or params.get("take_profit_pct") or 0.15),
                    trailing_pct=signal.trailing_pct if signal.trailing_pct is not None else params.get("trailing_pct"),
                    max_hold_days=max_hold_days,
                    thesis=thesis,
                    evidence=[str(item) for item in list(signal.evidence or []) if str(item).strip()],
                    metadata={
                        "factor_values": dict(signal.factor_values or {}),
                        "signal_metadata": dict(signal.metadata or {}),
                    },
                )
            )
        plan = ManagerPlan(
            manager_id=manager_spec.manager_id,
            manager_name=manager_spec.display_name,
            as_of_date=run_context.as_of_date,
            regime=run_context.regime,
            positions=positions,
            cash_reserve=float(signal_packet.cash_reserve or 0.0),
            max_positions=default_max_positions or len(positions),
            budget_weight=self._resolve_budget_weight(manager_spec, run_context),
            confidence=resolve_agent_context_confidence(manager_output.agent_context, default=0.65),
            reasoning=str(signal_packet.reasoning or manager_output.agent_context.summary or "").strip(),
            source_manager_id=manager_output.manager_id,
            source_manager_config_ref=manager_output.manager_config_ref,
            evidence=self._build_evidence(signal_packet.context.market_stats, manager_spec, run_context, manager_output),
            metadata={"mandate": manager_spec.mandate},
        )
        return self.risk_check_service.sanitize_manager_plan(
            plan,
            max_single_position=float(manager_spec.risk_profile.get("max_single_position", 0.30) or 0.30),
        )

    @staticmethod
    def _resolve_budget_weight(manager_spec: ManagerSpec, run_context: ManagerRunContext) -> float:
        if manager_spec.manager_id in run_context.budget_weights:
            return float(run_context.budget_weights[manager_spec.manager_id])
        active_count = max(1, len(run_context.active_manager_ids or []))
        return round(1.0 / active_count, 8)

    @staticmethod
    def _build_evidence(
        market_stats: Dict[str, float],
        manager_spec: ManagerSpec,
        run_context: ManagerRunContext,
        manager_output: ManagerOutput,
    ) -> Dict[str, object]:
        return {
            "manager_id": manager_spec.manager_id,
            "style_profile": dict(manager_spec.style_profile or {}),
            "market_stats": dict(market_stats or {}),
            "governance_context": dict(run_context.governance_context or {}),
            "candidate_codes": list(manager_output.signal_packet.top_codes(limit=None)),
        }


@dataclass
class _MergedPositionState:
    weight: float = 0.0
    manager_weights: Dict[str, float] = field(default_factory=dict)
    source_managers: List[str] = field(default_factory=list)
    stop_losses: List[float] = field(default_factory=list)
    take_profits: List[float] = field(default_factory=list)
    trailing: List[float] = field(default_factory=list)
    max_hold_days: List[int] = field(default_factory=list)
    theses: List[str] = field(default_factory=list)


class PortfolioAssembler:
    """Combines manager plans into a single portfolio plan."""

    def __init__(
        self,
        *,
        config: PortfolioAssemblyConfig | None = None,
        risk_check_service: RiskCheckService | None = None,
    ) -> None:
        self.config = config or PortfolioAssemblyConfig()
        self.risk_check_service = risk_check_service or RiskCheckService()

    def assemble(
        self,
        manager_plans: Iterable[ManagerPlan],
        *,
        manager_weights: Dict[str, float] | None = None,
        regime: str | None = None,
        as_of_date: str | None = None,
    ) -> PortfolioPlan:
        plans = [plan for plan in list(manager_plans or []) if plan.positions]
        if not plans:
            return PortfolioPlan(
                as_of_date=str(as_of_date or ""),
                regime=str(regime or "unknown"),
                positions=[],
                cash_reserve=1.0,
                active_manager_ids=[],
                manager_weights={},
                confidence=0.0,
                reasoning="no_active_manager_plans",
                metadata={"source_plan_count": 0},
            )
        weights = self._normalize_manager_weights(plans, manager_weights)
        merged: Dict[str, _MergedPositionState] = {}
        duplicate_count = 0
        weighted_cash_reserve = 0.0
        weighted_confidence = 0.0
        for plan in plans:
            plan_budget = weights.get(plan.manager_id, 0.0)
            weighted_cash_reserve += plan_budget * float(plan.cash_reserve or 0.0)
            weighted_confidence += plan_budget * float(plan.confidence or 0.0)
            for position in list(plan.positions or []):
                contribution = float(position.target_weight or 0.0) * plan_budget
                if contribution <= 0:
                    continue
                record = merged.setdefault(position.code, _MergedPositionState())
                if record.manager_weights:
                    duplicate_count += 1
                record.weight = float(record.weight) + contribution
                manager_weight_map = dict(record.manager_weights)
                manager_weight_map[plan.manager_id] = round(
                    manager_weight_map.get(plan.manager_id, 0.0) + contribution,
                    8,
                )
                record.manager_weights = manager_weight_map
                record.source_managers = sorted(manager_weight_map.keys())
                record.stop_losses.append(float(position.stop_loss_pct or 0.0))
                record.take_profits.append(float(position.take_profit_pct or 0.0))
                if position.trailing_pct is not None:
                    record.trailing.append(float(position.trailing_pct))
                record.max_hold_days.append(int(position.max_hold_days or 30))
                if position.thesis:
                    record.theses.append(position.thesis)
        portfolio_positions: List[PortfolioPlanPosition] = []
        for code, payload in merged.items():
            theses = [item for item in list(payload.theses) if str(item).strip()]
            thesis = " | ".join(theses[:2])
            portfolio_positions.append(
                PortfolioPlanPosition(
                    code=code,
                    target_weight=float(payload.weight),
                    source_managers=list(payload.source_managers),
                    manager_weights=dict(payload.manager_weights),
                    stop_loss_pct=min(list(payload.stop_losses) or [0.05]),
                    take_profit_pct=sum(list(payload.take_profits) or [0.15]) / max(len(list(payload.take_profits) or [1]), 1),
                    trailing_pct=(
                        sum(list(payload.trailing)) / len(list(payload.trailing))
                        if list(payload.trailing)
                        else None
                    ),
                    max_hold_days=max(list(payload.max_hold_days) or [30]),
                    thesis=thesis,
                    metadata={"duplicate_sources": len(list(payload.source_managers)) > 1},
                )
            )
        cash_reserve = max(0.0, min(1.0, round(weighted_cash_reserve, 8)))
        clean_positions = self.risk_check_service.sanitize_portfolio_positions(
            sorted(portfolio_positions, key=lambda item: item.target_weight, reverse=True),
            exposure_target=max(0.0, 1.0 - cash_reserve),
            max_single_position=self.config.max_single_position,
            max_positions=self.config.max_positions,
        )
        return PortfolioPlan(
            as_of_date=as_of_date or plans[0].as_of_date,
            regime=regime or plans[0].regime,
            positions=clean_positions,
            cash_reserve=cash_reserve,
            active_manager_ids=list(weights.keys()),
            manager_weights=weights,
            confidence=round(weighted_confidence, 8),
            reasoning=(
                f"assembled {len(clean_positions)} holdings from "
                f"{len(plans)} manager plans"
            ),
            metadata={
                "source_plan_count": len(plans),
                "duplicate_holdings": duplicate_count,
            },
        )

    @staticmethod
    def _normalize_manager_weights(
        plans: List[ManagerPlan],
        manager_weights: Dict[str, float] | None = None,
    ) -> Dict[str, float]:
        raw = {
            plan.manager_id: float(
                dict(manager_weights or {}).get(plan.manager_id, plan.budget_weight or 0.0)
            )
            for plan in plans
        }
        total = sum(max(0.0, value) for value in raw.values())
        if total <= 0:
            equal = round(1.0 / max(len(plans), 1), 8)
            return {plan.manager_id: equal for plan in plans}
        return {
            key: round(max(0.0, value) / total, 8)
            for key, value in raw.items()
        }

__all__ = [
    'PortfolioAssemblyConfig',
    'RiskCheckService',
    'PlanAssemblyService',
    'PortfolioAssembler',
]
