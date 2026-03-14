from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class SelfAssessmentSnapshot:
    """单周期自我评估快照（用于冻结门控与追踪）"""

    cycle_id: int
    cutoff_date: str
    regime: str
    plan_source: str
    return_pct: float
    is_profit: bool
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    excess_return: float = 0.0
    benchmark_passed: bool = False


@dataclass
class EventCallbackState:
    callback: Optional[Callable] = None


_event_callback_state = EventCallbackState()


def set_event_callback(callback: Callable) -> None:
    """设置事件回调，用于推送实时事件到前端"""
    _event_callback_state.callback = callback


def emit_event(event_type: str, data: dict) -> None:
    """发射事件到前端"""
    callback = _event_callback_state.callback
    if callback:
        try:
            callback(event_type, data)
        except Exception as exc:
            logger.warning("Event callback failed for %s: %s", event_type, exc)
