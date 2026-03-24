from __future__ import annotations

# Agent base

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

from invest_evolution.config import agent_config_registry, config
from invest_evolution.config.control_plane import build_component_llm_caller
from invest_evolution.investment.contracts import AgentContext
from invest_evolution.investment.shared.llm import LLMCaller

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


# Regime and governance selector agents

logger = logging.getLogger(__name__)

try:
    from invest_evolution.investment.memory import MarketSituationMemory as _MarketSituationMemory
except ImportError:
    _MarketSituationMemory = None


_MARKET_REGIME_SYSTEM_PROMPT = """你是一个专业的A股市场分析师。
你的唯一职责是判断当前市场的整体状态。

你需要基于提供的市场统计数据，判断市场处于以下三种状态之一：
- bull（牛市）：多数股票上涨，趋势向上
- bear（熊市）：多数股票下跌，趋势向下
- oscillation（震荡市）：方向不明，上下波动

请严格以JSON格式输出，不要有其他内容：
{
    "regime": "bull 或 bear 或 oscillation",
    "confidence": 0.0到1.0之间的数字,
    "suggested_exposure": 0.0到1.0之间的数字（建议总仓位比例）,
    "reasoning": "一句话说明判断依据"
}"""




class MarketRegimeAgent(InvestAgent):
    """
    市场分析师

    判断当前市场处于牛市/熊市/震荡市
    输出市场状态审计结论，不携带策略参数。
    两种模式：analyze()（LLM）/ _fallback_analysis()（纯算法兜底）
    """

    def __init__(self, llm_caller=None):
        super().__init__(AgentConfig(name="MarketRegime", role="regime"), llm_caller)
        self.history: List[Dict] = []

    def perceive(self, data: dict, context: AgentContext | None = None) -> dict:
        """感知：接收市场统计数据"""
        del context
        return data

    def reason(self, perception: dict) -> dict:
        """推理：调用分析逻辑"""
        return self.analyze(perception)

    def act(self, reasoning: dict) -> dict:
        """行动：返回最终分析结果（不含交易参数）"""
        return reasoning

    def analyze_context(self, agent_context: AgentContext) -> dict:
        market_stats = dict(agent_context.market_stats or {})
        result = self._fallback_analysis(market_stats)
        result["reasoning"] = agent_context.summary or result.get("reasoning", "")
        return result

    def analyze(self, market_stats: dict) -> dict:
        """
        LLM 版本：调用大模型判断市场状态

        Args:
            market_stats: compute_market_stats() 的输出

        Returns:
            {"regime", "confidence", "suggested_exposure", "reasoning", "source"}
        """
        if not self.llm:
            return self._fallback_analysis(market_stats)

        try:
            result = self.llm.call_json(self.config.system_prompt, self._build_prompt(market_stats))
        except (ValueError, TypeError) as e:
            logger.warning(f"MarketRegime LLM调用异常(数据/参数): {e}")
            return self._fallback_analysis(market_stats)
        except Exception as e:
            logger.exception(f"MarketRegime LLM调用失败(网络/未知): {e}")
            return self._fallback_analysis(market_stats)

        if result.get("_parse_error"):
            return self._fallback_analysis(market_stats)

        result = self._validate(result)
        result["source"] = "llm"
        self._record(result, market_stats)
        return result

    def _fallback_analysis(self, market_stats: dict) -> dict:
        """纯算法版本：基于统计规则判断，不调用 LLM"""
        regime, confidence, reasoning = self._rule_based_judgment(market_stats)
        exposure_map = {"bull": 0.8, "oscillation": 0.5, "bear": 0.2}
        result = {
            "regime": regime,
            "confidence": confidence,
            "suggested_exposure": exposure_map[regime],
            "reasoning": reasoning,
            "source": "algorithm",
        }
        self._record(result, market_stats)
        return result

    def get_last_regime(self) -> str:
        return self.history[-1]["regime"] if self.history else "oscillation"

    def regime_changed(self) -> bool:
        """最近两次判断是否不同"""
        return len(self.history) >= 2 and self.history[-1]["regime"] != self.history[-2]["regime"]

    def _build_prompt(self, stats: dict) -> str:
        lines = [
            "以下是当前A股市场的统计数据：",
            "",
            f"- 统计股票数: {stats.get('valid_stocks', 0)}",
            f"- 近5日上涨股票占比: {stats.get('advance_ratio_5d', 0):.0%}",
            f"- 近5日平均涨幅: {stats.get('avg_change_5d', 0):+.2f}%",
            f"- 近5日涨幅中位数: {stats.get('median_change_5d', 0):+.2f}%",
            f"- 近20日平均涨幅: {stats.get('avg_change_20d', 0):+.2f}%",
            f"- 近20日涨幅中位数: {stats.get('median_change_20d', 0):+.2f}%",
            f"- 站上20日均线占比: {stats.get('above_ma20_ratio', 0):.0%}",
            f"- 20日平均波动率: {stats.get('avg_volatility', 0):.4f}",
            "",
            "请判断当前市场状态。",
        ]
        return "\n".join(lines)

    def _rule_based_judgment(self, stats: dict):
        median_20d = stats.get("median_change_20d", 0)
        advance = stats.get("advance_ratio_5d", 0.5)
        above_ma20 = stats.get("above_ma20_ratio", 0.5)

        score = 0.0
        reasons = []

        if median_20d > 5:
            score += 2
            reasons.append(f"20日涨幅{median_20d:+.1f}%强势")
        elif median_20d > 0:
            score += 1
            reasons.append(f"20日涨幅{median_20d:+.1f}%温和")
        elif median_20d > -5:
            score -= 1
            reasons.append(f"20日跌幅{median_20d:+.1f}%偏弱")
        else:
            score -= 2
            reasons.append(f"20日跌幅{median_20d:+.1f}%疲弱")

        if advance > 0.6:
            score += 1
            reasons.append(f"多数股票上涨({advance:.0%})")
        elif advance < 0.4:
            score -= 1
            reasons.append(f"多数股票下跌({advance:.0%})")

        if above_ma20 > 0.6:
            score += 1
            reasons.append(f"多数在MA20上方({above_ma20:.0%})")
        elif above_ma20 < 0.4:
            score -= 1
            reasons.append(f"多数在MA20下方({above_ma20:.0%})")

        if score >= 2:
            regime, confidence = "bull", min(0.9, 0.5 + score * 0.1)
        elif score <= -2:
            regime, confidence = "bear", min(0.9, 0.5 + abs(score) * 0.1)
        else:
            regime, confidence = "oscillation", 0.5

        return regime, confidence, "；".join(reasons) if reasons else "数据不足"

    def _validate(self, result: dict) -> dict:
        if result.get("regime") not in {"bull", "bear", "oscillation"}:
            result["regime"] = "oscillation"
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
        if not isinstance(result.get("suggested_exposure"), (int, float)):
            result["suggested_exposure"] = {"bull": 0.8, "oscillation": 0.5, "bear": 0.2}[result["regime"]]
        result["suggested_exposure"] = max(0.0, min(1.0, result["suggested_exposure"]))
        if not isinstance(result.get("reasoning"), str):
            result["reasoning"] = ""
        return result

    def _record(self, result: dict, stats: dict):
        self.history.append({
            "regime": result["regime"],
            "confidence": result["confidence"],
            "source": result.get("source", "unknown"),
            "cutoff_date": stats.get("cutoff_date", ""),
        })



# ============================================================
# Part 3: 趋势猎手 Agent

__all__ = [name for name in globals() if not name.startswith('_')]
