import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

from invest.shared import LLMCaller

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Agent 配置"""
    name: str
    role: str          # "commander" / "hunter" / "regime" / "judge" / "strategist"

    @property
    def system_prompt(self) -> str:
        from config import agent_config_registry
        cfg = agent_config_registry.get_config(self.name)
        return cfg.get("system_prompt", f"You are {self.name}.")

    @property
    def llm_model(self) -> str:
        from config import agent_config_registry
        cfg = agent_config_registry.get_config(self.name)
        return cfg.get("llm_model", "")

    @property
    def llm_api_key(self) -> str:
        from config import agent_config_registry
        cfg = agent_config_registry.get_config(self.name)
        return cfg.get("llm_api_key", "")

    @property
    def llm_api_base(self) -> str:
        from config import agent_config_registry
        cfg = agent_config_registry.get_config(self.name)
        return cfg.get("llm_api_base", "")


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
            api_key = self.config.llm_api_key
            api_base = self.config.llm_api_base
            # 即便是全量 fallback，我们也应当为每个 Agent 造出一个独立的实例以追踪 token 消耗
            # 所以只要外部想剥离 (llm is None)，我们就主动孵化。无值则自动fallback全局设置
            from invest.core import LLMCaller
            self.llm = LLMCaller(
                model=model if model else None,
                api_key=api_key if api_key else None,
                api_base=api_base if api_base else None,
            )
            logger.info(f"[{self.config.name}] 采用独立大模型配置: Model={self.llm.model}")
            
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
