from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from config import PROJECT_ROOT
from invest.contracts import AgentContext, ModelOutput, SignalPacket


@dataclass
class ModelConfig:
    name: str
    path: Path
    data: Dict[str, Any]


class InvestmentModel(ABC):
    model_name = "base"
    default_config_relpath: Optional[str] = None

    def __init__(self, config_path: Optional[str | Path] = None, runtime_overrides: Optional[Dict[str, Any]] = None):
        self.config = self.load_config(config_path)
        self.runtime_overrides = dict(runtime_overrides or {})

    @classmethod
    def resolve_config_path(cls, config_path: Optional[str | Path]) -> Path:
        if config_path is None:
            if not cls.default_config_relpath:
                raise ValueError(f"{cls.__name__} requires config_path")
            return PROJECT_ROOT / "invest" / "models" / cls.default_config_relpath
        path = Path(config_path)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        return path

    @classmethod
    def load_config(cls, config_path: Optional[str | Path]) -> ModelConfig:
        path = cls.resolve_config_path(config_path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        name = str(data.get("name") or path.stem)
        return ModelConfig(name=name, path=path, data=data)

    def effective_params(self) -> Dict[str, Any]:
        merged = dict(self.config.data.get("params", {}))
        merged.update(self.runtime_overrides)
        return merged

    def update_runtime_overrides(self, params: Dict[str, Any]) -> None:
        self.runtime_overrides.update(params or {})

    @abstractmethod
    def build_signal_packet(self, stock_data: Dict[str, Any], cutoff_date: str) -> SignalPacket:
        raise NotImplementedError

    @abstractmethod
    def build_agent_context(self, stock_data: Dict[str, Any], cutoff_date: str, signal_packet: SignalPacket) -> AgentContext:
        raise NotImplementedError

    def process(self, stock_data: Dict[str, Any], cutoff_date: str) -> ModelOutput:
        signal_packet = self.build_signal_packet(stock_data, cutoff_date)
        agent_context = self.build_agent_context(stock_data, cutoff_date, signal_packet)
        return ModelOutput(
            model_name=self.model_name,
            config_name=self.config.name,
            signal_packet=signal_packet,
            agent_context=agent_context,
        )
