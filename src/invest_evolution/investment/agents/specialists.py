from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from invest_evolution.investment.contracts import AgentContext, EvalReport
from invest_evolution.investment.foundation.risk import sanitize_risk_params
from invest_evolution.investment.shared.policy import format_stock_table
from .base import AgentConfig, InvestAgent, RegimeResult

# Hunter agents

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


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
    default_stop_loss_pct: float | None = None,
    default_take_profit_pct: float | None = None,
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


def _normalize_candidate_code(code: str, valid_codes: Sequence[str]) -> str:
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


class GovernanceSelectorAgent(InvestAgent):
    """Thin selector that ranks governance candidates on the current canonical payload."""

    def __init__(self, llm_caller=None):
        super().__init__(AgentConfig(name="GovernanceSelector", role="selector"), llm_caller)

    def perceive(self, data: Mapping[str, Any]) -> dict[str, Any]:
        return dict(data or {})

    def reason(self, perception: Mapping[str, Any]) -> dict[str, Any]:
        return self.analyze(perception)

    def act(self, reasoning: dict) -> dict:
        return reasoning

    def analyze(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        current_manager_id = str(
            payload.get("current_manager_id") or payload.get("dominant_manager_id") or ""
        ).strip()
        allowed_manager_ids = [
            str(item).strip()
            for item in list(payload.get("allowed_manager_ids") or [])
            if str(item).strip()
        ]
        candidate_weights = {
            str(manager_id).strip(): float(weight or 0.0)
            for manager_id, weight in dict(payload.get("candidate_weights") or {}).items()
            if str(manager_id).strip()
        }
        candidate_manager_ids = [
            str(item).strip()
            for item in list(payload.get("candidate_manager_ids") or payload.get("active_manager_ids") or [])
            if str(item).strip()
        ]
        if not candidate_manager_ids and current_manager_id:
            candidate_manager_ids = [current_manager_id]

        ranked_candidates = [
            manager_id
            for manager_id in sorted(
                candidate_manager_ids,
                key=lambda manager_id: (
                    candidate_weights.get(manager_id, 0.0),
                    manager_id != current_manager_id,
                    manager_id,
                ),
                reverse=True,
            )
            if not allowed_manager_ids or manager_id in allowed_manager_ids
        ]
        if not ranked_candidates and current_manager_id:
            ranked_candidates = [current_manager_id]
        dominant_manager_id = ranked_candidates[0] if ranked_candidates else current_manager_id
        reasoning = (
            f"按治理候选权重排序，优先选择 {dominant_manager_id or 'unknown'}。"
            if dominant_manager_id
            else "治理候选为空，保持当前经理。"
        )
        return {
            "dominant_manager_id": dominant_manager_id,
            "candidate_manager_ids": ranked_candidates,
            "candidate_weights": {
                manager_id: candidate_weights.get(manager_id, 0.0)
                for manager_id in ranked_candidates
            },
            "allowed_manager_ids": allowed_manager_ids,
            "reasoning": reasoning,
            "confidence": 0.6 if ranked_candidates else 0.0,
        }


class TrendHunterAgent(InvestAgent):
    """
    趋势猎手

    1. pre_filter(): 算法预筛出趋势候选（~20只）
    2. analyze():    LLM 精选 3-5 只
    两种模式任意降级
    """

    def __init__(self, llm_caller=None):
        super().__init__(AgentConfig(name="TrendHunter", role="hunter"), llm_caller)

    def perceive(self, data: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """感知：对全市场股票进行趋势预过滤"""
        return self.pre_filter(data)

    def reason(self, perception: Sequence[Mapping[str, Any]], context: Optional[RegimeResult] = None) -> dict:
        """推理：结合显式上下文进行 LLM 或算法分析。"""
        regime = dict(context) if context else {"regime": "oscillation"}
        return self.analyze(perception, regime)

    def act(self, reasoning: dict) -> dict:
        """行动：返回选股方案"""
        return reasoning

    def pre_filter(self, summaries: Sequence[Mapping[str, Any]], max_candidates: int = 20) -> list[dict[str, Any]]:
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
            if s["ma_trend"] == "多头":
                ts += 0.3
            if s["macd"] == "金叉":
                ts += 0.3
            elif s["macd"] == "看多":
                ts += 0.15
            if s["vol_ratio"] > 1.0:
                ts += 0.1
            if 40 <= s["rsi"] <= 65:
                ts += 0.15
            if s["change_20d"] > 0:
                ts += 0.15

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

    def analyze(self, candidates: Sequence[Mapping[str, Any]], regime: dict) -> dict:
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
            + "请从中选择3-5只最有上涨潜力的股票。"
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
            logger.warning("TrendHunter structured output parse error; falling back to algorithmic picks")
            return self._fallback_analysis(candidates, contract_status="fallback_algorithm")

        result = self._validate(result, [c["code"] for c in candidates])
        logger.info(f"🎯 TrendHunter(LLM): 推荐{len(result['picks'])}只, 置信度{result['confidence']:.0%}")
        return result

    def _fallback_analysis(
        self,
        candidates: Sequence[Mapping[str, Any]],
        *,
        contract_status: str = "fallback_algorithm",
    ) -> dict:
        """算法兜底：按趋势评分取前 5"""
        if not candidates:
            return {
                "picks": [],
                "overall_view": "无候选",
                "confidence": 0.0,
                "contract_status": contract_status,
            }
        picks = []
        for s in candidates[:5]:
            ma_trend = str(s.get("ma_trend", "?") or "?")
            macd = str(s.get("macd", "?") or "?")
            rsi = _safe_float(s.get("rsi", 50), default=50.0)
            picks.append({
                "code": s["code"],
                "score": min(1.0, s.get("trend_score", s.get("algo_score", 0.5))),
                "reasoning": f"MA{ma_trend}/MACD{macd}/RSI{rsi:.0f}",
            })
        logger.info(f"🎯 TrendHunter(算法): 推荐{len(picks)}只")
        return {
            "picks": picks,
            "overall_view": "算法选股",
            "confidence": 0.5,
            "contract_status": contract_status,
        }

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
        result["contract_status"] = str(result.get("contract_status") or "validated")
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

    def perceive(self, data: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """感知：对全市场股票进行超跌预过滤"""
        return self.pre_filter(data)

    def reason(self, perception: Sequence[Mapping[str, Any]], context: Optional[RegimeResult] = None) -> dict:
        """推理：结合显式上下文进行反弹潜力分析。"""
        regime = dict(context) if context else {"regime": "oscillation"}
        return self.analyze(perception, regime)

    def act(self, reasoning: dict) -> dict:
        """行动：返回选股方案"""
        return reasoning

    def pre_filter(self, summaries: Sequence[Mapping[str, Any]], max_candidates: int = 15) -> list[dict[str, Any]]:
        """算法预筛：RSI<40，BB位置<0.4，5日跌幅 -15%~0%"""
        candidates = []
        for s in summaries:
            if s["rsi"] >= 40:
                continue
            if s["bb_pos"] >= 0.4:
                continue
            if s["change_5d"] > 0:
                continue
            if s["change_5d"] < -15:
                continue

            cs = 0.0
            if s["rsi"] < 30:
                cs += 0.35
            elif s["rsi"] < 35:
                cs += 0.25
            elif s["rsi"] < 40:
                cs += 0.15
            if s["bb_pos"] < 0.2:
                cs += 0.25
            elif s["bb_pos"] < 0.3:
                cs += 0.15
            if s["vol_ratio"] > 1.2:
                cs += 0.15
            if s["change_5d"] < -5:
                cs += 0.15
            if s["change_20d"] > s["change_5d"] * 3:
                cs += 0.1

            s_copy = dict(s)
            s_copy["contrarian_score"] = round(cs, 3)
            candidates.append(s_copy)

        candidates.sort(key=lambda x: x["contrarian_score"], reverse=True)
        result = candidates[:max_candidates]
        logger.info(f"🔍 Contrarian预筛: {len(summaries)}只 → {len(result)}只超跌候选")
        return result

    def analyze(self, candidates: Sequence[Mapping[str, Any]], regime: dict) -> dict:
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
            + "请从中选择2-4只最有反弹潜力的股票。"
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
            logger.warning("Contrarian structured output parse error; falling back to algorithmic picks")
            return self._fallback_analysis(candidates, contract_status="fallback_algorithm")

        result = self._validate(result, [c["code"] for c in candidates])
        logger.info(f"🎯 Contrarian(LLM): 推荐{len(result['picks'])}只, 置信度{result['confidence']:.0%}")
        return result

    def _fallback_analysis(
        self,
        candidates: Sequence[Mapping[str, Any]],
        *,
        contract_status: str = "fallback_algorithm",
    ) -> dict:
        """算法兜底：按反弹潜力评分取前 5"""
        if not candidates:
            return {
                "picks": [],
                "overall_view": "无候选",
                "confidence": 0.0,
                "contract_status": contract_status,
            }
        picks = []
        for s in candidates[:5]:
            rsi = _safe_float(s.get("rsi", 50), default=50.0)
            bb_pos = _safe_float(s.get("bb_pos", 0.5), default=0.5)
            change_5d = _safe_float(s.get("change_5d", 0.0), default=0.0)
            picks.append({
                "code": s["code"],
                "score": min(1.0, s.get("contrarian_score", s.get("algo_score", 0.5))),
                "reasoning": f"RSI{rsi:.0f}/BB{bb_pos:.2f}/{change_5d:+.1f}%",
            })
        return {
            "picks": picks,
            "overall_view": "算法选股",
            "confidence": 0.5,
            "contract_status": contract_status,
        }

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
        result["contract_status"] = str(result.get("contract_status") or "validated")
        return result

# ============================================================
# Part 5: 策略分析师 Agent


def _candidate_codes(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(item.get("code") or "").strip() for item in candidates if str(item.get("code") or "").strip()]
_QUALITY_SYSTEM_PROMPT = "你是价值质量分析师。你的职责是从候选股中识别估值合理、质量稳定、基本面更扎实的标的。请只输出 JSON，格式如下：{\"picks\": [{\"code\": \"股票代码\", \"score\": 0.5, \"reasoning\": \"选股理由\"}], \"confidence\": 0.5, \"overall_view\": \"总体观点\"}"
_DEFENSIVE_SYSTEM_PROMPT = "你是防御型配置分析师。你的职责是从候选股中识别低波动、回撤更可控、在弱势市里更抗跌的标的。请只输出 JSON，格式如下：{\"picks\": [{\"code\": \"股票代码\", \"score\": 0.5, \"reasoning\": \"选股理由\"}], \"confidence\": 0.5, \"overall_view\": \"总体观点\"}"


class QualityAgent(InvestAgent):
    def __init__(self, llm_caller=None):
        super().__init__(AgentConfig(name="QualityAgent", role="hunter"), llm_caller)

    def perceive(self, data: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        return list(data or [])

    def reason(self, perception: Sequence[Mapping[str, Any]]) -> dict:
        return self.analyze(perception)

    def act(self, reasoning: dict) -> dict:
        return reasoning

    def analyze_context(self, agent_context: AgentContext) -> dict:
        return self.analyze(list(agent_context.stock_summaries or []), regime=agent_context.regime)

    def analyze(self, candidates: Sequence[Mapping[str, Any]], regime: str = "oscillation") -> dict:
        if not candidates:
            return {"picks": [], "overall_view": "无候选", "confidence": 0.0}
        if self.llm:
            try:
                user_msg = f"当前市场状态: {regime}\n\n以下是候选股摘要：\n\n{format_stock_table(candidates)}\n\n请挑选最符合价值质量风格的 2-4 只股票。"
                result = self.llm.call_json(_QUALITY_SYSTEM_PROMPT, user_msg, warn_on_parse_error=False)
                if result and not result.get("_parse_error"):
                    validated = self._validate(result, _candidate_codes(candidates))
                    if validated.get("picks"):
                        return validated
                    logger.info("QualityAgent returned no usable picks; falling back to algorithm")
            except Exception:
                logger.debug("QualityAgent LLM failed; fallback to algorithm", exc_info=True)
        return self._fallback_analysis(candidates)

    def _fallback_analysis(self, candidates: Sequence[Mapping[str, Any]]) -> dict:
        ranked = []
        for item in candidates:
            score = float(item.get("value_quality_score", item.get("algo_score", 0.0)) or 0.0)
            pe = float(item.get("pe_ttm", item.get("pe", 0.0)) or 0.0)
            pb = float(item.get("pb", 0.0) or 0.0)
            roe = float(item.get("roe", 0.0) or 0.0)
            score += 0.08 if 0 < pe <= 25 else 0.0
            score += 0.06 if 0 < pb <= 3 else 0.0
            score += 0.10 if roe >= 8 else 0.0
            ranked.append((score, item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        picks = []
        for score, item in ranked[:4]:
            pe = item.get("pe_ttm", item.get("pe", 0.0))
            pb = item.get("pb", 0.0)
            roe = item.get("roe", 0.0)
            picks.append({
                "code": item["code"],
                "score": round(min(1.0, max(0.0, score)), 4),
                "reasoning": f"估值/质量视角：PE {pe}, PB {pb}, ROE {roe}",
            })
        return {"picks": picks, "overall_view": "价值质量优先，强调估值约束与盈利质量", "confidence": 0.55 if picks else 0.0}

    def _validate(self, result: dict, valid_codes: Sequence[str]) -> dict:
        valid = []
        for item in result.get("picks", []):
            code = _normalize_candidate_code(item.get("code", ""), valid_codes)
            if not code:
                continue
            valid.append({
                "code": code,
                "score": max(0.0, min(1.0, float(item.get("score", 0.5) or 0.5))),
                "reasoning": str(item.get("reasoning", "")),
            })
        result["picks"] = valid[:4]
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5) or 0.5)))
        if not isinstance(result.get("overall_view"), str):
            result["overall_view"] = ""
        return result


class DefensiveAgent(InvestAgent):
    def __init__(self, llm_caller=None):
        super().__init__(AgentConfig(name="DefensiveAgent", role="hunter"), llm_caller)

    def perceive(self, data: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        return list(data or [])

    def reason(self, perception: Sequence[Mapping[str, Any]]) -> dict:
        return self.analyze(perception)

    def act(self, reasoning: dict) -> dict:
        return reasoning

    def analyze_context(self, agent_context: AgentContext) -> dict:
        return self.analyze(list(agent_context.stock_summaries or []), regime=agent_context.regime)

    def analyze(self, candidates: Sequence[Mapping[str, Any]], regime: str = "bear") -> dict:
        if not candidates:
            return {"picks": [], "overall_view": "无候选", "confidence": 0.0}
        if self.llm:
            try:
                user_msg = f"当前市场状态: {regime}\n\n以下是候选股摘要：\n\n{format_stock_table(candidates)}\n\n请挑选最适合防御配置的 2-4 只股票。"
                result = self.llm.call_json(_DEFENSIVE_SYSTEM_PROMPT, user_msg, warn_on_parse_error=False)
                if result and not result.get("_parse_error"):
                    validated = self._validate(result, _candidate_codes(candidates))
                    if validated.get("picks"):
                        return validated
                    logger.info("DefensiveAgent returned no usable picks; falling back to algorithm")
            except Exception:
                logger.debug("DefensiveAgent LLM failed; fallback to algorithm", exc_info=True)
        return self._fallback_analysis(candidates)

    def _fallback_analysis(self, candidates: Sequence[Mapping[str, Any]]) -> dict:
        ranked = []
        for item in candidates:
            score = float(item.get("defensive_score", item.get("algo_score", 0.0)) or 0.0)
            volatility = float(item.get("volatility", 0.0) or 0.0)
            ma_trend = str(item.get("ma_trend", "交叉"))
            score += 0.10 if volatility and volatility < 0.03 else 0.0
            score += 0.05 if ma_trend == "多头" else 0.0
            ranked.append((score, item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        picks = []
        for score, item in ranked[:4]:
            vol = item.get("volatility", 0.0)
            change20 = item.get("change_20d", 0.0)
            picks.append({
                "code": item["code"],
                "score": round(min(1.0, max(0.0, score)), 4),
                "reasoning": f"防御视角：波动 {vol}, 20日涨跌 {change20:+.2f}%",
            })
        return {"picks": picks, "overall_view": "优先低波、回撤可控与抗跌性更强的标的", "confidence": 0.58 if picks else 0.0}

    def _validate(self, result: dict, valid_codes: Sequence[str]) -> dict:
        valid = []
        for item in result.get("picks", []):
            code = _normalize_candidate_code(item.get("code", ""), valid_codes)
            if not code:
                continue
            valid.append({
                "code": code,
                "score": max(0.0, min(1.0, float(item.get("score", 0.5) or 0.5))),
                "reasoning": str(item.get("reasoning", "")),
            })
        result["picks"] = valid[:4]
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5) or 0.5)))
        if not isinstance(result.get("overall_view"), str):
            result["overall_view"] = ""
        return result


# Reviewer agents
def _string_items(raw: Any, *, limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()][:limit]


def _normalize_agent_weight_adjustments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, list):
        return {}
    normalized: dict[str, Any] = {}
    for item in raw:
        if isinstance(item, dict):
            agent = str(item.get("agent") or item.get("name") or item.get("agent_name") or "").strip()
            if not agent:
                continue
            weight = item.get("weight")
            if weight is None:
                weight = item.get("value")
            if weight is None:
                weight = item.get("adjustment")
            normalized[agent] = weight
            continue
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            agent = str(item[0] or "").strip()
            if agent:
                normalized[agent] = item[1]
    return normalized


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

    def review_report(self, eval_report: EvalReport) -> dict:
        regime = {"regime": eval_report.regime}
        picks = {"picks": [{"code": code, "score": 0.5} for code in eval_report.selected_codes]}
        return self.review(picks, {"picks": []}, regime)

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

    def _review_llm(self, all_picks: Sequence[Mapping[str, Any]], regime: dict) -> dict:
        llm = self.llm
        if llm is None:
            return self._review_algorithm(all_picks, regime)
        codes = [p["code"] for p in all_picks]
        user_msg = (
            f"当前市场状态: {regime.get('regime', 'unknown')}\n\n"
            f"候选股票: {', '.join(codes)}\n\n"
            f"请评估这些股票的组合风险。"
        )
        try:
            result = llm.call_json(self.config.system_prompt, user_msg)
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
        if not isinstance(result.get("concerns"), list):
            result["concerns"] = []
        if not isinstance(result.get("suggestions"), list):
            result["suggestions"] = []

        logger.info(f"📊 Strategist: 风险等级 {result['risk_level']}")
        return result

    def _review_algorithm(self, all_picks: Sequence[Mapping[str, Any]], regime: dict) -> dict:
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

        return {
            "risk_level": risk_level,
            "assessment": f"{regime_str}市环境，{len(all_picks)}只候选，风险{risk_level}级",
            "concerns": concerns,
            "suggestions": suggestions,
        }



# ============================================================
# Part 6: 复盘决策综合员
# ============================================================


class ReviewDecisionAgent(InvestAgent):
    """复盘决策综合员。

    在复盘阶段综合策略分析、参数建议和 Agent 历史表现，
    形成下一轮可执行的调参/调权建议。
    该角色不负责编排运行时，也不再承担组合构建职责。
    """

    def __init__(self, llm_caller=None, policy: Optional[dict] = None):
        super().__init__(AgentConfig(name="ReviewDecision", role="review_decision"), llm_caller)
        self.review_policy = dict(policy or {})

    def perceive(self, data: dict) -> dict:
        return data

    def reason(self, perception: dict) -> dict:
        return self.decide(
            facts=dict(perception.get("facts") or {}),
            strategy_analysis=dict(perception.get("strategy_analysis") or {}),
            evo_assessment=dict(perception.get("evo_assessment") or {}),
            current_params=dict(perception.get("current_params") or {}),
        )

    def act(self, reasoning: dict) -> dict:
        return reasoning

    def set_policy(self, policy: Optional[dict] = None) -> None:
        self.review_policy = dict(policy or {})

    def decide(
        self,
        facts: dict,
        strategy_analysis: dict,
        evo_assessment: dict,
        current_params: dict,
    ) -> dict:
        if not self.llm or facts.get("empty"):
            return self._fallback_decision(facts, strategy_analysis, evo_assessment)

        user_msg = self._build_prompt(facts, strategy_analysis, evo_assessment, current_params)
        try:
            result = self.llm.call_json(self.config.system_prompt, user_msg)
        except Exception as exc:
            logger.exception(f"ReviewDecision LLM调用失败: {exc}")
            return self._fallback_decision(facts, strategy_analysis, evo_assessment)

        if result.get("_parse_error"):
            return self._fallback_decision(facts, strategy_analysis, evo_assessment)

        return self._validate_decision(result, facts)

    def _build_prompt(
        self,
        facts: dict,
        strategy_analysis: dict,
        evo_assessment: dict,
        current_params: dict,
    ) -> str:
        agent_accuracy = facts.get("agent_accuracy", {})
        agent_lines = []
        for name, stats in agent_accuracy.items():
            agent_lines.append(
                f"- {name}: 准确率{stats.get('accuracy', 0):.0%}, 交易{stats.get('traded_count', 0)}次, 盈利{stats.get('profitable_count', 0)}次"
            )
        if not agent_lines:
            agent_lines.append("- 暂无足够 Agent 准确率样本")

        research_feedback = dict(facts.get("research_feedback") or {})
        recommendation = dict(research_feedback.get("recommendation") or {})
        research_section: list[str] = []
        if research_feedback:
            research_section = [
                "## 问股校准反馈",
                f"- 建议偏置: {recommendation.get('bias') or 'unknown'}",
                f"- 样本数: {int(research_feedback.get('sample_count') or 0)}",
                f"- 摘要: {recommendation.get('summary') or '无'}",
            ]

        sections = [
            f"## 近期表现\n胜率{facts.get('win_rate', 0):.0%}，平均收益{facts.get('avg_return', 0):+.2f}%，总轮数{facts.get('total_cycles', 0)}",
            "## Agent 准确率",
            *agent_lines,
            *research_section,
            f"## 策略分析师意见\n问题：{strategy_analysis.get('problems', [])}\n建议：{strategy_analysis.get('suggestions', [])}",
            f"## 进化裁判意见\n方向：{evo_assessment.get('evolution_direction', 'maintain')}\n参数调整：{evo_assessment.get('param_adjustments', {})}\n建议：{evo_assessment.get('suggestions', [])}",
            f"## 当前参数\n{current_params}",
            "请综合以上事实，输出下一轮的采纳建议。只允许输出 strategy_suggestions / param_adjustments / agent_weight_adjustments / reasoning。",
        ]
        return "\n".join(sections)

    def _validate_decision(self, result: dict, facts: dict) -> dict:
        result["strategy_suggestions"] = _string_items(result.get("strategy_suggestions"), limit=6)

        if not isinstance(result.get("param_adjustments"), dict):
            result["param_adjustments"] = {}
        result["param_adjustments"] = self._sanitize_adjustment_payload(result.get("param_adjustments", {}))

        valid_agents = set((facts or {}).get("agent_accuracy", {}).keys())
        min_weight = float(self._policy_value('agent_weight.min', 0.3) or 0.3)
        max_weight = float(self._policy_value('agent_weight.max', 2.0) or 2.0)
        default_weight = float(self._policy_value('agent_weight.default', 1.0) or 1.0)
        clean_weights: dict[str, float] = {}
        for agent, weight in _normalize_agent_weight_adjustments(result.get("agent_weight_adjustments")).items():
            if valid_agents and agent not in valid_agents:
                continue
            try:
                clean_weights[agent] = round(max(min_weight, min(max_weight, float(weight))), 2)
            except (TypeError, ValueError):
                clean_weights[agent] = default_weight
        result["agent_weight_adjustments"] = clean_weights

        if not isinstance(result.get("reasoning"), str):
            result["reasoning"] = ""
        return result

    def _fallback_decision(self, facts: dict, strategy_analysis: dict, evo_assessment: dict) -> dict:
        min_trades = int(self._policy_value('agent_weight.min_traded_count', 3) or 3)
        formula_base = float(self._policy_value('agent_weight.formula_base', 0.5) or 0.5)
        default_weight = float(self._policy_value('agent_weight.default', 1.0) or 1.0)
        min_weight = float(self._policy_value('agent_weight.min', 0.3) or 0.3)
        max_weight = float(self._policy_value('agent_weight.max', 2.0) or 2.0)

        weight_adjustments: dict[str, float] = {}
        for agent, stats in (facts.get("agent_accuracy") or {}).items():
            accuracy = stats.get("accuracy", 0.5)
            traded_count = stats.get("traded_count", 0)
            if traded_count >= min_trades:
                weight_adjustments[agent] = round(max(min_weight, min(max_weight, formula_base + float(accuracy))), 2)
            else:
                weight_adjustments[agent] = default_weight

        param_adjustments = {
            key: value
            for key, value in (evo_assessment.get("param_adjustments") or {}).items()
            if value is not None
        }

        strategy_suggestions: list[str] = []
        for raw in list(strategy_analysis.get("suggestions") or []) + list(evo_assessment.get("suggestions") or []):
            item = str(raw).strip()
            if item and item not in strategy_suggestions:
                strategy_suggestions.append(item)
            if len(strategy_suggestions) >= 6:
                break

        decision = {
            "strategy_suggestions": strategy_suggestions,
            "param_adjustments": param_adjustments,
            "agent_weight_adjustments": weight_adjustments,
            "reasoning": f"复盘综合: 方向={evo_assessment.get('evolution_direction', 'maintain')}",
        }
        return self._validate_decision(decision, facts)

    def _policy_value(self, path: str, default: Any) -> Any:
        current: Any = self.review_policy
        for key in path.split('.'):
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current

    def _sanitize_adjustment_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, float] = {}
        for key, value in (payload or {}).items():
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if key in {"stop_loss_pct", "take_profit_pct", "position_size", "cash_reserve", "trailing_pct"} and 1.0 < numeric <= 100.0:
                numeric = numeric / 100.0
            normalized[key] = numeric

        risk_like = {
            key: value
            for key, value in normalized.items()
            if key in {"stop_loss_pct", "take_profit_pct", "position_size"}
        }
        clean_params = sanitize_risk_params(risk_like)

        cash_bounds = dict(self._policy_value('param_clamps.cash_reserve', {'min': 0.0, 'max': 0.80}) or {})
        trailing_bounds = dict(self._policy_value('param_clamps.trailing_pct', {'min': 0.03, 'max': 0.20}) or {})
        if 'cash_reserve' in normalized:
            clean_params['cash_reserve'] = max(float(cash_bounds.get('min', 0.0)), min(float(cash_bounds.get('max', 0.80)), normalized['cash_reserve']))
        if 'trailing_pct' in normalized:
            clean_params['trailing_pct'] = max(float(trailing_bounds.get('min', 0.03)), min(float(trailing_bounds.get('max', 0.20)), normalized['trailing_pct']))
        return clean_params


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
            llm = self.llm
            if llm is None:
                return quick_result
            result = llm.call_json(self.config.system_prompt, user_msg)
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

__all__ = [name for name in globals() if not name.startswith('_')]
