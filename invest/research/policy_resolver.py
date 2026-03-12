from __future__ import annotations

from typing import Any, Dict

from invest.models.base import InvestmentModel
from .contracts import PolicySnapshot, RESEARCH_CONTRACT_VERSION, RESEARCH_FEATURE_VERSION, stable_hash


DEFAULT_DATA_WINDOW = {
    "lookback_days": 120,
    "simulation_days": 30,
    "universe_definition": "max_stocks=50|min_history_days=60",
}


def build_policy_signature(
    *,
    investment_model: InvestmentModel,
    routing_context: Dict[str, Any] | None = None,
    data_window: Dict[str, Any] | None = None,
    feature_version: str = RESEARCH_FEATURE_VERSION,
    code_contract_version: str = RESEARCH_CONTRACT_VERSION,
) -> Dict[str, Any]:
    return {
        "model_name": str(getattr(investment_model, "model_name", "unknown") or "unknown"),
        "config_name": str(getattr(getattr(investment_model, "config", None), "name", "unknown") or "unknown"),
        "params": dict(investment_model.effective_params() or {}),
        "risk_policy": dict(investment_model.config_section("risk_policy", {}) or {}),
        "execution_policy": dict(investment_model.config_section("execution", {}) or {}),
        "evaluation_policy": dict(investment_model.config_section("evaluation_policy", {}) or {}),
        "review_policy": dict(investment_model.config_section("review_policy", {}) or {}),
        "agent_weights": dict(investment_model.config_section("agent_weights", {}) or {}),
        "routing_context": dict(routing_context or {}),
        "data_window": dict(DEFAULT_DATA_WINDOW | dict(data_window or {})),
        "feature_version": str(feature_version),
        "code_contract_version": str(code_contract_version),
    }


def resolve_policy_snapshot(
    *,
    investment_model: InvestmentModel,
    routing_context: Dict[str, Any] | None = None,
    data_window: Dict[str, Any] | None = None,
    metadata: Dict[str, Any] | None = None,
) -> PolicySnapshot:
    signature = build_policy_signature(
        investment_model=investment_model,
        routing_context=routing_context,
        data_window=data_window,
    )
    version_hash = stable_hash(signature)
    return PolicySnapshot(
        policy_id=f"policy_{version_hash[:16]}",
        model_name=str(signature["model_name"]),
        config_name=str(signature["config_name"]),
        params=dict(signature["params"]),
        risk_policy=dict(signature["risk_policy"]),
        execution_policy=dict(signature["execution_policy"]),
        evaluation_policy=dict(signature["evaluation_policy"]),
        review_policy=dict(signature["review_policy"]),
        agent_weights=dict(signature["agent_weights"]),
        routing_context=dict(signature["routing_context"]),
        feature_version=str(signature["feature_version"]),
        data_window=dict(signature["data_window"]),
        code_contract_version=str(signature["code_contract_version"]),
        version_hash=version_hash,
        signature=signature,
        metadata=dict(metadata or {}),
    )
