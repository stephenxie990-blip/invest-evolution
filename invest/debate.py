"""
投资进化系统 — 红蓝对抗辩论编排器 (Phase 3)

借鉴 TradingAgents 的多轮辩论机制：
- DebateOrchestrator: 多空对决（Bull vs Bear → 裁判）
- RiskDebateOrchestrator: 三方风控辩论（激进 / 保守 / 中立 → 风控裁判）

设计原则：
1. LLM dry-run 友好（每个环节均有算法兜底）
2. 轮数可配置（默认1轮，可通过 EvolutionConfig 调整）
3. 结果格式统一，便于 meetings.py 集成
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from invest.shared.contracts import TradingPlan
    from invest.shared.llm import LLMCaller

logger = logging.getLogger(__name__)


def _extract_jsonish_segment(raw: str, key: str, next_keys: List[str] | None = None) -> str:
    text = (raw or '').strip()
    if not text:
        return ''
    key_pattern = rf'["\']?{re.escape(key)}["\']?\s*:\s*'
    match = re.search(key_pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ''
    segment = text[match.end():]
    if next_keys:
        next_alt = '|'.join(re.escape(item) for item in next_keys)
        end_match = re.search(
            rf',\s*["\']?(?:{next_alt})["\']?\s*:',
            segment,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if end_match:
            segment = segment[:end_match.start()]
    return segment.strip().rstrip(',').strip()


def _extract_jsonish_string(raw: str, key: str, next_keys: List[str] | None = None) -> str:
    segment = _extract_jsonish_segment(raw, key, next_keys)
    if not segment:
        return ''
    if segment.startswith(('"', "'")):
        segment = segment[1:]
    if segment.endswith(('"', "'")):
        segment = segment[:-1]
    segment = segment.rstrip('}').rstrip(']').strip()
    return segment.replace('\\n', ' ').replace('\n', ' ').replace('\\"', '"').strip()


def _extract_jsonish_float(raw: str, key: str, next_keys: List[str] | None = None) -> float | None:
    segment = _extract_jsonish_segment(raw, key, next_keys)
    if not segment:
        return None
    match = re.search(r'-?\d+(?:\.\d+)?', segment)
    if not match:
        return None
    return float(match.group(0))


def _extract_jsonish_enum(raw: str, key: str, allowed: List[str], next_keys: List[str] | None = None) -> str:
    segment = _extract_jsonish_segment(raw, key, next_keys)
    lowered = segment.lower()
    for item in allowed:
        if item.lower() in lowered:
            return item
    return ''


def _extract_jsonish_string_list(raw: str, key: str, next_keys: List[str] | None = None) -> List[str]:
    segment = _extract_jsonish_segment(raw, key, next_keys)
    if not segment:
        return []
    quoted = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', segment)
    if not quoted:
        quoted = re.findall(r"'([^'\\]*(?:\\.[^'\\]*)*)'", segment)
    if quoted:
        return [item.replace('\\"', '"').strip() for item in quoted if item.strip()]
    if segment.startswith('['):
        segment = segment[1:]
    if segment.endswith(']'):
        segment = segment[:-1]
    return [part.strip().strip('"').strip("'") for part in segment.split(',') if part.strip()]



# ===========================================================
# Prompt 模板
# ===========================================================

_BULL_SYSTEM = """你是一位对当前股票持乐观看多立场的投资研究员（Bull Analyst）。
你的任务是为给定的候选股票构建强有力的多方论点：
- 指出增长潜力、竞争优势、正向技术信号
- 用事实和数据反驳空方的质疑
- 关注动量、趋势和情绪面的积极因素
保持论据具体、有针对性，避免泛泛而谈。"""

_BEAR_SYSTEM = """你是一位对当前股票持谨慎看空立场的风险研究员（Bear Analyst）。
你的任务是指出候选股票潜在的下行风险：
- 指出高估风险、技术面疲软、资金面异常
- 质疑多方论点中的薄弱环节
- 强调止损保护的重要性和可能的下跌空间
保持论据具体、有针对性，避免过度悲观。"""

_DEBATE_JUDGE_SYSTEM = """你是一位独立的投资决策裁判（Research Manager）。
你的职责是综合多空双方的辩论，做出客观的最终裁定：
- 公正权衡多空双方的论点，不偏袒任何一方
- 基于证据强度和风险调整后的预期做出判断
- 给出明确的 verdict（buy/hold/avoid）和置信度（0~1）
必须严格以 JSON 格式输出。"""

_AGGRESSIVE_SYSTEM = """你是一位激进型风险分析师（Aggressive Analyst）。
你认为高收益值得承担高风险，主张适度扩大敞口以捕捉机会。
分析当前交易计划的风险收益比，提出你的激进型风控建议。"""

_CONSERVATIVE_SYSTEM = """你是一位保守型风险分析师（Conservative Analyst）。
你将资本安全置于首位，主张严格控制回撤，宁可错过机会也不承担过大风险。
分析当前交易计划的潜在风险，提出你的保守型风控建议。"""

_NEUTRAL_SYSTEM = """你是一位中立型风险分析师（Neutral Analyst）。
你在激进与保守之间寻求平衡，基于市场实际情况给出均衡的风控建议。
综合激进和保守两方的观点，找出最合理的风险敞口范围。"""

_RISK_JUDGE_SYSTEM = """你是一位风险管理裁判（Risk Manager）。
你的职责是综合激进、保守、中立三方的风控建议，做出最终的组合风险裁定：
- 确定最终的仓位规模建议（占比）
- 确定止损止盈参数范围
- 给出整体风险等级（low/medium/high）
必须严格以 JSON 格式输出。"""


# ===========================================================
# 多空辩论
# ===========================================================

class DebateOrchestrator:
    """多空辩论编排器

    流程：
    1. BullAdvocate 发表看多论点（携带历史记忆）
    2. BearCritic 反驳并给出看空论点
    3. （可配置多轮往返）
    4. deep_llm 裁判综合判定，输出 verdict

    集成到 SelectionMeeting 中，对已筛选候选股票进行辩论审查。

    Example::

        debate = DebateOrchestrator(fast_llm, deep_llm, max_rounds=1)
        result = debate.debate(stock_info, regime)
        print(result["verdict"])  # "buy" / "hold" / "avoid"
    """

    def __init__(
        self,
        fast_llm: "LLMCaller",
        deep_llm: "LLMCaller",
        max_rounds: int = 1,
    ):
        self.fast_llm = fast_llm
        self.deep_llm = deep_llm
        self.max_rounds = max_rounds

    def debate(
        self,
        stock_info: Dict,
        regime: Dict,
        memory_hints: Optional[str] = None,
    ) -> Dict:
        """对单只股票进行多空辩论。

        Args:
            stock_info: 股票摘要字典（code, close, rsi, macd 等），通常来自 summarize_stocks()
            regime: 市场状态字典（regime, confidence, suggested_exposure 等）
            memory_hints: 来自 MarketSituationMemory 的历史情境提示文本

        Returns:
            {
              "verdict": "buy" | "hold" | "avoid",
              "confidence": 0.0 ~ 1.0,
              "bull_summary": str,
              "bear_summary": str,
              "reasoning": str,
              "source": "debate_llm" | "debate_fallback"
            }
        """
        stock_desc = self._format_stock(stock_info)
        regime_desc = self._format_regime(regime)
        memory_section = memory_hints or ""

        bull_history = ""
        bear_history = ""

        # 多轮辩论
        for round_idx in range(self.max_rounds):
            # Bull 发言
            bull_prompt = (
                f"候选股票信息：\n{stock_desc}\n\n"
                f"当前市场环境：{regime_desc}\n\n"
                f"{memory_section}\n\n"
                f"空方此前论点：{bear_history}\n\n"
                "请给出你的看多论点（不超过200字）："
            )
            bull_arg = self.fast_llm.call(_BULL_SYSTEM, bull_prompt, temperature=0.7, max_tokens=300)
            if not bull_arg or "dry_run" in bull_arg:
                bull_arg = f"[Round {round_idx+1}] Bull：技术指标显示趋势向上，动量良好，建议买入。"
            bull_history += f"\n[Round {round_idx+1}] Bull：{bull_arg}"

            # Bear 发言
            bear_prompt = (
                f"候选股票信息：\n{stock_desc}\n\n"
                f"当前市场环境：{regime_desc}\n\n"
                f"多方此前论点：{bull_history}\n\n"
                "请给出你的看空/风险提示论点（不超过200字）："
            )
            bear_arg = self.fast_llm.call(_BEAR_SYSTEM, bear_prompt, temperature=0.7, max_tokens=300)
            if not bear_arg or "dry_run" in bear_arg:
                bear_arg = f"[Round {round_idx+1}] Bear：估值存在高估风险，回撤风险不可忽视，建议谨慎。"
            bear_history += f"\n[Round {round_idx+1}] Bear：{bear_arg}"

        # 裁判判定
        judge_prompt = (
            f"候选股票：{stock_info.get('code', '?')} | {stock_desc}\n\n"
            f"市场环境：{regime_desc}\n\n"
            f"多方辩论记录：{bull_history}\n\n"
            f"空方辩论记录：{bear_history}\n\n"
            "请综合双方论点，做出最终裁定。\n"
            "仅输出单行 JSON 对象，不要使用 Markdown 代码块，不要补充解释，不要在字符串值中再嵌套双引号。\n"
            "严格以 JSON 格式输出：\n"
            '{"verdict": "buy/hold/avoid", "confidence": 0.0~1.0, '
            '"bull_summary": "多方核心观点", '
            '"bear_summary": "空方核心观点", '
            '"reasoning": "裁判综合理由（不超过100字）"}'
        )
        result = self.deep_llm.call_json(_DEBATE_JUDGE_SYSTEM, judge_prompt, temperature=0.3, max_tokens=400, warn_on_parse_error=False)

        if result.get("_parse_error") or not result.get("verdict"):
            recovered = self._recover_judge_result(result.get("_raw", ""))
            if recovered is not None:
                recovered["source"] = "debate_llm_recovered"
                return recovered
            return self.debate_fallback(stock_info)

        result["source"] = "debate_llm"
        return result

    def debate_fallback(self, stock_info: Dict) -> Dict:
        """算法兜底（LLM 不可用时）：根据技术指标直接给出判定。"""
        rsi = stock_info.get("rsi", 50)
        macd = stock_info.get("macd", "中性")
        ma_trend = stock_info.get("ma_trend", "交叉")
        change_5d = stock_info.get("change_5d", 0)

        # 规则打分
        score = 0
        if ma_trend == "多头":
            score += 2
        elif ma_trend == "空头":
            score -= 2
        if macd in ("金叉", "看多"):
            score += 1
        elif macd in ("死叉", "看空"):
            score -= 1
        if 35 <= rsi <= 65:
            score += 1
        elif rsi < 30 or rsi > 70:
            score -= 1
        if change_5d > 0:
            score += 1
        elif change_5d < -3:
            score -= 1

        if score >= 3:
            verdict, conf = "buy", 0.7
        elif score >= 1:
            verdict, conf = "hold", 0.5
        else:
            verdict, conf = "avoid", 0.6

        return {
            "verdict": verdict,
            "confidence": conf,
            "bull_summary": f"MA趋势:{ma_trend}, MACD:{macd}, RSI:{rsi:.0f}",
            "bear_summary": f"5日涨跌:{change_5d:+.1f}%，技术面综合评估",
            "reasoning": f"算法规则评分: {score}/5",
            "source": "debate_fallback",
        }

    def _recover_judge_result(self, raw: str) -> Optional[Dict]:
        verdict = _extract_jsonish_enum(raw, "verdict", ["buy", "hold", "avoid"], ["confidence", "bull_summary", "bear_summary", "reasoning"])
        if not verdict:
            return None
        confidence = _extract_jsonish_float(raw, "confidence", ["bull_summary", "bear_summary", "reasoning"])
        return {
            "verdict": verdict,
            "confidence": max(0.0, min(1.0, float(confidence if confidence is not None else 0.5))),
            "bull_summary": _extract_jsonish_string(raw, "bull_summary", ["bear_summary", "reasoning"]) or "",
            "bear_summary": _extract_jsonish_string(raw, "bear_summary", ["reasoning"]) or "",
            "reasoning": _extract_jsonish_string(raw, "reasoning") or "",
        }

    def _format_stock(self, info: Dict) -> str:
        return (
            f"代码:{info.get('code','?')} | 收盘:{info.get('close','?')} | "
            f"5日涨跌:{info.get('change_5d',0):+.1f}% | RSI:{info.get('rsi',50):.0f} | "
            f"MACD:{info.get('macd','?')} | MA趋势:{info.get('ma_trend','?')} | "
            f"BB位置:{info.get('bb_pos',0.5):.2f}"
        )

    def _format_regime(self, regime: Dict) -> str:
        return (
            f"市场状态:{regime.get('regime','?')} | "
            f"置信度:{regime.get('confidence',0):.0%} | "
            f"建议仓位:{regime.get('suggested_exposure',0.5):.0%}"
        )


# ===========================================================
# 三方风控辩论
# ===========================================================

class RiskDebateOrchestrator:
    """三方风控辩论编排器

    流程（借鉴 TradingAgents 的 ConditionalLogic）：
    1. AggressiveAnalyst 发言
    2. ConservativeAnalyst 反驳
    3. NeutralAnalyst 居间
    4. （可配置多轮）
    5. RiskJudge (deep_llm) 最终裁定

    集成到 ReviewMeeting 中，在 EvoJudge 评估之前执行。

    Example::

        risk_debate = RiskDebateOrchestrator(fast_llm, deep_llm, max_rounds=1)
        result = risk_debate.assess_risk(trading_plan, regime, portfolio_state)
        print(result["risk_level"])  # "low" / "medium" / "high"
    """

    def __init__(
        self,
        fast_llm: "LLMCaller",
        deep_llm: "LLMCaller",
        max_rounds: int = 1,
    ):
        self.fast_llm = fast_llm
        self.deep_llm = deep_llm
        self.max_rounds = max_rounds

    def assess_risk(
        self,
        trading_plan: "TradingPlan",
        regime: Dict,
        portfolio_state: Optional[Dict] = None,
    ) -> Dict:
        """对交易计划进行三方风控辩论。

        Args:
            trading_plan: 当前交易计划
            regime: 市场状态字典
            portfolio_state: 当前组合状态（可选，含持仓数、市值等）

        Returns:
            {
              "risk_level": "low" | "medium" | "high",
              "position_size_suggestion": 0.1 ~ 0.3,
              "stop_loss_suggestion": 0.03 ~ 0.10,
              "take_profit_suggestion": 0.10 ~ 0.30,
              "key_concerns": [str],
              "reasoning": str,
              "source": "risk_debate_llm" | "risk_debate_fallback"
            }
        """
        plan_desc = self._format_plan(trading_plan)
        regime_desc = (
            f"市场状态:{regime.get('regime','?')} | "
            f"建议仓位:{regime.get('suggested_exposure',0.5):.0%}"
        )
        portfolio_desc = self._format_portfolio(portfolio_state)

        aggressive_hist = ""
        conservative_hist = ""
        neutral_hist = ""
        full_hist = ""

        for round_idx in range(self.max_rounds):
            # AggressiveAnalyst
            agg_prompt = (
                f"交易计划：\n{plan_desc}\n市场环境：{regime_desc}\n{portfolio_desc}\n\n"
                f"此前辩论：{full_hist}\n\n"
                "从激进型角度分析风险收益，强调机会成本，给出建议（不超过150字）："
            )
            agg_arg = self.fast_llm.call(_AGGRESSIVE_SYSTEM, agg_prompt, temperature=0.7, max_tokens=250)
            if not agg_arg or "dry_run" in agg_arg:
                agg_arg = "激进型：建议满仓操作，当前行情机会大于风险，不应错过。"
            aggressive_hist += f"\n[R{round_idx+1}] 激进：{agg_arg}"
            full_hist += "\n" + aggressive_hist

            # ConservativeAnalyst
            con_prompt = (
                f"交易计划：\n{plan_desc}\n市场环境：{regime_desc}\n{portfolio_desc}\n\n"
                f"此前辩论：{full_hist}\n\n"
                "从保守型角度强调风控，严格审视下行风险，给出建议（不超过150字）："
            )
            con_arg = self.fast_llm.call(_CONSERVATIVE_SYSTEM, con_prompt, temperature=0.7, max_tokens=250)
            if not con_arg or "dry_run" in con_arg:
                con_arg = "保守型：当前市场波动较大，建议轻仓并设置较严格止损。"
            conservative_hist += f"\n[R{round_idx+1}] 保守：{con_arg}"
            full_hist += "\n" + conservative_hist

            # NeutralAnalyst
            neu_prompt = (
                f"交易计划：\n{plan_desc}\n市场环境：{regime_desc}\n{portfolio_desc}\n\n"
                f"激进方：{aggressive_hist}\n保守方：{conservative_hist}\n\n"
                "从中立型角度平衡激进与保守，给出均衡建议（不超过150字）："
            )
            neu_arg = self.fast_llm.call(_NEUTRAL_SYSTEM, neu_prompt, temperature=0.7, max_tokens=250)
            if not neu_arg or "dry_run" in neu_arg:
                neu_arg = "中立型：建议适度仓位，设置合理止损，在机会与风险之间取得平衡。"
            neutral_hist += f"\n[R{round_idx+1}] 中立：{neu_arg}"
            full_hist += "\n" + neutral_hist

        # 风控裁判
        judge_prompt = (
            f"交易计划：\n{plan_desc}\n市场环境：{regime_desc}\n\n"
            f"激进方意见：{aggressive_hist}\n"
            f"保守方意见：{conservative_hist}\n"
            f"中立方意见：{neutral_hist}\n\n"
            "请综合三方意见，给出最终风险裁定。\n"
            "仅输出单行 JSON 对象，不要使用 Markdown 代码块，不要补充解释，不要在字符串值中再嵌套双引号。\n"
            "严格以 JSON 格式输出：\n"
            '{"risk_level": "low/medium/high", '
            '"position_size_suggestion": 0.0~0.3, '
            '"stop_loss_suggestion": 0.03~0.10, '
            '"take_profit_suggestion": 0.10~0.30, '
            '"key_concerns": ["关键风险点1", "关键风险点2"], '
            '"reasoning": "裁判综合理由（不超过100字）"}'
        )
        result = self.deep_llm.call_json(_RISK_JUDGE_SYSTEM, judge_prompt, temperature=0.2, max_tokens=400, warn_on_parse_error=False)

        if result.get("_parse_error") or not result.get("risk_level"):
            recovered = self._recover_risk_judge_result(result.get("_raw", ""))
            if recovered is not None:
                recovered["source"] = "risk_debate_llm_recovered"
                return recovered
            return self.assess_risk_fallback(trading_plan, regime)

        result["source"] = "risk_debate_llm"
        return result

    def assess_risk_fallback(
        self,
        trading_plan: "TradingPlan",
        regime: Dict,
    ) -> Dict:
        """算法兜底：基于市场状态和计划参数直接判定风险。"""
        regime_name = regime.get("regime", "shock")
        suggested_exposure = regime.get("suggested_exposure", 0.5)
        n_positions = len(getattr(trading_plan, "positions", []))

        # 基于市场状态的默认风控参数
        if regime_name == "bull":
            risk_level = "low"
            pos_size = min(0.25, suggested_exposure / max(n_positions, 1))
            sl, tp = 0.05, 0.15
        elif regime_name == "bear":
            risk_level = "high"
            pos_size = min(0.15, suggested_exposure / max(n_positions, 1))
            sl, tp = 0.04, 0.10
        else:  # shock
            risk_level = "medium"
            pos_size = min(0.20, suggested_exposure / max(n_positions, 1))
            sl, tp = 0.05, 0.12

        return {
            "risk_level": risk_level,
            "position_size_suggestion": round(pos_size, 2),
            "stop_loss_suggestion": sl,
            "take_profit_suggestion": tp,
            "key_concerns": [f"当前市场:{regime_name}，{n_positions}只持仓"],
            "reasoning": f"算法兜底：基于市场状态({regime_name})和建议仓位({suggested_exposure:.0%})",
            "source": "risk_debate_fallback",
        }

    def _recover_risk_judge_result(self, raw: str) -> Optional[Dict]:
        risk_level = _extract_jsonish_enum(
            raw,
            "risk_level",
            ["low", "medium", "high"],
            ["position_size_suggestion", "stop_loss_suggestion", "take_profit_suggestion", "key_concerns", "reasoning"],
        )
        if not risk_level:
            return None
        pos_size = _extract_jsonish_float(raw, "position_size_suggestion", ["stop_loss_suggestion", "take_profit_suggestion", "key_concerns", "reasoning"])
        stop_loss = _extract_jsonish_float(raw, "stop_loss_suggestion", ["take_profit_suggestion", "key_concerns", "reasoning"])
        take_profit = _extract_jsonish_float(raw, "take_profit_suggestion", ["key_concerns", "reasoning"])
        return {
            "risk_level": risk_level,
            "position_size_suggestion": max(0.0, min(0.3, float(pos_size if pos_size is not None else 0.1))),
            "stop_loss_suggestion": max(0.01, min(0.15, float(stop_loss if stop_loss is not None else 0.05))),
            "take_profit_suggestion": max(0.05, min(0.5, float(take_profit if take_profit is not None else 0.15))),
            "key_concerns": _extract_jsonish_string_list(raw, "key_concerns", ["reasoning"]) or ["LLM输出格式异常，已自动恢复"],
            "reasoning": _extract_jsonish_string(raw, "reasoning") or "LLM输出格式异常，已自动恢复。",
        }

    def _format_plan(self, trading_plan: "TradingPlan") -> str:
        codes = getattr(trading_plan, "stock_codes", [])
        positions = getattr(trading_plan, "positions", [])
        lines = [f"持仓数:{len(codes)}只 | 来源:{getattr(trading_plan, 'source', '?')}"]
        for p in positions[:5]:  # 最多显示5只
            lines.append(
                f"  - {p.code}: 仓位{p.weight:.0%}, "
                f"止损{p.stop_loss_pct:.0%}, 止盈{p.take_profit_pct:.0%}"
            )
        return "\n".join(lines)

    def _format_portfolio(self, portfolio_state: Optional[Dict]) -> str:
        if not portfolio_state:
            return ""
        return (
            f"组合状态：持仓{portfolio_state.get('position_count',0)}只 | "
            f"市值{portfolio_state.get('portfolio_value',0):.0f} | "
            f"收益{portfolio_state.get('portfolio_return',0):+.2%}"
        )
