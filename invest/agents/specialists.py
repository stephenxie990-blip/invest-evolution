import logging
from typing import Any, Mapping, Sequence

from invest.contracts import AgentContext
from invest.shared.summaries import format_stock_table
from .base import AgentConfig, InvestAgent

logger = logging.getLogger(__name__)


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
                    return self._validate(result, _candidate_codes(candidates))
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
        valid_set = set(valid_codes)
        valid = []
        for item in result.get("picks", []):
            code = str(item.get("code", "")).strip()
            if code not in valid_set:
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
                    return self._validate(result, _candidate_codes(candidates))
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
        valid_set = set(valid_codes)
        valid = []
        for item in result.get("picks", []):
            code = str(item.get("code", "")).strip()
            if code not in valid_set:
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


__all__ = ["QualityAgent", "DefensiveAgent"]
