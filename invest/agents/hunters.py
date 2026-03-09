import logging
import re
from typing import Any, Dict, List, Optional

from invest.contracts import AgentContext
from invest.shared import (
    LLMCaller,
    compute_bb_position,
    compute_macd_signal,
    compute_rsi,
    format_stock_table,
)
from .base import AgentConfig, InvestAgent, RegimeResult

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


def _recover_hunter_result(
    raw: str,
    valid_codes: List[str],
) -> dict:
    confidence = _extract_jsonish_float(raw, 'confidence')
    overall_view = _extract_jsonish_string(raw, 'overall_view', ['confidence'])

    code_matches = list(re.finditer(r'["\']?code["\']?\s*:\s*["\']', raw or '', flags=re.IGNORECASE))
    picks: List[Dict[str, object]] = []
    for idx, match in enumerate(code_matches):
        start = match.start()
        end = code_matches[idx + 1].start() if idx + 1 < len(code_matches) else len(raw or '')
        segment = (raw or '')[start:end]
        code_match = re.search(r'["\']?code["\']?\s*:\s*["\']([^"\']+)', segment, flags=re.IGNORECASE)
        if not code_match:
            continue
        code = _normalize_candidate_code(code_match.group(1), valid_codes)
        if not code:
            continue
        score = _extract_jsonish_float(segment, 'score', ['reasoning'])
        reasoning = _extract_jsonish_string(segment, 'reasoning', ['code'])
        picks.append({
            'code': code,
            'score': float(score if score is not None else 0.5),
            'reasoning': reasoning or 'LLM 输出截断，已自动恢复',
        })

    return {
        'picks': picks,
        'overall_view': overall_view or 'LLM 输出截断，已自动恢复',
        'confidence': float(confidence if confidence is not None else 0.5),
    }


def _normalize_candidate_code(code: str, valid_codes: List[str]) -> str:
    raw = str(code or '').strip()
    if not raw:
        return ''
    if raw in valid_codes:
        return raw

    compact = raw.lower().replace('.', '').replace('_', '')
    digits = ''.join(ch for ch in compact if ch.isdigit())

    for valid in valid_codes:
        valid_compact = valid.lower().replace('.', '').replace('_', '')
        if compact == valid_compact:
            return valid
        valid_digits = ''.join(ch for ch in valid_compact if ch.isdigit())
        if digits and digits == valid_digits:
            return valid
    return ''

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

    def analyze_context(self, agent_context: AgentContext) -> dict:
        regime = {"regime": agent_context.regime, "reasoning": agent_context.summary}
        return self.analyze(agent_context.stock_summaries, regime)

    def analyze(self, candidates: List[dict], regime: dict) -> dict:
        """LLM 精选，可选为插入历史情境记忆。"""
        if not self.llm or not candidates:
            return self._fallback_analysis(candidates)

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
            result = self.llm.call_json(self.config.system_prompt, user_msg, warn_on_parse_error=False)
        except (ValueError, TypeError) as e:
            logger.warning(f"TrendHunter LLM调用异常(数据/参数): {e}")
            return self._fallback_analysis(candidates)
        except Exception as e:
            logger.exception(f"TrendHunter LLM调用失败(网络/未知): {e}")
            return self._fallback_analysis(candidates)

        if result.get("_parse_error"):
            recovered = _recover_hunter_result(result.get("_raw", ""), [c["code"] for c in candidates])
            if recovered.get("picks"):
                result = recovered
            else:
                return self._fallback_analysis(candidates)

        result = self._validate(result, [c["code"] for c in candidates])
        logger.info(f"🎯 TrendHunter(LLM): 推荐{len(result['picks'])}只, 置信度{result['confidence']:.0%}")
        return result

    def _fallback_analysis(self, candidates: List[dict]) -> dict:
        """算法兜底：按趋势评分取前 5"""
        if not candidates:
            return {"picks": [], "overall_view": "无候选", "confidence": 0.0}
        picks = []
        for s in candidates[:5]:
            picks.append({
                "code": s["code"],
                "score": min(1.0, s.get("trend_score", s.get("algo_score", 0.5))),
                "reasoning": f"MA{s['ma_trend']}/MACD{s['macd']}/RSI{s['rsi']:.0f}",
            })
        logger.info(f"🎯 TrendHunter(算法): 推荐{len(picks)}只")
        return {"picks": picks, "overall_view": "算法选股", "confidence": 0.5}

    def _validate(self, result: dict, valid_codes: List[str]) -> dict:
        valid_picks = []
        for p in result.get("picks", []):
            code = _normalize_candidate_code(p.get("code", ""), valid_codes)
            if not code:
                continue
            valid_picks.append({
                "code": code,
                "score": max(0.0, min(1.0, float(p.get("score", 0.5)))),
                "reasoning": str(p.get("reasoning", "")),
            })
        if not valid_picks and valid_codes:
            return self._fallback_analysis([
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
- 超跌反弹风险较高，但你只负责识别候选，不负责给执行参数
- 必须从提供的候选列表中选择，不要编造股票代码

请从候选股中选择2-4只最有反弹潜力的股票。

严格以JSON格式输出，不要有其他文字：
{
    "picks": [
        {
            "code": "候选列表中的股票代码",
            "score": 0.0到1.0的评分,
            "reasoning": "一句话选择理由"
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
            return self._fallback_analysis(candidates)

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
            result = self.llm.call_json(self.config.system_prompt, user_msg, warn_on_parse_error=False)
        except (ValueError, TypeError) as e:
            logger.warning(f"Contrarian LLM调用异常(数据/参数): {e}")
            return self._fallback_analysis(candidates)
        except Exception as e:
            logger.exception(f"Contrarian LLM调用失败(网络/未知): {e}")
            return self._fallback_analysis(candidates)

        if result.get("_parse_error"):
            recovered = _recover_hunter_result(result.get("_raw", ""), [c["code"] for c in candidates])
            if recovered.get("picks"):
                result = recovered
            else:
                return self._fallback_analysis(candidates)

        result = self._validate(result, [c["code"] for c in candidates])
        logger.info(f"🎯 Contrarian(LLM): 推荐{len(result['picks'])}只, 置信度{result['confidence']:.0%}")
        return result

    def _fallback_analysis(self, candidates: List[dict]) -> dict:
        """算法兜底：按反弹潜力评分取前 5"""
        if not candidates:
            return {"picks": [], "overall_view": "无候选", "confidence": 0.0}
        picks = []
        for s in candidates[:5]:
            picks.append({
                "code": s["code"],
                "score": min(1.0, s.get("contrarian_score", s.get("algo_score", 0.5))),
                "reasoning": f"RSI{s['rsi']:.0f}/BB{s['bb_pos']:.2f}/{s['change_5d']:+.1f}%",
            })
        return {"picks": picks, "overall_view": "算法选股", "confidence": 0.5}

    def _validate(self, result: dict, valid_codes: List[str]) -> dict:
        valid_picks = []
        for p in result.get("picks", []):
            code = _normalize_candidate_code(p.get("code", ""), valid_codes)
            if not code:
                continue
            valid_picks.append({
                "code": code,
                "score": max(0.0, min(1.0, float(p.get("score", 0.5)))),
                "reasoning": str(p.get("reasoning", "")),
            })
        if not valid_picks and valid_codes:
            return self._fallback_analysis([
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

__all__ = ["TrendHunterAgent", "ContrarianAgent"]
