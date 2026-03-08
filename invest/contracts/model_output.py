from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from .agent_context import AgentContext
from .signal_packet import SignalPacket


@dataclass
class ModelOutput:
    """Dual-channel model output for both machine execution and LLM reasoning."""

    model_name: str
    config_name: str
    signal_packet: SignalPacket
    agent_context: AgentContext

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "config_name": self.config_name,
            "signal_packet": self.signal_packet.to_dict(),
            "agent_context": self.agent_context.to_dict(),
        }
