from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from invest_evolution.investment.contracts import ManagerOutput, ManagerPlan, ManagerRunContext, ManagerSpec
from invest_evolution.investment.governance.planning import PlanAssemblyService
from invest_evolution.investment.runtimes import create_manager_runtime, resolve_manager_runtime_config_ref

# Manager base

from invest_evolution.investment.runtimes.base import ManagerRuntime


@dataclass
class ManagerExecutionArtifacts:
    spec: ManagerSpec
    run_context: ManagerRunContext
    manager_output: ManagerOutput
    manager_plan: ManagerPlan


class ManagerAgent(ABC):
    """Manager-level runtime owner that orchestrates shared capabilities."""

    def __init__(
        self,
        *,
        spec: ManagerSpec,
        runtime: ManagerRuntime,
        plan_assembly_service: PlanAssemblyService | None = None,
    ) -> None:
        self.spec = spec
        self.runtime = runtime
        self.plan_assembly_service = plan_assembly_service or PlanAssemblyService()

    @abstractmethod
    def generate_manager_output(
        self,
        stock_data: Dict[str, Any],
        run_context: ManagerRunContext,
    ) -> ManagerOutput:
        raise NotImplementedError

    def run(
        self,
        stock_data: Dict[str, Any],
        run_context: ManagerRunContext,
    ) -> ManagerExecutionArtifacts:
        self.runtime.update_runtime_overrides(dict(run_context.runtime_params or {}))
        manager_output = self.generate_manager_output(stock_data, run_context)
        manager_plan = self.plan_assembly_service.build_manager_plan(
            manager_spec=self.spec,
            manager_output=manager_output,
            run_context=run_context,
        )
        return ManagerExecutionArtifacts(
            spec=self.spec,
            run_context=run_context,
            manager_output=manager_output,
            manager_plan=manager_plan,
        )


class RuntimeBackedManager(ManagerAgent):
    """Thin manager adapter around the existing runtime implementations."""

    def generate_manager_output(
        self,
        stock_data: Dict[str, Any],
        run_context: ManagerRunContext,
    ) -> ManagerOutput:
        return self.runtime.process(stock_data, run_context.as_of_date)


# Manager registry

DEFAULT_MANAGER_CAPABILITIES = [
    "screening",
    "scoring",
    "risk_check",
    "plan_assembly",
    "simulation",
    "attribution",
    "memory_retrieval",
    "cognitive_assist",
]


def _default_specs() -> List[ManagerSpec]:
    return [
        ManagerSpec(
            manager_id="momentum",
            runtime_id="momentum",
            display_name="Momentum Manager",
            runtime_config_ref=str(resolve_manager_runtime_config_ref("momentum")),
            mandate="Capture persistent trend continuation in stronger tapes.",
            style_profile={"bull": 1.0, "oscillation": 0.35, "bear": 0.1},
            factor_profile={"trend": 0.9, "breadth": 0.7},
            risk_profile={"max_single_position": 0.28},
            capability_allowlist=DEFAULT_MANAGER_CAPABILITIES,
        ),
        ManagerSpec(
            manager_id="mean_reversion",
            runtime_id="mean_reversion",
            display_name="Mean Reversion Manager",
            runtime_config_ref=str(resolve_manager_runtime_config_ref("mean_reversion")),
            mandate="Exploit oversold snap-back opportunities only after reversal confirmation appears.",
            style_profile={"bull": 0.35, "oscillation": 0.58, "bear": 0.25},
            factor_profile={"reversion": 0.95, "oversold": 0.8},
            risk_profile={"max_single_position": 0.24},
            capability_allowlist=DEFAULT_MANAGER_CAPABILITIES,
        ),
        ManagerSpec(
            manager_id="value_quality",
            runtime_id="value_quality",
            display_name="Value Quality Manager",
            runtime_config_ref=str(resolve_manager_runtime_config_ref("value_quality")),
            mandate="Prefer durable balance sheets and valuation support.",
            style_profile={"bull": 0.55, "oscillation": 0.84, "bear": 0.85},
            factor_profile={"quality": 0.9, "value": 0.9},
            risk_profile={"max_single_position": 0.22},
            capability_allowlist=DEFAULT_MANAGER_CAPABILITIES,
        ),
        ManagerSpec(
            manager_id="defensive_low_vol",
            runtime_id="defensive_low_vol",
            display_name="Defensive Manager",
            runtime_config_ref=str(resolve_manager_runtime_config_ref("defensive_low_vol")),
            mandate="Protect capital and lead allocation in weak or range-bound environments.",
            style_profile={"bull": 0.25, "oscillation": 0.88, "bear": 1.0},
            factor_profile={"defensive": 0.95, "low_vol": 0.9},
            risk_profile={"max_single_position": 0.20},
            capability_allowlist=DEFAULT_MANAGER_CAPABILITIES,
        ),
    ]


def resolve_manager_config_ref(manager_id: str) -> str:
    registry = ManagerRegistry()
    return str(registry.resolve(manager_id).runtime_config_ref or resolve_manager_runtime_config_ref(manager_id))


def looks_like_manager_config_ref(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    path = Path(text).expanduser()
    return (
        path.is_absolute()
        or path.suffix.lower() in {".yaml", ".yml", ".json"}
        or any(separator in text for separator in ("/", "\\"))
    )


def normalize_manager_config_ref(
    value: Any,
    *,
    preserve_bare_filename: bool = True,
) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not looks_like_manager_config_ref(text):
        return text
    if preserve_bare_filename and "/" not in text and "\\" not in text:
        return text
    try:
        return str(Path(text).expanduser().resolve(strict=False))
    except Exception:
        return text


@lru_cache(maxsize=1)
def _known_manager_ids_for_canonicalization() -> tuple[str, ...]:
    return tuple(
        sorted(
            (
                str(manager_id).strip()
                for manager_id in ManagerRegistry().list_manager_ids()
                if str(manager_id).strip()
            ),
            key=len,
            reverse=True,
        )
    )


def _infer_manager_id_from_config_ref(manager_config_ref: Any) -> str:
    text = str(manager_config_ref or "").strip()
    if not text:
        return ""
    path = Path(text)
    probes = (
        text.lower(),
        path.name.lower(),
        path.stem.lower(),
    )
    for manager_id in _known_manager_ids_for_canonicalization():
        normalized_manager_id = manager_id.lower()
        if any(normalized_manager_id in probe for probe in probes):
            return manager_id
    return ""


def canonical_manager_config_ref(
    manager_id: Any,
    manager_config_ref: Any = None,
    *,
    fallback: Any = None,
) -> str:
    normalized_manager_id = str(manager_id or "").strip()
    direct_ref = str(manager_config_ref or "").strip()
    fallback_ref = str(fallback or "").strip()
    candidate_ref = direct_ref or fallback_ref
    if not normalized_manager_id:
        return normalize_manager_config_ref(candidate_ref)
    if candidate_ref and looks_like_manager_config_ref(candidate_ref):
        inferred_manager_id = _infer_manager_id_from_config_ref(candidate_ref)
        if inferred_manager_id and inferred_manager_id != normalized_manager_id:
            return normalize_manager_config_ref(
                resolve_manager_config_ref(normalized_manager_id)
            )
        return normalize_manager_config_ref(candidate_ref)
    return normalize_manager_config_ref(resolve_manager_config_ref(normalized_manager_id))


class ManagerRegistry:
    """Registry for manager specs and their runtime constructors."""

    def __init__(
        self,
        *,
        specs: Iterable[ManagerSpec] | None = None,
        builder: Callable[[ManagerSpec, Optional[Dict[str, object]]], ManagerAgent] | None = None,
    ) -> None:
        self._specs: Dict[str, ManagerSpec] = {}
        self._builder = builder
        for spec in list(specs or _default_specs()):
            self.register(spec)

    def register(self, spec: ManagerSpec) -> None:
        self._specs[spec.manager_id] = spec

    def resolve(self, manager_id: str) -> ManagerSpec:
        key = str(manager_id or "").strip()
        if key not in self._specs:
            raise KeyError(f"unknown manager_id: {manager_id}")
        return self._specs[key]

    def list_manager_ids(self) -> List[str]:
        return list(self._specs.keys())

    def list_specs(self, manager_ids: Iterable[str] | None = None) -> List[ManagerSpec]:
        if manager_ids is None:
            return list(self._specs.values())
        return [self.resolve(manager_id) for manager_id in manager_ids]

    def build_manager(
        self,
        manager_id: str,
        *,
        runtime_overrides: Optional[Dict[str, object]] = None,
    ) -> ManagerAgent:
        spec = self.resolve(manager_id)
        if self._builder is not None:
            return self._builder(spec, runtime_overrides)
        runtime = create_manager_runtime(
            spec.runtime_id,
            runtime_config_ref=spec.runtime_config_ref or None,
            runtime_overrides=runtime_overrides,
        )
        setattr(runtime, "manager_id", spec.manager_id)
        setattr(runtime, "manager_config_ref", spec.runtime_config_ref or "")
        return RuntimeBackedManager(
            spec=spec,
            runtime=runtime,
            plan_assembly_service=PlanAssemblyService(),
        )


def build_default_manager_registry(
    *,
    manager_ids: Iterable[str] | None = None,
) -> ManagerRegistry:
    registry = ManagerRegistry()
    if manager_ids is None:
        return registry
    selected_specs = registry.list_specs(manager_ids)
    return ManagerRegistry(specs=selected_specs)

__all__ = [
    'ManagerExecutionArtifacts',
    'ManagerAgent',
    'RuntimeBackedManager',
    'DEFAULT_MANAGER_CAPABILITIES',
    'looks_like_manager_config_ref',
    'normalize_manager_config_ref',
    'canonical_manager_config_ref',
    'resolve_manager_config_ref',
    'ManagerRegistry',
    'build_default_manager_registry',
]
