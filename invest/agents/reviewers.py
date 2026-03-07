import logging
from typing import Dict, List

from .base import AgentConfig, InvestAgent

logger = logging.getLogger(__name__)


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
            trailing_raw = p.get("trailing_pct", 0.10)
            trailing_pct = None if trailing_raw in (None, "", "null") else max(0.05, min(0.20, float(trailing_raw)))
            valid_positions.append({
                "code": code,
                "weight": max(0.03, min(0.25, float(p.get("weight", 0.15)))),
                "stop_loss_pct": max(0.01, min(0.15, float(p.get("stop_loss_pct", 0.05)))),
                "take_profit_pct": max(0.05, min(0.50, float(p.get("take_profit_pct", 0.15)))),
                "trailing_pct": trailing_pct,
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

__all__ = ["StrategistAgent", "CommanderAgent", "EvoJudgeAgent"]
