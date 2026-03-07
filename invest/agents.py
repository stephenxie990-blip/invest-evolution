"""
投资进化系统 - 所有 Agent 定义

包含：
1. AgentConfig / Belief / InvestAgent   — 基类
2. MarketRegimeAgent                    — 市场状态判断（牛/熊/震荡）
3. TrendHunterAgent                     — 趋势猎手（上升趋势初期股票）
4. ContrarianAgent                      — 逆向猎手（超跌反弹机会）
5. CommanderAgent                       — 指挥官（整合所有意见，最终建仓计划）
6. StrategistAgent                      — 策略分析师（组合风险评估）
7. EvoJudgeAgent                        — 进化裁判（评估Agent表现，触发进化）

所有 Agent 共享：
- 同一份 import
- LLM + 算法双模式（LLM 失败自动 fallback）
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

from invest.core import (
    LLMCaller,
    format_stock_table,
    compute_rsi,
    compute_macd_signal,
    compute_bb_position,
)

logger = logging.getLogger(__name__)

# 延迟导入避免循环依赖
try:
    from invest.memory import MarketSituationMemory as _MarketSituationMemory
except ImportError:
    _MarketSituationMemory = None


# ============================================================
# Part 1: 基类
# ============================================================

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
# Part 2: 市场状态 Agent
# ============================================================

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

# 三种市场状态对应的交易参数
REGIME_PARAMS = {
    "bull": {
        "top_n": 8,
        "max_positions": 5,
        "position_size": 0.20,
        "stop_loss_pct": 0.07,
        "take_profit_pct": 0.20,
    },
    "oscillation": {
        "top_n": 6,
        "max_positions": 4,
        "position_size": 0.20,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.15,
    },
    "bear": {
        "top_n": 3,
        "max_positions": 2,
        "position_size": 0.15,
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.10,
    },
}


class MarketRegimeAgent(InvestAgent):
    """
    市场分析师

    判断当前市场处于牛市/熊市/震荡市
    输出影响后续选股数量、仓位大小、止损止盈参数
    两种模式：analyze()（LLM）/ analyze_fallback()（纯算法）
    """

    def __init__(self, llm_caller=None):
        super().__init__(AgentConfig(name="MarketRegime", role="regime"), llm_caller)
        self.history: List[Dict] = []

    def perceive(self, market_stats: dict) -> dict:
        """感知：接收市场统计数据"""
        return market_stats

    def reason(self, perception: dict) -> dict:
        """推理：调用分析逻辑"""
        return self.analyze(perception)

    def act(self, reasoning: dict) -> dict:
        """行动：返回最终分析结果（含参数）"""
        return reasoning

    def analyze(self, market_stats: dict) -> dict:
        """
        LLM 版本：调用大模型判断市场状态

        Args:
            market_stats: compute_market_stats() 的输出

        Returns:
            {"regime", "confidence", "suggested_exposure", "reasoning", "source", "params"}
        """
        if not self.llm:
            return self.analyze_fallback(market_stats)

        try:
            result = self.llm.call_json(self.config.system_prompt, self._build_prompt(market_stats))
        except (ValueError, TypeError) as e:
            logger.warning(f"MarketRegime LLM调用异常(数据/参数): {e}")
            return self.analyze_fallback(market_stats)
        except Exception as e:
            logger.exception(f"MarketRegime LLM调用失败(网络/未知): {e}")
            return self.analyze_fallback(market_stats)

        if result.get("_parse_error"):
            return self.analyze_fallback(market_stats)

        result = self._validate(result)
        result["source"] = "llm"
        result["params"] = REGIME_PARAMS.get(result["regime"], REGIME_PARAMS["oscillation"])
        self._record(result, market_stats)
        return result

    def analyze_fallback(self, market_stats: dict) -> dict:
        """纯算法版本：基于统计规则判断，不调用 LLM"""
        regime, confidence, reasoning = self._rule_based_judgment(market_stats)
        exposure_map = {"bull": 0.8, "oscillation": 0.5, "bear": 0.2}
        result = {
            "regime": regime,
            "confidence": confidence,
            "suggested_exposure": exposure_map[regime],
            "reasoning": reasoning,
            "source": "algorithm",
            "params": REGIME_PARAMS[regime],
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
        avg_20d = stats.get("avg_change_20d", 0)
        median_20d = stats.get("median_change_20d", 0)
        advance = stats.get("advance_ratio_5d", 0.5)
        above_ma20 = stats.get("above_ma20_ratio", 0.5)

        score = 0.0
        reasons = []

        if median_20d > 5:
            score += 2; reasons.append(f"20日涨幅{median_20d:+.1f}%强势")
        elif median_20d > 0:
            score += 1; reasons.append(f"20日涨幅{median_20d:+.1f}%温和")
        elif median_20d > -5:
            score -= 1; reasons.append(f"20日跌幅{median_20d:+.1f}%偏弱")
        else:
            score -= 2; reasons.append(f"20日跌幅{median_20d:+.1f}%疲弱")

        if advance > 0.6:
            score += 1; reasons.append(f"多数股票上涨({advance:.0%})")
        elif advance < 0.4:
            score -= 1; reasons.append(f"多数股票下跌({advance:.0%})")

        if above_ma20 > 0.6:
            score += 1; reasons.append(f"多数在MA20上方({above_ma20:.0%})")
        elif above_ma20 < 0.4:
            score -= 1; reasons.append(f"多数在MA20下方({above_ma20:.0%})")

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
# ============================================================

_TREND_HUNTER_SYSTEM_PROMPT = """你是一个专业的趋势交易猎手，专注于寻找A股中处于上升趋势的股票。

你的分析依据（按重要性排序）：
1. 均线状态：优先选择MA趋势为"多头"的股票
2. MACD信号：优先选择"金叉"或"看多"的股票
3. RSI水平：优先选择RSI在35-70区间的股票（有上升空间且未过热）
4. 近期走势：优先选择5日和20日涨幅为正的股票
5. 量比：量比较高的股票更好，但不作为硬性条件

注意：
- 不要设置过于严格的硬性门槛，根据整体表现综合评判
- 如果没有完美的候选，选择相对最好的
- 必须从提供的候选列表中选择，不要编造股票代码

请从候选股中选择3-5只最有上涨潜力的股票。

严格以JSON格式输出，不要有其他文字：
{
    "picks": [
        {
            "code": "候选列表中的股票代码",
            "score": 0.0到1.0的评分,
            "reasoning": "一句话选择理由",
            "stop_loss_pct": 0.03到0.07之间的止损比例,
            "take_profit_pct": 0.10到0.25之间的止盈比例
        }
    ],
    "overall_view": "一句话总结",
    "confidence": 0.0到1.0
}"""


class TrendHunterAgent(InvestAgent):
    """
    趋势猎手

    1. pre_filter(): 算法预筛出趋势候选（~20只）
    2. analyze():    LLM 精选 3-5 只
    两种模式任意降级
    """

    def __init__(self, llm_caller=None):
        super().__init__(AgentConfig(name="TrendHunter", role="hunter"), llm_caller)

    def perceive(self, data: List[dict]) -> List[dict]:
        """感知：对全市场股票进行趋势预过滤"""
        return self.pre_filter(data)

    def reason(self, perception: List[dict], context: Optional[RegimeResult] = None) -> dict:
        """推理：结合显式上下文进行 LLM 或算法分析。"""
        regime = context or {"regime": "oscillation"}
        return self.analyze(perception, regime)

    def act(self, reasoning: dict) -> dict:
        """行动：返回选股方案"""
        return reasoning

    def pre_filter(self, summaries: List[dict], max_candidates: int = 20) -> List[dict]:
        """
        算法预筛：从全部摘要中筛出趋势候选

        条件：MA 非空头，MACD 非死叉/看空，RSI 25-75，5日涨跌 > -3%
        """
        candidates = []
        for s in summaries:
            if s["ma_trend"] == "空头":
                continue
            if s["macd"] in ("死叉", "看空"):
                continue
            if s["rsi"] > 75 or s["rsi"] < 25:
                continue
            if s["change_5d"] < -3:
                continue

            ts = 0.0
            if s["ma_trend"] == "多头":  ts += 0.3
            if s["macd"] == "金叉":      ts += 0.3
            elif s["macd"] == "看多":    ts += 0.15
            if s["vol_ratio"] > 1.0:     ts += 0.1
            if 40 <= s["rsi"] <= 65:     ts += 0.15
            if s["change_20d"] > 0:      ts += 0.15

            s_copy = dict(s)
            s_copy["trend_score"] = round(ts, 3)
            candidates.append(s_copy)

        candidates.sort(key=lambda x: x["trend_score"], reverse=True)
        result = candidates[:max_candidates]
        logger.info(f"🔍 TrendHunter预筛: {len(summaries)}只 → {len(result)}只趋势候选")
        return result

    def analyze(self, candidates: List[dict], regime: dict) -> dict:
        """LLM 精选，可选为插入历史情境记忆。"""
        if not self.llm or not candidates:
            return self.analyze_fallback(candidates)

        # 检索 BM25 历史教训并构建提示文本
        memory_section = ""
        if self.situation_memory is not None and len(self.situation_memory) > 0:
            situation_desc = (
                f"市场状态:{regime.get('regime', '?')} | "
                f"5日涨跌中位:机器计算 | RSI平均:{candidates[0].get('rsi', 50):.0f}"
            )
            memory_section = self.situation_memory.format_hints_for_prompt(
                situation_desc, n_matches=2
            )

        user_msg = (
            f"当前市场状态: {regime.get('regime', '未知')}（{regime.get('reasoning', '')}）\n\n"
            f"以下是{len(candidates)}只趋势候选股的技术指标：\n\n"
            f"{format_stock_table(candidates)}\n\n"
            + (f"{memory_section}\n\n" if memory_section else "")
            + f"请从中选择3-5只最有上涨潜力的股票。"
        )

        try:
            result = self.llm.call_json(self.config.system_prompt, user_msg)
        except (ValueError, TypeError) as e:
            logger.warning(f"TrendHunter LLM调用异常(数据/参数): {e}")
            return self.analyze_fallback(candidates)
        except Exception as e:
            logger.exception(f"TrendHunter LLM调用失败(网络/未知): {e}")
            return self.analyze_fallback(candidates)

        if result.get("_parse_error"):
            return self.analyze_fallback(candidates)

        result = self._validate(result, [c["code"] for c in candidates])
        logger.info(f"🎯 TrendHunter(LLM): 推荐{len(result['picks'])}只, 置信度{result['confidence']:.0%}")
        return result

    def analyze_fallback(self, candidates: List[dict]) -> dict:
        """算法兜底：按趋势评分取前 5"""
        if not candidates:
            return {"picks": [], "overall_view": "无候选", "confidence": 0.0}
        picks = []
        for s in candidates[:5]:
            picks.append({
                "code": s["code"],
                "score": min(1.0, s.get("trend_score", s.get("algo_score", 0.5))),
                "reasoning": f"MA{s['ma_trend']}/MACD{s['macd']}/RSI{s['rsi']:.0f}",
                "stop_loss_pct": 0.05,
                "take_profit_pct": 0.15,
            })
        logger.info(f"🎯 TrendHunter(算法): 推荐{len(picks)}只")
        return {"picks": picks, "overall_view": "算法选股", "confidence": 0.5}

    def _validate(self, result: dict, valid_codes: List[str]) -> dict:
        valid_picks = []
        for p in result.get("picks", []):
            code = p.get("code", "")
            if code not in valid_codes:
                continue
            valid_picks.append({
                "code": code,
                "score": max(0.0, min(1.0, float(p.get("score", 0.5)))),
                "reasoning": str(p.get("reasoning", "")),
                "stop_loss_pct": max(0.01, min(0.15, float(p.get("stop_loss_pct", 0.05)))),
                "take_profit_pct": max(0.05, min(0.50, float(p.get("take_profit_pct", 0.15)))),
            })
        if not valid_picks and valid_codes:
            return self.analyze_fallback([
                {"code": c, "trend_score": 0.5, "algo_score": 0.5,
                 "ma_trend": "?", "macd": "?", "rsi": 50}
                for c in valid_codes[:3]
            ])
        result["picks"] = valid_picks[:8]
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
        if not isinstance(result.get("overall_view"), str):
            result["overall_view"] = ""
        return result



# ============================================================
# Part 4: 逆向猎手 Agent
# ============================================================

_CONTRARIAN_SYSTEM_PROMPT = """你是一个专业的逆向投资猎手，专注于寻找A股中被过度抛售、有反弹潜力的股票。

你的分析依据（按重要性排序）：
1. RSI水平：优先选择RSI低于40的超卖股票
2. 布林带位置：优先选择BB位置低于0.3的股票（接近下轨）
3. 近期跌幅：优先选择20日跌幅较大但近5日企稳的股票
4. 量比：底部放量是反弹信号，但不作为硬性条件

注意：
- 超跌反弹的风险较大，止损应比趋势股更宽
- 必须从提供的候选列表中选择，不要编造股票代码

请从候选股中选择2-4只最有反弹潜力的股票。

严格以JSON格式输出，不要有其他文字：
{
    "picks": [
        {
            "code": "候选列表中的股票代码",
            "score": 0.0到1.0的评分,
            "reasoning": "一句话选择理由",
            "stop_loss_pct": 0.06到0.12之间的止损比例,
            "take_profit_pct": 0.12到0.30之间的止盈比例
        }
    ],
    "overall_view": "一句话总结",
    "confidence": 0.0到1.0
}"""


class ContrarianAgent(InvestAgent):
    """
    逆向猎手

    1. pre_filter(): 算法预筛超跌候选（~15只）
    2. analyze():    LLM 精选 2-4 只
    """

    def __init__(self, llm_caller=None):
        super().__init__(AgentConfig(name="Contrarian", role="hunter"), llm_caller)

    def perceive(self, data: List[dict]) -> List[dict]:
        """感知：对全市场股票进行超跌预过滤"""
        return self.pre_filter(data)

    def reason(self, perception: List[dict], context: Optional[RegimeResult] = None) -> dict:
        """推理：结合显式上下文进行反弹潜力分析。"""
        regime = context or {"regime": "oscillation"}
        return self.analyze(perception, regime)

    def act(self, reasoning: dict) -> dict:
        """行动：返回选股方案"""
        return reasoning

    def pre_filter(self, summaries: List[dict], max_candidates: int = 15) -> List[dict]:
        """算法预筛：RSI<40，BB位置<0.4，5日跌幅 -15%~0%"""
        candidates = []
        for s in summaries:
            if s["rsi"] >= 40:       continue
            if s["bb_pos"] >= 0.4:   continue
            if s["change_5d"] > 0:   continue
            if s["change_5d"] < -15: continue

            cs = 0.0
            if s["rsi"] < 30:   cs += 0.35
            elif s["rsi"] < 35: cs += 0.25
            elif s["rsi"] < 40: cs += 0.15
            if s["bb_pos"] < 0.2:   cs += 0.25
            elif s["bb_pos"] < 0.3: cs += 0.15
            if s["vol_ratio"] > 1.2: cs += 0.15
            if s["change_5d"] < -5:  cs += 0.15
            if s["change_20d"] > s["change_5d"] * 3: cs += 0.1

            s_copy = dict(s)
            s_copy["contrarian_score"] = round(cs, 3)
            candidates.append(s_copy)

        candidates.sort(key=lambda x: x["contrarian_score"], reverse=True)
        result = candidates[:max_candidates]
        logger.info(f"🔍 Contrarian预筛: {len(summaries)}只 → {len(result)}只超跌候选")
        return result

    def analyze(self, candidates: List[dict], regime: dict) -> dict:
        """LLM 精选，可选为插入历史情境记忆。"""
        if not self.llm or not candidates:
            return self.analyze_fallback(candidates)

        # 检索 BM25 历史教训
        memory_section = ""
        if self.situation_memory is not None and len(self.situation_memory) > 0:
            situation_desc = (
                f"市场状态:{regime.get('regime', '?')} | "
                f"RSI超卖候选: {candidates[0].get('rsi', 30):.0f}"
            )
            memory_section = self.situation_memory.format_hints_for_prompt(
                situation_desc, n_matches=2
            )

        user_msg = (
            f"当前市场状态: {regime.get('regime', '未知')}（{regime.get('reasoning', '')}）\n\n"
            f"以下是{len(candidates)}只超跌候选股的技术指标：\n\n"
            f"{format_stock_table(candidates)}\n\n"
            + (f"{memory_section}\n\n" if memory_section else "")
            + f"请从中选择2-4只最有反弹潜力的股票。"
        )

        try:
            result = self.llm.call_json(self.config.system_prompt, user_msg)
        except (ValueError, TypeError) as e:
            logger.warning(f"Contrarian LLM调用异常(数据/参数): {e}")
            return self.analyze_fallback(candidates)
        except Exception as e:
            logger.exception(f"Contrarian LLM调用失败(网络/未知): {e}")
            return self.analyze_fallback(candidates)

        if result.get("_parse_error"):
            return self.analyze_fallback(candidates)

        result = self._validate(result, [c["code"] for c in candidates])
        logger.info(f"🎯 Contrarian(LLM): 推荐{len(result['picks'])}只, 置信度{result['confidence']:.0%}")
        return result

    def analyze_fallback(self, candidates: List[dict]) -> dict:
        """算法兜底：按反弹潜力评分取前 5"""
        if not candidates:
            return {"picks": [], "overall_view": "无候选", "confidence": 0.0}
        picks = []
        for s in candidates[:5]:
            picks.append({
                "code": s["code"],
                "score": min(1.0, s.get("contrarian_score", s.get("algo_score", 0.5))),
                "reasoning": f"RSI{s['rsi']:.0f}/BB{s['bb_pos']:.2f}/{s['change_5d']:+.1f}%",
                "stop_loss_pct": 0.03,
                "take_profit_pct": 0.10,
            })
        return {"picks": picks, "overall_view": "算法选股", "confidence": 0.5}

    def _validate(self, result: dict, valid_codes: List[str]) -> dict:
        valid_picks = []
        for p in result.get("picks", []):
            code = p.get("code", "")
            if code not in valid_codes:
                continue
            valid_picks.append({
                "code": code,
                "score": max(0.0, min(1.0, float(p.get("score", 0.5)))),
                "reasoning": str(p.get("reasoning", "")),
                "stop_loss_pct": max(0.01, min(0.15, float(p.get("stop_loss_pct", 0.03)))),
                "take_profit_pct": max(0.05, min(0.50, float(p.get("take_profit_pct", 0.10)))),
            })
        if not valid_picks and valid_codes:
            return self.analyze_fallback([
                {"code": c, "contrarian_score": 0.5, "algo_score": 0.5,
                 "rsi": 30, "bb_pos": 0.2, "change_5d": -5}
                for c in valid_codes[:3]
            ])
        result["picks"] = valid_picks[:8]
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
        if not isinstance(result.get("overall_view"), str):
            result["overall_view"] = ""
        return result



# ============================================================
# Part 5: 策略分析师 Agent
# ============================================================

_STRATEGIST_SYSTEM_PROMPT = """你是资深策略分析师，负责审查投资组合并提供风险评估。

你的职责：
1. 评估组合的整体风险水平
2. 识别潜在风险点
3. 提供优化建议

审查维度：
- 行业集中度（避免过度集中）
- 风格偏好（成长/价值/平衡）
- 相关性（同涨同跌风险）
- 市场敏感度

严格以JSON格式输出：
{
    "risk_level": "low/medium/high",
    "assessment": "一句话评估",
    "concerns": ["风险点1", "风险点2"],
    "suggestions": ["建议1", "建议2"]
}"""


class StrategistAgent(InvestAgent):
    """策略分析师 — 审查组合风险，提供优化建议"""

    def __init__(self, llm_caller=None):
        super().__init__(AgentConfig(name="Strategist", role="strategist"), llm_caller)

    def perceive(self, data: dict) -> dict:
        """感知：获取猎手推荐和市场状态"""
        return data

    def reason(self, perception: dict) -> dict:
        """推理：评估组合风险"""
        trend_picks = perception.get("trend_picks", {"picks": []})
        contrarian_picks = perception.get("contrarian_picks", {"picks": []})
        regime = perception.get("regime", {"regime": "oscillation"})
        return self.review(trend_picks, contrarian_picks, regime)

    def act(self, reasoning: dict) -> dict:
        """行动：返回风险评估报告"""
        return reasoning

    def review(self, trend_picks: dict, contrarian_picks: dict, regime: dict) -> dict:
        """
        审查组合，给出风险评估

        Returns:
            {"risk_level", "assessment", "concerns", "suggestions"}
        """
        all_picks = trend_picks.get("picks", []) + contrarian_picks.get("picks", [])
        if not all_picks:
            return {"risk_level": "medium", "assessment": "无候选股票", "concerns": [], "suggestions": []}

        if self.llm:
            return self._review_llm(all_picks, regime)
        return self._review_algorithm(all_picks, regime)

    def _review_llm(self, all_picks: List[dict], regime: dict) -> dict:
        codes = [p["code"] for p in all_picks]
        user_msg = (
            f"当前市场状态: {regime.get('regime', 'unknown')}\n\n"
            f"候选股票: {', '.join(codes)}\n\n"
            f"请评估这些股票的组合风险。"
        )
        try:
            result = self.llm.call_json(self.config.system_prompt, user_msg)
        except (ValueError, TypeError) as e:
            logger.warning(f"Strategist LLM调用异常(数据/参数): {e}")
            return self._review_algorithm(all_picks, regime)
        except Exception as e:
            logger.exception(f"Strategist LLM调用失败(网络/未知): {e}")
            return self._review_algorithm(all_picks, regime)

        if result.get("_parse_error"):
            return self._review_algorithm(all_picks, regime)

        if result.get("risk_level") not in ("low", "medium", "high"):
            result["risk_level"] = "medium"
        if not isinstance(result.get("concerns"), list): result["concerns"] = []
        if not isinstance(result.get("suggestions"), list): result["suggestions"] = []

        logger.info(f"📊 Strategist: 风险等级 {result['risk_level']}")
        return result

    def _review_algorithm(self, all_picks: List[dict], regime: dict) -> dict:
        regime_str = regime.get("regime", "oscillation")
        concerns, suggestions = [], []

        if len(all_picks) > 5:
            concerns.append(f"持仓过多({len(all_picks)}只)，建议精简到3-4只")
            suggestions.append("减少持仓数量，聚焦核心标的")

        scores = [p.get("score", 0) for p in all_picks]
        if scores and sum(scores) / len(scores) < 0.4:
            concerns.append("整体评分偏低，选股质量可能不足")
            suggestions.append("提高选股标准")

        if regime_str == "bear":
            risk_level = "high"
            concerns.append("熊市环境，系统性风险较高")
            suggestions.append("降低仓位，控制风险")
        elif regime_str == "bull":
            risk_level = "low"
        else:
            risk_level = "medium"

        stop_losses = [p.get("stop_loss_pct", 0.05) for p in all_picks]
        if stop_losses and max(stop_losses) > 0.10:
            concerns.append("部分股票止损设置过宽")
            suggestions.append("收紧止损线")

        return {
            "risk_level": risk_level,
            "assessment": f"{regime_str}市环境，{len(all_picks)}只候选，风险{risk_level}级",
            "concerns": concerns,
            "suggestions": suggestions,
        }



# ============================================================
# Part 6: 指挥官 Agent
# ============================================================

_COMMANDER_SYSTEM_PROMPT = """你是投资团队的指挥官，负责综合所有分析师的意见做最终决策。

你的决策原则：
1. 市场状态决定总仓位：
   - 牛市：积极配置，选4-6只，现金储备20%
   - 震荡市：均衡配置，选3-4只，现金储备30%
   - 熊市：保守配置，选1-2只，现金储备50%
2. 尽量从趋势猎手和逆向猎手的推荐中都选一些（分散风险）
3. 高评分的股票给更高权重，但单只不超过25%
4. 每只股票的止损止盈采纳推荐者的建议
5. 趋势股可以设置 trailing_pct（跟踪止盈，建议0.08-0.12）

重要：你必须选择股票。不能返回空的positions列表。

严格以JSON格式输出，不要有其他文字：
{
    "positions": [
        {
            "code": "股票代码",
            "weight": 0.05到0.25之间的仓位权重,
            "stop_loss_pct": 止损比例,
            "take_profit_pct": 止盈比例,
            "trailing_pct": 跟踪止盈回撤比例（趋势股建议0.10，逆向股设为null）,
            "entry_method": "market",
            "source": "trend_hunter或contrarian",
            "reasoning": "选择理由"
        }
    ],
    "cash_reserve": 0.15到0.55之间的现金储备比例,
    "reasoning": "整体决策理由"
}"""


class CommanderAgent(InvestAgent):
    """
    指挥官

    整合所有 Agent 意见，产出最终 TradingPlan 的 JSON 数据
    由 SelectionMeeting 转换为 TradingPlan 对象
    支持 Agent 权重调整（Phase 4）
    """

    def __init__(self, llm_caller=None, agent_weights: dict = None):
        super().__init__(AgentConfig(name="Commander", role="commander"), llm_caller)
        self.agent_weights = agent_weights or {
            "trend_hunter": 1.0,
            "contrarian": 1.0,
            "strategist": 1.0,
        }

    def perceive(self, data: dict) -> dict:
        """感知：收集所有子 Agent 的结论"""
        return data

    def reason(self, perception: dict) -> dict:
        """推理：整合所有结论做出最终决策"""
        regime = perception.get("regime", {})
        trend_picks = perception.get("trend_picks", {"picks": []})
        contrarian_picks = perception.get("contrarian_picks", {"picks": []})
        strategy_review = perception.get("strategy_review", {"risk_level": "medium"})
        return self.integrate(regime, trend_picks, contrarian_picks, strategy_review)

    def act(self, reasoning: dict) -> dict:
        """行动：产出建仓计划"""
        return reasoning

    def set_agent_weights(self, weights: dict):
        """设置 Agent 权重（由 ReviewMeeting 调用）"""
        self.agent_weights = weights
        logger.info(f"📊 Commander 权重已更新: {weights}")

    def integrate(
        self,
        regime: dict,
        trend_picks: dict,
        contrarian_picks: dict,
        strategy_review: dict,
    ) -> dict:
        """
        整合所有意见，输出建仓决策

        Returns:
            {"positions": [...], "cash_reserve": float, "reasoning": str}
        """
        if not self.llm:
            return self.integrate_fallback(regime, trend_picks, contrarian_picks, strategy_review)

        user_msg = self._build_prompt(regime, trend_picks, contrarian_picks, strategy_review)
        try:
            result = self.llm.call_json(self.config.system_prompt, user_msg)
        except Exception as e:
            logger.exception(f"Commander LLM调用失败: {e}")
            return self.integrate_fallback(regime, trend_picks, contrarian_picks, strategy_review)

        if result.get("_parse_error"):
            return self.integrate_fallback(regime, trend_picks, contrarian_picks, strategy_review)

        all_valid_codes = set(
            p["code"] for p in trend_picks.get("picks", []) + contrarian_picks.get("picks", [])
        )
        result = self._validate(result, all_valid_codes, regime)
        logger.info(f"👨‍✈️ Commander(LLM): {len(result['positions'])}只入选, 现金储备{result['cash_reserve']:.0%}")
        return result

    def integrate_fallback(
        self,
        regime: dict,
        trend_picks: dict,
        contrarian_picks: dict,
        strategy_review: dict,
    ) -> dict:
        """算法兜底：合并推荐、按加权评分排序、分配仓位"""
        regime_str = regime.get("regime", "oscillation")
        regime_params = regime.get("params", {})
        max_pos = regime_params.get("max_positions", 3)
        cash_reserve = {"bull": 0.20, "oscillation": 0.30, "bear": 0.50}.get(regime_str, 0.30)

        trend_w = self.agent_weights.get("trend_hunter", 1.0)
        contrarian_w = self.agent_weights.get("contrarian", 1.0)

        all_picks = []
        for p in trend_picks.get("picks", []):
            all_picks.append({**p, "source": "trend_hunter",
                               "weighted_score": p.get("score", 0.5) * trend_w})
        for p in contrarian_picks.get("picks", []):
            all_picks.append({**p, "source": "contrarian",
                               "weighted_score": p.get("score", 0.5) * contrarian_w})

        # 去重（同一只取加权分数高的）
        seen: Dict[str, dict] = {}
        for p in all_picks:
            code = p["code"]
            if code not in seen or p["weighted_score"] > seen[code]["weighted_score"]:
                seen[code] = p
        unique = sorted(seen.values(), key=lambda x: x["weighted_score"], reverse=True)
        selected = unique[:max_pos]

        available = 1.0 - cash_reserve
        base_weight = min(available / len(selected), 0.25) if selected else 0.20

        positions = [{
            "code": p["code"],
            "weight": round(base_weight, 3),
            "stop_loss_pct": p.get("stop_loss_pct", 0.05),
            "take_profit_pct": p.get("take_profit_pct", 0.15),
            "trailing_pct": p.get("trailing_pct", 0.10),
            "entry_method": "market",
            "source": p.get("source", "algorithm"),
            "reasoning": p.get("reasoning", "算法整合"),
        } for p in selected]

        logger.info(f"👨‍✈️ Commander(算法): {len(positions)}只入选, 现金储备{cash_reserve:.0%}")
        return {"positions": positions, "cash_reserve": cash_reserve,
                "reasoning": f"算法整合: {regime_str}市, {len(positions)}只持仓"}

    def _build_prompt(self, regime, trend_picks, contrarian_picks, strategy_review) -> str:
        regime_str = regime.get("regime", "未知")
        regime_conf = regime.get("confidence", 0)
        regime_params = regime.get("params", {})
        trend_w = self.agent_weights.get("trend_hunter", 1.0)
        contrarian_w = self.agent_weights.get("contrarian", 1.0)

        lines = [
            f"## 市场状态",
            f"判断: {regime_str} (置信度{regime_conf:.0%})",
            f"建议最大持仓数: {regime_params.get('max_positions', 3)}",
            "",
            f"## Agent 权重",
            f"- 趋势猎手权重: {trend_w:.1f}",
            f"- 逆向猎手权重: {contrarian_w:.1f}",
            "",
            f"## 趋势猎手推荐 (置信度{trend_picks.get('confidence', 0):.0%}, 权重{trend_w:.1f})",
        ]
        for p in trend_picks.get("picks", []):
            lines.append(
                f"- {p['code']} 评分{p['score']:.2f} "
                f"止损{p['stop_loss_pct']:.0%} 止盈{p['take_profit_pct']:.0%} "
                f"| {p.get('reasoning', '')}"
            )
        if not trend_picks.get("picks"):
            lines.append("- （无推荐）")

        lines.append(f"\n## 逆向猎手推荐 (置信度{contrarian_picks.get('confidence', 0):.0%}, 权重{contrarian_w:.1f})")
        for p in contrarian_picks.get("picks", []):
            lines.append(
                f"- {p['code']} 评分{p['score']:.2f} "
                f"止损{p['stop_loss_pct']:.0%} 止盈{p['take_profit_pct']:.0%} "
                f"| {p.get('reasoning', '')}"
            )
        if not contrarian_picks.get("picks"):
            lines.append("- （无推荐）")

        lines.append(f"\n## 策略分析师评估 (风险: {strategy_review.get('risk_level', '未知')})")
        lines.append(strategy_review.get("assessment", ""))
        for c in strategy_review.get("concerns", []):
            lines.append(f"- ⚠️ {c}")
        for s in strategy_review.get("suggestions", []):
            lines.append(f"- 💡 {s}")

        lines.append("\n请综合以上信息，输出最终建仓计划。")
        return "\n".join(lines)

    def _validate(self, result: dict, valid_codes: set, regime: dict) -> dict:
        regime_params = regime.get("params", {})
        max_pos = regime_params.get("max_positions", 5)

        valid_positions = []
        for p in result.get("positions", []):
            code = p.get("code", "")
            if code not in valid_codes:
                continue
            valid_positions.append({
                "code": code,
                "weight": max(0.03, min(0.25, float(p.get("weight", 0.15)))),
                "stop_loss_pct": max(0.01, min(0.15, float(p.get("stop_loss_pct", 0.05)))),
                "take_profit_pct": max(0.05, min(0.50, float(p.get("take_profit_pct", 0.15)))),
                "trailing_pct": max(0.05, min(0.20, float(p.get("trailing_pct", 0.10)))),
                "entry_method": p.get("entry_method", "market"),
                "source": p.get("source", "commander"),
                "reasoning": str(p.get("reasoning", "")),
            })

        valid_positions = valid_positions[:max_pos]
        cash = max(0.0, min(0.6, float(result.get("cash_reserve", 0.3))))
        total_weight = sum(p["weight"] for p in valid_positions)
        available = 1.0 - cash

        if total_weight > available and total_weight > 0:
            for p in valid_positions:
                p["weight"] = round(p["weight"] / total_weight * available, 3)

        if not valid_positions:
            return self.integrate_fallback(
                regime,
                {"picks": [{"code": c, "score": 0.5, "stop_loss_pct": 0.05,
                             "take_profit_pct": 0.15, "trailing_pct": 0.10, "reasoning": "兜底"}
                            for c in list(valid_codes)[:3]]},
                {"picks": []},
                {"risk_level": "medium", "concerns": []},
            )

        result["positions"] = valid_positions
        result["cash_reserve"] = cash
        if not isinstance(result.get("reasoning"), str):
            result["reasoning"] = ""
        return result



# ============================================================
# Part 7: 进化裁判 Agent（补全实现）
# ============================================================

_EVO_JUDGE_SYSTEM_PROMPT = """你是投资系统的进化裁判。你的职责是基于各Agent的历史表现，
评估是否需要触发策略进化，并给出具体的优化方向。

请严格以JSON格式输出：
{
    "should_evolve": true或false,
    "evolution_trigger": "performance_decay/new_market_regime/consistent_loss/none中的一个",
    "priority_agents": ["需要重点优化的agent名称列表"],
    "suggestions": ["具体优化建议1", "建议2"],
    "confidence": 0.0到1.0,
    "reasoning": "判断理由"
}"""


class EvoJudgeAgent(InvestAgent):
    """
    进化裁判

    职责：评估 Agent 表现，决定是否触发策略进化
    结合历史表现数据 + LLM 分析，给出进化决策
    """

    def __init__(self, llm_caller=None):
        super().__init__(AgentConfig(name="EvoJudge", role="judge"), llm_caller)
        self.evolution_threshold = 0.40  # 准确率低于此阈值触发进化
        self.consecutive_loss_trigger = 3  # 连续亏损次数触发进化
        self._evolution_count = 0         # 已触发进化次数

    def should_evolve(self, agent_stats: Dict) -> bool:
        """
        快速判断是否需要进化（不使用 LLM，供主循环频繁调用）

        Args:
            agent_stats: AgentTracker.compute_accuracy() 的输出

        Returns:
            bool: 是否需要触发进化
        """
        if not agent_stats:
            return False

        # 检查是否有 Agent 准确率过低
        for agent_name, stats in agent_stats.items():
            traded = stats.get("traded_count", 0)
            accuracy = stats.get("accuracy", 0)
            if traded >= 5 and accuracy < self.evolution_threshold:
                logger.info(
                    f"⚠️ EvoJudge: {agent_name} 准确率 {accuracy:.0%} < 阈值 {self.evolution_threshold:.0%}，触发进化"
                )
                return True

        return False

    def evaluate(
        self,
        agent_stats: Dict,
        cycle_results: List[Dict],
        market_regime: str = "oscillation",
    ) -> dict:
        """
        全面评估，给出详细的进化决策

        Args:
            agent_stats: AgentTracker.compute_accuracy() 输出
            cycle_results: 最近 N 轮结果 [{return_pct, is_profit, ...}]
            market_regime: 当前市场状态

        Returns:
            {"should_evolve", "evolution_trigger", "priority_agents",
             "suggestions", "confidence", "reasoning"}
        """
        # 先做快速规则判断
        quick_result = self._quick_evaluate(agent_stats, cycle_results)

        # 如果有 LLM 且规则已经确定需要进化，用 LLM 补充细节
        if self.llm and quick_result["should_evolve"]:
            return self._llm_evaluate(agent_stats, cycle_results, market_regime, quick_result)

        return quick_result

    def _quick_evaluate(self, agent_stats: Dict, cycle_results: List[Dict]) -> dict:
        """规则判断进化触发条件"""
        priority_agents = []
        suggestions = []
        trigger = "none"
        confidence = 0.3

        # 检查 Agent 准确率
        for name, stats in agent_stats.items():
            if stats.get("traded_count", 0) >= 5:
                acc = stats.get("accuracy", 0)
                if acc < self.evolution_threshold:
                    priority_agents.append(name)
                    suggestions.append(f"{name} 准确率 {acc:.0%}，需调整选股逻辑")
                    trigger = "performance_decay"
                    confidence = 0.7

        # 检查连续亏损
        if cycle_results:
            recent = cycle_results[-self.consecutive_loss_trigger:]
            if len(recent) >= self.consecutive_loss_trigger:
                consecutive_loss = all(not r.get("is_profit", False) for r in recent)
                if consecutive_loss:
                    trigger = "consistent_loss"
                    confidence = 0.85
                    suggestions.append(f"连续{self.consecutive_loss_trigger}轮亏损，需重新审视策略")

        # 检查整体胜率
        if cycle_results:
            win_rate = sum(1 for r in cycle_results if r.get("is_profit")) / len(cycle_results)
            if win_rate < 0.4:
                suggestions.append(f"整体胜率 {win_rate:.0%} 偏低")
                if trigger == "none":
                    trigger = "performance_decay"
                    confidence = max(confidence, 0.5)

        should_evolve = trigger != "none"
        if should_evolve:
            self._evolution_count += 1

        return {
            "should_evolve": should_evolve,
            "evolution_trigger": trigger,
            "priority_agents": priority_agents,
            "suggestions": suggestions,
            "confidence": confidence,
            "reasoning": f"规则判断: trigger={trigger}, 优先Agent={priority_agents}",
        }

    def _llm_evaluate(
        self,
        agent_stats: Dict,
        cycle_results: List[Dict],
        market_regime: str,
        quick_result: dict,
    ) -> dict:
        """用 LLM 补充进化建议"""
        stats_summary = []
        for name, stats in agent_stats.items():
            stats_summary.append(
                f"- {name}: 总推荐{stats.get('total_picks', 0)}次, "
                f"交易{stats.get('traded_count', 0)}次, "
                f"准确率{stats.get('accuracy', 0):.0%}"
            )

        recent_returns = [r.get("return_pct", 0) for r in (cycle_results or [])[-10:]]
        user_msg = (
            f"## 当前市场状态\n{market_regime}\n\n"
            f"## Agent 表现统计\n" + "\n".join(stats_summary) + "\n\n"
            f"## 最近10轮收益率\n{recent_returns}\n\n"
            f"## 规则判断结果\n"
            f"- 触发原因: {quick_result['evolution_trigger']}\n"
            f"- 优先优化: {quick_result['priority_agents']}\n\n"
            f"请给出详细的进化方向建议。"
        )

        try:
            result = self.llm.call_json(self.config.system_prompt, user_msg)
            if not result.get("_parse_error"):
                # 合并 LLM 建议和规则建议
                result["suggestions"] = list(set(
                    quick_result["suggestions"] + result.get("suggestions", [])
                ))
                result["priority_agents"] = list(set(
                    quick_result["priority_agents"] + result.get("priority_agents", [])
                ))
                return result
        except Exception as e:
            logger.exception(f"EvoJudge LLM调用失败: {e}")

        return quick_result

    def get_evolution_count(self) -> int:
        """返回已触发进化次数"""
        return self._evolution_count

    def perceive(self, data: dict) -> dict:
        """感知：获取 Agent 统计和周期结果"""
        return data

    def reason(self, perception: dict) -> dict:
        """推理：分析是否需要进化"""
        agent_stats = perception.get("agent_stats", {})
        cycle_results = perception.get("cycle_results", [])
        market_regime = perception.get("market_regime", "oscillation")
        return self.evaluate(agent_stats, cycle_results, market_regime)

    def act(self, reasoning: dict) -> dict:
        """行动：输出进化指挥意见"""
        return reasoning
