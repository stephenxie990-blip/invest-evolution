import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import config
from invest.shared import AgentTracker, LLMCaller

try:
    from invest.debate import DebateOrchestrator, RiskDebateOrchestrator
    _HAS_DEBATE = True
except ImportError:
    _HAS_DEBATE = False

logger = logging.getLogger(__name__)


class ReviewMeeting:
    """
    复盘会议编排器

    流程：
    1. 事实对账（算法）：Agent 预测 vs 实际结果
    2. Strategist 分析（LLM）：策略层面问题
    3. EvoJudge 评估（LLM）：参数层面调整建议
    4. Commander 决策（LLM）：最终采纳

    触发条件：连续亏损 ≥ 3 / 每 N 个 cycle / 重大偏差
    """

    def __init__(
        self,
        llm_caller: Optional[LLMCaller] = None,
        agent_tracker: Optional[AgentTracker] = None,
        strategist: Optional[Any] = None,
        evo_judge: Optional[Any] = None,
        commander: Optional[Any] = None,
        enable_risk_debate: bool = True,
        max_risk_discuss_rounds: int = 1,
        deep_llm_caller: Optional[LLMCaller] = None,
    ):
        from invest.agents import StrategistAgent, EvoJudgeAgent, CommanderAgent
        self.llm = llm_caller
        self.tracker = agent_tracker
        self.strategist = strategist or StrategistAgent()
        self.evo_judge = evo_judge or EvoJudgeAgent()
        self.commander = commander or CommanderAgent()
        self.review_count = 0

        self._risk_debate: Optional[Any] = None
        if _HAS_DEBATE and enable_risk_debate and llm_caller is not None:
            deep = deep_llm_caller or llm_caller
            self._risk_debate = RiskDebateOrchestrator(
                fast_llm=llm_caller,
                deep_llm=deep,
                max_rounds=max_risk_discuss_rounds,
            )
            logger.info("ReviewMeeting: risk debate enabled (max_rounds=%d)", max_risk_discuss_rounds)

    def run(
        self,
        recent_results: List[dict],
        agent_accuracy: dict,
        current_params: dict,
        regime_history: List[str] = None,
    ) -> dict:
        """
        运行复盘会议

        Args:
            recent_results: 最近 N 个 cycle 结果
            agent_accuracy: AgentTracker.compute_accuracy() 的输出
            current_params: 当前策略参数
            regime_history: 最近的市场状态列表

        Returns:
            {"strategy_suggestions", "param_adjustments",
             "agent_weight_adjustments", "reasoning"}
        """
        self.review_count += 1
        logger.info(f"\n{'='*50}")
        logger.info(f"📋 复盘会议 #{self.review_count} 开始")

        facts = self._compile_facts(recent_results, agent_accuracy)
        self._log_facts(facts)

        strategy_analysis = self._strategist_analysis(facts, current_params)
        evo_assessment = self._evo_judge_assessment(facts, strategy_analysis)
        decision = self._commander_decision(facts, strategy_analysis, evo_assessment, current_params)

        # 反思闭环：让所有 Agent 根据事实和决策进行自我更新
        self._trigger_reflections(facts, decision)

        logger.info(f"📋 复盘会议 #{self.review_count} 完成")
        logger.info(f"  策略建议: {len(decision.get('strategy_suggestions', []))}条")
        logger.info(f"  参数调整: {decision.get('param_adjustments', {})}")
        logger.info(f"  权重调整: {decision.get('agent_weight_adjustments', {})}")
        logger.info(f"{'='*50}\n")

        return decision

    def _trigger_reflections(self, facts: dict, decision: dict):
        """触发所有 Agent 的结构化反思，并将结果写入各 Agent 的记忆库 (Phase 4)"""
        if facts.get("empty"):
            return

        is_overall_profit = facts.get("win_rate", 0) >= 0.5
        win_rate = facts.get("win_rate", 0)
        avg_return = facts.get("avg_return", 0)
        problems = decision.get("strategy_suggestions", [])

        # 构建结构化上下文，供四步骤反思法使用
        situation = (
            f"市场概述：近{facts.get('total_cycles', 0)}周期 "
            f"胜率{win_rate:.0%} 均收益{avg_return:+.2f}%"
        )
        action = f"采纳建议: {problems[:2] if problems else ['维持现状']}"
        result_desc = f"胜率{win_rate:.0%}，均收益{avg_return:+.2f}%"

        outcome = {
            "correct": is_overall_profit,
            "delta": 0.05,
            "win_rate": win_rate,
            "situation": situation,
            "action": action,
            "result": result_desc,
        }

        self.strategist.reflect(outcome)
        self.evo_judge.reflect(outcome)
        self.commander.reflect(outcome)
        
    def _compile_facts(self, recent_results: List[dict], agent_accuracy: dict) -> dict:
        """编译事实数据"""
        if not recent_results:
            return {"empty": True}

        total = len(recent_results)
        wins = sum(1 for r in recent_results if r.get("is_profit"))
        returns = [r.get("return_pct", 0) for r in recent_results]
        avg_return = sum(returns) / total

        meeting_results = [r for r in recent_results if r.get("plan_source") == "meeting"]
        algo_results = [r for r in recent_results if r.get("plan_source") != "meeting"]

        regime_stats: Dict[str, Dict] = {}
        for r in recent_results:
            rg = r.get("regime", "unknown")
            regime_stats.setdefault(rg, {"total": 0, "wins": 0, "returns": []})
            regime_stats[rg]["total"] += 1
            if r.get("is_profit"):
                regime_stats[rg]["wins"] += 1
            regime_stats[rg]["returns"].append(r.get("return_pct", 0))

        return {
            "empty": False,
            "total_cycles": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": wins / total,
            "avg_return": round(avg_return, 2),
            "returns": returns,
            "meeting_stats": {
                "count": len(meeting_results),
                "wins": sum(1 for r in meeting_results if r.get("is_profit")),
                "win_rate": sum(1 for r in meeting_results if r.get("is_profit")) / len(meeting_results)
                    if meeting_results else 0,
            },
            "algo_stats": {
                "count": len(algo_results),
                "wins": sum(1 for r in algo_results if r.get("is_profit")),
                "win_rate": sum(1 for r in algo_results if r.get("is_profit")) / len(algo_results)
                    if algo_results else 0,
            },
            "regime_stats": {
                rg: {
                    "total": s["total"],
                    "wins": s["wins"],
                    "win_rate": round(s["wins"] / s["total"], 2) if s["total"] > 0 else 0,
                    "avg_return": round(sum(s["returns"]) / len(s["returns"]), 2),
                }
                for rg, s in regime_stats.items()
            },
            "agent_accuracy": agent_accuracy,
        }

    def _log_facts(self, facts: dict):
        if facts.get("empty"):
            logger.info("  无数据")
            return
        logger.info(
            f"  📊 近期表现: {facts['total_cycles']}轮, "
            f"胜率{facts['win_rate']:.0%}, 均收益{facts['avg_return']:+.2f}%"
        )
        for rg, rs in facts.get("regime_stats", {}).items():
            logger.info(f"    {rg}: {rs['total']}轮, 胜率{rs['win_rate']:.0%}")
        for agent, stats in facts.get("agent_accuracy", {}).items():
            logger.info(
                f"    {agent}: 推荐{stats['total_picks']}次, "
                f"盈利{stats['profitable_count']}/{stats['traded_count']} "
                f"({stats['accuracy']:.0%})"
            )

    def _strategist_analysis(self, facts: dict, current_params: dict) -> dict:
        if not self.llm or facts.get("empty"):
            return self._strategist_fallback(facts)

        system = (
            "你是投资策略分析师。请分析近期交易表现，找出策略层面的问题。\n"
            '以JSON输出：{"problems": ["问题1"], "suggestions": ["建议1"], "confidence": 0.0-1.0}'
        )
        user = self._format_facts_for_llm(facts, current_params)

        try:
            result = self.llm.call_json(system, user)
            if not result.get("_parse_error"):
                logger.info(f"  📋 Strategist(LLM): {len(result.get('problems', []))}个问题")
                return result
        except Exception as e:
            logger.exception(f"Strategist LLM调用失败: {e}")

        return self._strategist_fallback(facts)

    def _evo_judge_assessment(self, facts: dict, strategy_analysis: dict) -> dict:
        if not self.llm or facts.get("empty"):
            return self._evo_judge_fallback(facts)

        system = (
            "你是进化裁判。基于策略分析结果，给出具体的参数调整建议。\n"
            '以JSON输出：{"param_adjustments": {"stop_loss_pct": 值或null, '
            '"take_profit_pct": 值或null, "position_size": 值或null}, '
            '"evolution_direction": "aggressive/conservative/maintain", "confidence": 0.0-1.0}'
        )
        user = (
            f"近期表现：胜率{facts['win_rate']:.0%}，平均收益{facts['avg_return']:+.2f}%\n"
            f"策略问题：{strategy_analysis.get('problems', [])}\n"
            f"策略建议：{strategy_analysis.get('suggestions', [])}\n"
            f"请给出参数调整建议。"
        )

        try:
            result = self.llm.call_json(system, user)
            if not result.get("_parse_error"):
                logger.info(f"  📋 EvoJudge(LLM): 方向={result.get('evolution_direction', '?')}")
                return result
        except Exception as e:
            logger.exception(f"EvoJudge LLM调用失败: {e}")

        return self._evo_judge_fallback(facts)

    def _commander_decision(
        self,
        facts: dict,
        strategy_analysis: dict,
        evo_assessment: dict,
        current_params: dict,
    ) -> dict:
        if not self.llm or facts.get("empty"):
            return self._commander_fallback(facts, evo_assessment)

        system = (
            "你是投资团队指挥官。综合策略分析和进化评估，做最终决策。\n"
            '以JSON输出：{"strategy_suggestions": ["建议1"], '
            '"param_adjustments": {"key": value}, '
            '"agent_weight_adjustments": {"trend_hunter": 1.0, "contrarian": 0.8}, '
            '"reasoning": "决策理由"}'
        )

        aa = facts.get("agent_accuracy", {})
        agent_lines = [
            f"  {name}: 准确率{stats['accuracy']:.0%} (盈利{stats['profitable_count']}/{stats['traded_count']})"
            for name, stats in aa.items()
        ]

        user = (
            f"## 近期表现\n胜率{facts['win_rate']:.0%}，平均收益{facts['avg_return']:+.2f}%\n\n"
            f"## Agent准确率\n" + "\n".join(agent_lines) + "\n\n"
            f"## 策略分析师意见\n问题：{strategy_analysis.get('problems', [])}\n"
            f"建议：{strategy_analysis.get('suggestions', [])}\n\n"
            f"## 进化裁判意见\n方向：{evo_assessment.get('evolution_direction', '未知')}\n"
            f"参数调整：{evo_assessment.get('param_adjustments', {})}\n\n"
            f"## 当前参数\n{current_params}\n\n"
            f"请综合以上信息，输出最终决策。"
            f"对于Agent权重：准确率高的给更高权重（>1.0），低的降低（<1.0）。"
        )

        try:
            result = self.llm.call_json(system, user)
            if not result.get("_parse_error"):
                return self._validate_decision(result, facts)
        except Exception as e:
            logger.exception(f"Commander LLM调用失败: {e}")

        return self._commander_fallback(facts, evo_assessment)

    # ===== 算法兜底 =====

    def _strategist_fallback(self, facts: dict) -> dict:
        problems, suggestions = [], []
        if facts.get("win_rate", 0) < 0.4:
            problems.append("胜率过低"); suggestions.append("收紧选股标准")
        if facts.get("avg_return", 0) < -3:
            problems.append("平均亏损过大"); suggestions.append("降低仓位")
        return {"problems": problems, "suggestions": suggestions, "confidence": 0.4}

    def _evo_judge_fallback(self, facts: dict) -> dict:
        adjustments, direction = {}, "maintain"
        win_rate = facts.get("win_rate", 0.5)
        if win_rate < 0.35:
            adjustments = {"stop_loss_pct": 0.03, "position_size": 0.15}
            direction = "conservative"
        elif win_rate > 0.65:
            adjustments = {"position_size": 0.25}
            direction = "aggressive"
        return {"param_adjustments": adjustments, "evolution_direction": direction, "confidence": 0.4}

    def _commander_fallback(self, facts: dict, evo_assessment: dict) -> dict:
        weight_adjustments = {}
        for agent, stats in facts.get("agent_accuracy", {}).items():
            acc = stats.get("accuracy", 0.5)
            weight_adjustments[agent] = round(0.5 + acc, 2) if stats.get("traded_count", 0) >= 3 else 1.0

        param_adj = {k: v for k, v in evo_assessment.get("param_adjustments", {}).items() if v is not None}
        return {
            "strategy_suggestions": evo_assessment.get("suggestions", []),
            "param_adjustments": param_adj,
            "agent_weight_adjustments": weight_adjustments,
            "reasoning": f"算法决策: 方向={evo_assessment.get('evolution_direction', 'maintain')}",
        }

    def _format_facts_for_llm(self, facts: dict, params: dict) -> str:
        lines = [
            f"## 近期交易表现（最近{facts['total_cycles']}轮）",
            f"- 胜率: {facts['win_rate']:.0%} ({facts['wins']}胜{facts['losses']}负)",
            f"- 平均收益: {facts['avg_return']:+.2f}%",
        ]
        for rg, rs in facts.get("regime_stats", {}).items():
            lines.append(f"- {rg}: {rs['total']}轮, 胜率{rs['win_rate']:.0%}")
        lines.append(f"\n## 当前参数\n{params}")
        lines.append("\n请分析存在的问题，给出改进建议。")
        return "\n".join(lines)

    def _validate_decision(self, result: dict, facts: dict) -> dict:
        if not isinstance(result.get("strategy_suggestions"), list):
            result["strategy_suggestions"] = []
        if not isinstance(result.get("param_adjustments"), dict):
            result["param_adjustments"] = {}

        clean_params = {}
        for k, v in result.get("param_adjustments", {}).items():
            if v is None:
                continue
            try:
                v = float(v)
                if k == "stop_loss_pct":    v = max(0.02, min(0.15, v))
                elif k == "take_profit_pct": v = max(0.05, min(0.50, v))
                elif k == "position_size":   v = max(0.05, min(0.30, v))
                clean_params[k] = v
            except (TypeError, ValueError):
                continue
        result["param_adjustments"] = clean_params

        clean_weights = {}
        for agent, w in result.get("agent_weight_adjustments", {}).items():
            try:
                clean_weights[agent] = round(max(0.3, min(2.0, float(w))), 2)
            except (TypeError, ValueError):
                clean_weights[agent] = 1.0
        result["agent_weight_adjustments"] = clean_weights

        if not isinstance(result.get("reasoning"), str):
            result["reasoning"] = ""
        return result


# ============================================================
# Part 3: 会议记录持久化（合并 MeetingLogger + MeetingRecorder）

__all__ = ["ReviewMeeting"]
