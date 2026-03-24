"""Shared public contracts and runtime helpers for the invest domain."""

from .contracts import PositionPlan, TradingPlan, make_simple_plan
from ..foundation.compute import compute_market_stats, summarize_stocks
from .llm import LLMCaller, parse_llm_json_object
from .policy import (
    AgentTracker,
    CognitiveAssistService,
    MemoryRetrievalService,
    PredictionRecord,
    TraceLog,
    deep_merge,
    format_stock_table,
    get_manager_style_profile,
    manager_regime_compatibility,
    normalize_config_ref,
    normalize_freeze_gate_policy,
    normalize_promotion_gate_policy,
    resolve_governance_matrix,
)

__all__ = [
    'PositionPlan',
    'TradingPlan',
    'make_simple_plan',
    'LLMCaller',
    'parse_llm_json_object',
    'CognitiveAssistService',
    'MemoryRetrievalService',
    'PredictionRecord',
    'AgentTracker',
    'TraceLog',
    'deep_merge',
    'resolve_governance_matrix',
    'normalize_promotion_gate_policy',
    'normalize_freeze_gate_policy',
    'normalize_config_ref',
    'get_manager_style_profile',
    'manager_regime_compatibility',
    'format_stock_table',
    'compute_market_stats',
    'summarize_stocks',
]
