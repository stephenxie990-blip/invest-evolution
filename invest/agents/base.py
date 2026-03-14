import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

from config import agent_config_registry, config
from config.control_plane import build_component_llm_caller
from invest.shared.llm import LLMCaller

_MODEL_ALIAS_FAST = {"", "fast"}
_MODEL_ALIAS_DEEP = {"deep"}

logger = logging.getLogger(__name__)

_COMMON_PROMPT_CONTRACT = (
    "\n\n共同约束：\n"
    "- 只依据输入中的事实、候选、统计和上下文判断，不得编造未提供的股票代码、指标值、市场事实或字段。\n"
    "- 角色边界必须清晰：只做本角色职责内的判断，不越权替代其他 Agent。\n"
    "- 若证据不足或冲突，降低 confidence，并选择更保守的结论或建议。\n"
    "- 所有数值字段必须输出裸数字，不带百分号、中文单位或解释性文字。\n"
    "- 最终只输出要求的 JSON 对象，不输出 Markdown、代码块、前后缀说明或多余字段。"
)


@dataclass
class AgentConfig:
    """Agent 配置"""
    name: str
    role: str          # "commander" / "hunter" / "regime" / "judge" / "strategist"

    def _raw_registry_config(self) -> dict[str, Any]:
        return agent_config_registry.get_config(self.name)

    @property
    def system_prompt(self) -> str:
        cfg = self._raw_registry_config()
        base_prompt = cfg.get("system_prompt", f"You are {self.name}.")
        return base_prompt + _COMMON_PROMPT_CONTRACT

    @property
    def llm_model_setting(self) -> str:
        cfg = self._raw_registry_config()
        return str(cfg.get("llm_model", "") or "").strip()

    @property
    def llm_model(self) -> str:
        model = self.llm_model_setting.lower()
        if model in _MODEL_ALIAS_FAST:
            return config.llm_fast_model
        if model in _MODEL_ALIAS_DEEP:
            return config.llm_deep_model
        return self.llm_model_setting


class RegimeResult(TypedDict, total=False):
    regime: str
    confidence: float
    suggested_exposure: float
    reasoning: str
    source: str
    params: dict[str, Any]





@dataclass
class Belief:
    """Agent 信念"""
    confidence: float = 0.5
    bias: Dict[str, float] = field(default_factory=dict)
    history: List[Dict] = field(default_factory=list)

    def update(self, correct: bool, confidence_delta: float = 0.05):
        """根据决策结果更新信念"""
        self.confidence = max(
            0.1, min(0.9, self.confidence + (confidence_delta if correct else -confidence_delta))
        )


class InvestAgent(ABC):
    """
    投资 Agent 基类
    """

    def __init__(self, agent_config: AgentConfig, llm_caller=None):
        self.config = agent_config
        self.llm: Optional[LLMCaller] = llm_caller
        
        # 独立实例孵化逻辑：若未传 llm_caller，且注册表里有独立配置，则自我独立孵化大模型能力
        if self.llm is None:
            model = self.config.llm_model
            model_setting = self.config.llm_model_setting
            self.llm = build_component_llm_caller(
                f"agent.{self.config.name}",
                fallback_model=model,
            )
            logger.info(
                "[%s] 采用独立大模型配置: model_setting=%s, resolved_model=%s",
                self.config.name,
                model_setting or "<inherit-fast>",
                self.llm.model,
            )
            
        self.belief = Belief()
        self.memory: List[Dict] = []                        # 内部日志记忆
        self.situation_memory: Optional[Any] = None         # BM25 市场情境记忆库 (Phase 2)

    def reset_memory(self, keep_last: int = 10):
        """重置记忆，只保留最近 N 条"""
        if len(self.memory) > keep_last:
            self.memory = self.memory[-keep_last:]

    @abstractmethod
    def perceive(self, data: Any) -> Any:
        """感知：将原始数据转化为结构化观察"""
        pass

    @abstractmethod
    def reason(self, perception: Any) -> Any:
        """推理：基于感知做出预测或逻辑判断"""
        pass

    @abstractmethod
    def act(self, reasoning: Any) -> Any:
        """行动：将推理转化为具体操作建议"""
        pass

    def reflect(self, outcome: Any):
        """反思：基于实际结果更新信念和偏差。

        採用 TradingAgents 的四步反思模式（当 LLM 可用时）：
        1. 推因分析 (Reasoning)  — 决策是否正确？关键因素是什么？
        2. 改进方案 (Improvement) — 如不正确，应如何修正？
        3. 经验总结 (Summary)    — 本次核心教训？
        4. 关键提炼 (Query)      — 可迁移到未来决策的核心洞察。
        """
        if not outcome:
            return

        is_correct = outcome.get("correct", False)
        confidence_delta = outcome.get("delta", 0.05)
        self.belief.update(is_correct, confidence_delta)

        # 结构化反思（LLM + 算法兆底）
        reflection_text = ""
        if self.llm:
            situation = outcome.get("situation", "")
            action = outcome.get("action", "")
            result_desc = outcome.get("result", "")

            system_prompt = (
                "你是一位专业投资分析师，正在对一次投资决策进行反思总结。\n"
                "请严格按照四步骤进行分析：\n"
                "1. 推因分析 (Reasoning)：该决策是否正确？关键影响因素是什么？\n"
                "2. 改进方案 (Improvement)：如果决策错误，应如何具体修正？\n"
                "3. 经验总结 (Summary)：本次核心教训是什么？\n"
                "4. 关键提炼 (Query)：用 1-2 句话概括可迁移到未来决策的核心洞察。"
            )
            user_prompt = (
                f"市场情境：{situation}\n\n"
                f"采取的决策/行动：{action}\n\n"
                f"实际结果：{result_desc}\n\n"
                f"('correct': {is_correct})"
            )
            raw = self.llm.call(system_prompt, user_prompt, temperature=0.5, max_tokens=300)
            if raw and "dry_run" not in raw:
                reflection_text = raw

        # 即使 LLM 不可用，也记录简单反思
        if not reflection_text:
            reflection_text = (
                f"{'[+]正确决策' if is_correct else '[-]错误决策'}："
                f"市场={outcome.get('situation', '')}; "
                f"结果={outcome.get('result', '')}"
            )

        # 存入 BM25 情境记忆库
        if self.situation_memory is not None:
            situation_text = outcome.get("situation", "")
            action_text = outcome.get("action", "")
            if situation_text and action_text:
                self.situation_memory.add_experience(
                    situation=situation_text,
                    action=action_text,
                    outcome=reflection_text[:300],
                    context={"agent": self.config.name, "correct": is_correct},
                )

        entry = {
            "type": "reflection",
            "timestamp": datetime.now().isoformat(),
            "correct": is_correct,
            "new_confidence": self.belief.confidence,
            "reflection": reflection_text[:200],
        }
        self.memory.append(entry)
        self.belief.history.append(entry)
        self.reset_memory(keep_last=50)
        if len(self.belief.history) > 200:
            self.belief.history = self.belief.history[-200:]
        logger.info(
            "Agent %s 反思完成: 正确=%s, 新信心=%.2f",
            self.config.name, is_correct, self.belief.confidence
        )


# ============================================================

__all__ = ["AgentConfig", "RegimeResult", "Belief", "InvestAgent"]
