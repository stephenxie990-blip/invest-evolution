from importlib import import_module
from types import ModuleType

from .core import (
    AgentContext,
    AllocationPlan,
    ManagerAttribution,
    ManagerOutput,
    ManagerPlan,
    ManagerPlanPosition,
    ManagerResult,
    ManagerRunContext,
    ManagerSpec,
    PortfolioPlan,
    PortfolioPlanPosition,
    PositionSnapshot,
    TradeRecordContract,
    resolve_agent_context_confidence,
)
from .reports import (
    AllocationReviewReport,
    EvalReport,
    GovernanceDecision,
    ManagerReviewReport,
    SignalPacket,
    SignalPacketContext,
    StockSignal,
    StockSummaryView,
    StrategyAdvice,
)

PACKAGE_MODULES = {
    "core": "invest_evolution.investment.contracts.core",
    "reports": "invest_evolution.investment.contracts.reports",
}


def load_package(name: str) -> ModuleType:
    return import_module(PACKAGE_MODULES[name])


def __getattr__(name: str):
    if name in PACKAGE_MODULES:
        module = load_package(name)
        globals()[name] = module
        return module
    for package_name in ("core", "reports"):
        module = load_package(package_name)
        if hasattr(module, name):
            value = getattr(module, name)
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    exported = set(globals()) | set(PACKAGE_MODULES)
    for package_name in ("core", "reports"):
        exported.update(dir(load_package(package_name)))
    return sorted(exported)

__all__ = [
    'AgentContext',
    'resolve_agent_context_confidence',
    'ManagerSpec',
    'ManagerRunContext',
    'ManagerPlanPosition',
    'ManagerPlan',
    'ManagerOutput',
    'ManagerAttribution',
    'ManagerResult',
    'PortfolioPlanPosition',
    'PortfolioPlan',
    'AllocationPlan',
    'PositionSnapshot',
    'TradeRecordContract',
    'ManagerReviewReport',
    'AllocationReviewReport',
    'EvalReport',
    'GovernanceDecision',
    'StockSummaryView',
    'StockSignal',
    'SignalPacketContext',
    'SignalPacket',
    'StrategyAdvice',
    'core',
    'reports',
    'PACKAGE_MODULES',
    'load_package',
]
