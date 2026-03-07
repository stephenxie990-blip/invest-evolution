"""投资进化系统公共导出（懒加载）与可选 legacy alias。"""

from __future__ import annotations

import importlib
import os
import sys
import types
from typing import Dict, Tuple

_EXPORTS: Dict[str, Tuple[str, str]] = {
    "config": ("config", "config"),
    "EvolutionConfig": ("config", "EvolutionConfig"),
    "PROJECT_ROOT": ("config", "PROJECT_ROOT"),
    "LLMCaller": ("core", "LLMCaller"),
    "TradingPlan": ("core", "TradingPlan"),
    "PositionPlan": ("core", "PositionPlan"),
    "make_simple_plan": ("core", "make_simple_plan"),
    "compute_market_stats": ("core", "compute_market_stats"),
    "summarize_stocks": ("core", "summarize_stocks"),
    "AgentTracker": ("core", "AgentTracker"),
    "MarketRegimeAgent": ("agents", "MarketRegimeAgent"),
    "TrendHunterAgent": ("agents", "TrendHunterAgent"),
    "ContrarianAgent": ("agents", "ContrarianAgent"),
    "CommanderAgent": ("agents", "CommanderAgent"),
    "StrategistAgent": ("agents", "StrategistAgent"),
    "EvoJudgeAgent": ("agents", "EvoJudgeAgent"),
    "SelectionMeeting": ("meetings", "SelectionMeeting"),
    "ReviewMeeting": ("meetings", "ReviewMeeting"),
    "MeetingRecorder": ("meetings", "MeetingRecorder"),
    "SimulatedTrader": ("trading", "SimulatedTrader"),
    "EmergencyDetector": ("trading", "EmergencyDetector"),
    "EmergencyType": ("trading", "EmergencyType"),
    "EmergencyEvent": ("trading", "EmergencyEvent"),
    "RiskController": ("trading", "RiskController"),
    "DataManager": ("data", "DataManager"),
    "StrategyEvaluator": ("evaluation", "StrategyEvaluator"),
    "BenchmarkEvaluator": ("evaluation", "BenchmarkEvaluator"),
    "PerformanceAnalyzer": ("evaluation", "PerformanceAnalyzer"),
}

__all__ = list(_EXPORTS.keys())


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if not target:
        raise AttributeError(name)
    mod_name, attr_name = target
    mod = importlib.import_module(mod_name)
    value = getattr(mod, attr_name)
    globals()[name] = value
    return value


def install_legacy_aliases() -> None:
    """Optional: provide src.* aliases for old imports."""
    src_pkg = sys.modules.setdefault("src", types.ModuleType("src"))
    if not hasattr(src_pkg, "__path__"):
        src_pkg.__path__ = []

    module_names = [
        "agents", "brain_runtime", "brain_scheduler", "brain_tools",
        "commander", "config", "core", "data", "evaluation",
        "meetings", "optimization", "trading", "train",
    ]
    for mod_name in module_names:
        mod = importlib.import_module(mod_name)
        sys.modules.setdefault(f"src.{mod_name}", mod)


if os.environ.get("INVEST_ENABLE_LEGACY_SRC", "0") == "1":
    install_legacy_aliases()
