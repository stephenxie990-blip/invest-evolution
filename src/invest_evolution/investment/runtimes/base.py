from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml

from invest_evolution.agent_runtime.runtime import enforce_path_within_root
from invest_evolution.config import PROJECT_ROOT
from invest_evolution.investment.contracts import (
    AgentContext,
    ManagerOutput,
    SignalPacket,
    StockSummaryView,
)
from .candidate_universe import build_candidate_universe
from .catalog import (
    COMMON_BENCHMARK_DEFAULTS,
    COMMON_EXECUTION_DEFAULTS,
    COMMON_PARAM_DEFAULTS,
    COMMON_RISK_DEFAULTS,
)
from .ops import validate_runtime_config


def _runtimes_root() -> Path:
    return PROJECT_ROOT / "src" / "invest_evolution" / "investment" / "runtimes"


def _runtime_configs_dir() -> Path:
    return _runtimes_root() / "configs"


@dataclass
class RuntimeConfig:
    name: str
    path: Path
    data: Dict[str, Any]


class ManagerRuntime(ABC):
    runtime_id = "base"
    default_config_relpath: Optional[str] = None

    def __init__(
        self,
        runtime_config_ref: Optional[str | Path] = None,
        runtime_overrides: Optional[Dict[str, Any]] = None,
    ):
        self.config = self.load_runtime_config(runtime_config_ref)
        self.runtime_overrides = dict(runtime_overrides or {})

    @staticmethod
    def _resolve_named_runtime_config(text: str) -> Optional[Path]:
        candidate = str(text or "").strip()
        if not candidate:
            return None
        for suffix in (".yaml", ".yml"):
            path = _runtime_configs_dir() / f"{candidate}{suffix}"
            if path.exists():
                return enforce_path_within_root(PROJECT_ROOT, path)
        return None

    @classmethod
    def resolve_runtime_config_ref(
        cls, runtime_config_ref: Optional[str | Path]
    ) -> Path:
        if runtime_config_ref is None:
            if not cls.default_config_relpath:
                raise ValueError(f"{cls.__name__} requires runtime_config_ref")
            default_path = _runtimes_root() / cls.default_config_relpath
            return enforce_path_within_root(PROJECT_ROOT, default_path)
        text = str(runtime_config_ref or "").strip()
        path = Path(text)
        looks_like_path = (
            path.is_absolute()
            or path.suffix.lower() in {".yaml", ".yml", ".json"}
            or "/" in text
            or "\\" in text
        )
        if not looks_like_path:
            named_path = cls._resolve_named_runtime_config(text)
            if named_path is not None:
                return named_path
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        try:
            return enforce_path_within_root(PROJECT_ROOT, path)
        except ValueError as exc:
            raise ValueError(
                f"{cls.__name__} runtime config ref escapes project root"
            ) from exc

    @classmethod
    def load_runtime_config(
        cls, runtime_config_ref: Optional[str | Path]
    ) -> RuntimeConfig:
        path = cls.resolve_runtime_config_ref(runtime_config_ref)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        validate_runtime_config(data)
        name = str(data.get("name") or path.stem)
        return RuntimeConfig(name=name, path=path, data=data)

    def effective_params(self) -> Dict[str, Any]:
        merged = dict(self.config.data.get("params", {}))
        merged.update(self.runtime_overrides)
        return merged

    def update_runtime_overrides(self, params: Dict[str, Any]) -> None:
        self.runtime_overrides = {
            **self.runtime_overrides,
            **dict(params or {}),
        }

    def config_section(self, key: str, default: Any = None) -> Any:
        value = self.config.data.get(key, default)
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, list):
            return list(value)
        return value

    def runtime_config_ref(self) -> str:
        return str(
            getattr(self.config, "path", "") or getattr(self.config, "name", "") or ""
        ).strip()

    def param(self, key: str, default: Any = None) -> Any:
        if key in self.runtime_overrides:
            return self.runtime_overrides[key]
        params = self.config.data.get("params", {}) or {}
        if key in params:
            return params[key]
        if key in COMMON_PARAM_DEFAULTS:
            return COMMON_PARAM_DEFAULTS[key]
        return default

    def risk_param(self, key: str, default: Any = None) -> Any:
        if key in self.runtime_overrides:
            return self.runtime_overrides[key]
        params = self.config.data.get("params", {}) or {}
        if key in params:
            return params[key]
        risk = self.config.data.get("risk", {}) or {}
        if key in risk:
            return risk[key]
        if key in COMMON_RISK_DEFAULTS:
            return COMMON_RISK_DEFAULTS[key]
        return default

    def execution_param(self, key: str, default: Any = None) -> Any:
        execution = self.config.data.get("execution", {}) or {}
        if key in execution:
            return execution[key]
        if key in COMMON_EXECUTION_DEFAULTS:
            return COMMON_EXECUTION_DEFAULTS[key]
        return default

    def benchmark_param(self, key: str, default: Any = None) -> Any:
        benchmark = self.config.data.get("benchmark", {}) or {}
        if key in benchmark:
            return benchmark[key]
        if key in COMMON_BENCHMARK_DEFAULTS:
            return COMMON_BENCHMARK_DEFAULTS[key]
        return default

    def scoring_section(self) -> Dict[str, Any]:
        scoring = self.config.data.get("scoring", {}) or {}
        return dict(scoring)

    def regime_profile(self, regime: str) -> Dict[str, Any]:
        normalized_regime = str(regime or "").strip()
        profiles = self.config.data.get("regime_profiles", {}) or {}
        profile = dict(profiles.get(normalized_regime) or {})
        return {
            "params": dict(profile.get("params") or {}),
            "risk": dict(profile.get("risk") or {}),
            "filters": dict(profile.get("filters") or {}),
            "source": "regime_profiles"
            if profile
            else self.regime_profile_source(normalized_regime),
        }

    def regime_profile_source(self, regime: str) -> str:
        normalized_regime = str(regime or "").strip()
        if not normalized_regime:
            return "default"
        profiles = self.config.data.get("regime_profiles", {}) or {}
        if dict(profiles.get(normalized_regime) or {}):
            return "regime_profiles"
        legacy_prefix = f"{normalized_regime}_"
        params = self.config.data.get("params", {}) or {}
        if any(str(key).startswith(legacy_prefix) for key in params.keys()):
            return "legacy_prefix"
        if any(
            str(key).startswith(legacy_prefix) for key in self.runtime_overrides.keys()
        ):
            return "legacy_prefix_override"
        return "default"

    def regime_param(self, regime: str, key: str, default: Any = None) -> Any:
        normalized_regime = str(regime or "").strip()
        profile_params = dict(
            self.regime_profile(normalized_regime).get("params") or {}
        )
        if key in profile_params:
            return profile_params[key]
        if normalized_regime:
            legacy_key = f"{normalized_regime}_{key}"
            legacy_value = self.param(legacy_key, None)
            if legacy_value is not None:
                return legacy_value
        return self.param(key, default)

    def regime_risk_param(self, regime: str, key: str, default: Any = None) -> Any:
        normalized_regime = str(regime or "").strip()
        profile_risk = dict(self.regime_profile(normalized_regime).get("risk") or {})
        if key in profile_risk:
            return profile_risk[key]
        if normalized_regime:
            legacy_key = f"{normalized_regime}_{key}"
            legacy_value = self.risk_param(legacy_key, None)
            if legacy_value is not None:
                return legacy_value
        return self.risk_param(key, default)

    def regime_filter(self, regime: str, key: str, default: Any = None) -> Any:
        normalized_regime = str(regime or "").strip()
        profile_filters = dict(
            self.regime_profile(normalized_regime).get("filters") or {}
        )
        if key in profile_filters:
            return profile_filters[key]
        if normalized_regime:
            legacy_key = f"{normalized_regime}_{key}"
            legacy_value = self.param(legacy_key, None)
            if legacy_value is not None:
                return legacy_value
        return self.param(key, default)

    @staticmethod
    def build_stock_summary_views(
        items: Iterable[Dict[str, Any] | StockSummaryView],
    ) -> list[StockSummaryView]:
        return [StockSummaryView.from_mapping(item) for item in list(items or [])]

    @staticmethod
    def estimate_context_confidence(signal_packet: SignalPacket) -> float:
        scores = [
            float(item.score)
            for item in list(signal_packet.signals or [])[
                : max(1, signal_packet.max_positions or 3)
            ]
        ]
        if not scores:
            return 0.5
        average = sum(scores) / len(scores)
        return round(max(0.5, min(0.95, average)), 4)

    def select_candidate_codes(
        self, stock_data: Dict[str, Any], cutoff_date: str
    ) -> list[str]:
        candidate_pool_size = int(self.param("candidate_pool_size"))
        min_history_days = int(self.param("candidate_min_history_days", 20))
        max_staleness_days = int(self.param("candidate_max_staleness_days", 5))
        selected = build_candidate_universe(
            stock_data,
            cutoff_date=cutoff_date,
            candidate_pool_size=candidate_pool_size,
            min_history_days=min_history_days,
            max_staleness_days=max_staleness_days,
        )
        if selected or not stock_data:
            return selected
        return build_candidate_universe(
            stock_data,
            cutoff_date=cutoff_date,
            candidate_pool_size=candidate_pool_size,
            min_history_days=min_history_days,
            max_staleness_days=3650,
        )

    @abstractmethod
    def build_signal_packet(
        self, stock_data: Dict[str, Any], cutoff_date: str
    ) -> SignalPacket:
        raise NotImplementedError

    @abstractmethod
    def build_agent_context(
        self, stock_data: Dict[str, Any], cutoff_date: str, signal_packet: SignalPacket
    ) -> AgentContext:
        raise NotImplementedError

    def process(self, stock_data: Dict[str, Any], cutoff_date: str) -> ManagerOutput:
        signal_packet = self.build_signal_packet(stock_data, cutoff_date)
        agent_context = self.build_agent_context(stock_data, cutoff_date, signal_packet)
        return ManagerOutput(
            manager_id=self.runtime_id,
            manager_config_ref=self.runtime_config_ref(),
            signal_packet=signal_packet,
            agent_context=agent_context,
        )
