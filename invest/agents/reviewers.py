import logging
from typing import Any, Dict, List, Optional

from invest.contracts import EvalReport
from invest.foundation.risk import sanitize_risk_params
from .base import AgentConfig, InvestAgent

logger = logging.getLogger(__name__)


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

    def _review_llm(self, all_picks: List[dict], regime: dict) -> dict:
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

__all__ = ["StrategistAgent", "ReviewDecisionAgent", "EvoJudgeAgent"]
