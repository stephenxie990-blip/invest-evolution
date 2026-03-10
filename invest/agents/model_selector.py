from __future__ import annotations

import logging
from typing import Any, Dict, List

from .base import AgentConfig, InvestAgent

logger = logging.getLogger(__name__)

_MODEL_SELECTOR_SYSTEM_PROMPT = """你是投资模型路由顾问。
你的唯一职责是在市场状态已知的前提下，从允许的投资模型中选择最合适的模型。

候选模型包括：
- momentum：趋势/牛市更强时优先
- mean_reversion：震荡和均值回归更强时优先
- defensive_low_vol：熊市/高波动/防御阶段优先
- value_quality：不确定或需要质量因子兜底时优先

请严格输出 JSON：
{
  "selected_model": "模型名",
  "candidate_models": ["模型1", "模型2"],
  "confidence": 0.0,
  "reasoning": "一句话说明",
  "source": "llm"
}
"""


class ModelSelectorAgent(InvestAgent):
    """Provide advisory model selection without owning final execution authority."""

    def __init__(self, llm_caller=None):
        super().__init__(AgentConfig(name="ModelSelector", role="router"), llm_caller)

    def perceive(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return dict(data or {})

    def reason(self, perception: Dict[str, Any]) -> Dict[str, Any]:
        return self.analyze(perception)

    def act(self, reasoning: Dict[str, Any]) -> Dict[str, Any]:
        return reasoning

    def analyze(self, routing_context: Dict[str, Any]) -> Dict[str, Any]:
        if not self.llm:
            return self._fallback_analysis(routing_context)
        try:
            result = self.llm.call_json(self.config.system_prompt, self._build_prompt(routing_context))
        except Exception as exc:
            logger.warning("ModelSelector LLM 调用失败，回退规则路由: %s", exc)
            return self._fallback_analysis(routing_context)
        if result.get("_parse_error"):
            return self._fallback_analysis(routing_context)
        return self._validate(result, routing_context)

    def _build_prompt(self, routing_context: Dict[str, Any]) -> str:
        regime = routing_context.get("regime", "unknown")
        current_model = routing_context.get("current_model", "momentum")
        allowed_models = routing_context.get("allowed_models") or []
        candidate_models = routing_context.get("candidate_models") or []
        weights = routing_context.get("candidate_weights") or {}
        market_stats = routing_context.get("market_stats") or {}
        return (
            f"当前模型: {current_model}\n"
            f"市场状态: {regime}\n"
            f"允许模型: {allowed_models}\n"
            f"候选模型: {candidate_models}\n"
            f"候选权重: {weights}\n"
            f"市场统计: {market_stats}\n"
        )

    def _fallback_analysis(self, routing_context: Dict[str, Any]) -> Dict[str, Any]:
        regime = str(routing_context.get("regime") or "unknown")
        allowed_models = [str(item) for item in (routing_context.get("allowed_models") or []) if str(item).strip()]
        candidate_models = [str(item) for item in (routing_context.get("candidate_models") or []) if str(item).strip()]
        current_model = str(routing_context.get("current_model") or "momentum")
        preferred_by_regime = {
            "bull": ["momentum", "value_quality", "mean_reversion", "defensive_low_vol"],
            "oscillation": ["mean_reversion", "value_quality", "defensive_low_vol", "momentum"],
            "bear": ["defensive_low_vol", "value_quality", "mean_reversion", "momentum"],
            "unknown": ["value_quality", "defensive_low_vol", "momentum", "mean_reversion"],
        }
        ordered = candidate_models or preferred_by_regime.get(regime, preferred_by_regime["unknown"])
        if allowed_models:
            ordered = [name for name in ordered if name in allowed_models]
        if not ordered:
            ordered = [current_model]
        selected_model = ordered[0]
        confidence_map = {"bull": 0.72, "oscillation": 0.68, "bear": 0.78, "unknown": 0.55}
        return {
            "selected_model": selected_model,
            "candidate_models": ordered[:3],
            "confidence": confidence_map.get(regime, 0.55),
            "reasoning": f"基于 regime={regime} 的模型适配顺序，建议优先使用 {selected_model}。",
            "source": "algorithm",
        }

    def _validate(self, result: Dict[str, Any], routing_context: Dict[str, Any]) -> Dict[str, Any]:
        allowed_models = [str(item) for item in (routing_context.get("allowed_models") or []) if str(item).strip()]
        fallback = self._fallback_analysis(routing_context)
        selected_model = str(result.get("selected_model") or fallback["selected_model"]).strip()
        if allowed_models and selected_model not in allowed_models:
            selected_model = fallback["selected_model"]
        candidate_models = result.get("candidate_models") if isinstance(result.get("candidate_models"), list) else fallback["candidate_models"]
        candidate_models = [str(item).strip() for item in candidate_models if str(item).strip()]
        if allowed_models:
            candidate_models = [item for item in candidate_models if item in allowed_models]
        if not candidate_models:
            candidate_models = [selected_model]
        confidence = result.get("confidence", fallback["confidence"])
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = float(fallback["confidence"])
        confidence = max(0.0, min(1.0, confidence))
        reasoning = str(result.get("reasoning") or fallback["reasoning"]).strip()
        return {
            "selected_model": selected_model,
            "candidate_models": candidate_models,
            "confidence": confidence,
            "reasoning": reasoning,
            "source": str(result.get("source") or "llm"),
        }
